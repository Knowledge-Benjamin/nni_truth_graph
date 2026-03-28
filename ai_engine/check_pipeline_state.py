"""
Diagnostic script: prints a breakdown of where claims are stuck in the pipeline.
Run at any time: python check_pipeline_state.py
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("\n" + "=" * 70)
    print("  PIPELINE STATE DIAGNOSTIC")
    print("=" * 70)

    # ── raw_urls ──────────────────────────────────────────────────────────
    print("\n[raw_urls] URL queue status:")
    cur.execute("SELECT status, COUNT(*) FROM raw_urls GROUP BY status ORDER BY count DESC;")
    for row in cur.fetchall():
        print(f"  {row[0]:35s} {row[1]:>6}")

    # ── raw_articles ──────────────────────────────────────────────────────
    print("\n[raw_articles] Article queue status:")
    cur.execute("SELECT status, COUNT(*) FROM raw_articles GROUP BY status ORDER BY count DESC;")
    for row in cur.fetchall():
        print(f"  {row[0]:35s} {row[1]:>6}")

    # ── extracted_claims ─────────────────────────────────────────────────
    print("\n[extracted_claims] Claims by (pipeline_stage, status):")
    cur.execute("""
        SELECT COALESCE(pipeline_stage, '** NULL **'), status, COUNT(*)
        FROM extracted_claims
        GROUP BY pipeline_stage, status
        ORDER BY pipeline_stage NULLS FIRST, status;
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (no rows in extracted_claims yet)")
    for row in rows:
        print(f"  {row[0]:35s} | {row[1]:20s} | {row[2]:>6}")

    # ── NULL pipeline_stage alert ─────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM extracted_claims WHERE pipeline_stage IS NULL;")
    null_count = cur.fetchone()[0]
    if null_count > 0:
        print(f"\n[!] WARNING: {null_count} claims have pipeline_stage=NULL.")
        print("    These are old stuck rows from before the fix.")
        print("    Run the repair query below to rescue them:")
        print("""
    UPDATE extracted_claims
    SET pipeline_stage = 'STAGE_4_RESOLUTION'
    WHERE pipeline_stage IS NULL
      AND status = 'PROCESSING';
    """)

    # ── GRAPH_COMMITTED summary ───────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM extracted_claims WHERE status = 'GRAPH_COMMITTED';")
    committed = cur.fetchone()[0]
    print(f"\n[Neo4j] Claims written to graph (GRAPH_COMMITTED): {committed}")

    print("\n" + "=" * 70 + "\n")
    cur.close()
    conn.close()

if __name__ == "__main__":
    run()
