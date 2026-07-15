"""
ai_engine/orchestrator/report_writer.py
─────────────────────────────────────────────────────────────────────────────
Living Investigation Report Writer

Called by the orchestrator on every tick for each ACTIVE investigation.
Incrementally builds a multi-chapter dossier as new claims arrive.

Architecture:
  - Mirrors article_worker.py's MD5 hash-diff logic so only changed chapters
    are re-generated (unchanged chapters are preserved as-is).
  - Uses self-hosted Ollama (gemma2:9b) exclusively via llm_pool.
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
                cp.internet_original_source AS original_source
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


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _chapter_hash(items: list) -> str:
    """MD5 of sorted claim IDs — same as article_worker's section hash."""
    key = sorted([str(i.get('claim_id', i)) for i in items])
    return hashlib.md5("".join(key).encode()).hexdigest()


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
    """Format a single claim for LLM context."""
    badge = "🥇 " if c.get('corroboration_count', 1) >= 3 else "🥈 " if c.get('corroboration_count', 1) == 2 else ""
    pred  = str(c.get('predicate') or '').replace('_', ' ').lower()
    line  = f"{badge}[REF:{c['claim_id']}] {c['subject']} {pred} {c['object_entity']}"
    if c.get('temporal_anchor'): line += f" (when: {c['temporal_anchor']})"
    if c.get('spatial_anchor'):  line += f" (where: {c['spatial_anchor']})"
    if c.get('quote_context'):   line += f'\n   > "{str(c["quote_context"])[:200]}"'
    src = c.get('original_source') or c.get('source_name') or 'Unknown'
    url = c.get('original_url') or c.get('source_url') or 'N/A'
    line += f"\n   Source: {src} | URL: {url} | Score: {round(float(c.get('epistemic_score') or 0.5), 2)}"
    return line


# ─────────────────────────────────────────────────────────────────────────────
# LLM Section Generator (self-hosted only)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_chapter(
    investigation_target: str,
    chapter_title: str,
    instruction: str,
    facts: list[str],
    existing_content: Optional[str] = None,
    max_facts: int = 40
) -> str:
    """Write/expand a single chapter using the local LLM."""
    
    parts = [
        f"You are writing a formal intelligence investigation dossier about: \"{investigation_target}\".",
        f"Write the chapter titled: \"{chapter_title}\".",
        "",
        "MISSION:",
        instruction,
        "",
        "STRICT RULES:",
        "1. Write in formal investigative report prose. Authoritative, precise, third-person.",
        "2. Every factual statement MUST end with [REF:<id>] citing the fact ID from the evidence below.",
        "3. You MUST include a '## References' section at the very end of the chapter mapping every [REF:<id>] used to its Source Name and URL.",
        "4. Use markdown: ## for section headers, **bold** for key names.",
        "5. DO NOT hallucinate. Only use the provided facts. Do not invent claims.",
        "5. Structure with clear paragraphs. Use bullet points only for lists of names/sources.",
        "6. OUTPUT: Return only raw JSON matching the schema. No code blocks. No commentary.",
    ]
    
    if existing_content:
        parts += ["", "=== EXISTING CONTENT (EXPAND, DO NOT REPEAT) ===", existing_content, ""]
    
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
            max_tokens=800,
        )
        return _normalize_response_content(resp)
    except Exception as e:
        print(f"  [ReportWriter] LLM error in '{chapter_title}': {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Chapter Builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_sitrep(target: str, claims: list[dict], leads: list[dict],
                  inv_meta: dict, existing: Optional[str]) -> str:
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
    return _generate_chapter(target, "Executive Situation Report", instruction, top_facts, existing)


def _build_target_profile(target: str, claims: list[dict], existing: Optional[str]) -> str:
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
    return _generate_chapter(target, "Target Profile & Background", instruction, facts, existing)


def _build_timeline(target: str, claims: list[dict], existing: Optional[str]) -> str:
    """Chapter 3: Chronological Event Timeline."""
    timed = [c for c in claims if c.get('temporal_anchor') and str(c['temporal_anchor']).strip()]
    timed.sort(key=lambda c: str(c.get('temporal_anchor') or ''))
    facts = [_claim_line(c) for c in timed[:60]]
    if not facts:
        return ""
    instruction = (
        "Write a strict chronological timeline of all events and facts that have a date. "
        "Format each entry as: **[DATE]** — Event description [REF:id]. "
        "Cover the full span of the investigation from earliest to most recent."
    )
    return _generate_chapter(target, "Chronological Event Timeline", instruction, facts, existing)


def _build_actors_map(target: str, claims: list[dict], existing: Optional[str]) -> str:
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
        "Use **Actor Name** (Role) format for each person or organisation."
    )
    return _generate_chapter(target, "Key Actors & Network Map", instruction, facts, existing)


def _build_evidence_page(target: str, page_claims: list[dict],
                          page_num: int, total_pages: int,
                          existing: Optional[str]) -> str:
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
        instruction, facts, existing, max_facts=PAGE_SIZE
    )


def _build_source_assessment(target: str, sources: list[dict], existing: Optional[str]) -> str:
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
    return _generate_chapter(target, "Source Intelligence Assessment", instruction, src_lines, existing)


def _build_leads_coverage(target: str, leads: list[dict], existing: Optional[str]) -> str:
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
    return _generate_chapter(target, "Lead Threads & OSINT Coverage", instruction, lead_lines, existing)


def _build_contradictions(target: str, claims: list[dict], existing: Optional[str]) -> str:
    """Chapter: Contradictions & Disputes."""
    contra = [c for c in claims if c.get('claim_status') == 'CONTRADICTED']
    if not contra:
        return ""
    facts = [_claim_line(c) for c in contra[:60]]
    instruction = (
        "Write the contradictions and disputes chapter. "
        "Document every case where the evidence base contains conflicting claims. "
        "For each contradiction: state both sides, cite sources, assess which is more credible. "
        "Flag unresolved contradictions clearly."
    )
    return _generate_chapter(target, "Contradictions & Disputes", instruction, facts, existing)


def _build_knowledge_gaps(target: str, leads: list[dict], inv_meta: dict,
                           existing: Optional[str]) -> str:
    """Chapter: Knowledge Gaps & Open Questions."""
    pending  = [l for l in leads if l['status'] == 'PENDING']
    gap_lines = []
    for l in pending[:40]:
        gap_lines.append(f"- Unexplored: [{l['lead_type']}] **{l['entity_name']}** (priority {l['priority']})")
    if not gap_lines:
        gap_lines = ["- No significant pending leads remain."]
    instruction = (
        "Write the knowledge gaps chapter. "
        "Document what this investigation did NOT fully resolve: "
        "unexplored leads, questions that arose but couldn't be answered, "
        "recommended next steps for a follow-up investigation."
    )
    return _generate_chapter(target, "Knowledge Gaps & Open Questions", instruction, gap_lines, existing)


# ─────────────────────────────────────────────────────────────────────────────
# Report Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_existing_report(pg_conn, investigation_id: int) -> tuple[dict, dict]:
    """Load existing report JSON and chapter hashes from DB."""
    with pg_conn.cursor() as cur:
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
    with pg_conn.cursor() as cur:
        cur.execute("""
            UPDATE investigations
            SET report               = %s,
                report_chapter_hashes = %s,
                report_updated_at    = NOW()
            WHERE id = %s
        """, (Json(report), Json(hashes), investigation_id))
    pg_conn.commit()


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
) -> None:
    """
    Called by the orchestrator on each tick.
    Incrementally builds/updates the investigation report chapters.
    Only regenerates chapters where new claims have arrived (hash diff).
    """
    print(f"  [ReportWriter] Tick for investigation #{investigation_id}: '{investigation_target[:60]}'")
    
    import psycopg2
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    # Open connection to load data
    load_conn = psycopg2.connect(DATABASE_URL)
    try:
        claims  = _fetch_investigation_claims(load_conn, investigation_id)
        leads   = _fetch_investigation_leads(load_conn, investigation_id)
        sources = _fetch_investigation_sources(load_conn, investigation_id)
        existing_report, existing_hashes = _load_existing_report(load_conn, investigation_id)
    finally:
        load_conn.close()

    if not claims:
        print(f"  [ReportWriter] No claims yet for #{investigation_id} — skipping.")
        return

    updated_report = dict(existing_report)
    updated_hashes = dict(existing_hashes)
    all_refs: list = list(existing_report.get('_references', []))
    changed = False

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

    def _maybe_write(key: str, order: int, title: str,
                      new_hash: str, generator, claims_used: list):
        if existing_hashes.get(key) == new_hash:
            print(f"    [SKIP] Chapter '{title}' unchanged.")
            return
        print(f"    [GEN ] Chapter '{title}'...")
        existing_content = existing_report.get(key, {}).get('content')
        content = generator(existing_content)
        _write_chapter(key, order, title, content, new_hash, claims_used)
        time.sleep(1)  # pace the LLM calls

    # ── Chapter 1: SITREP ────────────────────────────────────────────────────
    sitrep_hash = _chapter_hash(claims[:20] + leads[:10])
    _maybe_write("ch1_sitrep", 1, "Executive Situation Report", sitrep_hash,
                  lambda ex: _build_sitrep(investigation_target, claims, leads, inv_meta, ex),
                  claims[:20])

    # ── Chapter 2: Target Profile ────────────────────────────────────────────
    profile_claims = claims[:50]
    profile_hash   = _chapter_hash(profile_claims)
    _maybe_write("ch2_profile", 2, "Target Profile & Background", profile_hash,
                  lambda ex: _build_target_profile(investigation_target, profile_claims, ex),
                  profile_claims)

    # ── Chapter 3: Timeline ──────────────────────────────────────────────────
    timed = [c for c in claims if c.get('temporal_anchor')]
    if timed:
        timed_hash = _chapter_hash(timed)
        _maybe_write("ch3_timeline", 3, "Chronological Event Timeline", timed_hash,
                      lambda ex: _build_timeline(investigation_target, claims, ex),
                      timed)

    # ── Chapter 4: Actors Map ────────────────────────────────────────────────
    actors_hash = _chapter_hash(claims[:60])
    _maybe_write("ch4_actors", 4, "Key Actors & Network Map", actors_hash,
                  lambda ex: _build_actors_map(investigation_target, claims, ex),
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
                      lambda ex, pc=page_claims, pn=page_num, tp=total_pages:
                          _build_evidence_page(investigation_target, pc, pn, tp, ex),
                      page_claims)

    base_order = 5 + total_pages

    # ── Chapter: Source Assessment ───────────────────────────────────────────
    src_hash = hashlib.md5(json.dumps([s['name'] for s in sources[:60]], sort_keys=True).encode()).hexdigest()
    _maybe_write("ch_sources", base_order, "Source Intelligence Assessment", src_hash,
                  lambda ex: _build_source_assessment(investigation_target, sources, ex),
                  [])

    # ── Chapter: Lead Coverage ───────────────────────────────────────────────
    leads_hash = _chapter_hash([{'claim_id': l['entity_name']} for l in leads])
    _maybe_write("ch_leads", base_order + 1, "Lead Threads & OSINT Coverage", leads_hash,
                  lambda ex: _build_leads_coverage(investigation_target, leads, ex),
                  [])

    # ── Chapter: Contradictions ──────────────────────────────────────────────
    contra = [c for c in claims if c.get('claim_status') == 'CONTRADICTED']
    if contra:
        contra_hash = _chapter_hash(contra)
        _maybe_write("ch_contradictions", base_order + 2, "Contradictions & Disputes", contra_hash,
                      lambda ex: _build_contradictions(investigation_target, contra, ex),
                      contra)

    # ── Chapter: Knowledge Gaps ──────────────────────────────────────────────
    gaps_hash = _chapter_hash([{'claim_id': l['entity_name']} for l in leads if l['status'] == 'PENDING'])
    _maybe_write("ch_gaps", base_order + 3, "Knowledge Gaps & Open Questions", gaps_hash,
                  lambda ex: _build_knowledge_gaps(investigation_target, leads, inv_meta, ex),
                  [])

    if not changed:
        print(f"  [ReportWriter] No changes for #{investigation_id}. All chapters current.")
        return

    # Sort and save references
    updated_report['_references'] = sorted(all_refs,
                                            key=lambda x: x.get('corroboration_count', 0),
                                            reverse=True)
                                            
    save_conn = psycopg2.connect(DATABASE_URL)
    save_conn.autocommit = False
    try:
        _save_report(save_conn, investigation_id, updated_report, updated_hashes)
        save_conn.commit()
    finally:
        save_conn.close()

    print(f"  [ReportWriter] Saved report update for #{investigation_id} "
          f"({len(updated_report)-1} chapters, {len(claims)} claims).")
