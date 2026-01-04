import requests
import psycopg2
import zipfile
import io
import csv
import logging
import os
import sys
from dotenv import load_dotenv

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Env - try local .env file first, then system env vars are auto-available
# CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars on Render
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    logger.info(f"✅ Loaded .env from {env_path}")
else:
    logger.info("ℹ️ No .env file found - using system environment variables (Render deployment)")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL not found.")
    sys.exit(1)

def get_latest_gdelt_url():
    """Fetches the URL of the latest GDELT 2.0 Events Export."""
    try:
        # lastupdate.txt Line 0 is the Export CSV
        resp = requests.get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt", timeout=10)
        if resp.status_code == 200:
            line = resp.text.split('\n')[0]
            url = line.split(' ')[2]
            return url
    except Exception as e:
        logger.error(f"Failed to fetch GDELT URL: {e}")
    return None

def ingest():
    url = get_latest_gdelt_url()
    if not url: return

    logger.info(f"⬇️ Downloading GDELT Events: {url}")
    
    try:
        r = requests.get(url, timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        filename = z.namelist()[0]
        
        logger.info(f"📂 Parsing: {filename}")
        
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        inserted_count = 0
        skipped_count = 0
        
        with z.open(filename) as f:
            text_stream = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
            reader = csv.reader(text_stream, delimiter='\t')
            
            for row in reader:
                try:
                    # GDELT 2.0 Event CSV Layout Strategy
                    # We need:
                    # Col 31: NumMentions (Int)
                    # Last Col: SourceURL (String)
                    
                    if len(row) < 50: continue # Malformed row?
                    
                    # Col 31 is Index 30? Or Index 31?
                    # CSV is 0-indexed. 
                    # Documentation says "Column 31". That usually means Index 30.
                    # BUT my debug showed Col[30] = 2.80 (Float).
                    # So "Column 31" might be Index 30 (Goldstein).
                    # "Column 32" might be Index 31 (NumMentions).
                    # Let's try Index 31.
                    
                    mentions_val = row[31] # Index 31
                    num_mentions = int(mentions_val)
                    
                    if num_mentions < 10: 
                        skipped_count += 1
                        continue
                        
                    url = row[-1] # Valid Source URL is always last
                    if not url.startswith('http'): continue

                    # Insert into articles table
                    cur.execute("""
                        INSERT INTO articles (url, publisher, raw_text, ingestion_source)
                        VALUES (%s, 'GDELT_EVENT_HI', %s, 'GDELT')
                        ON CONFLICT (url) DO NOTHING
                        RETURNING id;
                    """, (url, ""))
                    
                    article_id_result = cur.fetchone()
                    if article_id_result:
                        article_id = article_id_result[0]
                        
                        # ✅ NEW: Create processing_queue entry for the article
                        # This ensures GDELT articles flow through the ingestion pipeline
                        cur.execute("""
                            INSERT INTO processing_queue (article_id, status, attempts)
                            VALUES (%s, 'PENDING', 0)
                            ON CONFLICT (article_id) DO NOTHING;
                        """, (article_id,))
                        
                        inserted_count += 1
                        if inserted_count % 10 == 0:
                            print(f"✅ [HIT] {num_mentions} Mentions: {url[:60]}...")
                            conn.commit()
                            
                except (ValueError, IndexError):
                    continue
                    
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"🎉 Done. Inserted: {inserted_count}. Skipped (Low Impact): {skipped_count}")
        
    except Exception as e:
        logger.error(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    ingest()
