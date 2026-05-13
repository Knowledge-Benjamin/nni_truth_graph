"""
Live migration: add Neo4j indexes required by the asymmetric debunking model.

Run once against a live database BEFORE running Stage 9 with the new
handle_contradicts logic.  Uses IF NOT EXISTS so it is idempotent.

New indexes:
  - claim_verdict_idx           : Claim(verdict)
  - controversy_subject_predicate_idx : Controversy(subject, predicate)
  - controversy_open_idx        : Controversy(open)
  - controversy_resolved_idx    : Controversy(resolved)
"""
import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase  # type: ignore

load_dotenv(os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
    print("ERROR: NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set in .env")
    sys.exit(1)

# Narrow types: the guard above guarantees these are str, not None
assert NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

INDEXES = [
    (
        "claim_verdict_idx",
        "CREATE INDEX claim_verdict_idx IF NOT EXISTS FOR (c:Claim) ON (c.verdict);"
    ),
    (
        "controversy_subject_predicate_idx",
        """CREATE INDEX controversy_subject_predicate_idx IF NOT EXISTS
           FOR (cv:Controversy) ON (cv.subject, cv.predicate);"""
    ),
    (
        "controversy_open_idx",
        "CREATE INDEX controversy_open_idx IF NOT EXISTS FOR (cv:Controversy) ON (cv.open);"
    ),
    (
        "controversy_resolved_idx",
        "CREATE INDEX controversy_resolved_idx IF NOT EXISTS FOR (cv:Controversy) ON (cv.resolved);"
    ),
]

print("[migrate_debunking_indexes] Connecting to Neo4j...")

try:
    with driver.session() as session:
        for name, cypher in INDEXES:
            try:
                session.run(cypher)  # type: ignore[arg-type]
                print(f"  ✓ {name}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print(f"  ~ {name} already exists, skipping.")
                else:
                    print(f"  ✗ {name}: {e}")

    print("\n✅ Debunking index migration complete.")
except Exception as e:
    print(f"❌ Migration failed: {e}")
    sys.exit(1)
finally:
    driver.close()
