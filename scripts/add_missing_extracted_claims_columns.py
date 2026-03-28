import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

ALTERS = [
    "ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS model_version VARCHAR(255);",
    "ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(255);",
    "ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS ai_metadata JSONB;"
]

def main():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            for sql in ALTERS:
                cur.execute(sql)
                print(f"Executed: {sql}")
        conn.close()
        print("All missing columns added to 'extracted_claims'.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
