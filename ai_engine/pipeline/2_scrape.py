import os
import time
import random
import psycopg2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from ai_engine.core.logger import get_printer
print = get_printer(2)  # Bright Yellow

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

# Scraper is non-AI and uses Playwright only; increase concurrency for throughput
MAX_WORKERS = 2

PAYWALL_DOMAINS = {
    "bloomberg.com", "wsj.com", "ft.com", "nytimes.com",
    "economist.com", "thetimes.co.uk", "telegraph.co.uk",
    "washingtonpost.com", "theathletic.com", "barrons.com",
}

# Sites that are permanently inaccessible to scrapers (DOI redirectors,
# JS-only portals, hard bot walls). Mark as FAILED_NO_ACCESS immediately
# — no retries wasted.
DEAD_END_DOMAINS = {
    "science.org", "doi.org", "nature.com", "cell.com",
    "thelancet.com", "nejm.org", "jamanetwork.com",
    "link.springer.com", "onlinelibrary.wiley.com",
    "reuters.com", "apnews.com",  # Heavy JS/bot wall via raw fetch
}

MAX_SCRAPE_RETRIES = 3   # How many times to retry a FAILED URL before giving up

def fetch_and_extract(url, page):
    """
    Fetches the URL using a Playwright Page and extracts a plain-text body plus
    lightweight metadata (title only). We intentionally avoid trafilatura's
    network/timeout stack here because it relies on `signal`, which is not
    safe inside worker threads on HF Spaces (causing "signal only works in
    main thread of the main interpreter").
    """
    try:
        domain = urlparse(url).netloc.lstrip("www.")
        if any(domain == p or domain.endswith("." + p) for p in PAYWALL_DOMAINS):
            print(f"      [PAYWALL SKIP] {domain} is a known hard-paywall domain.")
            return None, "paywall"

        if any(domain == p or domain.endswith("." + p) for p in DEAD_END_DOMAINS):
            print(f"      [DEAD-END SKIP] {domain} is a known inaccessible domain.")
            return None, "dead_end"

        # Project Gutenberg: convert ebook listing page to the actual plain text URL
        if "gutenberg.org/ebooks/" in url:
            book_id = url.rstrip("/").split("/")[-1]
            url = f"https://www.gutenberg.org/files/{book_id}/{book_id}-0.txt"
            print(f"      [GUTENBERG] Resolved to plain text: {url}")

        html = None
        text = None
        try:
            # Emulate browser behavior but don't wait for total network idle (trackers keep it alive forever)
            # 'domcontentloaded' guarantees the HTML is there. We add a small explicit wait for hydration.
            response = page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # Give JS frameworks 2s to hydrate content
            
            if response and response.ok:
                html = page.content()
                try:
                    text = page.inner_text("body")
                except Exception:
                    text = None
            elif response and response.status == 403:
                print(f"      [403 FORBIDDEN] Blocked by {domain}. Moving on.")
            else:
                status = response.status if response else "Unknown"
                print(f"      [SCRAPER ERROR] HTTP {status} for {url}")
                
        except PlaywrightTimeoutError:
            print(f"      [TIMEOUT] Playwright timed out loading {url}. Grabbing whatever rendered.")
            # If it timed out, try to grab whatever HTML it managed to render anyway
            try:
                html = page.content()
                try:
                    text = page.inner_text("body")
                except Exception:
                    text = None
            except Exception:
                pass
        except Exception as e:
             print(f"      [SCRAPER ERROR] Playwright failed: {e}")

        # If Playwright did not yield meaningful content, abort and let the
        # caller handle retries.
        if not html and not text:
            return None, None

        # Build a minimal "article" body from the page text (fallback to raw
        # HTML if needed). This is less fancy than trafilatura but robust and
        # thread-safe.
        body = (text or "").strip()
        if not body and html:
            body = html

        # Lightweight metadata: we only care about title and hero image.
        meta = {}
        try:
            meta["title"] = page.title()
        except Exception:
            pass
            
        try:
            og_img = page.query_selector("meta[property='og:image']")
            if og_img:
                meta["image"] = og_img.get_attribute("content")
            else:
                img = page.query_selector("article img")
                if img:
                    meta["image"] = img.get_attribute("src")
        except Exception:
            pass

        return body, meta

    except Exception as e:
        print(f"      [FETCH ERROR] Failed reading {url}: {e}")
        return None, None

def scraper_worker(worker_id):
    """
    A single thread worker loop that pulls 1 URL at a time using FOR UPDATE SKIP LOCKED.
    Iterates as fast as possible until the queue is completely empty.
    Instantiates ONE Playwright browser context per worker thread to avoid spin-up overhead.
    """
    try:
        # Build the thread-local headless browser
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
                # Create a persistent context, rotating headers/user agents here if needed
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                
                # Block heavy/unnecessary resources to speed up scraping drastically
                context.route("**/*", lambda route: route.abort() 
                              if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
                              else route.continue_())
                
                page = context.new_page()
            except Exception as bw_err:
                print(f"[FATAL W-{worker_id}] Failed to launch Playwright browser: {bw_err}")
                return

            conn = psycopg2.connect(DATABASE_URL)
            items_processed = 0
            
            # We need actual transactions for locking
            while items_processed < 50:
                try:
                    with conn.cursor() as cursor:
                        # Fetch exactly 1 URL, locking it — includes retryable FAILEDs
                        cursor.execute("""
                            SELECT id, url, metadata, COALESCE((metadata->>'retry_count')::int, 0)
                            FROM raw_urls 
                            WHERE status IN ('PENDING_SCRAPE')
                            LIMIT 1 
                            FOR UPDATE SKIP LOCKED;
                        """)
                        
                        row = cursor.fetchone()
                        if not row:
                            conn.rollback()
                            break # Queue is empty, exit thread naturally
                            
                        url_id, url, initial_metadata, retry_count = row
                        print(f"  [W-{worker_id}] Processing (attempt {retry_count+1}): {url}")
                        
                        # 2. Scrape it using the Playwright page
                        raw_text, scraped_meta = fetch_and_extract(url, page)
                        
                        if raw_text and len(raw_text.strip()) > 100:
                            # 3. Successful scrape
                            s_meta = scraped_meta if isinstance(scraped_meta, dict) else (vars(scraped_meta) if hasattr(scraped_meta, '__dict__') else {})
                            title = s_meta.get('title') or (initial_metadata or {}).get('title') or 'Unknown Title'
                            author = s_meta.get('author') or (initial_metadata or {}).get('author') or 'Unknown Author'
                            
                            pub_date = None
                            if s_meta.get('date'):
                                try:
                                    pub_date = datetime.strptime(s_meta['date'], '%Y-%m-%d')
                                except ValueError:
                                    pass
                                    
                            cursor.execute("""
                                INSERT INTO raw_articles (url_id, title, author, publish_date, raw_text, status)
                                VALUES (%s, %s, %s, %s, %s, 'PENDING_CLASSIFICATION')
                                RETURNING id;
                            """, (url_id, title, author, pub_date, raw_text))
                            
                            article_id = cursor.fetchone()[0]
                            
                            # Insert media provenance if a hero image was discovered
                            image_url = s_meta.get('image')
                            if image_url:
                                try:
                                    import requests
                                    # Forward to Vision Server for SigLIP embedding and pHash
                                    VISION_URL = os.getenv("VISION_INFERENCE_URL", "http://localhost:7860")
                                    resp = requests.post(f"{VISION_URL}/embed_media", json={"image_urls": [image_url]}, timeout=10)
                                    if resp.status_code == 200:
                                        data = resp.json()
                                        embed = data["embeddings"][0]
                                        phash = data["phashes"][0]
                                        cursor.execute("""
                                            INSERT INTO media_provenance (raw_article_id, media_url, phash, clip_embedding)
                                            VALUES (%s, %s, %s, %s::vector)
                                        """, (article_id, image_url, phash, embed))
                                        print(f"      -> [VISION W-{worker_id}] Linked Hero Image via SigLIP Vector.")
                                    else:
                                        # Store url silently if server offline
                                        cursor.execute("INSERT INTO media_provenance (raw_article_id, media_url) VALUES (%s, %s)", (article_id, image_url))
                                except Exception as e:
                                    # Non-fatal if vision server isn't up
                                    cursor.execute("INSERT INTO media_provenance (raw_article_id, media_url) VALUES (%s, %s)", (article_id, image_url))
                                    print(f"      -> [VISION WARNING] Saved {image_url} but skipped SigLIP: {e}")
                            
                            cursor.execute("UPDATE raw_urls SET status = 'SCRAPED' WHERE id = %s", (url_id,))
                            print(f"      -> [SUCCESS W-{worker_id}] Extracted {len(raw_text)} chars.")
                        elif scraped_meta == "paywall" or any(
                            urlparse(url).netloc.lstrip('www.') == p or
                            urlparse(url).netloc.lstrip('www.').endswith('.' + p)
                            for p in PAYWALL_DOMAINS
                        ):
                            cursor.execute("UPDATE raw_urls SET status = 'FAILED_PAYWALL' WHERE id = %s", (url_id,))
                            print(f"      -> [PAYWALL W-{worker_id}] Marked as FAILED_PAYWALL, will not retry.")
                        elif scraped_meta == "dead_end":
                            cursor.execute("UPDATE raw_urls SET status = 'FAILED_NO_ACCESS' WHERE id = %s", (url_id,))
                            print(f"      -> [DEAD-END W-{worker_id}] Marked as FAILED_NO_ACCESS, will not retry.")
                        else:
                            # Retry logic: increment retry_count; give up after MAX_SCRAPE_RETRIES
                            new_retry = retry_count + 1
                            if new_retry >= MAX_SCRAPE_RETRIES:
                                cursor.execute(
                                    "UPDATE raw_urls SET status = 'FAILED' WHERE id = %s",
                                    (url_id,)
                                )
                                print(f"      -> [EXHAUSTED W-{worker_id}] {new_retry} attempts, permanently FAILED.")
                            else:
                                import json as _json
                                meta_update = dict(initial_metadata or {})
                                meta_update['retry_count'] = new_retry
                                cursor.execute(
                                    "UPDATE raw_urls SET status = 'PENDING_SCRAPE', metadata = %s WHERE id = %s",
                                    (_json.dumps(meta_update), url_id)
                                )
                                print(f"      -> [RETRY W-{worker_id}] Queued for retry #{new_retry}.")
                        
                    conn.commit()
                    items_processed += 1
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  [ERROR W-{worker_id} Loop] {e}")
                    conn.rollback()
                    time.sleep(2)
        
        conn.close()
    except Exception as fatal_e:
        print(f"[FATAL W-{worker_id}] {fatal_e}")

def process_scraping_queue():
    """
    Stage 2: Main Orchestrator. Checks pending queue depth and dynamically spins up
    thread pool workers to blast through the queue securely. (Single Pass)
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 2: Concurrent Scraping Engine (Single Pass)")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM raw_urls WHERE status = 'PENDING_SCRAPE';")
        pending_count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        if pending_count == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return
            
        # --- Auto-Scaling Logic ---
        # Don't spin up 25 workers if there's only 3 URLs pending.
        workers_to_use = min(MAX_WORKERS, max(1, pending_count))
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Found {pending_count} pending URLs. Spinning up {workers_to_use} concurrent threads...")
        
        # Spin up the scalable worker pool
        with ThreadPoolExecutor(max_workers=workers_to_use) as executor:
            # We launch Exactly N workers. The workers will self-assign using SKIP LOCKED and terminate when the queue hits 0.
            futures = [executor.submit(scraper_worker, i) for i in range(workers_to_use)]
            for f in futures:
                f.result() # Wait for all threads to cleanly finish pulling from the queue
                
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch complete. Re-evaluating queue...")
        
    except KeyboardInterrupt:
        print("Stopping Scraper Engine.")
    except Exception as e:
        print(f"Fatal orchestration error: {e}")

if __name__ == "__main__":
    process_scraping_queue()
