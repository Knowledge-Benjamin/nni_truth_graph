import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Enable pgvector
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    print("pgvector extension enabled.")
    
    # Create article_categories
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_categories (
            id SERIAL PRIMARY KEY,
            article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
            embedding VECTOR(768),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)
    print("article_categories table created.")
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS article_categories_embedding_idx 
        ON article_categories USING hnsw (embedding vector_cosine_ops);
    """)
    print("HNSW index created for cosine similarity search.")
    
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'conn' in locals() and conn:
        cursor.close()
        conn.close()
