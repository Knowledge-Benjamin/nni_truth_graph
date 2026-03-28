import psycopg2
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM raw_urls WHERE status='PENDING_SCRAPE'")
print("Pending URLs to scrape:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM raw_articles WHERE status='PENDING_CLASSIFICATION'")
print("Pending Articles to classify (Stage 3):", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM raw_articles WHERE status='PENDING_EXTRACTION'")
print("Pending Articles to extract (Stage 4):", cur.fetchone()[0])

cur.execute("SELECT pipeline_stage, status, COUNT(*) FROM extracted_claims GROUP BY pipeline_stage, status;")
print("Claims:", cur.fetchall())
