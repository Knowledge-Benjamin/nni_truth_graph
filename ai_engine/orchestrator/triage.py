"""
ai_engine/orchestrator/triage.py
─────────────────────────────────────────────────────────────────────────────
Intake & Triage -- Classifies a raw investigation target into:
  - goal_type   : PROFILING | EXHAUSTIVE_COLLECTION | INFRASTRUCTURE | FINANCIAL
  - target_type : EMAIL | IP | DOMAIN | WALLET | PERSON | ORGANISATION | QUESTION
  - initial seed queries for SearXNG
  - (optionally) seeds the investigation_leads table with typed entities

Uses the existing llm_router singleton. No new API configuration needed.
"""

import re
import json
import psycopg2
from psycopg2.extras import Json
from typing import Optional, Union, Any
from pydantic import BaseModel, Field, field_validator

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from ai_engine.core.llm_router import llm_pool


# ── Pydantic schema for the triage LLM response ─────────────────────────────

class TriageResult(BaseModel):
    goal_type: str = Field(
        description=(
            "Classify the investigation goal. "
            "PROFILING = deep footprint on a specific person/entity. "
            "EXHAUSTIVE_COLLECTION = find as many instances of a category as possible (e.g. 'all funders'). "
            "INFRASTRUCTURE = technical/cyber investigation (IPs, domains, servers). "
            "FINANCIAL = follow the money (wallets, shell companies, transactions)."
        )
    )
    target_type: str = Field(
        description=(
            "Type of the raw target string. "
            "One of: EMAIL | IP | DOMAIN | WALLET | PERSON | ORGANISATION | QUESTION"
        )
    )
    canonical_target: str = Field(
        description="The cleaned, canonical version of the target (trim whitespace, lowercase emails, etc.)"
    )
    exhaust_predicate: Optional[str] = Field(
        default=None,
        description=(
            "Only for EXHAUSTIVE_COLLECTION: the Neo4j relationship predicate we are trying to map. "
            "E.g. 'FUNDS', 'IS_AFFILIATED_WITH', 'OPERATES_INFRASTRUCTURE_AT'. "
            "Null for all other goal types."
        )
    )
    initial_queries: list[str] = Field(
        default_factory=list,
        description=(
            "3-7 specific SearXNG search strings to launch the investigation. "
            "Crafted like a professional OSINT analyst: specific, using operators where useful."
        )
    )
    seed_leads: list[dict] = Field(
        default_factory=list,
        description=(
            "If the target decomposes into multiple known entities, list them here. "
            "Each entry: {entity_name: str, lead_type: str, priority: int (0-100), context: str (rationale for relevance)}."
        )
    )
    rationale: str = Field(description="1-2 sentences explaining the triage decisions made.")

    @field_validator('initial_queries', mode='before')
    def _accept_flexible_initial_queries(cls, v: Any) -> list[str]:
        """
        Accept either a list of strings or a list of dicts for `initial_queries`.
        If dicts are provided, extract the best textual representation
        (`entity_name`, `query`, `text`, `name`) or fall back to stringifying
        the dict.
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return [str(v)]

        out: list[str] = []
        for item in v:
            if isinstance(item, str):
                out.append(item)
                continue
            if isinstance(item, dict):
                s = item.get('entity_name') or item.get('query') or item.get('text') or item.get('name')
                if not s:
                    try:
                        # Join values to make a reasonable search string
                        s = ' '.join(str(x) for x in item.values() if x)
                    except Exception:
                        s = json.dumps(item)
                out.append(s)
                continue
            out.append(str(item))
        return out


# ── Regex-based pre-classification (fast, no LLM) ───────────────────────────

_EMAIL_RE  = re.compile(r'^[\w.+\-]+@[\w\-]+\.[\w.]+$')
_IP_RE     = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')
_DOMAIN_RE = re.compile(r'^(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}$')
_WALLET_RE = re.compile(r'^(0x[0-9a-fA-F]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})$')


def _pre_classify_target_type(target: str) -> Optional[str]:
    t = target.strip()
    if _EMAIL_RE.match(t):  return "EMAIL"
    if _IP_RE.match(t):     return "IP"
    if _WALLET_RE.match(t): return "WALLET"
    if _DOMAIN_RE.match(t): return "DOMAIN"
    return None


# ── Main triage function ─────────────────────────────────────────────────────

def triage_target(target: str, neo4j_driver=None, pg_conn=None) -> "TriageResult":
    pre_type = _pre_classify_target_type(target)

    postgres_context = ""
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                # 1. Sweep investigations
                cur.execute("""
                    SELECT id, target, findings->>'last_harvest_summary' AS summary
                    FROM investigations
                    WHERE target ILIKE %s OR findings->>'last_harvest_summary' ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT 3
                """, (f"%{target}%", f"%{target}%"))
                past_invs = cur.fetchall()
                if past_invs:
                    postgres_context += "\n\nPAST INVESTIGATIONS MENTIONING TARGET:\n"
                    for pid, ptarget, psumm in past_invs:
                        if psumm:
                            postgres_context += f"- Inv #{pid} (Target: {ptarget}): {psumm[:200]}...\n"
                        else:
                            postgres_context += f"- Inv #{pid} (Target: {ptarget})\n"
                
                # 2. Sweep raw articles
                cur.execute("""
                    SELECT id, title
                    FROM raw_articles
                    WHERE title ILIKE %s
                    LIMIT 5
                """, (f"%{target}%",))
                past_arts = cur.fetchall()
                if past_arts:
                    postgres_context += "\nEXISTING INTERNAL ARTICLES MENTIONING TARGET:\n"
                    for aid, atitle in past_arts:
                        postgres_context += f"- #{aid}: {atitle}\n"
        except Exception as e:
            print(f"[Triage] Postgres context lookup failed (non-fatal): {e}")

    graph_context = ""
    graph_seed_leads: list[dict] = []
    if neo4j_driver and not pre_type:
        try:
            with neo4j_driver.session() as session:
                # Fix: was toLower() missing $name — now correctly passes parameter
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE toLower(e.name) = toLower($name)
                       OR toLower(e.name) CONTAINS toLower($name)
                    WITH e LIMIT 1
                    OPTIONAL MATCH (e)-[r]-(related:Entity)
                    WHERE related.name IS NOT NULL
                    WITH e, related, type(r) AS rel_type,
                         coalesce(related.mention_count, 0) AS popularity
                    ORDER BY popularity DESC
                    RETURN e.name AS name, e.mention_count AS mentions,
                           collect(DISTINCT {
                               rel: rel_type,
                               other: related.name,
                               pop: coalesce(related.mention_count, 0)
                           })[..150] AS relations
                    """,
                    {"name": target}
                )
                record = result.single()
                if record:
                    name     = record["name"]
                    mentions = record["mentions"] or 0
                    rels     = record["relations"] or []

                    # Build graph context hint for the LLM
                    graph_context = (
                        f"\n\nIMPORTANT — target already in knowledge graph: "
                        f"{name} ({mentions} mentions). "
                        f"Known relations sample: "
                        + ", ".join(f"{r['rel']}→{r['other']}" for r in rels[:10] if r.get('other'))
                        + "\nFocus queries on what is NOT yet in the graph."
                    )

                    # Seed ALL graph neighbors directly as leads — no LLM gatekeeping
                    for rel in rels:
                        other = rel.get("other")
                        if not other or not other.strip():
                            continue
                        pop = rel.get("pop", 0) or 0
                        # Priority: higher mention count → higher priority (cap 95)
                        priority = min(95, 40 + min(55, int(pop / 5)))
                        lead_type = "PERSON" if any(
                            kw in (rel.get("rel") or "").upper()
                            for kw in ("BORN", "FOUNDED_BY", "CEO", "DIRECTOR", "APPOINTED", "HIRED")
                        ) else "ORGANISATION" if any(
                            kw in (rel.get("rel") or "").upper()
                            for kw in ("OWNS", "CONTROLS", "FUNDS", "PARTNER", "SUBSIDIARY", "IS_PART_OF")
                        ) else "GENERAL"
                        graph_seed_leads.append({
                            "entity_name": other.strip(),
                            "lead_type":   lead_type,
                            "priority":    priority,
                            "context":     f"Discovered in knowledge graph as a direct neighbor of {name} with relationship: {rel.get('rel')}",
                        })

                    print(f"[Triage] Graph seed: found {len(graph_seed_leads)} neighbors for '{name}'")
        except Exception as e:
            print(f"[Triage] Neo4j context lookup failed (non-fatal): {e}")

    type_hint = f"\n\nPRE-CLASSIFIED target type (from regex): {pre_type}" if pre_type else ""

    system_prompt = (
        "You are the Lead Detective of an enterprise OSINT investigation bureau. "
        "Perform intake triage on a new investigation target. "
        "Classify the goal type, target type, and generate precise SearXNG search queries "
        "that will surface actionable intelligence. "
        "Think like a seasoned OSINT analyst: use operator syntax, enumerate likely sub-targets."
    )

    user_prompt = (
        f"New investigation target submitted:\n\n"
        f"  TARGET: {target}\n"
        f"{type_hint}"
        f"{postgres_context}"
        f"{graph_context}\n\n"
        "Perform triage. Generate the goal_type, target_type, canonical form, "
        "initial SearXNG queries, any seed leads you can derive from the target itself, "
        "and a brief rationale."
    )

    result = llm_pool.chat_completions_create(
        model="TIER_HEAVY",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        response_model=TriageResult,
        temperature=0.2,
    )
    # Attach graph neighbors so persist_triage can insert them
    result._graph_seed_leads = graph_seed_leads  # type: ignore[attr-defined]
    return result


def persist_triage(investigation_id: int, triage: "TriageResult", pg_conn) -> None:
    # Merge LLM seed_leads + graph-sourced leads
    graph_leads: list[dict] = getattr(triage, '_graph_seed_leads', [])
    all_leads = list(triage.seed_leads) + graph_leads

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE investigations
            SET goal_type = %s,
                findings  = findings || %s::jsonb
            WHERE id = %s
            """,
            (
                triage.goal_type,
                Json({
                    "canonical_target":  triage.canonical_target,
                    "target_type":       triage.target_type,
                    "exhaust_predicate": triage.exhaust_predicate,
                    "triage_rationale":  triage.rationale,
                    "initial_queries":   triage.initial_queries,
                }),
                investigation_id,
            )
        )
        inserted_leads = 0
        for lead in all_leads:
            cur.execute(
                """
                INSERT INTO investigation_leads
                    (investigation_id, entity_name, lead_type, priority, status, context)
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
                ON CONFLICT (investigation_id, entity_name) DO NOTHING
                """,
                (
                    investigation_id,
                    lead.get("entity_name", ""),
                    lead.get("lead_type", "GENERAL"),
                    lead.get("priority", 50),
                    lead.get("context", "Seed lead identified during triage."),
                )
            )
            inserted_leads += 1
    pg_conn.commit()
    print(
        f"[Triage] Investigation #{investigation_id}: "
        f"goal={triage.goal_type}, target_type={triage.target_type}, "
        f"queries={len(triage.initial_queries)}, "
        f"llm_leads={len(triage.seed_leads)}, graph_leads={len(graph_leads)}, "
        f"total_leads_inserted={inserted_leads}"
    )
