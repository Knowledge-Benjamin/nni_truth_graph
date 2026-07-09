import os
import time
import psycopg2
from threading import current_thread
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from ai_engine.core.logger import get_printer
from ai_engine.core.inference_pool import inference_pool as hf_pool
print = get_printer(3)  # Bright Blue

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

# Use HF Inference API for free embeddings — direct HTTP, no langchain dependency
HF_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"


def embed_text(text):
    """Generates a 768-dimensional text embedding via Hugging Face Inference API."""
    try:
        return hf_pool.embed(text)
    except Exception as e:
        print(f"      [HF INFERENCE API ERROR] Embedding failed: {e}")
        return None

MAX_WORKERS = 6  # Concurrency limit for Classification to respect HuggingFace API rate limits

def classification_worker(worker_id):
    """
    Pulls PENDING_CLASSIFICATION articles via FOR UPDATE SKIP LOCKED
    Generates Semantic Embeddings via Hugging Face API to act as high-dimensional classifiers.
    Routes to PENDING_EXTRACTION.
    """
    try:
        for __phase, (__limit, __filter_clause) in enumerate([
            (100, "AND ru.metadata->>'investigation_id' IS NOT NULL"),
            (50, "AND ru.metadata->>'investigation_id' IS NULL")
        ]):
            items_processed = 0

            while items_processed < __limit:
                try:
                    article_id = None
                    title = None
                    raw_text = None

                    with psycopg2.connect(DATABASE_URL) as claim_conn:
                        with claim_conn.cursor() as cursor:
                            cursor.execute(f"""
                                SELECT a.id, a.title, a.raw_text
                                FROM raw_articles a
                                JOIN raw_urls ru ON a.url_id = ru.id
                                WHERE a.status = 'PENDING_CLASSIFICATION'
                                {__filter_clause}
                                    ORDER BY a.id ASC
                                LIMIT 1
                                FOR UPDATE OF a SKIP LOCKED;
                            """)

                            row = cursor.fetchone()
                            if not row:
                                claim_conn.rollback()
                                break

                            article_id, title, raw_text = row
                            cursor.execute("UPDATE raw_articles SET status = 'PROCESSING_CLASSIFICATION' WHERE id = %s", (article_id,))
                            claim_conn.commit()

                    if article_id is None or title is None or raw_text is None:
                        break

                    print(f"  [W-{worker_id}] Classifying: {title[:50]}...")

                    # --- LOCAL LANGUAGE DETECTION & TRANSLATION ---
                    try:
                        from langdetect import detect
                        from ai_engine.core.sunbird_api import SunbirdClient

                        detected_lang = detect(raw_text)
                        if detected_lang != 'en':
                            print(f"      -> [TRANSLATION] Detected non-English language ({detected_lang}). Routing to Sunbird AI...")
                            translated_text = SunbirdClient.translate_to_english(raw_text)

                            if translated_text and translated_text != raw_text:
                                with psycopg2.connect(DATABASE_URL) as update_conn:
                                    with update_conn.cursor() as cursor:
                                        cursor.execute("UPDATE raw_articles SET raw_text = %s WHERE id = %s", (translated_text, article_id))
                                    update_conn.commit()
                                raw_text = translated_text
                                print(f"      -> [SUNBIRD SUCCESS] Translated to English.")
                    except Exception as lang_e:
                        print(f"      -> [LANGDETECT ERROR] Could not detect language: {lang_e}")

                    chunk_to_embed = raw_text[:4000]
                    embedding = embed_text(chunk_to_embed)

                    if embedding:
                        embedding_literal = f"[{','.join(str(f) for f in embedding)}]"
                        with psycopg2.connect(DATABASE_URL) as write_conn:
                            with write_conn.cursor() as cursor:
                                cursor.execute(f"""
                                    INSERT INTO article_categories (article_id, embedding)
                                    VALUES (%s, %s::vector)
                                """, (article_id, embedding_literal))
                                cursor.execute("UPDATE raw_articles SET status = 'PENDING_EXTRACTION' WHERE id = %s", (article_id,))
                            write_conn.commit()
                        print(f"      -> [SUCCESS W-{worker_id}] Generated 768D Semantic Projection.")
                    else:
                        with psycopg2.connect(DATABASE_URL) as write_conn:
                            with write_conn.cursor() as cursor:
                                cursor.execute("UPDATE raw_articles SET status = 'PENDING_CLASSIFICATION' WHERE id = %s", (article_id,))
                            write_conn.commit()
                        print(f"      -> [FAILED W-{worker_id}] Classification aborted. Kept in queue.")

                    items_processed += 1
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  [ERROR W-{worker_id}] Loop] {e}")
                    try:
                        with psycopg2.connect(DATABASE_URL) as rollback_conn:
                            with rollback_conn.cursor() as cursor:
                                if 'article_id' in locals() and article_id is not None:
                                    cursor.execute("UPDATE raw_articles SET status = 'PENDING_CLASSIFICATION' WHERE id = %s", (article_id,))
                            rollback_conn.commit()
                    except Exception:
                        pass
                    time.sleep(2)
    except Exception as fatal_e:
        print(f"[FATAL W-{worker_id}] {fatal_e}")

def process_classification_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 3: Semantic Classification Engine (Single Pass)")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Backlog of older articles scraped as 'PENDING_EXTRACTION' before we added Stage 3
        # We will convert all PENDING_EXTRACTION that lack a vector to PENDING_CLASSIFICATION
        cursor.execute("""
            UPDATE raw_articles 
            SET status = 'PENDING_CLASSIFICATION'
            WHERE status = 'PENDING_EXTRACTION' 
              AND id NOT IN (SELECT article_id FROM article_categories);
        """)
        
        cursor.execute("SELECT COUNT(*) FROM raw_articles WHERE status = 'PENDING_CLASSIFICATION';")
        count_row = cursor.fetchone()
        pending_count = count_row[0] if count_row else 0
        
        cursor.close()
        conn.close()
        
        if pending_count == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return
            
        workers_to_use = min(MAX_WORKERS, max(1, pending_count))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Found {pending_count} pending articles. Spinning up {workers_to_use} classification threads...")
        
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(classification_worker, i) for i in range(workers_to_use)]
            for f in futures:
                f.result()
                
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch complete.")
        
    except KeyboardInterrupt:
        print("Stopping Classification Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    process_classification_queue()
