import os
import sys
import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

def seed_neo4j_queue():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    
    with conn.cursor() as cur:
        print("Ensuring new article_incorporated schema column exists...")
        cur.execute("ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS article_incorporated BOOLEAN DEFAULT FALSE;")
        
        print("Pushing all extracted claims back to STAGE_8_MUTATION_QUEUE as AUTO_APPROVE...")
        cur.execute("""
            UPDATE extracted_claims 
            SET pipeline_stage = 'STAGE_8_MUTATION_QUEUE',
                status = 'AUTO_APPROVE',
                article_incorporated = FALSE
            WHERE subject IS NOT NULL
        """)
        updated = cur.rowcount
        print(f"Successfully queued {updated} claims for Neo4j mutation!")

        
    conn.close()

if __name__ == "__main__":
    seed_neo4j_queue()
