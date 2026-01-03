import psycopg2
import os
import logging
import requests
import time
import json
import sys
from dotenv import load_dotenv

# Set UTF-8 encoding for logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('classification.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(env_path)
DATABASE_URL = os.getenv("DATABASE_URL")
HF_TOKEN = os.getenv("HF_TOKEN")

# Configuration
BATCH_SIZE = 500
SLEEP_BETWEEN_BATCHES = 60  # Sleep 60 seconds between batches when queue empty
AUTO_MODE = True  # Run continuously instead of one batch

# Model: DeBERTa-v3 optimized for Zero-Shot
# Alternative: "facebook/bart-large-mnli" (Stable)
MODEL_ID = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
API_URL = f"https://router.huggingface.co/hf-inference/models/{MODEL_ID}"

IPTC_TOPICS = [
    "Arts, Culture, Entertainment and Media",
    "Conflict, War and Peace",
    "Crime, Law and Justice",
    "Disaster, Accident and Emergency Incident",
    "Economy, Business and Finance",
    "Education",
    "Environment",
    "Health",
    "Human Interest",
    "Labour",
    "Lifestyle and Leisure",
    "Politics and Government",
    "Religion",
    "Science and Technology",
    "Society",
    "Sport",
    "Weather"
]

def query_hf_api(payload):
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        return response.json()
    except Exception as e:
        logger.error(f"API Request Failed: {e}")
        return None

def classify_batch():
    """Fetch and classify one batch from processing queue. Returns count of classified articles."""
    if not HF_TOKEN:
        logger.error("[ERROR] HF_TOKEN not found. Please add it to .env")
        return 0
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # 1. Get Topic IDs Map
        cur.execute("SELECT name, id FROM topics;")
        topic_map = {row[0]: row[1] for row in cur.fetchall()}
        
        if not topic_map:
            logger.error("[ERROR] No topics found in DB. Run setup_topics_schema.py first.")
            cur.close()
            conn.close()
            return 0

        # 2. Query from processing_queue - only articles with SCRAPED status
        query = """
            SELECT a.id, a.title, a.raw_text FROM articles a
            INNER JOIN processing_queue pq ON a.id = pq.article_id
            WHERE pq.status = 'SCRAPED'
            AND a.id NOT IN (SELECT article_id FROM article_topics)
            AND a.title IS NOT NULL AND a.title != ''
            LIMIT %s;
        """
        cur.execute(query, (BATCH_SIZE,))
        rows = cur.fetchall()
        
        if not rows:
            logger.info("[WAIT] Queue empty - no articles to classify. Waiting...")
            cur.close()
            conn.close()
            return 0
        
        logger.info(f"[CLASSIFY] Batch of {len(rows)} articles from queue...")
        classified_count = 0
        
        for row in rows:
            aid, title, raw_text = row
            
            # Skip if raw_text is None
            if not raw_text:
                logger.warning(f"[SKIP] Article {aid}: No content, skipping")
                continue
            
            # Construct text to classify (Title + partial body)
            text_to_classify = f"{title}. {raw_text[:500]}"
            
            payload = {
                "inputs": text_to_classify,
                "parameters": {"candidate_labels": IPTC_TOPICS}
            }
            
            result = query_hf_api(payload)
            
            # Handle API Loading/Error
            if isinstance(result, dict) and 'error' in result:
                logger.warning(f"[API_ERROR] Article {aid}: {result['error']}")
                if "loading" in result['error'].lower():
                    time.sleep(5)  # Wait for model load
                    result = query_hf_api(payload)  # Retry once
            
            if result and 'labels' in result:
                # Top 1 Topic
                top_topic = result['labels'][0]
                confidence = result['scores'][0]
                
                if confidence > 0.4:  # Threshold
                    tid = topic_map.get(top_topic)
                    if tid:
                        # Insert classification
                        cur.execute("""
                            INSERT INTO article_topics (article_id, topic_id, confidence)
                            VALUES (%s, %s, %s)
                            ON CONFLICT DO NOTHING;
                        """, (aid, tid, confidence))
                        
                        # Update queue status to CLASSIFIED
                        cur.execute("""
                            UPDATE processing_queue 
                            SET status = 'CLASSIFIED', updated_at = NOW()
                            WHERE article_id = %s;
                        """, (aid,))
                        
                        logger.info(f"[OK] Article {aid} -> {top_topic} ({confidence:.2f})")
                        classified_count += 1
                else:
                    logger.info(f"[LOW_CONF] Article {aid}: confidence ({confidence:.2f})")
                    # Mark as CLASSIFIED even if confidence is low
                    cur.execute("""
                        UPDATE processing_queue 
                        SET status = 'CLASSIFIED', updated_at = NOW()
                        WHERE article_id = %s;
                    """, (aid,))
            else:
                logger.warning(f"[NO_RESULT] Article {aid}: No classification result, skipping")
            
            conn.commit()  # Commit after each article
            time.sleep(0.5)  # Rate limit courtesy
        
        cur.close()
        conn.close()
        
        logger.info(f"[BATCH_COMPLETE] {classified_count}/{len(rows)} classified")
        return classified_count
        
    except Exception as e:
        logger.error(f"[BATCH_ERROR] {e}")
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()
        return 0

def run_continuous():
    """Run continuous classification in a loop"""
    logger.info("[START] Continuous classification mode...")
    logger.info(f"[CONFIG] BATCH_SIZE: {BATCH_SIZE}, SLEEP_BETWEEN_BATCHES: {SLEEP_BETWEEN_BATCHES}s")
    
    batch_num = 0
    while True:
        try:
            batch_num += 1
            logger.info(f"[BATCH_{batch_num}] Starting...")
            classified = classify_batch()
            
            if classified == 0:
                logger.info(f"[SLEEP] {SLEEP_BETWEEN_BATCHES}s until next check...")
                time.sleep(SLEEP_BETWEEN_BATCHES)
            else:
                time.sleep(5)  # Short sleep between batches when processing
                
        except KeyboardInterrupt:
            logger.info("[STOP] Classifier stopped by user")
            break
        except Exception as e:
            logger.error(f"[LOOP_ERROR] {e}")
            time.sleep(10)  # Wait before retrying

if __name__ == "__main__":
    if AUTO_MODE:
        run_continuous()
    else:
        logger.info("Running single batch mode")
        classify_batch()
