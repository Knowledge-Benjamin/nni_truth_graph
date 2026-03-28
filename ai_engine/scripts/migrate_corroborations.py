import psycopg2
import os
from dotenv import load_dotenv

def run_migration():
    """
    Creates the claim_corroborations table to support the Fossil Record implementation.
    This safely applies schema changes to PostgreSQL.
    """
    print("=== Temporal Provenance Matrix Migration ===")
    
    # Load .env
    env_path = os.path.join(os.path.dirname(__file__), '../.env')
    load_dotenv(env_path)
    db_url = os.getenv('DATABASE_URL')
    
    if not db_url:
        print("DATABASE_URL not found in .env")
        return
        
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        
        cur.execute("""
        CREATE TABLE IF NOT EXISTS claim_corroborations (
            id SERIAL PRIMARY KEY,
            claim_id INTEGER NOT NULL REFERENCES extracted_claims(id) ON DELETE CASCADE,
            raw_article_id INTEGER REFERENCES raw_articles(id) ON DELETE SET NULL,
            quote_context TEXT,
            source_tier INTEGER DEFAULT 3,
            source_trust FLOAT DEFAULT 0.40,
            discovered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # Index for fast lookups during Graph Mutation / Epistemic Scoring
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_claim_corroborations_claim_id 
        ON claim_corroborations(claim_id);
        """)
        
        print("Successfully created `claim_corroborations` table and index.")
        
        cur.close()
        conn.close()
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    run_migration()
