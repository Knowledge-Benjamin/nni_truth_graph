import psycopg2
import os
from dotenv import load_dotenv

def run_migration():
    """
    Creates the media_provenance table to support the Visual Provenance Matrix.
    This schema integrates phash deduplication and pgvector Hugging Face embeddings.
    """
    print("=== Media Provenance Matrix Migration ===")
    
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
        
        # Ensure pgvector is available
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        cur.execute("""
        CREATE TABLE IF NOT EXISTS media_provenance (
            id SERIAL PRIMARY KEY,
            claim_id INTEGER REFERENCES extracted_claims(id) ON DELETE CASCADE,
            raw_article_id INTEGER REFERENCES raw_articles(id) ON DELETE SET NULL,
            media_url TEXT NOT NULL,
            phash VARCHAR(64),
            clip_embedding vector(768),
            synthetic_probability FLOAT DEFAULT 0.0,
            discovered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # Index for extremely fast Cosine Similarity lookups
        try:
            cur.execute("""
            CREATE INDEX idx_media_provenance_clip 
            ON media_provenance USING hnsw (clip_embedding vector_cosine_ops);
            """)
        except Exception as idx_err:
            if "already exists" not in str(idx_err):
                print(f"HNSW index warning: {idx_err}")
                
        # Index for standard relational joins
        try:
            cur.execute("""
            CREATE INDEX idx_media_provenance_claim 
            ON media_provenance(claim_id);
            """)
        except Exception:
            pass
            
        print("Successfully created `media_provenance` table and indexes.")
        
        cur.close()
        conn.close()
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    run_migration()
