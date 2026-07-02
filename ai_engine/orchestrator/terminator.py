"""
ai_engine/orchestrator/terminator.py
─────────────────────────────────────────────────────────────────────────────
Terminator: Checks all four termination conditions for an active investigation
and marks it COMPLETED if any condition is met.

Conditions:
  1. Goal Achieved  — LLM flagged goal_achieved=True in last harvest
  2. Exhaustion     — No PENDING or CLAIMED leads remain
  3. Diminishing Returns — novel_discoveries in last N harvests < threshold
  4. Hard Limits    — leads_explored >= max_leads OR age > max_days
"""

import os
import sys
from datetime import datetime, timezone
from psycopg2.extras import Json, RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from ai_engine.core.license_manager import decrement_investigation_credit


DIMINISHING_THRESHOLD = 3   # if fewer than this many novel leads appear after a harvest, count it
DIMINISHING_WINDOW    = 3   # how many consecutive 'dry' harvests before terminating


def check_termination(investigation_id: int, pg_conn) -> tuple[bool, str]:
    """
    Returns (should_terminate: bool, reason: str).
    If should_terminate is True, the caller should mark the investigation COMPLETED.
    """
    with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT status, goal_type, findings, max_leads, max_days,
                   leads_explored, novel_discoveries, created_at
            FROM investigations
            WHERE id = %s
            """,
            (investigation_id,)
        )
        inv = cur.fetchone()

    if not inv or inv["status"] != "ACTIVE":
        return False, "Not active"

    findings = inv["findings"] or {}

    # ── Condition 1: Goal Achieved ───────────────────────────────────────────
    if findings.get("goal_achieved") is True:
        return True, "GOAL_ACHIEVED"

    # ── Condition 2: Exhaustion ──────────────────────────────────────────────
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM investigation_leads
            WHERE investigation_id = %s AND status IN ('PENDING', 'CLAIMED')
            """,
            (investigation_id,)
        )
        pending_count = cur.fetchone()[0]

    if pending_count == 0 and inv["leads_explored"] > 0:
        return True, "EXHAUSTION"

    # ── Condition 3: Diminishing Returns ────────────────────────────────────
    # We track this in findings as a list of recent harvest novel counts
    dry_harvests = findings.get("dry_harvests", 0)
    last_novel   = findings.get("last_harvest_novel_count", 999)
    if last_novel < DIMINISHING_THRESHOLD:
        dry_harvests += 1
    else:
        dry_harvests = 0

    if dry_harvests >= DIMINISHING_WINDOW:
        return True, "DIMINISHING_RETURNS"

    # ── Condition 4: Hard Limits ─────────────────────────────────────────────
    if inv["leads_explored"] >= (inv["max_leads"] or 500):
        return True, "HARD_LIMIT_LEADS"

    created_at = inv["created_at"]
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created_at).days
        if age_days >= (inv["max_days"] or 14):
            return True, "HARD_LIMIT_DAYS"

    # Update the dry_harvests counter in findings
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE investigations SET findings = findings || %s::jsonb WHERE id = %s",
            (Json({"dry_harvests": dry_harvests}), investigation_id)
        )
    pg_conn.commit()

    return False, "CONTINUE"


from typing import Any

def complete_investigation(investigation_id: int, reason: str, pg_conn, neo4j_driver=None) -> None:
    """
    Marks the investigation as COMPLETED, writes the final summary to findings,
    and optionally queries Neo4j for a final subgraph snapshot.
    """
    final_summary: dict[str, Any] = {"termination_reason": reason, "completed_at": datetime.now(timezone.utc).isoformat()}

    # For EXHAUSTIVE_COLLECTION: pull the final subgraph predicate mapping from Neo4j
    if neo4j_driver:
        try:
            with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT findings->>'exhaust_predicate' AS pred, findings->>'canonical_target' AS tgt FROM investigations WHERE id = %s",
                    (investigation_id,)
                )
                row = cur.fetchone()
            if row and row["pred"] and row["tgt"]:
                pred = row["pred"]
                tgt  = row["tgt"]
                with neo4j_driver.session() as session:
                    result = session.run(
                        f"""
                        MATCH (e:Entity)-[r:{pred}]->(target:Entity)
                        WHERE toLower(target.name) CONTAINS toLower()
                        RETURN e.name AS entity, type(r) AS relation, target.name AS target_entity
                        LIMIT 500
                        """,
                        {"tgt": tgt}
                    )
                    records = [{"entity": r["entity"], "relation": r["relation"], "target": r["target_entity"]}
                               for r in result]
                    final_summary["final_graph_snapshot"] = records
                    final_summary["total_mapped"] = len(records)
                    print(f"[Terminator] Final graph snapshot: {len(records)} nodes found for predicate {pred}")
        except Exception as e:
            print(f"[Terminator] Neo4j final snapshot failed (non-fatal): {e}")

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE investigations
            SET status = 'COMPLETED',
                completed_at = NOW(),
                findings = findings || %s::jsonb
            WHERE id = %s
            """,
            (Json(final_summary), investigation_id)
        )
    pg_conn.commit()
    print(f"[Terminator] Investigation #{investigation_id} COMPLETED — reason: {reason}")
    
    # Securely deduct 1 compute credit upon successful completion
    try:
        decrement_investigation_credit(pg_conn, investigation_id)
    except Exception as e:
        print(f"[Terminator] ERROR deducting credit for #{investigation_id}: {e}")
