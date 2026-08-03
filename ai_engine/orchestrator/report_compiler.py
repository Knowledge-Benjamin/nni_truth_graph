"""
ai_engine/orchestrator/report_compiler.py
─────────────────────────────────────────────────────────────────────────────
Final Report Compiler — called by terminator.py when an investigation closes.

Performs the final pass:
  1. Generates the sealed Executive Summary with full findings context
  2. Writes the Investigator Notes chapter (stats, duration, termination reason)
  3. Seals the report with a CLASSIFICATION header
  4. Exports the full report as a Markdown file to disk
  5. Updates investigations.report with the final sealed state
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from psycopg2.extras import Json

from .report_writer import (
    _fetch_investigation_claims,
    _fetch_investigation_leads,
    _fetch_investigation_sources,
    _load_existing_report,
    _save_report,
    _generate_chapter,
    _export_markdown,
    _claim_line,
)

REPORT_OUTPUT_DIR = os.getenv("REPORT_OUTPUT_DIR", "./investigation_reports")


def _build_executive_summary(target: str, claims: list[dict], leads: list[dict],
                               termination_reason: str, inv_row: dict) -> str:
    """
    Final sealed Executive Summary — comprehensive synthesis of the entire investigation.
    Written once at close with full knowledge of all findings.
    """
    explored = sum(1 for l in leads if l['status'] == 'EXPLORED')
    total_cls = len(claims)
    contra    = sum(1 for c in claims if c['claim_status'] == 'CONTRADICTED')
    duration  = ""
    if inv_row.get('created_at') and inv_row.get('completed_at'):
        try:
            dur = inv_row['completed_at'] - inv_row['created_at']
            duration = f"{dur.days}d {dur.seconds // 3600}h {(dur.seconds % 3600) // 60}m"
        except Exception:
            pass

    reason_map = {
        "GOAL_ACHIEVED":       "The primary investigation goal was answered with high confidence.",
        "EXHAUSTION":          "All identified leads have been fully explored.",
        "DIMINISHING_RETURNS": "Consecutive harvests yielded insufficient novel intelligence.",
        "HARD_LIMIT_LEADS":    "The maximum lead exploration limit was reached.",
        "HARD_LIMIT_DAYS":     "The maximum investigation duration was reached.",
    }
    reason_text = reason_map.get(termination_reason, termination_reason)

    top_facts = [_claim_line(c) for c in claims[:20]]

    instruction = (
        f"Write the FINAL SEALED Executive Summary for this completed investigation. "
        f"This is the opening chapter of the formal dossier. "
        f"Cover: (1) Investigation mandate and scope, "
        f"(2) Key findings and conclusions — what was definitively established, "
        f"(3) Key findings that remain uncertain or contested, "
        f"(4) Final assessment and recommendations. "
        f"Termination reason: {reason_text}. "
        f"Investigation stats: {total_cls} total claims extracted, "
        f"{explored} leads explored, {contra} contradicted claims, "
        f"duration: {duration or 'unknown'}. "
        f"Write with the gravity and precision of a formal intelligence report conclusion."
    )
    return _generate_chapter(
        target, "EXECUTIVE SUMMARY — FINAL SEALED REPORT",
        instruction, top_facts, existing_content=None, max_facts=20
    )


def _build_investigator_notes(target: str, claims: list[dict], leads: list[dict],
                               sources: list[dict], termination_reason: str,
                               inv_row: dict) -> str:
    """Final chapter: Investigator Notes & Case Statistics."""
    explored  = sum(1 for l in leads if l['status'] == 'EXPLORED')
    pending   = sum(1 for l in leads if l['status'] == 'PENDING')
    total_cls = len(claims)
    contra    = sum(1 for c in claims if c['claim_status'] == 'CONTRADICTED')
    high_corr = sum(1 for c in claims if c.get('corroboration_count', 1) >= 3)
    total_src = len(sources)
    high_trust_src = sum(1 for s in sources if float(s.get('epistemic_trust_score') or 0) >= 0.8)

    duration  = "N/A"
    if inv_row.get('created_at') and inv_row.get('completed_at'):
        try:
            dur = inv_row['completed_at'] - inv_row['created_at']
            duration = f"{dur.days} days, {dur.seconds // 3600} hours, {(dur.seconds % 3600) // 60} minutes"
        except Exception:
            pass

    notes_facts = [
        f"- Investigation Target: {target}",
        f"- Investigation ID: #{inv_row.get('id', 'N/A')}",
        f"- Opened: {str(inv_row.get('created_at', 'N/A'))[:19]}",
        f"- Closed: {str(inv_row.get('completed_at', 'N/A'))[:19]}",
        f"- Total Duration: {duration}",
        f"- Termination Reason: {termination_reason}",
        f"- Total Claims Extracted: {total_cls}",
        f"- Highly Corroborated Claims (3+ sources): {high_corr}",
        f"- Contradicted Claims: {contra}",
        f"- Total Leads Identified: {len(leads)}",
        f"- Leads Explored: {explored}",
        f"- Leads Pending at Close: {pending}",
        f"- Unique Sources Used: {total_src}",
        f"- High-Trust Sources (≥0.8): {high_trust_src}",
        f"- Pipeline model: gemma-4-e4b (self-hosted Ollama)",
    ]
    instruction = (
        "Write the Investigator Notes chapter. "
        "Present the case statistics formally, as a closing administrative record. "
        "Briefly note any significant methodological observations or limitations. "
        "This is the final page of the dossier."
    )
    return _generate_chapter(
        target, "Investigator Notes & Case Statistics",
        instruction, notes_facts, existing_content=None
    )


def compile_final_report(
    investigation_id: int,
    termination_reason: str,
    pg_conn,
) -> None:
    """
    Called by terminator.py upon investigation completion.
    Seals the report, writes the final Executive Summary and Investigator Notes,
    and exports the full dossier as a Markdown file.
    """
    print(f"[ReportCompiler] Compiling final report for #{investigation_id}...")

    # Load investigation metadata
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT id, target, goal_type, status, created_at, completed_at,
                   findings, max_leads, max_days, leads_explored
            FROM investigations WHERE id = %s
        """, (investigation_id,))
        row = cur.fetchone()
        if not row:
            print(f"[ReportCompiler] Investigation #{investigation_id} not found.")
            return
        cols = [d[0] for d in cur.description]
        inv_row = dict(zip(cols, row))

    target = inv_row['target']

    claims  = _fetch_investigation_claims(pg_conn, investigation_id)
    leads   = _fetch_investigation_leads(pg_conn, investigation_id)
    sources = _fetch_investigation_sources(pg_conn, investigation_id)

    existing_report, existing_hashes = _load_existing_report(pg_conn, investigation_id)
    final_report = dict(existing_report)

    # ── Final Executive Summary (overwrites the live SITREP) ─────────────────
    print(f"  [ReportCompiler] Generating final Executive Summary...")
    exec_summary = _build_executive_summary(target, claims, leads, termination_reason, inv_row)
    if exec_summary:
        final_report['ch0_executive_summary'] = {
            "title": "EXECUTIVE SUMMARY — FINAL SEALED REPORT",
            "order": 0,
            "content": exec_summary,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "sealed": True,
        }

    # ── Remove live SITREP (replaced by sealed executive summary) ────────────
    final_report.pop('ch1_sitrep', None)

    # ── Investigator Notes (final chapter) ───────────────────────────────────
    print(f"  [ReportCompiler] Generating Investigator Notes...")
    max_order = max(
        (v.get('order', 0) for k, v in final_report.items() if not k.startswith('_')),
        default=20
    )
    notes_content = _build_investigator_notes(
        target, claims, leads, sources, termination_reason, inv_row
    )
    if notes_content:
        final_report[f'ch_final_notes'] = {
            "title": "Investigator Notes & Case Statistics",
            "order": max_order + 1,
            "content": notes_content,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "sealed": True,
        }

    # ── Seal the report ───────────────────────────────────────────────────────
    final_report['_meta'] = {
        "sealed": True,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "termination_reason": termination_reason,
        "total_claims": len(claims),
        "total_leads": len(leads),
        "total_sources": len(sources),
        "investigation_id": investigation_id,
        "target": target,
    }

    # Save sealed report to DB
    _save_report(pg_conn, investigation_id, final_report, existing_hashes)
    print(f"  [ReportCompiler] Sealed report saved to DB for #{investigation_id}.")

    # ── Export to Markdown file ───────────────────────────────────────────────
    try:
        os.makedirs(REPORT_OUTPUT_DIR, exist_ok=True)
        safe_target = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in target)[:60]
        filename = f"investigation_{investigation_id:04d}_{safe_target.replace(' ', '_')}.md"
        filepath = os.path.join(REPORT_OUTPUT_DIR, filename)

        md_content = _export_markdown(investigation_id, target, final_report, status="COMPLETED")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md_content)

        print(f"  [ReportCompiler] Markdown report exported to: {filepath}")

        # Store file path in findings
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE investigations SET findings = findings || %s::jsonb WHERE id = %s",
                (Json({"report_file": filepath}), investigation_id)
            )
        pg_conn.commit()

    except Exception as e:
        print(f"  [ReportCompiler] Markdown export failed (non-fatal): {e}")

    print(f"[ReportCompiler] ✅ Final report complete for investigation #{investigation_id}: '{target}'")
