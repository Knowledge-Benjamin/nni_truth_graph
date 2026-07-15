"""
One-time migration: move claims stuck at STAGE_6_DEDUP/HUMAN_REVIEW into
the new STAGE_HELD_FOR_REVIEW terminal holding stage.

These were produced before the S5 routing fix was applied — they scored
below the auto-approve threshold and were sent to STAGE_6_DEDUP with
HUMAN_REVIEW status, but S6 only processes PROCESSING items so they
were permanently invisible.
"""
import psycopg2, os

DATABASE_URL = os.environ['DATABASE_URL']

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False
cur = conn.cursor()

cur.execute("""
    SELECT COUNT(*) FROM extracted_claims
    WHERE pipeline_stage = 'STAGE_6_DEDUP'
      AND status IN ('HUMAN_REVIEW', 'AUTO_REJECT');
""")
count = cur.fetchone()[0]
print(f"Found {count} stuck claims to migrate -> STAGE_HELD_FOR_REVIEW")

if count == 0:
    print("Nothing to migrate.")
    conn.close()
    exit(0)

cur.execute("""
    UPDATE extracted_claims
    SET pipeline_stage = 'STAGE_HELD_FOR_REVIEW'
    WHERE pipeline_stage = 'STAGE_6_DEDUP'
      AND status IN ('HUMAN_REVIEW', 'AUTO_REJECT');
""")
print(f"Migrated {cur.rowcount} claims.")

conn.commit()
conn.close()
print("Done.")
