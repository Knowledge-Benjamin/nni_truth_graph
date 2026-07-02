"""
Migration: Add Forensic Auditability Columns
Run once to add content_sha256, snapshot_path, and archive_url to the schema.
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")


def migrate():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True

    with conn.cursor() as cur:
        print("Adding content_sha256 and snapshot_path to raw_articles...")
        cur.execute("""
            ALTER TABLE raw_articles
            ADD COLUMN IF NOT EXISTS content_sha256 TEXT,
            ADD COLUMN IF NOT EXISTS snapshot_path TEXT;
        """)

        print("Adding archive_url to raw_urls...")
        cur.execute("""
            ALTER TABLE raw_urls
            ADD COLUMN IF NOT EXISTS archive_url TEXT;
        """)

        print("Creating index on content_sha256 for deduplication...")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_articles_sha256
            ON raw_articles (content_sha256);
        """)

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
