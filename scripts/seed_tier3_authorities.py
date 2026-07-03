import os
import sys
import time
import requests
import psycopg2
from dotenv import load_dotenv

# Force UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, 'ai_engine/.env'))
from ai_engine.core.logger import get_printer
print = get_printer(3)  # Yellow/Gold for authority APIs

DATABASE_URL = os.getenv("DATABASE_URL")

# ── API Endpoints ────────────────────────────────────────────────────────────
OPENALEX_API = "https://api.openalex.org/works"
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def _ensure_source_exists(cur, name: str, url: str, domain: str, category: str, score: float) -> int:
    """Ensure an API source is registered and return its ID."""
    cur.execute("""
        INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (url) DO NOTHING
        RETURNING id;
    """, (name, url, domain, category, score))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id FROM sources WHERE url = %s", (url,))
        row = cur.fetchone()
    return row[0]

# ── 1. OPENALEX (Scientific Papers) ──────────────────────────────────────────
def fetch_openalex_abstracts(limit: int = 50):
    print(f"\n[Tier 3 · Scientific] Fetching {limit} latest scientific abstracts from OpenAlex...")
    
    # Filter for English works that have abstracts, sort by newest
    params = {
        "filter": "has_abstract:true,language:en",
        "sort": "publication_date:desc",
        "per-page": limit
    }
    
    headers = {"User-Agent": "LivingTruthGraph/1.0 (mailto:contact@example.com)"}
    
    try:
        resp = requests.get(OPENALEX_API, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"  [ERROR] OpenAlex API failed: {e}")
        return

    inserted_count = 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            source_id = _ensure_source_exists(
                cur, 
                "OpenAlex (Scientific Index)", 
                "https://openalex.org/", 
                "openalex.org", 
                "API", 
                0.98
            )
            
            for work in results:
                title = work.get("title")
                doi_url = work.get("doi") or work.get("id")
                abstract_inverted = work.get("abstract_inverted_index", {})
                
                if not title or not doi_url or not abstract_inverted:
                    continue
                    
                # Reconstruct abstract from inverted index
                # inverted index format: {"word": [pos1, pos2]}
                words_by_pos = {}
                for word, positions in abstract_inverted.items():
                    for p in positions:
                        words_by_pos[p] = word
                        
                max_pos = max(words_by_pos.keys()) if words_by_pos else -1
                abstract_words = [words_by_pos.get(i, "") for i in range(max_pos + 1)]
                abstract_text = " ".join(abstract_words).strip()
                
                if len(abstract_text) < 100:
                    continue
                    
                # Add DOI to raw_urls so the db constraint holds
                cur.execute("""
                    INSERT INTO raw_urls (source_id, url, status)
                    VALUES (%s, %s, 'SCRAPED')
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id;
                """, (source_id, doi_url))
                row = cur.fetchone()
                
                # If it didn't return an ID, we already ingested this abstract
                if not row:
                    continue
                    
                url_id = row[0]
                
                # Directly bypass scraping, inject as PENDING_CLASSIFICATION
                cur.execute("""
                    INSERT INTO raw_articles (url_id, title, author, publish_date, raw_text, status)
                    VALUES (%s, %s, 'OpenAlex Abstract', CURRENT_TIMESTAMP, %s, 'PENDING_CLASSIFICATION')
                """, (url_id, title, abstract_text))
                
                inserted_count += 1
                
            conn.commit()
            print(f"  [SUCCESS] Injected {inserted_count} new scientific abstracts for immediate classification.")
            
    except Exception as e:
        print(f"  [DB ERROR] Failed injecting OpenAlex data: {e}")
        conn.rollback()
    finally:
        conn.close()

# ── 2. GDELT (Geopolitical Events) ──────────────────────────────────────────
def fetch_gdelt_events(limit: int = 50):
    print(f"\n[Tier 3 · Geopolitical] Fetching {limit} latest global events via GDELT Doc API...")
    
    # Query for global policy, conflict, and AI events
    params = {
        "query": "(geopolitics OR conflict OR policy OR artificial intelligence OR economy)",
        "mode": "artlist",
        "format": "json",
        "maxrecords": limit,
        "timespan": "24h",
        "sort": "DateDesc"
    }
    
    headers = {"User-Agent": "LivingTruthGraph/1.0 (mailto:contact@example.com)"}
    
    try:
        resp = requests.get(GDELT_DOC_API, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
    except Exception as e:
        print(f"  [ERROR] GDELT API failed (it frequently flakes or rate-limits, this is normal): {e}")
        return

    inserted_count = 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            source_id = _ensure_source_exists(
                cur, 
                "GDELT Global News Monitor", 
                "https://gdeltproject.org/", 
                "gdeltproject.org", 
                "API", 
                0.80
            )
            
            for article in articles:
                url = article.get("url")
                if not url:
                    continue
                    
                # Put the URL into the queue for the Scraper Stage
                cur.execute("""
                    INSERT INTO raw_urls (source_id, url)
                    VALUES (%s, %s)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id;
                """, (source_id, url))
                
                if cur.fetchone():
                    inserted_count += 1
                    
            conn.commit()
            print(f"  [SUCCESS] Queued {inserted_count} new global event URLs for Stage 2 Scraper.")
            
    except Exception as e:
        print(f"  [DB ERROR] Failed injecting GDELT data: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    print("=== Launching Tier 3 Ingestion Engine (Authority Archives) ===")
    
    # Fetch 50 new scientific abstracts
    fetch_openalex_abstracts(limit=50)
    
    # Be polite to APIs
    time.sleep(2)
    
    # Fetch 50 global geopolitical URLs
    fetch_gdelt_events(limit=50)
    
    print("\n=== Tier 3 Ingestion Complete ===")
    print("Academic abstracts are ready for classification.")
    print("GDELT URLs are queued for scraping.")
