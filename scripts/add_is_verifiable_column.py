import os
import psycopg2
from dotenv import load_dotenv

# Load from ai_engine/.env for consistency
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

ALTER_SQL = """
ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS is_verifiable BOOLEAN;
"""

def main():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(ALTER_SQL)
            print("Column 'is_verifiable' added to 'extracted_claims' table (if not exists).")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
