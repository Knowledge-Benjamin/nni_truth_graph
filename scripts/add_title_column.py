import psycopg2
import os
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

def add_title():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        logger.info("Adding 'title' column if missing...")
        cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS title TEXT;")
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Migration Complete.")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    add_title()
