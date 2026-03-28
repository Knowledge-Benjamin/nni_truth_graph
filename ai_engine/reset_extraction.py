import psycopg2, os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
conn.autocommit = True
cur = conn.cursor()

cur.execute("UPDATE raw_articles SET status='PENDING_EXTRACTION' WHERE status='FAILED_EXTRACTION'")
print(f"Reset {cur.rowcount} articles for extraction.")
