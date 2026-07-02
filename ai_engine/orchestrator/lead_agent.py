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
import hashlib
import requests
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, Field

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

def run_lead_agent(
    investigation_id: int,
    investigation_target: str,
    goal_type: str,
    searxng_url: str,
    pg_conn,
    searxng_source_id: int,
) -> bool:
    """
    Atomically claims one PENDING lead, generates search queries,
    and injects URLs. Returns True if a lead was processed, False if none found.
    """
    claimed_lead = None

    with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Atomic claim: SELECT ... FOR UPDATE SKIP LOCKED
        # This is the same pattern used by existing worker.py and outbox_worker.js
        cur.execute(
            """
            SELECT id, entity_name, lead_type, priority
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
            return False  # No pending leads for this investigation

        lead_id    = row["id"]
        entity     = row["entity_name"]
        lead_type  = row["lead_type"]

        # Mark as CLAIMED immediately so no other agent touches it
        cur.execute(
            """
            UPDATE investigation_leads
            SET status = 'CLAIMED', claimed_at = NOW()
            WHERE id = %s
            """,
            (lead_id,)
        )
        pg_conn.commit()
        claimed_lead = {"id": lead_id, "entity": entity, "lead_type": lead_type}

    if not claimed_lead:
        return False

    entity    = claimed_lead["entity"]
    lead_id   = claimed_lead["id"]
    lead_type = claimed_lead["lead_type"]

    print(f"[LeadAgent] Claimed lead #{lead_id} [{lead_type}]: '{entity}'")

    # ── Execute Domain-Specific Tools ────────────────────────────────────────
    # Based on lead_type, we execute specific OSINT APIs to gather structured data.
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
        
    # Filter out empty or error-only results if we want to save space
    valid_results = [r for r in tool_results if r and "[Error]" not in r]
    
    injected = 0
    if valid_results:
        # Join all the tool results into one large text blob
        combined_text = f"=== SYNTHETIC OSINT REPORT FOR {entity} ===\n\n" + "\n\n".join(valid_results)

        # ── Forensic Hash of API data ────────────────────────────────────
        # Give synthetic API data the same cryptographic auditability as scraped HTML.
        osint_sha256 = hashlib.sha256(combined_text.encode('utf-8', errors='replace')).hexdigest()
        
        with pg_conn.cursor() as cur:
            # 1. Insert synthetic URL to maintain foreign key structures
            synthetic_url = f"api://osint/lead/{lead_id}/{time.time()}"
            cur.execute(
                """
                INSERT INTO raw_urls (source_id, url, metadata, status)
                VALUES (%s, %s, %s, 'SCRAPED')
                RETURNING id
                """,
                (
                    searxng_source_id, 
                    synthetic_url,
                    Json({
                        "investigation_id": investigation_id,
                        "lead_id": lead_id,
                        "lead_entity": entity,
                        "synthetic_osint": True
                    })
                )
            )
            syn_url_id = cur.fetchone()[0]
            
            # 2. Insert directly into raw_articles with the text blob + forensic hash
            cur.execute(
                """
                INSERT INTO raw_articles
                    (url_id, title, author, publish_date, raw_text,
                     status, content_sha256)
                VALUES (%s, %s, %s, NOW(), %s, 'PENDING_CLASSIFICATION', %s)
                """,
                (
                    syn_url_id,
                    f"OSINT API Data for {entity}",
                    "LeadAgent Tools",
                    combined_text,
                    osint_sha256
                )
            )
            # Mark it for extraction immediately
            cur.execute("UPDATE raw_urls SET status = 'EXTRACTING' WHERE id = %s", (syn_url_id,))
        pg_conn.commit()
        injected += 1
        print(f"[LeadAgent] Injected API tool data for {entity} | SHA-256: {osint_sha256[:16]}...")

    # ── Ask the LLM to generate precise SearXNG queries for this lead ────────
    try:
        plan: LeadQueryPlan = llm_pool.chat_completions_create(
            model="TIER_HEAVY",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert OSINT analyst generating targeted search queries. "
                        "Given an investigation goal and a specific lead entity, produce "
                        "precise search queries that will reveal connections, associations, "
                        "public records, or digital footprints for this entity."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Investigation goal: {investigation_target}\n"
                        f"Goal type: {goal_type}\n"
                        f"Current lead entity: {entity} (type: {lead_type})\n\n"
                        "Generate targeted SearXNG queries to surface intelligence about this lead."
                    ),
                },
            ],
            response_model=LeadQueryPlan,
            temperature=0.3,
        )
        queries = plan.queries
        print(f"[LeadAgent] Lead #{lead_id}: generated {len(queries)} queries")
    except Exception as e:
        print(f"[LeadAgent] LLM query generation failed for lead #{lead_id}: {e}")
        queries = [f'"{entity}" site:news.google.com OR site:reuters.com']

    # ── Execute queries on SearXNG and inject URLs into raw_urls ────────────
    injected = 0
    for query in queries:
        try:
            resp = requests.get(
                f"{searxng_url.rstrip('/')}/search",
                params={
                    "q":        query,
                    "format":   "json",
                    "engines":  "google,bing,duckduckgo",
                    "time_range": "",  # no time restriction for investigations
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            results = resp.json().get("results", [])
            with pg_conn.cursor() as cur:
                for r in results:
                    url = r.get("url", "")
                    if not url:
                        continue
                    cur.execute(
                        """
                        INSERT INTO raw_urls
                            (source_id, url, metadata, status)
                        VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                        ON CONFLICT (url) DO NOTHING
                        """,
                        (
                            searxng_source_id,
                            url,
                            Json({
                                "investigation_id": investigation_id,
                                "lead_id":          lead_id,
                                "lead_entity":      entity,
                                "osint_query":      query,
                            }),
                        )
                    )
                    injected += 1
            pg_conn.commit()
            time.sleep(0.5)  # polite gap between SearXNG calls

        except Exception as e:
            print(f"[LeadAgent] SearXNG query failed for '{query}': {e}")

    # ── Mark the lead as EXPLORED ────────────────────────────────────────────
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE investigation_leads
            SET status = 'EXPLORED', explored_at = NOW()
            WHERE id = %s
            """,
            (lead_id,)
        )
        cur.execute(
            """
            UPDATE investigations
            SET leads_explored = leads_explored + 1
            WHERE id = %s
            """,
            (investigation_id,)
        )
    pg_conn.commit()
    print(f"[LeadAgent] Lead #{lead_id} EXPLORED — {injected} URLs injected into pipeline")
    return True
