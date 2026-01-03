import asyncio
import os
import logging
import random
import psycopg2
import time
from playwright.async_api import async_playwright
import trafilatura
from dotenv import load_dotenv

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Env
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"

# Configuration
BATCH_SIZE = 5  # Concurrent tabs
BATCH_LIMIT = 50  # Articles to process per run
AUTO_MODE = True  # Run continuously
SLEEP_BETWEEN_RUNS = 120  # Sleep 2 min between runs when queue empty

async def scrape_url(context, aid, url, timeout=20000):
    """Scrape a single URL with timeout handling"""
    page = await context.new_page()
    try:
        # Random Delay (Human behavior)
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        logger.info(f"[VISIT] Article {aid}: {url[:50]}...")
        
        # Go to page with timeout
        try:
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded"),
                timeout=timeout/1000  # Convert to seconds
            )
        except asyncio.TimeoutError:
            logger.warning(f"[TIMEOUT] Article {aid} exceeded {timeout}ms")
            return (aid, None)
        
        # Get rendered HTML
        html = await page.content()
        
        # Extract Main Content using Trafilatura
        extracted_text = trafilatura.extract(html, include_tables=False, include_comments=False)
        
        if extracted_text and len(extracted_text) > 200:
            logger.info(f"[SCRAPED] Article {aid}: {len(extracted_text)} chars")
            return (aid, extracted_text)
        else:
            logger.warning(f"[LOW_CONTENT] Article {aid}: only {len(extracted_text) if extracted_text else 0} chars")
            return (aid, None)
            
    except Exception as e:
        logger.error(f"[ERROR] Article {aid}: {str(e)[:80]}")
        return (aid, None)
    finally:
        try:
            await page.close()
        except:
            pass  # Ignore close errors

async def process_batch():
    """Process one batch of articles from the queue"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Get Unscraped Articles from processing_queue
        # Query articles that are PENDING in processing_queue (not yet scraped)
        cur.execute(f"""
            SELECT a.id, a.url FROM articles a
            INNER JOIN processing_queue pq ON a.id = pq.article_id
            WHERE pq.status = 'PENDING' AND (a.raw_text IS NULL OR LENGTH(a.raw_text) < 100)
            LIMIT {BATCH_LIMIT};
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        if not rows:
            logger.info("[WAIT] No articles to scrape. Queue satisfied.")
            return 0
        
        logger.info(f"[SCRAPE_BATCH] Processing {len(rows)} articles...")
        
        updates = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=USER_AGENT)
            
            # Process in chunks of BATCH_SIZE
            for i in range(0, len(rows), BATCH_SIZE):
                chunk = rows[i : i + BATCH_SIZE]
                tasks = [scrape_url(context, r[0], r[1]) for r in chunk]
                
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks),
                        timeout=120  # 2 minute timeout for entire chunk
                    )
                    
                    for aid, text in results:
                        if text:
                            updates.append((text, aid))
                except asyncio.TimeoutError:
                    logger.warning("[CHUNK_TIMEOUT] Chunk exceeded timeout, moving to next")
                    continue
                except Exception as e:
                    logger.error(f"[CHUNK_ERROR] {e}")
                    continue
            
            try:
                await browser.close()
            except:
                pass  # Ignore close errors
        
        # Update DB and processing_queue status - with retry logic
        if updates:
            logger.info(f"[SAVING] {len(updates)} articles to database...")
            
            # Retry loop for database commits
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Fresh connection for each attempt
                    conn = psycopg2.connect(DATABASE_URL)
                    cur = conn.cursor()
                    
                    for text, aid in updates:
                        cur.execute("UPDATE articles SET raw_text = %s WHERE id = %s", (text, aid))
                        cur.execute("UPDATE processing_queue SET status = 'SCRAPED', updated_at = NOW() WHERE article_id = %s", (aid,))
                    
                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    logger.info(f"[BATCH_COMPLETE] {len(updates)} articles updated")
                    return len(updates)
                    
                except Exception as e:
                    logger.error(f"[DB_ERROR] Attempt {attempt+1}/{max_retries}: {e}")
                    try:
                        cur.close()
                        conn.close()
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        logger.info(f"[RETRY] Waiting 5s before retry...")
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"[SAVE_FAILED] Could not save articles after {max_retries} attempts")
                        return 0
        else:
            logger.warning("[NO_SUCCESS] No articles successfully scraped in this batch")
            return 0
            
    except Exception as e:
        logger.error(f"[BATCH_ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 0
    finally:
        try:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()
        except:
            pass

async def run_continuous():
    """Run continuous scraping in a loop"""
    logger.info("[START] Continuous scraping mode...")
    logger.info(f"[CONFIG] BATCH_LIMIT: {BATCH_LIMIT}, BATCH_SIZE: {BATCH_SIZE}")
    
    run_num = 0
    while True:
        try:
            run_num += 1
            logger.info(f"[RUN_{run_num}] Starting batch processing...")
            scraped = await process_batch()
            
            if scraped == 0:
                logger.info(f"[SLEEP] {SLEEP_BETWEEN_RUNS}s until next check...")
                await asyncio.sleep(SLEEP_BETWEEN_RUNS)
            else:
                await asyncio.sleep(5)  # Short sleep between batches
                
        except KeyboardInterrupt:
            logger.info("[STOP] Scraper stopped by user")
            break
        except Exception as e:
            logger.error(f"[LOOP_ERROR] {e}")
            await asyncio.sleep(10)  # Wait before retrying

async def run_single():
    """Run single batch"""
    logger.info("[SINGLE] Running single batch...")
    await process_batch()

if __name__ == "__main__":
    if AUTO_MODE:
        asyncio.run(run_continuous())
    else:
        asyncio.run(run_single())
