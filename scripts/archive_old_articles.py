import psycopg2
import os
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Environment
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(env_path)

PROD_DSN = os.getenv("DATABASE_URL")
ARCHIVE_DSN = os.getenv("ARCHIVE_DATABASE_URL")

def archive_old_articles():
    """
    Hybrid Retention Policy:
    - Move articles older than 90 days to Archive DB
    - Keep production DB lean for fast queries
    - Preserve all data for compliance/audit
    """
    try:
        prod_conn = psycopg2.connect(PROD_DSN)
        archive_conn = psycopg2.connect(ARCHIVE_DSN)
        
        prod_cur = prod_conn.cursor()
        archive_cur = archive_conn.cursor()
        
        logger.info("📦 Starting Archive Job (90-day retention)...")
        
        # 1. Find old articles
        prod_cur.execute("""
            SELECT id, url, title, published_date, source, raw_text, processed_at, created_at
            FROM articles 
            WHERE processed_at < NOW() - INTERVAL '90 days'
              AND processed_at IS NOT NULL
            LIMIT 1000
        """)
        
        old_articles = prod_cur.fetchall()
        
        if not old_articles:
            logger.info("✅ No articles to archive.")
            return
        
        logger.info(f"📤 Archiving {len(old_articles)} articles...")
        
        # 2. Copy to Archive DB
        for article in old_articles:
            aid, url, title, pub_date, source, raw_text, proc_at, created_at = article
            
            archive_cur.execute("""
                INSERT INTO articles (id, url, title, published_date, source, raw_text, processed_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (aid, url, title, pub_date, source, raw_text, proc_at, created_at))
        
        # 3. Delete from Production DB
        article_ids = [a[0] for a in old_articles]
        prod_cur.execute("""
            DELETE FROM articles 
            WHERE id = ANY(%s)
        """, (article_ids,))
        
        prod_conn.commit()
        archive_conn.commit()
        
        logger.info(f"✅ Archived {len(old_articles)} articles to Archive DB.")
        logger.info(f"🗑️  Removed {len(old_articles)} articles from Production DB.")
        
        prod_cur.close()
        archive_cur.close()
        prod_conn.close()
        archive_conn.close()
        
    except Exception as e:
        logger.error(f"❌ Archive Error: {e}")
        raise

if __name__ == "__main__":
    archive_old_articles()
