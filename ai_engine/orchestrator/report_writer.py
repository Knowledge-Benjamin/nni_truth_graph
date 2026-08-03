"""
ai_engine/orchestrator/report_writer.py
─────────────────────────────────────────────────────────────────────────────
Living Investigation Report Writer

Called by the orchestrator on every tick for each ACTIVE investigation.
Incrementally builds a multi-chapter dossier as new claims arrive.

Architecture:
  - Mirrors article_worker.py's MD5 hash-diff logic so only changed chapters
    are re-generated (unchanged chapters are preserved as-is).
  - Uses self-hosted Ollama (gemma-4-e4b) exclusively via llm_pool.
  - Evidence Dossier is auto-paginated into sub-chapters of PAGE_SIZE claims.
  - Report is stored as structured JSON in investigations.report and also
    written as a human-readable Markdown file to disk.

Chapter Map:
  Chapter 1:  Executive Situation Report (updated live, finalized at close)
  Chapter 2:  Target Profile & Background
  Chapter 3:  Chronological Event Timeline
  Chapter 4:  Key Actors & Network Map
  Chapter 5+: Evidence Dossier (auto-paginated, 1 sub-chapter per PAGE_SIZE)
  Chapter N-3: Source Intelligence Assessment
  Chapter N-2: Lead Threads & OSINT Coverage
  Chapter N-1: Contradictions & Disputes
  Chapter N:   Knowledge Gaps & Open Questions
"""

import os
import sys
import json
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from ai_engine.core.llm_router import llm_pool

PAGE_SIZE = 80       # Claims per Evidence Dossier sub-chapter page
MAX_CHAPTERS = 200   # Hard cap on total pages


def _is_recoverable_db_error(exc: Exception) -> bool:
    """Return True when the database error suggests a dropped or stale socket."""
    if exc is None:
        return False
    message = str(exc).lower()
    return any(token in message for token in (
        "ssl connection has been closed unexpectedly",
        "connection reset",
        "connection aborted",
        "server closed the connection unexpectedly",
        "broken pipe",
        "could not receive data",
        "connection is closed",
        "connection closed",
        "closed unexpectedly",
        "connection lost",
    ))


def _connect_postgres(database_url: Optional[str] = None):
    """Create a fresh Postgres connection with conservative timeout settings."""
    import psycopg2

    dsn = database_url or os.getenv("DATABASE_URL")
    conn = psycopg2.connect(
        dsn,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    conn.autocommit = False
    return conn


def _ensure_pg_connection(pg_conn, database_url: Optional[str] = None):
    """Return a usable Postgres connection, recreating it when a prior socket is stale."""
    if pg_conn is None:
        return _connect_postgres(database_url)

    try:
        if getattr(pg_conn, "closed", False):
            raise RuntimeError("connection is closed")
        with pg_conn.cursor() as cur:
            cur.execute("SELECT 1")
        return pg_conn
    except Exception as exc:
        if _is_recoverable_db_error(exc):
            try:
                pg_conn.close()
            except Exception:
                pass
            return _connect_postgres(database_url)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema for LLM output
# ─────────────────────────────────────────────────────────────────────────────

class ChapterSection(BaseModel):
    content: str = Field(
        description="Narrative markdown text for this chapter section. "
                    "Professional, structured, sourced. No preamble. No commentary."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Data Fetchers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_investigation_claims(pg_conn, investigation_id: int) -> list[dict]:
    """Pull all pipeline-completed claims for this investigation."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT
                ec.id            AS claim_id,
                ec.subject,
                ec.predicate,
                ec.object_entity,
                ec.temporal_anchor,
                ec.spatial_anchor,
                ec.quote_context,
                ec.epistemic_score,
                ec.extraction_confidence,
                ec.status        AS claim_status,
                COALESCE((
                    SELECT COUNT(*) FROM extracted_claims sub
                    WHERE sub.spo_fingerprint = ec.spo_fingerprint
                      AND ec.spo_fingerprint IS NOT NULL
                ), 1) AS corroboration_count,
                ra.title         AS article_title,
                ra.publish_date,
                ru.url           AS source_url,
                s.name           AS source_name,
                s.epistemic_trust_score,
                cp.internet_original_url   AS original_url,
                cp.internet_original_source AS original_source,
                cp.neo4j_stance,
                cp.neo4j_matched_claim_id,
                cp.neo4j_similarity
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls     ru ON ra.url_id = ru.id
            JOIN sources       s ON ru.source_id = s.id
            LEFT JOIN claim_provenance cp ON cp.claim_id = ec.id
            WHERE (ru.metadata->>'investigation_id')::int = %s
              AND ec.status IN ('GRAPH_COMMITTED', 'AUTO_APPROVE', 'CONTRADICTED', 'PROCESSING')
            ORDER BY corroboration_count DESC, ec.epistemic_score DESC
            LIMIT 5000
        """, (investigation_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_investigation_leads(pg_conn, investigation_id: int) -> list[dict]:
    """Pull all leads (explored, pending, skipped) for this investigation."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT entity_name, lead_type, priority, status, created_at
            FROM investigation_leads
            WHERE investigation_id = %s
            ORDER BY priority DESC, created_at ASC
        """, (investigation_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_investigation_sources(pg_conn, investigation_id: int) -> list[dict]:
    """Pull all unique sources used in this investigation."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT s.name, s.domain, s.epistemic_trust_score,
                   COUNT(ec.id) AS claim_count
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls     ru ON ra.url_id = ru.id
            JOIN sources       s ON ru.source_id = s.id
            WHERE (ru.metadata->>'investigation_id')::int = %s
            GROUP BY s.name, s.domain, s.epistemic_trust_score
            ORDER BY claim_count DESC, s.epistemic_trust_score DESC
            LIMIT 200
        """, (investigation_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_investigation_evidence_counts(pg_conn, investigation_id: int) -> dict:
    """Return document/source/witness/reference counts for the investigation."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(DISTINCT ec.article_id) AS document_count,
                COUNT(DISTINCT ru.source_id) AS source_count,
                COUNT(DISTINCT cc.raw_article_id) AS witness_count,
                COUNT(DISTINCT ec.id) AS reference_count
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls ru ON ra.url_id = ru.id
            LEFT JOIN claim_corroborations cc ON cc.claim_id = ec.id
            WHERE (ru.metadata->>'investigation_id')::int = %s
              AND ec.status IN ('GRAPH_COMMITTED', 'AUTO_APPROVE', 'CONTRADICTED', 'PROCESSING')
        """, (investigation_id,))
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        if not row:
            return {"document_count": 0, "source_count": 0, "witness_count": 0, "reference_count": 0}
        return {k: int(v or 0) for k, v in dict(zip(cols, row)).items()}


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _chapter_hash(items: list) -> str:
    """MD5 of sorted claim IDs — same as article_worker's section hash."""
    key = sorted([str(i.get('claim_id', i)) for i in items])
    return hashlib.md5("".join(key).encode()).hexdigest()


def _load_chapter_relevance_state(report: dict, chapter_key: str) -> dict:
    """Return a per-chapter relevance cache that is independent across chapters."""
    states = report.get('_chapter_evidence_state', {}) if isinstance(report, dict) else {}
    state = states.get(chapter_key, {}) if isinstance(states, dict) else {}
    return {
        "relevant_claim_ids": [str(cid) for cid in state.get("relevant_claim_ids", []) if cid is not None],
        "ignored_claim_ids": [str(cid) for cid in state.get("ignored_claim_ids", []) if cid is not None],
        "context_claim_ids": [str(cid) for cid in state.get("context_claim_ids", []) if cid is not None],
    }


def _save_chapter_relevance_state(report: dict, chapter_key: str, state: dict):
    """Persist the chapter-level relevance cache in the report object."""
    if not isinstance(report, dict):
        return
    states = report.setdefault('_chapter_evidence_state', {})
    states[chapter_key] = {
        "relevant_claim_ids": sorted(set(str(cid) for cid in state.get("relevant_claim_ids", []))),
        "ignored_claim_ids": sorted(set(str(cid) for cid in state.get("ignored_claim_ids", []))),
        "context_claim_ids": sorted(set(str(cid) for cid in state.get("context_claim_ids", []))),
    }


def _plan_chapter_evidence(claims: list[dict], final_content: str, chapter_state: Optional[dict] = None):
    """Split chapter inputs into existing / new / context-only buckets using chapter-local relevance state."""
    chapter_state = chapter_state or {
        "relevant_claim_ids": [],
        "ignored_claim_ids": [],
        "context_claim_ids": [],
    }
    relevant_ids = {str(cid) for cid in chapter_state.get("relevant_claim_ids", [])}
    ignored_ids = {str(cid) for cid in chapter_state.get("ignored_claim_ids", [])}
    context_ids = {str(cid) for cid in chapter_state.get("context_claim_ids", [])}

    existing_content_ids = {
        str(c.get('claim_id'))
        for c in claims
        if c.get('claim_id') and f"[REF:{c.get('claim_id')}]" in (final_content or "")
    }

    new_claims = []
    existing_claims = []
    context_claims = []

    for claim in claims:
        cid = str(claim.get('claim_id'))
        if not cid:
            continue
        if cid in existing_content_ids:
            existing_claims.append(claim)
        elif cid in ignored_ids or cid in context_ids:
            context_claims.append(claim)
        else:
            new_claims.append(claim)

    # If the chapter has already marked these claims relevant in a prior pass,
    # they are still fundamentally new to the current draft and should not be
    # silently suppressed as context-only. That preserves chapter-local relevance.
    if relevant_ids:
        prior_relevant_ids = {cid for cid in relevant_ids if cid not in existing_content_ids and cid not in ignored_ids}
        if prior_relevant_ids:
            out_of_band = [c for c in claims if str(c.get('claim_id')) in prior_relevant_ids]
            new_claims = [c for c in new_claims if str(c.get('claim_id')) not in prior_relevant_ids] + out_of_band

    return new_claims, existing_claims, context_claims


def _extract_open_intelligence_gaps(report_text: str) -> list[dict]:
    """Parse report content for Open Intelligence Gap lines and return normalized lead candidates."""
    if not report_text:
        return []

    matches = []
    pattern = re.compile(
        r"Open Intelligence Gap:\s*\[(?P<lead_type>[^\]]+)\]\s*\*\*(?P<entity_name>.+?)\*\*\s*\(priority\s*(?P<priority>\d+)\)",
        re.IGNORECASE,
    )
    for line in report_text.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        lead_type = (m.group("lead_type") or "GENERAL").strip().upper()
        entity = (m.group("entity_name") or "").strip()
        priority = int(m.group("priority") or 0)
        if entity:
            matches.append({
                "entity_name": entity,
                "lead_type": lead_type,
                "priority": priority,
                "context": f"Re-seeded from Open Intelligence Gaps chapter: {line.strip()}",
            })
    return matches


def _persist_open_intelligence_gaps(pg_conn, investigation_id: int, report: dict) -> int:
    """Upsert any Open Intelligence Gaps found in the live report back into investigation_leads."""
    leads_to_insert: list[dict] = []
    for chapter in report.values():
        if not isinstance(chapter, dict):
            continue
        content = chapter.get("content") or ""
        for gap in _extract_open_intelligence_gaps(content):
            leads_to_insert.append(gap)

    if not leads_to_insert:
        return 0

    unique_by_entity: dict[str, dict] = {}
    for gap in leads_to_insert:
        unique_by_entity[gap["entity_name"]] = gap

    conn = _ensure_pg_connection(pg_conn)
    inserted = 0
    with conn.cursor() as cur:
        for gap in unique_by_entity.values():
            cur.execute(
                """
                INSERT INTO investigation_leads
                    (investigation_id, entity_name, lead_type, priority, status, context)
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
                ON CONFLICT (investigation_id, entity_name)
                DO UPDATE SET
                    lead_type = EXCLUDED.lead_type,
                    priority = GREATEST(investigation_leads.priority, EXCLUDED.priority),
                    status = 'PENDING',
                    context = EXCLUDED.context
                """,
                (
                    investigation_id,
                    gap["entity_name"],
                    gap["lead_type"],
                    gap["priority"],
                    gap["context"],
                ),
            )
            inserted += 1
    conn.commit()
    return inserted


def _normalize_response_content(response: object) -> str:
    """Accept common LLM response shapes and return the text body if available."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "message", "output", "response", "value"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = _normalize_response_content(value)
                if nested:
                    return nested
        if len(response) == 1:
            return _normalize_response_content(next(iter(response.values())))
        return ""
    for attr in ("content", "text", "output_text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value:
            return value
    if hasattr(response, "message"):
        message = getattr(response, "message")
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return _normalize_response_content(message)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
            if isinstance(dumped, dict):
                return _normalize_response_content(dumped)
        except Exception:
            pass
    return ""


def _claim_line(c: dict) -> str:
    """Format a single claim for LLM context with confidence, source-strength, and stance cues."""
    corroboration = int(c.get('corroboration_count') or 1)
    epistemic_score = float(c.get('epistemic_score') or 0.5)
    confidence_label = "high" if epistemic_score >= 0.8 else "medium" if epistemic_score >= 0.6 else "low"
    source_strength = "high" if corroboration >= 3 else "medium" if corroboration == 2 else "low"
    badge = "🥇 " if corroboration >= 3 else "🥈 " if corroboration == 2 else ""
    pred  = str(c.get('predicate') or '').replace('_', ' ').lower()
    line  = f"{badge}[REF:{c['claim_id']}] {c['subject']} {pred} {c['object_entity']}"
    if c.get('temporal_anchor'): line += f" (when: {c['temporal_anchor']})"
    if c.get('spatial_anchor'):  line += f" (where: {c['spatial_anchor']})"
    if c.get('quote_context'):   line += f'\n   > "{str(c["quote_context"])[:200]}"'
    src = c.get('original_source') or c.get('source_name') or 'Unknown'
    url = c.get('original_url') or c.get('source_url') or 'N/A'
    stance = str(c.get('neo4j_stance') or '').upper()
    matched = c.get('neo4j_matched_claim_id')
    similarity = c.get('neo4j_similarity')
    stance_suffix = ""
    if stance in {"CONTRADICTS", "EVOLVES", "CORROBORATES", "ENRICHES"}:
        stance_suffix = f" | Stance: {stance.lower()}"
        if matched:
            stance_suffix += f" | Matched claim: {matched}"
        if similarity is not None:
            stance_suffix += f" | Similarity: {round(float(similarity), 2)}"
    line += (
        f"\n   Source: {src} | URL: {url} | "
        f"Confidence: {confidence_label} ({round(epistemic_score, 2)}) | "
        f"Corroboration: {corroboration} | Source strength: {source_strength}{stance_suffix}"
    )
    return line


def _fetch_evidence_context(pg_conn, claims: list[dict], max_claims: int = 12) -> list[str]:
    """Pull compact article text context for claims that lack clear quotation context."""
    if not pg_conn or not claims:
        return []

    claim_ids = [c.get('claim_id') for c in claims if c.get('claim_id')]
    if not claim_ids:
        return []

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT ec.id, ra.raw_text
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            WHERE ec.id = ANY(%s)
        """, (claim_ids,))
        rows = cur.fetchall()

    context_rows = []
    for claim_id, raw_text in rows:
        if not raw_text:
            continue
        compact = re.sub(r"\s+", " ", str(raw_text))[:900]
        context_rows.append((claim_id, compact))

    if not context_rows:
        return []

    return [
        f"[REF:{claim_id}] Article context: {text}"
        for claim_id, text in context_rows[:max_claims]
    ]


def _select_relevant_context(claims_used: list[dict], evidence_context: list[str]) -> list[str]:
    """Return only evidence context tied to ambiguous or weakly supported claims."""
    if not claims_used or not evidence_context:
        return []

    relevant: list[str] = []
    ambiguous_claim_ids = {
        str(c.get('claim_id'))
        for c in claims_used
        if c.get('claim_id') and not (c.get('quote_context') or c.get('source_url') or c.get('original_url') or c.get('source_name') or c.get('original_source'))
    }

    for ctx in evidence_context:
        if any(f"[REF:{claim_id}]" in ctx for claim_id in ambiguous_claim_ids):
            relevant.append(ctx)

    if relevant:
        return relevant

    claim_ids = {str(c.get('claim_id')) for c in claims_used if c.get('claim_id')}
    for ctx in evidence_context:
        if any(f"[REF:{claim_id}]" in ctx for claim_id in claim_ids):
            relevant.append(ctx)

    return relevant[:6]


# ─────────────────────────────────────────────────────────────────────────────
# LLM Section Generator (self-hosted only)
# ─────────────────────────────────────────────────────────────────────────────

def _build_support_summary(claims: list[dict], support_metrics: Optional[dict] = None) -> str:
    """Create a compact evidence-support block for the chapter prompt."""
    document_count = int((support_metrics or {}).get('document_count') or 0)
    source_count = int((support_metrics or {}).get('source_count') or 0)
    witness_count = int((support_metrics or {}).get('witness_count') or 0)
    reference_count = int((support_metrics or {}).get('reference_count') or 0)

    if not any([document_count, source_count, witness_count, reference_count]):
        document_count = len({c.get('article_id') for c in claims if c.get('article_id')})
        source_count = len({c.get('source_id') or c.get('source_name') or c.get('original_source') or c.get('source_url') for c in claims if c.get('source_id') or c.get('source_name') or c.get('original_source') or c.get('source_url')})
        witness_count = max(1, len({c.get('claim_id') for c in claims if c.get('quote_context')}))
        reference_count = len(claims)

    confidence_score = 0.0
    if claims:
        confidence_score = sum(float(c.get('epistemic_score') or 0.5) for c in claims) / len(claims)
    confidence_pct = int(round(confidence_score * 100))

    return "\n".join([
        "### Evidence Support Summary",
        f"- Documents: {document_count}",
        f"- Sources: {source_count}",
        f"- Witnesses: {witness_count}",
        f"- References: {reference_count}",
        f"- Confidence: {confidence_pct}%",
    ])


def _generate_chapter(
    investigation_target: str,
    chapter_title: str,
    instruction: str,
    facts: list[str],
    existing_content: Optional[str] = None,
    max_facts: int = 40,
    evidence_context: Optional[list[str]] = None,
    new_facts: Optional[list[str]] = None,
    existing_facts: Optional[list[str]] = None,
    support_metrics: Optional[dict] = None,
) -> str:
    """Revise an existing chapter in place using the local LLM."""

    parts = [
        f"You are writing a formal intelligence investigation dossier about: \"{investigation_target}\".",
        f"Write the chapter titled: \"{chapter_title}\".",
        "",
        "MISSION:",
        instruction,
        "",
        "EVIDENCE SUPPORT SUMMARY:",
        _build_support_summary([], support_metrics),
        "",
        "STRICT RULES:",
        "1. Write in formal investigative report prose. Authoritative, precise, third-person.",
        "2. Revise the existing chapter in place. Preserve what remains accurate and useful, but remove or rewrite anything that is stale, unsupported, or out of context.",
        "3. Do not simply repeat the previous draft. Actively add, modify, enhance, and delete content so the chapter reflects the latest evidence and current understanding.",
        "4. Produce a detailed chapter, not a summary. Expand the narrative substantially with relevant facts, context, and evidence. There is no length cap; be comprehensive.",
        "5. Think like a professional investigator. Reason from the evidence provided. Make conclusions, explain the reasoning, and draw connections between facts. Do not merely list facts.",
        "6. Include a clear Assessment section or paragraph in every chapter that explains why the evidence matters, what significance the pattern has, and how it changes the investigative understanding. This is the 'why this matters' analysis layer.",
        "7. Compare the NEW EVIDENCE TO INCORPORATE against the ALREADY INTEGRATED EVIDENCE and the existing chapter. Only add material that meaningfully expands the report with relevant new facts and analytical insight.",
        "8. Every factual statement MUST include an inline citation immediately after the sentence or clause using [REF:<id>]. Do not leave factual claims uncited.",
        "9. Do not rely on a chapter-end references section alone. The paragraph itself must be verifiable on the fly.",
        "10. You MUST include a '## References' section at the very end of the chapter mapping every [REF:<id>] used to its Source Name and URL.",
        "11. Use markdown: ## for section headers, **bold** for key names.",
        "12. DO NOT hallucinate. Only use the provided facts. Do not invent claims.",
        "13. If a claim is unclear or needs additional context, use the optional evidence context provided below and avoid speculation.",
        "14. When a claim is weak, single-source, low-confidence, or poorly corroborated, say so explicitly and avoid overstating certainty.",
        "15. Prefer uncertainty-aware phrasing such as 'Evidence suggests...', 'It is likely...', 'There is moderate confidence...', 'There is insufficient evidence...', and 'Conflicting reporting exists...' when the evidence does not justify a hard assertion.",
        "16. When multiple claims are strong and corroborated, you may present the conclusion with higher confidence, but still note the degree of support.",
        "17. If multiple claims point to the same pattern, explain the implication and the reasoning trail from evidence to conclusion.",
        "18. Where relevant, explicitly surface graph-intelligence findings: connected entities, evidence clusters, corroboration strength, confidence levels, and contradictions or unresolved disputes.",
        "19. Explicitly include the Evidence Support Summary block in the chapter body so the reader can see the measurable support behind the claim.",
        "20. Structure with clear paragraphs. Use bullet points only for lists of names/sources.",
        "21. OUTPUT: Return only raw JSON matching the schema. No code blocks. No commentary.",
    ]

    if existing_content:
        parts += ["", "=== EXISTING CONTENT (REVISE IN PLACE; KEEP WHAT IS STILL TRUE, DELETE OR UPDATE WHAT IS OUTDATED) ===", existing_content, ""]

    if evidence_context:
        parts += ["", "=== OPTIONAL EVIDENCE CONTEXT (USE WHEN A CLAIM IS AMBIGUOUS OR NEEDS SUPPORT) ==="]
        for ctx in evidence_context:
            parts.append(ctx)
        parts.append("")

    if new_facts:
        parts += ["", "=== NEW EVIDENCE TO INCORPORATE ==="]
        for f in new_facts[:max_facts]:
            parts.append(f)
        parts.append("")

    if existing_facts:
        parts += ["", "=== ALREADY INTEGRATED EVIDENCE ==="]
        for f in existing_facts[:max_facts]:
            parts.append(f)
        parts.append("")

    parts.append("=== EVIDENCE BASE ===")
    for f in facts[:max_facts]:
        parts.append(f)

    prompt = "\n".join(parts)
    
    try:
        resp = llm_pool.chat_completions_create(
            model='TIER_HEAVY',
            messages=[{"role": "user", "content": prompt}],
            response_model=ChapterSection,
            temperature=0.3,
        )
        return _normalize_response_content(resp)
    except Exception as e:
        print(f"  [ReportWriter] LLM error in '{chapter_title}': {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Chapter Builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_sitrep(target: str, claims: list[dict], leads: list[dict],
                  inv_meta: dict, existing: Optional[str],
                  evidence_context: Optional[list[str]] = None,
                  new_facts: Optional[list[str]] = None,
                  existing_facts: Optional[list[str]] = None,
                  support_metrics: Optional[dict] = None) -> str:
    """Chapter 1: Executive Situation Report (live-updated)."""
    explored  = sum(1 for l in leads if l['status'] == 'EXPLORED')
    pending   = sum(1 for l in leads if l['status'] == 'PENDING')
    total_cls = len(claims)
    contra    = sum(1 for c in claims if c['claim_status'] == 'CONTRADICTED')
    top_facts = [_claim_line(c) for c in claims[:15]]
    
    instruction = (
        f"Write an executive situation report (SITREP) for this active investigation. "
        f"Cover: (1) what is being investigated and why, (2) current status summary, "
        f"(3) most significant findings so far, (4) outstanding threads. "
        f"Stats: {total_cls} claims extracted, {explored} leads explored, "
        f"{pending} leads pending, {contra} contradicted claims."
    )
    return _generate_chapter(target, "Executive Situation Report", instruction, top_facts, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_target_profile(target: str, claims: list[dict], existing: Optional[str],
                          evidence_context: Optional[list[str]] = None,
                          new_facts: Optional[list[str]] = None,
                          existing_facts: Optional[list[str]] = None,
                          support_metrics: Optional[dict] = None) -> str:
    """Chapter 2: Target Profile & Background."""
    # Filter for identity/classification claims
    profile_preds = {'IS_A','SUBCLASS_OF','IS_TYPE_OF','WAS_BORN_IN','FOUNDED_IN',
                     'ALIAS_OF','ALSO_KNOWN_AS','HAS_NATIONALITY','IS_MEMBER_OF',
                     'HAS_ROLE','HAS_POSITION','IS_AFFILIATED_WITH'}
    profile_claims = [c for c in claims if str(c.get('predicate','')).upper() in profile_preds]
    all_claims = profile_claims[:40] or claims[:40]
    facts = [_claim_line(c) for c in all_claims]
    instruction = (
        "Write a comprehensive profile of the investigation target. "
        "Cover identity, background, known aliases, affiliations, roles, and key biographical facts. "
        "This is the 'Who/What is the target?' chapter."
    )
    return _generate_chapter(target, "Target Profile & Background", instruction, facts, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_timeline(target: str, claims: list[dict], existing: Optional[str],
                    evidence_context: Optional[list[str]] = None,
                    new_facts: Optional[list[str]] = None,
                    existing_facts: Optional[list[str]] = None,
                    support_metrics: Optional[dict] = None) -> str:
    """Chapter 3: Chronological Event Timeline."""
    timed = [c for c in claims if c.get('temporal_anchor') and str(c['temporal_anchor']).strip()]
    timed.sort(key=lambda c: str(c.get('temporal_anchor') or ''))
    facts = [_claim_line(c) for c in timed[:60]]
    if not facts:
        return ""
    instruction = (
        "Write a strict chronological timeline of all events and facts that have a date. "
        "Treat the timeline as a temporal evolution narrative: explain how the situation changed from one year to the next, what accelerated or stalled, and what the visible pattern of progression is across the observed years. "
        "If a year or years are missing from the evidence, explicitly call out the missing years as a gap in the timeline and avoid inventing intermediate events. "
        "Format each entry as: **[DATE]** — Event description [REF:id]. "
        "Create a true intelligence-style chronology rather than a flat list. "
        "Cover the full span of the investigation from earliest to most recent."
    )
    return _generate_chapter(target, "Chronological Event Timeline", instruction, facts, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_actors_map(target: str, claims: list[dict], existing: Optional[str],
                      evidence_context: Optional[list[str]] = None,
                      new_facts: Optional[list[str]] = None,
                      existing_facts: Optional[list[str]] = None,
                      support_metrics: Optional[dict] = None) -> str:
    """Chapter 4: Key Actors & Network Map."""
    actor_preds = {'WORKS_FOR','IS_FUNDED_BY','IS_ASSOCIATED_WITH','CONTROLS','OWNS',
                   'IS_DIRECTOR_OF','IS_CEO_OF','IS_MEMBER_OF','COLLABORATED_WITH',
                   'IS_PARTNER_OF','HIRED','APPOINTED','WAS_ARRESTED_BY'}
    actor_claims = [c for c in claims if str(c.get('predicate','')).upper() in actor_preds]
    all_claims = actor_claims[:50] or claims[:30]
    facts = [_claim_line(c) for c in all_claims]
    instruction = (
        "Write the key actors and network map section. "
        "Identify and describe all named individuals, organisations, and entities "
        "involved with the target. Describe each actor's role and relationship. "
        "Use **Actor Name** (Role) format for each person or organisation. "
        "Where a relationship chain is salient, include a compact inline diagram line using the form: GRAPH: Person ↓ Organizations ↓ Companies ↓ Events ↓ Locations."
    )
    return _generate_chapter(target, "Key Actors & Network Map", instruction, facts, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_evidence_page(target: str, page_claims: list[dict],
                          page_num: int, total_pages: int,
                          existing: Optional[str],
                          evidence_context: Optional[list[str]] = None,
                          new_facts: Optional[list[str]] = None,
                          existing_facts: Optional[list[str]] = None,
                          support_metrics: Optional[dict] = None) -> str:
    """Chapter 5+: Evidence Dossier — single paginated sub-chapter."""
    facts = [_claim_line(c) for c in page_claims]
    instruction = (
        f"Write Evidence Dossier sub-chapter (page {page_num} of {total_pages}). "
        f"Present each claim as a formal evidential finding. "
        f"Group related claims under ## sub-headings by theme. "
        f"Flag CONTRADICTED claims with ⚠️ WARNING prefix. "
        f"Flag 🥇 highly corroborated facts (confirmed by 3+ independent sources). "
        f"Every claim MUST have [REF:id] citation."
    )
    return _generate_chapter(
        target, f"Evidence Dossier — Page {page_num} of {total_pages}",
        instruction, facts, existing, max_facts=PAGE_SIZE,
        evidence_context=evidence_context,
        new_facts=new_facts,
        existing_facts=existing_facts,
        support_metrics=support_metrics
    )


def _build_source_assessment(target: str, sources: list[dict], existing: Optional[str],
                              evidence_context: Optional[list[str]] = None,
                              new_facts: Optional[list[str]] = None,
                              existing_facts: Optional[list[str]] = None,
                              support_metrics: Optional[dict] = None) -> str:
    """Chapter: Source Intelligence Assessment."""
    src_lines = []
    for s in sources[:60]:
        score = round(float(s.get('epistemic_trust_score') or 0.5), 2)
        tier  = "HIGH" if score >= 0.8 else "MEDIUM" if score >= 0.5 else "LOW"
        src_lines.append(
            f"- **{s['name']}** ({s['domain']}) | Trust: {score} [{tier}] | Claims: {s['claim_count']}"
        )
    instruction = (
        "Write a source intelligence assessment. "
        "Evaluate the quality, diversity, and reliability of sources used. "
        "Note which sources provided the most evidence and flag any low-trust sources."
    )
    return _generate_chapter(target, "Source Intelligence Assessment", instruction, src_lines, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_leads_coverage(target: str, leads: list[dict], existing: Optional[str],
                          evidence_context: Optional[list[str]] = None,
                          new_facts: Optional[list[str]] = None,
                          existing_facts: Optional[list[str]] = None,
                          support_metrics: Optional[dict] = None) -> str:
    """Chapter: Lead Threads & OSINT Coverage."""
    explored = [l for l in leads if l['status'] == 'EXPLORED']
    pending  = [l for l in leads if l['status'] == 'PENDING']
    skipped  = [l for l in leads if l['status'] not in ('EXPLORED','PENDING')]
    
    lead_lines = ["**EXPLORED LEADS:**"]
    for l in explored[:50]:
        lead_lines.append(f"  ✅ [{l['lead_type']}] {l['entity_name']} (priority: {l['priority']})")
    lead_lines.append("\n**PENDING LEADS:**")
    for l in pending[:30]:
        lead_lines.append(f"  🔄 [{l['lead_type']}] {l['entity_name']} (priority: {l['priority']})")
    if skipped:
        lead_lines.append("\n**SKIPPED/DEFERRED:**")
        for l in skipped[:20]:
            lead_lines.append(f"  ⏭️ [{l['lead_type']}] {l['entity_name']}")
    
    instruction = (
        "Write the OSINT lead coverage chapter. "
        "Summarise how the investigation was conducted: which leads were followed, "
        "what was found per lead, and what remains unexplored. "
        "This documents the investigative methodology."
    )
    return _generate_chapter(target, "Lead Threads & OSINT Coverage", instruction, lead_lines, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_contradictions(target: str, claims: list[dict], existing: Optional[str],
                          evidence_context: Optional[list[str]] = None,
                          new_facts: Optional[list[str]] = None,
                          existing_facts: Optional[list[str]] = None,
                          support_metrics: Optional[dict] = None) -> str:
    """Chapter: Contradictions & Disputes."""
    contra = [c for c in claims if c.get('claim_status') == 'CONTRADICTED' or str(c.get('neo4j_stance') or '').upper() == 'CONTRADICTS']
    if not contra:
        return ""
    facts = [_claim_line(c) for c in contra[:60]]
    instruction = (
        "Write the contradictions and disputes chapter as a real evidentiary analysis. "
        "For each dispute, explain the conflicting claims, identify the sources on each side, note the confidence and corroboration of each claim, and assess which version is better supported. "
        "When one side has stronger provenance or a higher-confidence source, say so clearly. "
        "Treat disagreements as a core investigative finding, not a footnote."
    )
    return _generate_chapter(target, "Contradictions & Disputes", instruction, facts, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


def _build_knowledge_gaps(target: str, leads: list[dict], inv_meta: dict,
                           existing: Optional[str],
                           evidence_context: Optional[list[str]] = None,
                           new_facts: Optional[list[str]] = None,
                           existing_facts: Optional[list[str]] = None,
                           support_metrics: Optional[dict] = None) -> str:
    """Chapter: Knowledge Gaps & Open Questions."""
    pending = [l for l in leads if l.get('status') == 'PENDING']
    gap_lines = []
    for l in pending[:40]:
        entity = l.get('entity_name') or 'Unknown entity'
        lead_type = l.get('lead_type') or 'GENERAL'
        priority = l.get('priority') or 0
        gap_lines.append(
            f"- Open Intelligence Gap: [{lead_type}] **{entity}** (priority {priority}) — the next step is to investigate this unresolved line of inquiry and close the evidence hole."
        )
    if not gap_lines:
        gap_lines = ["- Open Intelligence Gap: No significant pending leads remain. The investigation has no clear unresolved line to pursue next."]
    instruction = (
        "Write the knowledge gaps chapter as an actionable Open Intelligence Gaps register. "
        "Translate unresolved leads into concrete analyst question statements that tell the reader where to investigate next. "
        "Use a dedicated 'Open Intelligence Gaps' section and present each item as a concrete unresolved question or missing evidence category, not just a generic lead name. "
        "Frame the section around the next step: what is unknown, why it matters, and how it should be investigated next."
    )
    return _generate_chapter(target, "Knowledge Gaps & Open Questions", instruction, gap_lines, existing, evidence_context=evidence_context, new_facts=new_facts, existing_facts=existing_facts, support_metrics=support_metrics)


# ─────────────────────────────────────────────────────────────────────────────
# Report Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_existing_report(pg_conn, investigation_id: int) -> tuple[dict, dict]:
    """Load existing report JSON and chapter hashes from DB."""
    conn = _ensure_pg_connection(pg_conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT report, report_chapter_hashes FROM investigations WHERE id = %s",
            (investigation_id,)
        )
        row = cur.fetchone()
    if not row:
        return {}, {}
    report = row[0] or {}
    hashes = row[1] or {}
    return report, hashes


def _save_report(pg_conn, investigation_id: int, report: dict, hashes: dict):
    """Persist the updated report JSON and hashes to DB."""
    from psycopg2.extras import Json

    conn = _ensure_pg_connection(pg_conn)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE investigations
            SET report               = %s,
                report_chapter_hashes = %s,
                report_updated_at    = NOW()
            WHERE id = %s
        """, (Json(report), Json(hashes), investigation_id))
    conn.commit()


def _checkpoint_report_state(pg_conn, investigation_id: int, report: dict, hashes: dict):
    """Durably checkpoint the current report state to DB so an interrupted tick can resume cleanly."""
    save_conn = _ensure_pg_connection(pg_conn)
    try:
        _save_report(save_conn, investigation_id, report, hashes)
        save_conn.commit()
    except Exception as exc:
        if _is_recoverable_db_error(exc):
            save_conn = _connect_postgres()
            _save_report(save_conn, investigation_id, report, hashes)
            save_conn.commit()
        else:
            raise
    finally:
        try:
            save_conn.close()
        except Exception:
            pass


def _export_markdown(investigation_id: int, target: str, report: dict, status: str = "ACTIVE") -> str:
    """Serialize the report dict to a Markdown string for file export."""
    lines = [
        "---",
        f"# INVESTIGATION DOSSIER",
        f"**Investigation ID:** #{investigation_id}  ",
        f"**Target:** {target}  ",
        f"**Status:** {status}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        "---",
        "",
    ]
    chapter_order = sorted(
        [k for k in report.keys() if not k.startswith('_')],
        key=lambda k: report[k].get('order', 999)
    )
    for key in chapter_order:
        chap = report[key]
        content = chap.get('content', '').strip()
        if not content:
            continue
        lines.append(f"## {chap.get('title', key)}")
        lines.append(f"*Last updated: {chap.get('last_updated', 'N/A')}*")
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    # References section
    refs = report.get('_references', [])
    if refs:
        lines.append("## References")
        for ref in refs:
            src  = ref.get('original_source') or ref.get('source_name') or 'Unknown'
            url  = ref.get('source_url') or ref.get('original_url') or ''
            date = str(ref.get('publish_date') or '')[:10]
            title= ref.get('article_title') or ''
            cid  = ref.get('claim_id') or ref.get('uuid') or ''
            link = f"[{src}]({url})" if url else src
            lines.append(f"- [{cid}] {link} — *{title}* ({date})")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def run_report_tick(
    investigation_id: int,
    investigation_target: str,
    inv_meta: dict = {},
    pg_conn=None,
) -> None:
    """
    Called by the orchestrator on each tick.
    Revisions the investigation report chapters in place using the prior draft,
    the latest evidence, and any extra article context needed to resolve ambiguity.
    """
    print(f"  [ReportWriter] Tick for investigation #{investigation_id}: '{investigation_target[:60]}'")
    
    import psycopg2
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Reuse the orchestrator's connection when provided; otherwise open a temporary one.
    load_conn = pg_conn
    should_close_conn = False
    if load_conn is None:
        load_conn = _connect_postgres(DATABASE_URL)
        should_close_conn = True
    else:
        load_conn = _ensure_pg_connection(load_conn, DATABASE_URL)

    try:
        claims  = _fetch_investigation_claims(load_conn, investigation_id)
        leads   = _fetch_investigation_leads(load_conn, investigation_id)
        sources = _fetch_investigation_sources(load_conn, investigation_id)
        evidence_counts = _fetch_investigation_evidence_counts(load_conn, investigation_id)
        existing_report, existing_hashes = _load_existing_report(load_conn, investigation_id)
        evidence_context = _fetch_evidence_context(load_conn, claims)
    finally:
        if should_close_conn:
            try:
                load_conn.close()
            except Exception:
                pass

    if not claims:
        print(f"  [ReportWriter] No claims yet for #{investigation_id} — skipping.")
        return

    updated_report = dict(existing_report)
    updated_hashes = dict(existing_hashes)
    all_refs: list = list(existing_report.get('_references', []))
    changed = False
    save_conn = _connect_postgres(DATABASE_URL)

    def _write_chapter(key: str, order: int, title: str, content: str,
                        new_hash: str, claims_used: list):
        nonlocal changed, all_refs
        if not content:
            return
        updated_report[key] = {
            "title": title,
            "order": order,
            "content": content,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        updated_hashes[key] = new_hash
        # Accumulate references
        for c in claims_used:
            cid = str(c.get('claim_id', ''))
            if cid and not any(r.get('claim_id') == cid for r in all_refs):
                all_refs.append({
                    "claim_id":       cid,
                    "source_name":    c.get('original_source') or c.get('source_name') or 'Unknown',
                    "source_url":     c.get('original_url') or c.get('source_url') or '',
                    "publish_date":   str(c.get('publish_date') or ''),
                    "article_title":  c.get('article_title') or '',
                    "quote_context":  c.get('quote_context') or '',
                    "epistemic_score": float(c.get('epistemic_score') or 0.5),
                    "corroboration_count": c.get('corroboration_count', 1),
                })
        changed = True
        _checkpoint_report_state(save_conn, investigation_id, updated_report, updated_hashes)

    def _maybe_write(key: str, order: int, title: str,
                      new_hash: str, generator, claims_used: list):
        print(f"    [REV ] Chapter '{title}'...")
        existing_content = existing_report.get(key, {}).get('content')
        chapter_context = _select_relevant_context(claims_used, evidence_context)
        chapter_state = _load_chapter_relevance_state(updated_report, key)

        remaining_claims = list(claims_used)
        final_content = existing_content or ""
        iterations = 0
        max_iterations = max(2, min(6, len(claims_used) // 10 + 1))

        while remaining_claims and iterations < max_iterations:
            iterations += 1
            new_claims, existing_claims, context_claims = _plan_chapter_evidence(
                remaining_claims,
                final_content,
                chapter_state,
            )
            new_evidence = [_claim_line(c) for c in new_claims[:20]]
            existing_evidence = [_claim_line(c) for c in existing_claims[:20]]
            context_evidence = [_claim_line(c) for c in context_claims[:20]]

            if not new_evidence and not existing_evidence:
                break

            if new_evidence:
                print(f"      [ADD ] {len(new_evidence)} new evidence item(s) for chapter '{title}' (iteration {iterations}).")
            if existing_evidence:
                print(f"      [REV ] {len(existing_evidence)} existing evidence item(s) carried forward for chapter '{title}' (iteration {iterations}).")
            if context_evidence:
                print(f"      [CTX ] {len(context_evidence)} chapter-context claim(s) preserved for later relevance checks in '{title}'.")

            content = generator(final_content, chapter_context, new_evidence, existing_evidence)
            if not content:
                content = final_content or ""
                print(f"      [WARN] Chapter '{title}' generated empty content; preserved prior draft.")

            final_content = content
            remaining_claims = [c for c in remaining_claims if str(c.get('claim_id')) not in {str(x.get('claim_id')) for x in existing_claims + new_claims}]
            if not new_claims:
                break

            chapter_state["relevant_claim_ids"] = list(set(chapter_state.get("relevant_claim_ids", [])) | {str(c.get('claim_id')) for c in new_claims})
            chapter_state["ignored_claim_ids"] = list(set(chapter_state.get("ignored_claim_ids", [])) | {str(c.get('claim_id')) for c in context_claims})
            chapter_state["context_claim_ids"] = list(set(chapter_state.get("context_claim_ids", [])) | {str(c.get('claim_id')) for c in context_claims})
            _save_chapter_relevance_state(updated_report, key, chapter_state)

        if not final_content:
            final_content = existing_content or ""
            print(f"      [WARN] Chapter '{title}' generated empty content; preserved prior draft.")

        chapter_state = _load_chapter_relevance_state(updated_report, key)
        chapter_state["relevant_claim_ids"] = list(set(chapter_state.get("relevant_claim_ids", [])) | {str(c.get('claim_id')) for c in claims_used if c.get('claim_id')})
        _save_chapter_relevance_state(updated_report, key, chapter_state)

        _write_chapter(key, order, title, final_content, new_hash, claims_used)
        time.sleep(1)  # pace the LLM calls

    # ── Chapter 1: SITREP ────────────────────────────────────────────────────
    sitrep_hash = _chapter_hash(claims[:20] + leads[:10])
    _maybe_write("ch1_sitrep", 1, "Executive Situation Report", sitrep_hash,
                  lambda ex, ctx, nf, ef: _build_sitrep(investigation_target, claims, leads, inv_meta, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  claims[:20])

    # ── Chapter 2: Target Profile ────────────────────────────────────────────
    profile_claims = claims[:50]
    profile_hash   = _chapter_hash(profile_claims)
    _maybe_write("ch2_profile", 2, "Target Profile & Background", profile_hash,
                  lambda ex, ctx, nf, ef: _build_target_profile(investigation_target, profile_claims, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  profile_claims)

    # ── Chapter 3: Timeline ──────────────────────────────────────────────────
    timed = [c for c in claims if c.get('temporal_anchor')]
    if timed:
        timed_hash = _chapter_hash(timed)
        _maybe_write("ch3_timeline", 3, "Chronological Event Timeline", timed_hash,
                      lambda ex, ctx, nf, ef: _build_timeline(investigation_target, claims, ex, ctx, nf, ef, support_metrics=evidence_counts),
                      timed)

    # ── Chapter 4: Actors Map ────────────────────────────────────────────────
    actors_hash = _chapter_hash(claims[:60])
    _maybe_write("ch4_actors", 4, "Key Actors & Network Map", actors_hash,
                  lambda ex, ctx, nf, ef: _build_actors_map(investigation_target, claims, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  claims[:60])

    # ── Chapters 5+: Evidence Dossier (paginated) ────────────────────────────
    pages = [claims[i:i+PAGE_SIZE] for i in range(0, min(len(claims), MAX_CHAPTERS * PAGE_SIZE), PAGE_SIZE)]
    total_pages = len(pages)
    for page_num, page_claims in enumerate(pages, start=1):
        key = f"ch5_evidence_p{page_num:03d}"
        order = 5 + page_num - 1
        title = f"Evidence Dossier — Page {page_num} of {total_pages}"
        page_hash = _chapter_hash(page_claims)
        _maybe_write(key, order, title, page_hash,
                      lambda ex, ctx, nf, ef, pc=page_claims, pn=page_num, tp=total_pages:
                          _build_evidence_page(investigation_target, pc, pn, tp, ex, ctx, nf, ef, support_metrics=evidence_counts),
                      page_claims)

    base_order = 5 + total_pages

    # ── Chapter: Source Assessment ───────────────────────────────────────────
    src_hash = hashlib.md5(json.dumps([s['name'] for s in sources[:60]], sort_keys=True).encode()).hexdigest()
    _maybe_write("ch_sources", base_order, "Source Intelligence Assessment", src_hash,
                  lambda ex, ctx, nf, ef: _build_source_assessment(investigation_target, sources, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  [])

    # ── Chapter: Lead Coverage ───────────────────────────────────────────────
    leads_hash = _chapter_hash([{'claim_id': l['entity_name']} for l in leads])
    _maybe_write("ch_leads", base_order + 1, "Lead Threads & OSINT Coverage", leads_hash,
                  lambda ex, ctx, nf, ef: _build_leads_coverage(investigation_target, leads, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  [])

    # ── Chapter: Contradictions ──────────────────────────────────────────────
    contra = [c for c in claims if c.get('claim_status') == 'CONTRADICTED']
    if contra:
        contra_hash = _chapter_hash(contra)
        _maybe_write("ch_contradictions", base_order + 2, "Contradictions & Disputes", contra_hash,
                      lambda ex, ctx, nf, ef: _build_contradictions(investigation_target, contra, ex, ctx, nf, ef, support_metrics=evidence_counts),
                      contra)

    # ── Chapter: Knowledge Gaps ──────────────────────────────────────────────
    gaps_hash = _chapter_hash([{'claim_id': l['entity_name']} for l in leads if l['status'] == 'PENDING'])
    _maybe_write("ch_gaps", base_order + 3, "Knowledge Gaps & Open Questions", gaps_hash,
                  lambda ex, ctx, nf, ef: _build_knowledge_gaps(investigation_target, leads, inv_meta, ex, ctx, nf, ef, support_metrics=evidence_counts),
                  [])

    if not changed:
        print(f"  [ReportWriter] No changes for #{investigation_id}. All chapters current.")
        return

    # Sort and save references
    updated_report['_references'] = sorted(all_refs,
                                            key=lambda x: x.get('corroboration_count', 0),
                                            reverse=True)

    try:
        _save_report(save_conn, investigation_id, updated_report, updated_hashes)
        save_conn.commit()
        seeded_gaps = _persist_open_intelligence_gaps(save_conn, investigation_id, updated_report)
        if seeded_gaps:
            print(f"  [ReportWriter] Re-seeded {seeded_gaps} Open Intelligence Gap lead(s) for #{investigation_id}.")
    except Exception as exc:
        if _is_recoverable_db_error(exc):
            save_conn = _connect_postgres(DATABASE_URL)
            _save_report(save_conn, investigation_id, updated_report, updated_hashes)
            save_conn.commit()
            seeded_gaps = _persist_open_intelligence_gaps(save_conn, investigation_id, updated_report)
            if seeded_gaps:
                print(f"  [ReportWriter] Re-seeded {seeded_gaps} Open Intelligence Gap lead(s) for #{investigation_id}.")
        else:
            raise
    finally:
        try:
            save_conn.close()
        except Exception:
            pass

    print(f"  [ReportWriter] Saved report update for #{investigation_id} "
          f"({len(updated_report)-1} chapters, {len(claims)} claims).")
