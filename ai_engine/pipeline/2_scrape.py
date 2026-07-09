import os
import time
import gzip
import hashlib
import json
import threading
import psycopg2
from psycopg2.extras import Json
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
MAX_WORKERS = 10

# ── Forensic Auditability ──────────────────────────────────────────────────────
# Local directory to store gzipped HTML snapshots. Relative to project root so
# it is easy to back up or mount to an S3-compatible storage volume.
SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'snapshots')
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def _compute_sha256(raw_html: str) -> str:
    """Return the SHA-256 hex digest of the raw page HTML (UTF-8 encoded)."""
    return hashlib.sha256(raw_html.encode('utf-8', errors='replace')).hexdigest()


def _save_snapshot(url: str, raw_html: str, sha256: str) -> str | None:
    """Persist a gzipped copy of the raw HTML to disk.

    Filename pattern: <sha256[:16]>_<url_hash[:8]>.html.gz
    Returns the relative path on success, None on failure.
    """
    try:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        filename = f"{sha256[:16]}_{url_hash}.html.gz"
        filepath = os.path.join(SNAPSHOT_DIR, filename)
        with gzip.open(filepath, 'wb') as f:
            # Store both the original URL and the raw HTML inside the archive
            header = json.dumps({"url": url, "sha256": sha256}).encode('utf-8')
            f.write(header + b'\n')
            f.write(raw_html.encode('utf-8', errors='replace'))
        return os.path.relpath(filepath)
    except Exception as e:
        print(f"      [SNAPSHOT WARNING] Could not save snapshot: {e}")
        return None


def _ping_archive_org(url: str, url_id: int, db_url: str) -> None:
    """Fire-and-forget: ask the Wayback Machine to save the page, then store the
    resulting archive URL back into raw_urls.archive_url.
    Runs in a background daemon thread so it never blocks the scraper.
    """
    try:
        import requests as _req
        save_url = f"https://web.archive.org/save/{url}"
        resp = _req.get(save_url, timeout=20, headers={"User-Agent": "NNI-Truth-Graph/1.0 (forensic-archival)"})
        # Wayback Machine returns the archived URL in the Content-Location header
        archive_url = None
        if resp.status_code in (200, 201, 302):
            location = resp.headers.get('Content-Location', '')
            if location:
                archive_url = f"https://web.archive.org{location}" if location.startswith('/') else location
            else:
                archive_url = save_url  # fallback: the save URL itself is useful
        if archive_url:
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            with conn.cursor() as c:
                c.execute("UPDATE raw_urls SET archive_url = %s WHERE id = %s", (archive_url, url_id))
            conn.close()
            print(f"      -> [ARCHIVE.ORG] Snapshot saved: {archive_url}")
    except Exception as e:
        print(f"      -> [ARCHIVE.ORG WARNING] Could not ping archive.org: {e}")

PAYWALL_DOMAINS = {
   
}

# Sites that are permanently inaccessible to scrapers (DOI redirectors,
# JS-only portals, hard bot walls). Mark as FAILED_NO_ACCESS immediately
# — no retries wasted.
DEAD_END_DOMAINS = {
      # Heavy JS/bot wall via raw fetch
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
        links = []
        try:
            # Emulate browser behavior but don't wait for total network idle (trackers keep it alive forever)
            # 'domcontentloaded' guarantees the HTML is there. We add a small explicit wait for hydration.
            response = page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # Give JS frameworks 2s to hydrate content
            
            if response and response.ok:
                html = page.content()
                try:
                    # Scope hyperlink discovery strictly to article body / main content area
                    links = page.evaluate('''() => {
                        const container = document.querySelector('article') || document.querySelector('[role="main"]') || document.body;
                        return Array.from(container.querySelectorAll('a[href]'))
                                    .map(a => a.href)
                                    .filter(href => href.startsWith('http'));
                    }''')
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
            return None, None, []

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

        return body, meta, links, html

    except Exception as e:
        print(f"      [FETCH ERROR] Failed reading {url}: {e}")
        return None, None, [], None

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
            
            # Fetch the Master Graph trusted domain map for the Authority Filter
            approved_domains_map = {}
            with conn.cursor() as c:
                 c.execute("SELECT id, domain FROM sources;")
                 for s_id, dom in c.fetchall():
                     approved_domains_map[dom] = s_id
                     
            video_whitelist = ('youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com', 'vimeo.com', 'instagram.com')
            
            # We need actual transactions for locking
            for __phase, (__limit, __filter_clause) in enumerate([
                (100, "AND metadata->>'investigation_id' IS NOT NULL"),
                (50, "AND metadata->>'investigation_id' IS NULL")
            ]):
                items_processed = 0
                while items_processed < __limit:
                    try:
                        with conn.cursor() as cursor:
                            # Fetch exactly 1 URL, locking it — includes retryable FAILEDs
                            cursor.execute(f"""
                                SELECT id, url, metadata, COALESCE((metadata->>'retry_count')::int, 0)
                                FROM raw_urls
                                WHERE status IN ('PENDING_SCRAPE')
                                {__filter_clause}
                                        ORDER BY id ASC
                                LIMIT 1
                                FOR UPDATE SKIP LOCKED;
                            """)
                        
                            row = cursor.fetchone()
                            if not row:
                                conn.rollback()
                                break # Queue is empty, exit thread naturally
                            
                            url_id, url, initial_metadata, retry_count = row

                            # Skip video-platform URLs — Stage 2A handles these via yt-dlp
                            url_domain = urlparse(url).netloc.lstrip("www.")
                            if any(url_domain == v or url_domain.endswith("." + v) for v in video_whitelist):
                                cursor.execute("UPDATE raw_urls SET status = 'PENDING_VIDEO' WHERE id = %s", (url_id,))
                                conn.commit()
                                print(f"  [W-{worker_id}] Video URL routed to Stage 2A: {url}")
                                continue

                            print(f"  [W-{worker_id}] Processing (attempt {retry_count+1}): {url}")
                        
                            # 2. Scrape it using the Playwright page
                            result = fetch_and_extract(url, page)
                            raw_text, scraped_meta = result[0], result[1]
                            discovered_links = result[2] if len(result) > 2 else []
                            raw_html = result[3] if len(result) > 3 else None
                        
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
                                    
                                # ── Forensic Auditability ─────────────────────────────
                                # Hash the raw HTML bytes (byte-identical page content);
                                # fall back to extracted text if HTML was not captured.
                                hashable_content = raw_html or raw_text or ""
                                sha256 = _compute_sha256(hashable_content)
                                snapshot_path = _save_snapshot(url, hashable_content, sha256)

                                cursor.execute(f"""
                                    INSERT INTO raw_articles
                                        (url_id, title, author, publish_date, raw_text,
                                         status, content_sha256, snapshot_path)
                                    VALUES (%s, %s, %s, %s, %s, 'PENDING_CLASSIFICATION', %s, %s)
                                    RETURNING id;
                                """, (url_id, title, author, pub_date, raw_text, sha256, snapshot_path))

                                row_art = cursor.fetchone()
                                article_id = row_art[0] if row_art else None
                                if article_id is None:
                                    conn.rollback()
                                    continue

                                # Asynchronously ping archive.org — never blocks the scraper
                                t = threading.Thread(
                                    target=_ping_archive_org,
                                    args=(url, url_id, DATABASE_URL),
                                    daemon=True
                                )
                                t.start()
                                print(f"      -> [FORENSIC] SHA-256: {sha256[:16]}... | Snapshot: {snapshot_path or 'skipped'}")
                            
                                image_url = s_meta.get('image')
                                if image_url:
                                    try:
                                        import requests
                                        # Forward to Vision Server for SigLIP embedding, pHash, and Deepfake score
                                        VISION_URL = os.getenv("VISION_INFERENCE_URL", "http://localhost:7860")
                                        resp = requests.post(f"{VISION_URL}/embed_media", json={"image_urls": [image_url]}, timeout=10)
                                        if resp.status_code == 200:
                                            data = resp.json()
                                            embed = data["embeddings"][0]
                                            phash = data.get("phashes", [None])[0]
                                            synth_prob = data.get("synthetic_prob", [0.0])[0]
                                            cursor.execute(f"""
                                                INSERT INTO media_provenance (raw_article_id, media_url, phash, clip_embedding, synthetic_probability)
                                                VALUES (%s, %s, %s, %s::vector, %s)
                                            """, (article_id, image_url, phash, embed, float(synth_prob)))
                                            print(f"      -> [VISION W-{worker_id}] Linked Hero Image via SigLIP Vector (Deepfake Prob: {synth_prob:.2f}).")
                                        else:
                                            # Store url silently if server offline
                                            cursor.execute("INSERT INTO media_provenance (raw_article_id, media_url) VALUES (%s, %s)", (article_id, image_url))
                                    except Exception as e:
                                        # Non-fatal if vision server isn't up
                                        cursor.execute("INSERT INTO media_provenance (raw_article_id, media_url) VALUES (%s, %s)", (article_id, image_url))
                                        print(f"      -> [VISION WARNING] Saved {image_url} but skipped SigLIP: {e}")
                            
                                # --- CRAWLER INJECTION (4-Layer Heuristic) ---
                                current_domain = urlparse(url).netloc.lstrip('www.')
                                queued_links = 0
                                for d_url in discovered_links:
                                    d_domain = urlparse(d_url).netloc.lstrip('www.')
                                    if not d_domain or d_domain == current_domain: continue # Self-loops
                                
                                    # Ad & Spam Drop
                                    if any(x in d_url.lower() for x in ['utm_', 'affiliate', 'login', 'subscribe', 'privacy', 'signup', '/settings', 'cookie']):
                                        continue
                                    
                                    target_source_id = None
                                    if d_domain in video_whitelist:
                                        # Fallback source ID if it's a raw video (it'll be handled natively)
                                        target_source_id = approved_domains_map.get(d_domain, 1) 
                                    elif d_domain in approved_domains_map:
                                        target_source_id = approved_domains_map[d_domain]
                                    
                                    if target_source_id is not None:
                                        try:
                                            cursor.execute(f"""
                                                INSERT INTO raw_urls (source_id, url, metadata, status)
                                                VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                                                ON CONFLICT (url) DO NOTHING
                                            """, (target_source_id, d_url, Json({"origin": "recursive_crawler", "source_article": url})))
                                            queued_links += cursor.rowcount
                                        except Exception: pass

                                cursor.execute("UPDATE raw_urls SET status = 'SCRAPED' WHERE id = %s", (url_id,))
                                print(f"      -> [SUCCESS W-{worker_id}] Extracted {len(raw_text)} chars. Crawled {queued_links} authoritative links.")
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
        count_row = cursor.fetchone()
        pending_count = count_row[0] if count_row else 0
        
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
        with ThreadPoolExecutor(max_workers=1) as executor:
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
