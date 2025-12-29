"""
Backfill Script: Process existing NNI.news articles into Truth Graph.
Connects to Neon PostgreSQL, fetches published articles, sends to AI Engine.
"""

import os
import psycopg2
import requests
import time
import re
from datetime import datetime
from dotenv import load_dotenv

# Load env variables (Local Dev support)
# We look for server/.env because that's where DATABASE_URL is stored locally
load_dotenv(os.path.join(os.path.dirname(__file__), '../server/.env'))

# Database connection
NEON_DB = os.getenv("DATABASE_URL")

# Truth Graph API 
# Allow dynamic port if set, otherwise default to 10000 (common Render default) or 3000
PORT = os.getenv("PORT", "3000")
TRUTH_GRAPH_API = f"http://localhost:{PORT}/api/ingest"

def strip_html(html_text):
    """Remove HTML tags from content"""
    if not html_text:
        return ""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', html_text)

def backfill_nni_articles():
    print("=" * 60)
    print("NNI.news → Truth Graph Backfill Script")
    print("=" * 60)
    
    if not NEON_DB:
        print("[ERROR] DATABASE_URL is missing! Cannot connect to NNI database.")
        print("Ensure 'server/.env' exists and contains DATABASE_URL.")
        return

    try:
        # Connect to Neon DB
        print("\n[..] Connecting to Neon PostgreSQL...")
        conn = psycopg2.connect(NEON_DB)
        cur = conn.cursor()
        print("[OK] Connected!")
        
        # DEBUG: Check total count without filters
        cur.execute('SELECT count(*) FROM articles;')
        raw_count = cur.fetchone()[0]
        print(f"\n[DEBUG] Total rows in 'articles' table (no filters): {raw_count}")

        # DEBUG: Check statuses
        if raw_count > 0:
            cur.execute('SELECT DISTINCT status FROM articles;')
            statuses = cur.fetchall()
            print(f"[DEBUG] Found article statuses: {[s[0] for s in statuses]}")

        # Fetch published articles using ROBUST verify logic
        print("\n[..] Fetching published articles...")
        # FIX: cast status to text and use TRIM + UPPER to be absolutely sure
        cur.execute("""
            SELECT id, title, slug, content, "publishedAt" 
            FROM articles 
            WHERE TRIM(UPPER(status::text)) = 'PUBLISHED' AND content IS NOT NULL
            ORDER BY "publishedAt" ASC
        """)
        
        articles = cur.fetchall()
        print(f"[OK] Found {len(articles)} articles to process")

        if not articles:
            print("[WARN]  No articles to process. Exiting.")
            conn.close()
            return

        # Process each article
        success_count = 0
        
        for art in articles:
            art_id, title, slug, content, published_at = art
            
            print(f"   > Processing: {title[:50]}...")
            
            # Prepare payload for Truth Graph API
            clean_content = strip_html(content)
            
            payload = {
                "text": f"{title}\n\n{clean_content}",
                "source": "nni.news",
                "sourceUrl": f"https://nni.news/articles/{slug}",
                "metadata": {
                    "original_id": str(art_id),
                    "published_at": str(published_at),
                    "author": "NNI Staff" # Default
                }
            }
            
            # Send to Ingestion API
            try:
                # We use the internal ingestion API which handles the AI Engine pipeline
                response = requests.post(TRUTH_GRAPH_API, json=payload)
                if response.status_code == 200:
                    print(f"     ✅ Ingested (Claims extracted)")
                    success_count += 1
                else:
                    print(f"     ❌ Failed: {response.text}")
            except Exception as e:
                print(f"     ⚠️ API Error: {e} (Is the server running on port {PORT}?)")
                
            # Rate limit politeness
            time.sleep(1)

        print("-" * 60)
        print(f"Backfill Complete. Successfully processed {success_count}/{len(articles)} articles.")
        
        conn.close()

    except Exception as e:
        print(f"\n[ERROR] Backfill failed: {e}")

if __name__ == "__main__":
    backfill_nni_articles()
