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


def _normalize_query_text(query: str) -> str:
    """Normalize a raw query string without breaking exact-phrase quoting."""
    if not query:
        return ""
    q = str(query).strip()
    q = re.sub(r"\s+", " ", q)
    # Preserve explicit double quotes around exact phrases. Only trim outer whitespace.
    return q.strip()


def _looks_like_low_signal_queries(target: str, target_type: str, queries: list[str]) -> bool:
    """Return True when the generated queries are too generic or obviously noisy."""
    if not queries:
        return True

    clean_target = (target or "").strip().strip('"').strip("'")
    clean_target_l = clean_target.lower()

    if target_type == "PERSON":
        # Person searches should include at least one high-signal modifier such as linkedin/profile/news/wiki.
        has_marker = any(marker in q.lower() for q in queries for marker in ["linkedin", "profile", "news", "bio", "wiki", "resume", "social", "site:"])
        if not has_marker:
            return True

        # Reject obvious duplicate/OR-chain noise like '"Name" OR "Name" biography'.
        for q in queries:
            ql = q.lower()
            if ql.count(clean_target_l) >= 3 or (" or " in ql and clean_target_l in ql and ql.count(clean_target_l) >= 2):
                return True
        return False

    return False


def _expand_person_queries(target: str, queries: list[str]) -> list[str]:
    """Expand person-target queries into higher-signal variants while preserving the exact phrase."""
    if not queries:
        return []

    clean_target = (target or "").strip().strip('"').strip("'")
    expanded: list[str] = []
    seen: set[str] = set()

    for q in queries:
        q = str(q).strip()
        if not q:
            continue
        if q not in seen:
            expanded.append(q)
            seen.add(q)

        if not clean_target:
            continue

        exact_phrase = f'"{clean_target}"'
        if q.startswith(exact_phrase):
            for suffix in [
                f'{q} profile',
                f'{q} site:linkedin.com/in',
                f'{q} -anitta -anita',
            ]:
                if suffix not in seen:
                    expanded.append(suffix)
                    seen.add(suffix)

    return expanded[:6]


def _build_rule_based_triage(target: str, pre_type: Optional[str], graph_seed_leads: list[dict]) -> "TriageResult":
    """Reliable deterministic triage fallback used when the LLM response is empty or malformed."""
    clean_target = (target or "").strip().strip('"').strip("'")
    cleaned = clean_target.lower()

    if pre_type:
        target_type = pre_type
    elif _EMAIL_RE.match(clean_target):
        target_type = "EMAIL"
    elif _IP_RE.match(clean_target):
        target_type = "IP"
    elif _WALLET_RE.match(clean_target):
        target_type = "WALLET"
    elif _DOMAIN_RE.match(clean_target):
        target_type = "DOMAIN"
    elif any(ch.isspace() for ch in clean_target) or "." not in clean_target and "@" not in clean_target:
        target_type = "PERSON"
    else:
        target_type = "QUESTION"

    goal_type = "PROFILING"
    canonical_target = cleaned
    rationale = f"Deterministic fallback triage generated from the supplied target '{clean_target}'."

    if target_type == "PERSON":
        queries = [
            f'"{clean_target}"',
            f'"{clean_target}" profile',
            f'"{clean_target}" site:linkedin.com/in',
            f'"{clean_target}" news -anitta -anita',
        ]
    elif target_type == "DOMAIN":
        queries = [
            f'{clean_target} whois',
            f'{clean_target} site:whois.domaintools.com',
            f'{clean_target} company records',
        ]
    elif target_type == "EMAIL":
        queries = [
            f'{clean_target} email leak',
            f'{clean_target} public profile',
            f'{clean_target} breach dump',
        ]
    elif target_type == "IP":
        queries = [
            f'{clean_target} abuseipdb',
            f'{clean_target} whois',
            f'{clean_target} shodan',
        ]
    elif target_type == "WALLET":
        queries = [
            f'{clean_target} blockchain explorer',
            f'{clean_target} transaction history',
            f'{clean_target} wallet analytics',
        ]
    else:
        queries = [
            f'"{clean_target}" investigation',
            f'"{clean_target}" news',
            f'"{clean_target}" profile',
        ]

    result = TriageResult(
        goal_type=goal_type,
        target_type=target_type,
        canonical_target=canonical_target,
        exhaust_predicate=None,
        initial_queries=queries,
        seed_leads=[],
        rationale=rationale,
    )
    result._graph_seed_leads = graph_seed_leads  # type: ignore[attr-defined]
    return result


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
        "Think like a seasoned OSINT analyst: prefer exact-phrase queries, use one or two high-signal modifiers, "
        "and avoid duplicate terms, long OR-chains, and generic noise."
    )

    user_prompt = (
        f"New investigation target submitted:\n\n"
        f"  TARGET: {target}\n"
        f"{type_hint}"
        f"{postgres_context}"
        f"{graph_context}\n\n"
        "Perform triage. Generate the goal_type, target_type, canonical form, "
        "3-5 initial SearXNG queries, any seed leads you can derive from the target itself, "
        "and a brief rationale. Keep each query specific and relevant; avoid repeated terms and broad OR-chains."
    )

    print(f"[Triage] Target='{target}'")
    print(f"[Triage] Prompting LLM with system prompt: {system_prompt}")
    print(f"[Triage] User prompt: {user_prompt}")

    try:
        result = llm_pool.chat_completions_create(
            model="TIER_HEAVY",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_model=TriageResult,
            temperature=0.2,
        )
    except Exception as e:
        print(f"[Triage] LLM triage failed: {e}. Falling back to deterministic rule-based triage.")
        return _build_rule_based_triage(target, pre_type, graph_seed_leads)

    if not result or getattr(result, 'goal_type', None) in (None, '') or getattr(result, 'target_type', None) in (None, ''):
        print("[Triage] LLM triage returned an empty or malformed result. Falling back to deterministic rule-based triage.")
        return _build_rule_based_triage(target, pre_type, graph_seed_leads)

    if not getattr(result, 'initial_queries', None):
        print("[Triage] LLM triage returned no queries. Falling back to deterministic rule-based triage.")
        return _build_rule_based_triage(target, pre_type, graph_seed_leads)

    normalized_queries = []
    for q in getattr(result, 'initial_queries', []) or []:
        n = _normalize_query_text(q)
        if n:
            normalized_queries.append(n)

    if _looks_like_low_signal_queries(target, getattr(result, 'target_type', '') or (pre_type or 'QUESTION'), normalized_queries):
        print("[Triage] LLM queries were low-signal or noisy. Falling back to deterministic rule-based triage.")
        return _build_rule_based_triage(target, pre_type, graph_seed_leads)

    if getattr(result, 'target_type', '') == 'PERSON':
        normalized_queries = _expand_person_queries(target, normalized_queries)

    result.initial_queries = normalized_queries
    print(f"[Triage] LLM triage result: goal={getattr(result, 'goal_type', None)}, target_type={getattr(result, 'target_type', None)}, canonical_target={getattr(result, 'canonical_target', None)}, queries={getattr(result, 'initial_queries', None)}")
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
