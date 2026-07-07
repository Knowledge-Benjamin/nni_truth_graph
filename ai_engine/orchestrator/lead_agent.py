"""
ai_engine/orchestrator/lead_agent.py
─────────────────────────────────────────────────────────────────────────────
Sub-Agent: Claims a single PENDING lead from investigation_leads using
FOR UPDATE SKIP LOCKED (prevents any two concurrent agents from working
the same lead), generates targeted SearXNG queries, and injects the
resulting URLs into raw_urls tagged with the investigation_id.

One instance of this module is a single sub-agent worker.
The Orchestrator spawns up to MAX_CONCURRENT_AGENTS of these concurrently.
"""

import os
import sys
import time
import json
import re
import hashlib
import requests
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from neo4j import GraphDatabase  # type: ignore
from pydantic import BaseModel, Field

SEARXNG_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
SEARXNG_SEARCH_ENGINES = "google,bing"


def create_searxng_headers(secret: str = "") -> dict:
    headers = {
        "User-Agent": SEARXNG_USER_AGENT,
        "Accept": "application/json",
    }
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-API-KEY"] = secret
    return headers


def build_searxng_params(query: str, time_range: str | None = None) -> dict:
    params = {
        "q": query,
        "format": "json",
        "engines": SEARXNG_SEARCH_ENGINES,
    }
    if time_range:
        params["time_range"] = time_range
    return params


def extract_searxng_html_links(html_text: str) -> list[str]:
    urls = []
    for match in re.finditer(r'<article[^>]*class=["\'][^"\']*result[^"\']*["\'][^>]*>(.*?)</article>', html_text, re.S | re.I):
        article_html = match.group(1)
        href_match = re.search(r'href=["\'](https?://[^"\']+)["\']', article_html, re.I)
        if href_match:
            urls.append(href_match.group(1))
    if not urls:
        urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', html_text, re.I)
    return urls

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from ai_engine.core.llm_router import llm_pool

from .tools import (
    get_whois, get_dns_resolution, get_shodan, get_censys,
    get_hibp_breaches, get_virustotal,
    search_opencorporates, search_edgar,
    get_eth_balance, get_btc_balance
)


# ── Pydantic schema for the query generation LLM call ───────────────────────

class LeadQueryPlan(BaseModel):
    queries: list[str] = Field(
        description=(
            "3-6 targeted SearXNG search queries for this specific lead entity. "
            "Use OSINT operator syntax where applicable."
        )
    )
    rationale: str = Field(description="One sentence: what we expect to find.")


# ── Core lead agent function ─────────────────────────────────────────────────

def _fetch_entity_article_from_neo4j(entity_name: str) -> str:
    """
    Fetches a pre-generated encyclopedic article from Neo4j for a given entity.
    Returns a distilled plaintext summary (max ~3000 chars) or an empty string.
    This connection is opened and closed immediately — never held during inference.
    """
    NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            result = session.run(
                "MATCH (e:Entity {name: $name}) RETURN e.article AS article LIMIT 1",
                name=entity_name
            )
            record = result.single()
        driver.close()

        if not record or not record["article"]:
            return ""

        article_json = json.loads(record["article"])
        # Flatten sections into a concise summary for the LLM context window
        parts = []
        for section_name, section_data in article_json.items():
            if section_name.startswith("_"):
                continue
            content = section_data.get("content", "") if isinstance(section_data, dict) else ""
            if content:
                parts.append(f"### {section_name}\n{content[:600]}")
        summary = "\n\n".join(parts)
        return summary[:3000]  # Hard cap to protect context window
    except Exception as e:
        print(f"[LeadAgent] Could not fetch article for '{entity_name}' from Neo4j: {e}")
        return ""


def run_lead_agent(
    investigation_id: int,
    investigation_target: str,
    goal_type: str,
    searxng_url: str,
    searxng_source_id: int,
    executive_summary: str = "",
    knowledge_gaps: str = "",
) -> bool:
    """
    Atomically claims one PENDING lead, generates search queries using
    overarching investigation context, and injects URLs. Returns True if
    a lead was processed, False if none found.
    """
    claimed_lead = None
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    agent_conn = psycopg2.connect(DATABASE_URL)
    agent_conn.autocommit = False
    try:
        with agent_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, entity_name, lead_type, priority, context
                FROM investigation_leads
                WHERE investigation_id = %s
                  AND status = 'PENDING'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (investigation_id,)
            )
            row = cur.fetchone()

            if not row:
                agent_conn.rollback()
                agent_conn.close()
                return False

            lead_id    = row["id"]
            entity     = row["entity_name"]
            lead_type  = row["lead_type"]
            context    = row.get("context") or "No specific context provided."

            cur.execute(
                """
                UPDATE investigation_leads
                SET status = 'CLAIMED', claimed_at = NOW()
                WHERE id = %s
                """,
                (lead_id,)
            )
            agent_conn.commit()
            claimed_lead = {"id": lead_id, "entity": entity, "lead_type": lead_type, "context": context}
    except Exception as e:
        agent_conn.rollback()
        agent_conn.close()
        print(f"[LeadAgent] DB error while claiming lead: {e}")
        return False
    agent_conn.close()

    if not claimed_lead:
        return False

    entity    = claimed_lead["entity"]
    lead_id   = claimed_lead["id"]
    lead_type = claimed_lead["lead_type"]
    context   = claimed_lead["context"]

    print(f"[LeadAgent] Claimed lead #{lead_id} [{lead_type}]: '{entity}'")

    # ── Fetch pre-generated article baseline from Neo4j (closed immediately) ──
    entity_article = _fetch_entity_article_from_neo4j(entity)
    if entity_article:
        print(f"[LeadAgent] Loaded existing article baseline for '{entity}' ({len(entity_article)} chars)")
    else:
        print(f"[LeadAgent] No existing article for '{entity}' — agent will rely on raw OSINT only")

    # ── Execute Domain-Specific Tools ────────────────────────────────────────
    tool_results = []
    
    if lead_type == "IP":
        tool_results.append(get_shodan(entity))
        tool_results.append(get_censys(entity))
        tool_results.append(get_virustotal(entity, "IP"))
    elif lead_type == "DOMAIN":
        tool_results.append(get_whois(entity))
        tool_results.append(get_dns_resolution(entity))
        tool_results.append(get_censys(entity))
        tool_results.append(get_virustotal(entity, "DOMAIN"))
    elif lead_type == "EMAIL":
        tool_results.append(get_hibp_breaches(entity))
    elif lead_type == "ORGANISATION":
        tool_results.append(search_opencorporates(entity))
        tool_results.append(search_edgar(entity))
    elif lead_type == "WALLET":
        tool_results.append(get_eth_balance(entity))
        tool_results.append(get_btc_balance(entity))
        
    valid_results = [r for r in tool_results if r and "[Error]" not in r]
    
    if valid_results:
        combined_text = f"=== SYNTHETIC OSINT REPORT FOR {entity} ===\n\n" + "\n\n".join(valid_results)
        osint_sha256 = hashlib.sha256(combined_text.encode('utf-8', errors='replace')).hexdigest()
        
        pg_conn = psycopg2.connect(DATABASE_URL)
        with pg_conn.cursor() as cur:
            synthetic_url = f"api://osint/lead/{lead_id}/{time.time()}"
            cur.execute(
                """
                INSERT INTO raw_urls (source_id, url, metadata, status)
                VALUES (%s, %s, %s, 'SCRAPED')
                RETURNING id
                """,
                (searxng_source_id, synthetic_url, Json({"investigation_id": investigation_id, "lead_id": lead_id, "lead_entity": entity, "synthetic_osint": True}))
            )
            syn_url_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO raw_articles (url_id, title, author, publish_date, raw_text, status, content_sha256)
                VALUES (%s, %s, %s, NOW(), %s, 'PENDING_CLASSIFICATION', %s)
                """,
                (syn_url_id, f"OSINT API Data for {entity}", "LeadAgent Tools", combined_text, osint_sha256)
            )
            cur.execute("UPDATE raw_urls SET status = 'EXTRACTING' WHERE id = %s", (syn_url_id,))
        pg_conn.commit()
        pg_conn.close()
        print(f"[LeadAgent] Injected API tool data for {entity} | SHA-256: {osint_sha256[:16]}...")

    # ── LLM Query Generation ─────────────────────────────────────────────────
    # Build article context block only when an article exists
    article_context_block = ""
    if entity_article:
        article_context_block = f"""
[EXISTING KNOWLEDGE BASE FOR '{entity.upper()}']
The following is a pre-existing encyclopedic article for this lead entity,
built from verified claims already in our knowledge graph. Use this as your
factual baseline. DO NOT re-search for already-known facts. Focus your queries
on the GAPS — what is NOT in this article that would advance the investigation.
{entity_article}
"""

    SYSTEM_PROMPT = f"""You are a specialized OSINT Research Agent part of a larger intelligence pipeline.
Your current target is: {investigation_target}
The overall investigation goal is: {goal_type}

[CURRENT INVESTIGATION STATE]
{executive_summary if executive_summary else 'Investigation just started. No comprehensive summary available yet.'}

[KNOWLEDGE GAPS & DIRECTION]
{knowledge_gaps if knowledge_gaps else 'Focus on initial discovery and profiling.'}
{article_context_block}
Your current task is to investigate a specific lead entity: {entity}
Reason this lead was added: {context}

Generate 3-6 highly targeted search queries. DO NOT run generic searches for the lead entity.
Instead, use the overarching investigation state, knowledge gaps, and the existing article above
to formulate specific queries about how the lead entity relates to the target and fills those gaps.
Prefer queries that surface NEW information not already captured in the existing article.
Use advanced search operators (site:, "exact phrase", AND/OR) if helpful.
"""
    try:
        plan: LeadQueryPlan = llm_pool.chat_completions_create(
            model="TIER_HEAVY",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate targeted SearXNG queries for {entity}."}
            ],
            response_model=LeadQueryPlan,
            temperature=0.3,
        )
        queries = plan.queries
    except Exception as e:
        print(f"[LeadAgent] LLM query generation failed: {e}")
        queries = [f'"{entity}"']

    # ── Execute queries on SearXNG ────────────────────────────────────────────
    unique_urls = set()
    for query in queries:
        try:
            resp = requests.get(
                f"{searxng_url.rstrip('/')}/search",
                params=build_searxng_params(query),
                headers=create_searxng_headers(os.getenv("SEARXNG_SECRET_KEY", "")),
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or data.get("data") or data.get("hits") or []
                if isinstance(results, dict): results = results.get("results") or results.get("data") or []
                for r in (results if isinstance(results, list) else []):
                    if r.get("url"): unique_urls.add(r["url"])
        except Exception as e:
            print(f"[LeadAgent] Query error: {e}")
            
    # Re-open connection to persist the URLs
    save_conn = psycopg2.connect(DATABASE_URL)
    save_conn.autocommit = False
    try:
        with save_conn.cursor() as cur:
            for url in unique_urls:
                cur.execute(
                    """
                    INSERT INTO raw_urls (source_id, url, metadata, status)
                    VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                    ON CONFLICT (url) DO NOTHING
                    """,
                    (searxng_source_id, url, Json({"investigation_id": investigation_id, "lead_id": lead_id, "lead_entity": entity}))
                )
            cur.execute("UPDATE investigation_leads SET status = 'EXPLORED', explored_at = NOW() WHERE id = %s", (lead_id,))
            cur.execute("UPDATE investigations SET leads_explored = leads_explored + 1 WHERE id = %s", (investigation_id,))
        save_conn.commit()
    finally:
        save_conn.close()

    print(f"[LeadAgent] Successfully injected {len(unique_urls)} URLs for lead #{lead_id}")
    return True
