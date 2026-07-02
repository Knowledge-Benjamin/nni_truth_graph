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
from typing import Optional
from pydantic import BaseModel, Field

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
        description=(
            "3-7 specific SearXNG search strings to launch the investigation. "
            "Crafted like a professional OSINT analyst: specific, using operators where useful."
        )
    )
    seed_leads: list[dict] = Field(
        default_factory=list,
        description=(
            "If the target decomposes into multiple known entities, list them here. "
            "Each entry: {entity_name: str, lead_type: str, priority: int (0-100)}."
        )
    )
    rationale: str = Field(description="1-2 sentences explaining the triage decisions made.")


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

def triage_target(target: str, neo4j_driver=None) -> "TriageResult":
    pre_type = _pre_classify_target_type(target)

    graph_context = ""
    if neo4j_driver and not pre_type:
        try:
            with neo4j_driver.session() as session:
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE toLower(e.name) = toLower()
                    OPTIONAL MATCH (e)-[r]-(related:Entity)
                    RETURN e.name AS name, e.mention_count AS mentions,
                           collect(DISTINCT {rel: type(r), other: related.name})[..10] AS relations
                    LIMIT 1
                    """,
                    {"name": target}
                )
                record = result.single()
                if record:
                    name     = record["name"]
                    mentions = record["mentions"] or 0
                    rels     = record["relations"] or []
                    graph_context = (
                        f"\n\nIMPORTANT -- this target already exists in our knowledge graph:\n"
                        f"  Entity: {name} ({mentions} mentions)\n"
                        f"  Known relations: {json.dumps(rels[:8], indent=2)}\n"
                        f"Focus your seed queries on what is NOT yet known."
                    )
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
    return result


def persist_triage(investigation_id: int, triage: "TriageResult", pg_conn) -> None:
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
        for lead in triage.seed_leads:
            cur.execute(
                """
                INSERT INTO investigation_leads
                    (investigation_id, entity_name, lead_type, priority, status)
                VALUES (%s, %s, %s, %s, 'PENDING')
                ON CONFLICT (investigation_id, entity_name) DO NOTHING
                """,
                (
                    investigation_id,
                    lead.get("entity_name", ""),
                    lead.get("lead_type", "GENERAL"),
                    lead.get("priority", 50),
                )
            )
    pg_conn.commit()
    print(
        f"[Triage] Investigation #{investigation_id}: "
        f"goal={triage.goal_type}, target_type={triage.target_type}, "
        f"queries={len(triage.initial_queries)}, seed_leads={len(triage.seed_leads)}"
    )
