import feedparser
import psycopg2
import json
import logging
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Env - try local .env file first, then fall back to system env vars
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()  # Load from system environment (Render)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL not found.")
    sys.exit(1)

def load_sources():
    json_path = os.path.join(os.path.dirname(__file__), '../data/trusted_sources.json')
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
            return data.get('trusted_sources', [])
    except Exception as e:
        logger.error(f"Failed to load sources: {e}")
        return []

def ingest_rss():
    sources = load_sources()
    logger.info(f"📡 Loaded {len(sources)} Trusted Sources.")
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    total_new = 0
    
    for source in sources:
        name = source['name']
        url = source['url']
        category = source['category']
        
        # logger.info(f"Fetching: {name}...")
        
        try:
            # Parse Feed
            feed = feedparser.parse(url)
            
            # Check for BOZO (Malformatted XML) but try to continue
            if feed.bozo:
                logger.warning(f"Feed parsing warning from {name}: {feed.bozo_exception}")
                
            if not hasattr(feed, 'entries'):
                logger.warning(f"⚠️  No entries in {name}")
                continue

                
            new_in_feed = 0
            
            for entry in feed.entries:
                try:
                    title = entry.get('title', '')
                    link = entry.get('link', '')
                    if not title:
                        logger.warning(f"No title found for entry from {name} (url: {link})")
                        continue
                    summary = entry.get('summary', '') or entry.get('description', '')
                    published_parsed = entry.get('published_parsed')
                    
                    if published_parsed:
                        published = datetime(*published_parsed[:6])
                    else:
                        published = datetime.now()
                        
                    if not link.startswith('http'): continue
                    
                    # Insert into Postgres
                    # We store 'RSS_TRUSTED' as source
                    cur.execute("""
                        INSERT INTO articles (url, title, publisher, raw_text, ingestion_source, published_date)
                        VALUES (%s, %s, %s, %s, 'RSS_TRUSTED', %s)
                        ON CONFLICT (url) DO UPDATE SET
                            title = EXCLUDED.title,
                            publisher = EXCLUDED.publisher,
                            ingestion_source = 'RSS_TRUSTED', -- Upgrade source trust
                            published_date = EXCLUDED.published_date
                        RETURNING id;
                    """, (link, title, name, summary, published))
                    
                    article_id_result = cur.fetchone()
                    if article_id_result:
                        article_id = article_id_result[0]
                        
                        # ✅ NEW: Create processing_queue entry for the article
                        # This ensures the article flows through the ingestion pipeline
                        cur.execute("""
                            INSERT INTO processing_queue (article_id, status, attempts)
                            VALUES (%s, 'PENDING', 0)
                            ON CONFLICT (article_id) DO NOTHING;
                        """, (article_id,))
                        
                        new_in_feed += 1
                        total_new += 1
                        
                except Exception as e:
                    logger.error(f"Failed to process RSS entry from {name}: {str(e)}")
                    continue
            
            if new_in_feed > 0:
                print(f"✅ {name}: +{new_in_feed} new articles.")
                conn.commit() # Commit after each feed

                
        except Exception as e:
            logger.error(f"Failed to process RSS feed '{url}' ({name}): {str(e)}")
            continue
    
    try:
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to commit transaction: {str(e)}")
    finally:
        cur.close()
        conn.close()
    
    logger.info(f"🎉 RSS Cycle Complete. Total New Articles: {total_new}")

if __name__ == "__main__":
    ingest_rss()
