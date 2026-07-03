"""
migrate_investigation_report.py
Adds the report, report_updated_at, and report_chapter_hashes columns
to the investigations table. Run once before deploying the report engine.
"""
import os, sys, psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report JSONB DEFAULT '{}';")
cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report_updated_at TIMESTAMPTZ;")
cur.execute("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report_chapter_hashes JSONB DEFAULT '{}';")

print("[Migration] Successfully added report columns to investigations table.")
cur.close()
conn.close()
