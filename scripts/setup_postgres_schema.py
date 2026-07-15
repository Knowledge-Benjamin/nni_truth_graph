import os
import psycopg2
from dotenv import load_dotenv

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

def setup_postgres_schema():
    print("Connecting to PostgreSQL to setup staging schemas...")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # 1. sources (Dynamic Trust Tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                domain TEXT NOT NULL,
                category TEXT,
                tier VARCHAR(50) DEFAULT 'tier3',
                epistemic_trust_score FLOAT DEFAULT 0.40,
                last_ingested_at TIMESTAMP WITH TIME ZONE,
                feed_etag TEXT,
                feed_modified TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created sources table")

        # 2. raw_urls (Stage 1 Queue)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_urls (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
                url TEXT UNIQUE NOT NULL,
                metadata JSONB,
                status TEXT DEFAULT 'PENDING_SCRAPE', -- PENDING_SCRAPE, SCRAPED, FAILED
                ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created raw_urls table")

        # 2. raw_articles (Stage 2 Queue)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_articles (
                id SERIAL PRIMARY KEY,
                url_id INTEGER REFERENCES raw_urls(id) ON DELETE CASCADE,
                title TEXT,
                author TEXT,
                publish_date TIMESTAMP WITH TIME ZONE,
                raw_text TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING_EXTRACTION', -- PENDING_EXTRACTION, EXTRACTED, FAILED
                scraped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created raw_articles table")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS article_extraction_progress (
                article_id INTEGER PRIMARY KEY REFERENCES raw_articles(id) ON DELETE CASCADE,
                total_chunks INTEGER DEFAULT 0,
                last_completed_chunk INTEGER DEFAULT -1,
                last_error TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created article_extraction_progress table")

        # 2.5 article_categories (Stage 3 Semantic Classification via pgvector)
        # We must enable the vector extension first on the Neon / Postgres instance
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS article_categories (
                id SERIAL PRIMARY KEY,
                article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
                embedding VECTOR(768), -- Hugging Face Embeddings are 768 dimensions
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Create an HNSW index for blazing fast similarity searches (cosine similarity)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS article_categories_embedding_idx 
            ON article_categories USING hnsw (embedding vector_cosine_ops);
        """)
        print("- Created article_categories (vector) table")

        # 3. extracted_claims (Stages 4-9 Buffer)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extracted_claims (
                id SERIAL PRIMARY KEY,
                article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
                
                -- Atomic structure
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_entity TEXT NOT NULL,
                
                -- Provenance & Confidence
                quote_context TEXT,
                extraction_confidence FLOAT,
                epistemic_score FLOAT DEFAULT NULL,
                temporal_anchor VARCHAR(255),
                spatial_anchor VARCHAR(255),
                spo_fingerprint VARCHAR(255),
                is_verifiable BOOLEAN,
                model_version VARCHAR(255),
                prompt_version VARCHAR(255),
                ai_metadata JSONB,
                investigation_id INTEGER REFERENCES investigations(id) ON DELETE CASCADE,
                
                -- Processing state
                pipeline_stage VARCHAR(50) DEFAULT 'STAGE_4_RESOLUTION', 
                -- e.g., STAGE_4_RESOLUTION, STAGE_5_DEDUP, STAGE_6_CROSS_REF, STAGE_7_SCORING, STAGE_8_REVIEW, STAGE_9_MUTATION
                
                status VARCHAR(50) DEFAULT 'PROCESSING',
                lifecycle VARCHAR(50) DEFAULT 'ACTIVE',
                -- PROCESSING, HUMAN_REVIEW_NEEDED, HUMAN_REVIEW_APPROVED, AUTO_APPROVED, REJECTED, MUTATED
                
                valid_from TIMESTAMP WITH TIME ZONE,
                valid_until TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created extracted_claims table")
        
        # Human Review Queue View (Optional, just ensuring indexes exist)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_claims_status ON extracted_claims(status);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_claims_spo ON extracted_claims(spo_fingerprint);
        """)

        # 4. claim_provenance (Neo4j cross-reference tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claim_provenance (
                id SERIAL PRIMARY KEY,
                claim_id INTEGER,
                neo4j_stance VARCHAR(50),
                neo4j_matched_claim_id VARCHAR(255),
                neo4j_similarity FLOAT,
                internet_original_source TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created claim_provenance table")

        # 5. auth_users (User authentication)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role VARCHAR(50) DEFAULT 'reviewer',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created auth_users table")

        # 6. auth_invites (User registration tokens)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_invites (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_by_user_id INTEGER REFERENCES auth_users(id),
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- Created auth_invites table")

        # 7. graph_outbox (Transactional Outbox Pattern for Neo4j mutations)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS graph_outbox (
                id SERIAL PRIMARY KEY,
                claim_id VARCHAR(255) NOT NULL,
                decision VARCHAR(50) NOT NULL,
                note TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE
            );
        """)
        print("- Created graph_outbox table")

        print("PostgreSQL schema setup complete!")
        
    except psycopg2.Error as e:
        print(f"PostgreSQL connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    setup_postgres_schema()
