from concurrent.futures import ThreadPoolExecutor
import os
import sys
import time
import json
import gzip
import random
import re
import feedparser
import requests
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
from urllib.parse import urlparse
from io import BytesIO

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_engine.core.searxng_client import request_searxng
from ai_engine.core.logger import get_printer
print = get_printer(1)  # Bright Cyan

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

SEARXNG_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ─── Configuration ────────────────────────────────────────────────────────────
RSS_API_EXCLUDE_HOSTS = {
    "openalex.org",
    "gdeltproject.org",
    "en.wikipedia.org",
}

RSS_API_EXCLUDE_URLS = {
    "https://openalex.org/",
    "https://gdeltproject.org/",
    "https://en.wikipedia.org",
}


def get_domain_from_url(url):
    return urlparse(url).netloc


def is_rss_candidate_source(url):
    parsed = urlparse(url)
    url_lower = url.lower()
    netloc = parsed.netloc.lower()

    if url_lower.rstrip('/') in RSS_API_EXCLUDE_URLS:
        return False
    if netloc in RSS_API_EXCLUDE_HOSTS and parsed.path in ('', '/'):
        return False

    if any(token in url_lower for token in ('rss', '/feed', 'format=rss', 'feedformat=rss', 'action=featuredfeed')):
        return True
    if parsed.path.endswith(('.xml', '.rss', '.atom', '.rdf')):
        return True

    if parsed.path in ('', '/'):
        return False
    return True


def create_searxng_headers(secret: str = "") -> dict:
    headers = {
        "User-Agent": SEARXNG_USER_AGENT,
        "Accept": "application/json",
    }
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-API-KEY"] = secret
    return headers


def build_searxng_params(query: str) -> dict:
    return {
        "q": query,
        "format": "json",
    }


def extract_searxng_html_links(html_text: str) -> list[str]:
    urls = []
    for match in re.finditer(r'<article[^>]*class=["\'][^"\']*result[^"\']*["\'][^>]*>(.*?)</article>', html_text, re.S | re.I):
        article_html = match.group(1)
        href_match = re.search(r'href=["\'](https?://[^"\']+)["\']', article_html, re.I)
        if href_match:
            urls.append(href_match.group(1))
    if not urls:
        urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', html_text, re.I)
    return urls


def seed_sources_if_empty(cursor):
    """
    Ensures the dynamic `sources` table contains RSS/feed source seeds.
    If trusted RSS source URLs are missing, seed them from the local config.
    """
    cursor.execute("SELECT COUNT(*) FROM sources;")
    total_count = cursor.fetchone()[0]

    sources_path = os.path.join(os.path.dirname(__file__), '../../data/trusted_sources.json')
    with open(sources_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    static_sources = data.get("trusted_sources", [])
    static_sources.append({
        "name": "Google News: Fast Update",
        "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "category": "Breaking",
        "trust_score": 4
    })

    seed_urls = [source["url"] for source in static_sources]
    cursor.execute("SELECT url FROM sources WHERE url = ANY(%s);", (seed_urls,))
    existing_urls = {row[0] for row in cursor.fetchall()}
    missing_sources = [source for source in static_sources if source["url"] not in existing_urls]

    if total_count == 0 or missing_sources:
        print("Dynamic 'sources' table needs seeding. Adding local + extended sources...")

        seeded = 0
        for source in missing_sources:
            domain = get_domain_from_url(source["url"])
            raw_score = source.get("trust_score", 4)
            trust_score = float(raw_score) / 10.0 if raw_score > 1 else raw_score
            cursor.execute("""
                INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING;
            """, (source.get("name", domain), source["url"], domain, source.get("category", "General"), trust_score))
            if cursor.rowcount == 1:
                seeded += 1

        print(f"  Seeded {seeded} new local sources.")

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
        print(f"Dynamic 'sources' table already initialized ({total_count} active sources).")


def process_rss_source(source_tuple):
    import psycopg2
    import feedparser
    from psycopg2.extras import Json
    from ai_engine.core.config import DATABASE_URL
    
    # We can rely on is_rss_candidate_source which is already in scope
    source_id, name, url, domain, trust_score, etag, modified = source_tuple
    print(f"Fetching from {name} (Trust: {trust_score})...")
    
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()
    new_urls_count = 0
    try:
        # In this file, is_rss_candidate_source is a global function
        # But since we are calling it, we must ensure it's accessible.
        # It's at the top level of 1_ingest.py, so it will be available.
        if not is_rss_candidate_source(url):
            print(f"  -> [SKIP] Not an RSS/feed endpoint: {url}")
            cursor.execute("UPDATE sources SET last_ingested_at = CURRENT_TIMESTAMP WHERE id = %s;", (source_id,))
            return 0

        feed = None
        try:
            feed = feedparser.parse(url, etag=etag, modified=modified)
            
            if getattr(feed, 'status', None) == 304:
                print(f"  -> [304 NOT MODIFIED] Skipping {name} - no updates since last run.")
                cursor.execute("UPDATE sources SET last_ingested_at = CURRENT_TIMESTAMP WHERE id = %s;", (source_id,))
                return 0
            
            if getattr(feed, 'bozo_exception', None):
                print(f"  Warning: Feed parsing issue for {url}: {feed.bozo_exception}")
            if not feed.entries:
                print(f"  Warning: No entries found for {url}")
                return 0
                
            for entry in feed.entries[:10]:
                link = entry.get('link')
                if not link:
                    continue
                    
                metadata = {
                    "title": entry.get('title', ''),
                    "author": entry.get('author', ''),
                    "published": entry.get('published', ''),
                    "summary": entry.get('summary', '')[:500]
                }
                    
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
        
        cursor.execute("UPDATE sources SET last_ingested_at = CURRENT_TIMESTAMP, feed_etag = %s, feed_modified = %s WHERE id = %s;", 
                       (getattr(feed, 'etag', None), getattr(feed, 'modified', None), source_id))
    finally:
        cursor.close()
        conn.close()
    return new_urls_count
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
                
                params = build_searxng_params(search_query)
                headers = create_searxng_headers(searxng_secret)
                
                resp = request_searxng(
                    f"{searxng_url.rstrip('/')}/search",
                    params=params,
                    headers=headers,
                    method="get",
                    timeout=10,
                    retries=2,
                )
                if resp.status_code == 200:
                    try:
                        searx_json = resp.json()
                    except Exception as parse_err:
                        print(f"  -> SearXNG JSON parse failed: {parse_err}")
                        print(f"  -> Response text: {resp.text[:500]}")
                        searx_json = {}

                    results = searx_json.get("results") or searx_json.get("data") or searx_json.get("hits") or []
                    if isinstance(results, dict):
                        results = results.get("results") or results.get("data") or []
                    if not isinstance(results, list):
                        print(f"  -> Unexpected SearXNG results format: {type(results).__name__}")
                        results = []

                    if len(results) == 0 and isinstance(searx_json, dict):
                        print(f"  -> SearXNG payload keys: {list(searx_json.keys())}")
                        if resp.headers.get("Content-Type", "").startswith("text/html"):
                            fallback_urls = extract_searxng_html_links(resp.text)
                            if fallback_urls:
                                print(f"  -> Fallback HTML scraping found {len(fallback_urls)} links.")
                                results = [{"url": url} for url in fallback_urls]

                    print(f"  -> SearXNG returned {len(results)} dynamic results.")
                    
                    if len(results) == 0 and isinstance(searx_json, dict):
                        print(f"  -> SearXNG payload keys: {list(searx_json.keys())}")
                    
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
        cursor.execute("SELECT id, name, url, domain, epistemic_trust_score, feed_etag, feed_modified FROM sources WHERE category NOT IN ('Dynamic', 'Discovered', 'Revalidation', 'API') ORDER BY epistemic_trust_score DESC;")
        active_sources = cursor.fetchall()
        
        print(f"Loaded {len(active_sources)} evolving sources for RSS ingestion.")

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(process_rss_source, active_sources))
        new_urls_count += sum(results)
        
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
