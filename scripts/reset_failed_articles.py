import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

RESET_SQL = """
UPDATE raw_articles SET status = 'PENDING_EXTRACTION' WHERE status = 'FAILED_EXTRACTION';
"""

def main():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(RESET_SQL)
            print("All FAILED_EXTRACTION articles reset to PENDING_EXTRACTION.")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
