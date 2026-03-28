# pyre-ignore-all-errors
import os
import sys
import psycopg2  # type: ignore
from neo4j import GraphDatabase  # type: ignore
from dotenv import load_dotenv  # type: ignore

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

def reset_articles():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    
    with conn.cursor() as cur:
        # We also need to reset the failure flags for claims just in case they were marked failed.
        print("Resetting article incorporation flags in PostgreSQL...")
        cur.execute("UPDATE extracted_claims SET article_incorporated = FALSE")
        print(f"PostgreSQL reset complete. ({cur.rowcount} claims marked for re-incorporation)")
    conn.close()

    print("\nConnecting to Neo4j...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        print("Purging previous generated articles from all Entities...")
        res = session.run("""
            MATCH (e:Entity)
            WHERE e.article IS NOT NULL OR e.article_stale = false OR e.article_failure_count > 0
            SET e.article = null,
                e.article_references = null,
                e.article_generated_at = null,
                e.article_last_success = null,
                e.article_claim_count = 0,
                e.article_last_attempt = null,
                e.article_failure_count = 0,
                e.article_stale = true
            RETURN count(e) AS changed
        """)
        record = res.single()
        print(f"Neo4j reset complete. ({record['changed']} entities scrubbed and marked stale)")
    
    driver.close()
    print("\nReset successfully finished! The graph is cleanly staged for a fresh Article Worker run.")

if __name__ == "__main__":
    reset_articles()
