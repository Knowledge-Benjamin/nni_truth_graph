import os
import time
import json
import gzip
import random
import feedparser
import requests
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
from urllib.parse import urlparse
from io import BytesIO

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from ai_engine.core.logger import get_printer
print = get_printer(1)  # Bright Cyan

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

# ─── Configuration ────────────────────────────────────────────────────────────
def get_domain_from_url(url):
    return urlparse(url).netloc


def seed_sources_if_empty(cursor):
    """
    Checks if the dynamic `sources` table is empty.
    If so, seeds it with both the local trusted_sources.json payload
    AND the extended open-knowledge sources (Wikipedia, arXiv, PLOS, etc.).
    """
    cursor.execute("SELECT COUNT(*) FROM sources;")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("Dynamic 'sources' table is empty. Seeding local + extended sources...")

        # ── Local sources (trusted_sources.json) ─────────────────────────────
        sources_path = os.path.join(os.path.dirname(__file__), '../../data/trusted_sources.json')
        with open(sources_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        static_sources = data.get("trusted_sources", [])
        
        # Add the fast-update Google News stream for major breaking events (Base Trust: 0.4)
        static_sources.append({
            "name": "Google News: Fast Update",
            "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
            "category": "Breaking",
            "trust_score": 4  # Will be normalized
        })
        
        for source in static_sources:
            domain = get_domain_from_url(source["url"])
            raw_score = source.get("trust_score", 4)
            trust_score = float(raw_score) / 10.0 if raw_score > 1 else raw_score
            cursor.execute("""
                INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING;
            """, (source.get("name", domain), source["url"], domain, source.get("category", "General"), trust_score))
        
        print(f"  Seeded {len(static_sources)} local sources.")

        # ── Extended open-knowledge sources ───────────────────────────────────
        try:
            import sys as _sys
            _scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
            if _scripts_dir not in _sys.path:
                _sys.path.insert(0, _scripts_dir)
            from add_extended_sources import add_extended_sources
            add_extended_sources()
        except Exception as _ext_err:
            print(f"  Warning: Could not seed extended sources: {_ext_err}")

    else:
        print(f"Dynamic 'sources' table already initialized ({count} active sources).")

def ingest_urls():
    """
    Stage 1: Ingests raw URLs from configured dynamic sources and pushes them to PostgreSQL.
    Now includes Dynamic Topic Hunting via SearXNG.
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 1: URL Ingestion")
    
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        seed_sources_if_empty(cursor)
        
        new_urls_count = 0

        # === 1. Dynamic Topic Hunting via SearXNG ===
        # We query the newest, most central entities in our graph and search for them on the live internet.
        searxng_url = os.getenv("SEARXNG_URL") # e.g. "https://knowledgebenji-searchserver.hf.space"
        searxng_secret = os.getenv("SEARXNG_SECRET_KEY", "")
        
        if searxng_url:
            print("[+] SearXNG URL detected. Executing Dynamic Topic Hunting...")
            try:
                import requests
                # Get a high-value entity to search for. For simplicity, we just pick a popular one or a recent claim.
                cursor.execute("""
                    SELECT subject FROM extracted_claims 
                    WHERE status = 'GRAPH_COMMITTED' 
                    ORDER BY id DESC LIMIT 5;
                """)
                recent_subjects = [row[0] for row in cursor.fetchall()]
                
                # Default fallback topic if graph is empty
                search_query = "latest news" 
                if recent_subjects:
                     import random
                     search_query = f'"{random.choice(recent_subjects)}" news'
                     
                print(f"  -> dynamically hunting for: {search_query}")
                
                params = {
                    "q": search_query,
                    "format": "json",
                    "engines": "google,bing,duckduckgo",
                    "time_range": "day" # Only get recent stuff for ingestion
                }
                headers = {}
                # If you implemented header-based auth in SearXNG, you'd pass it here, usually it's open if bot limiter is off
                
                resp = requests.get(f"{searxng_url.rstrip('/')}/search", params=params, headers=headers, timeout=15)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    print(f"  -> SearXNG returned {len(results)} dynamic results.")
                    
                    # Ensure we have a generic "Dynamic SearXNG" source to tie these to
                    cursor.execute("""
                        INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                        VALUES ('SearXNG Dynamic Ingest', %s, 'searxng.local', 'Dynamic', 0.5)
                        ON CONFLICT (url) DO NOTHING
                        RETURNING id;
                    """, (searxng_url,))
                    row = cursor.fetchone()
                    if row:
                        dynamic_source_id = row[0]
                    else:
                        cursor.execute("SELECT id FROM sources WHERE url = %s", (searxng_url,))
                        sel_row = cursor.fetchone()
                        dynamic_source_id = sel_row[0] if sel_row else None

                    if not dynamic_source_id:
                        print("  -> Failed to resolve dynamic source ID. Skipping SearXNG insert.")
                    else:
                        for r in results[:10]: # Take top 10 to avoid flooding
                            link = r.get("url")
                            if not link: continue
                            
                            metadata = {
                                "title": r.get('title', ''),
                                "author": r.get('engine', 'searxng'),
                                "published": r.get('publishedDate', ''),
                                "summary": r.get('content', '')[:500],
                                "origin": "dynamic_searxng_hunt"
                            }
                            try:
                                cursor.execute("""
                                    INSERT INTO raw_urls (source_id, url, metadata, status)
                                    VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                                    ON CONFLICT (url) DO NOTHING
                                    RETURNING id;
                                """, (dynamic_source_id, link, Json(metadata)))
                                if cursor.fetchone():
                                    new_urls_count += 1
                                    print(f"    -> [DYNAMIC QUEUED]: {link}")
                            except Exception as e:
                                pass
                else:
                     print(f"  -> SearXNG request failed: HTTP {resp.status_code}")
            except Exception as e:
                print(f"  -> [Dynamic Hunt Error]: {e}")
        else:
            print("[-] SEARXNG_URL not configured. Skipping dynamic topic hunting.")

        # === 2. Static RSS Ingestion ===
        # Load evolving sources directly from the database, including fetch state
        cursor.execute("SELECT id, name, url, domain, epistemic_trust_score, feed_etag, feed_modified FROM sources WHERE category NOT IN ('Dynamic', 'Discovered', 'Revalidation') ORDER BY epistemic_trust_score DESC;")
        active_sources = cursor.fetchall()
        
        print(f"Loaded {len(active_sources)} evolving sources for RSS ingestion.")
        
        for source_id, name, url, domain, trust_score, etag, modified in active_sources:
            print(f"Fetching from {name} (Trust: {trust_score})...")
            
            feed = None
            try:
                feed = feedparser.parse(url, etag=etag, modified=modified)
                
                if getattr(feed, 'status', None) == 304:
                    print(f"  -> [304 NOT MODIFIED] Skipping {name} - no updates since last run.")
                    
                    # Update timestamp even if skipped so we know we checked it
                    cursor.execute("UPDATE sources SET last_ingested_at = CURRENT_TIMESTAMP WHERE id = %s;", (source_id,))
                    continue
                
                if getattr(feed, 'bozo_exception', None):
                    print(f"  Warning: Feed parsing issue for {url}: {feed.bozo_exception}")
                if not feed.entries:
                    print(f"  Warning: No entries found for {url}")
                    continue
                    
                for entry in feed.entries[:10]: # Grab latest 10 to avoid overwhelming for now
                    link = entry.get('link')
                    if not link:
                        continue
                        
                    # Extract Metadata
                    metadata = {
                        "title": entry.get('title', ''),
                        "author": entry.get('author', ''),
                        "published": entry.get('published', ''),
                        "summary": entry.get('summary', '')[:500] # truncate summary
                    }
                        
                    # Insert if it doesn't already exist in the queue
                    try:
                        cursor.execute("""
                            INSERT INTO raw_urls (source_id, url, metadata, status)
                            VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                            ON CONFLICT (url) DO NOTHING
                            RETURNING id;
                        """, (source_id, link, Json(metadata)))
                        
                        result = cursor.fetchone()
                        if result:
                            new_urls_count += 1
                            print(f"    -> [SUCCESS] Queued: {link}")
                        else:
                            print(f"    -> [SKIPPED] Already in queue: {link}")
                    except Exception as e:
                        print(f"    -> [ERROR] Failed inserting {link}: {e}")
            except Exception as e:
                print(f"    -> [ERROR] Failed to fetch feed from {url}: {e}")
            
            # Update the last_ingested_at timestamp and the state properties for the dynamic source
            cursor.execute("UPDATE sources SET last_ingested_at = CURRENT_TIMESTAMP, feed_etag = %s, feed_modified = %s WHERE id = %s;", 
                           (getattr(feed, 'etag', None), getattr(feed, 'modified', None), source_id))
                    
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Extraction Complete. Pushed {new_urls_count} new URLs (with metadata) to the staging buffer.")

    except psycopg2.Error as e:
        print(f"Database error during Ingestion: {e}")
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()
if __name__ == "__main__":
    LOOP_INTERVAL_SECONDS = 300  # Re-run RSS ingest every 5 minutes
    print(f"[INGEST] Starting continuous ingest loop (RSS every {LOOP_INTERVAL_SECONDS}s)")
    while True:
        ingest_urls()
        print(f"[INGEST] Sleeping {LOOP_INTERVAL_SECONDS}s until next RSS cycle...")
        time.sleep(LOOP_INTERVAL_SECONDS)
