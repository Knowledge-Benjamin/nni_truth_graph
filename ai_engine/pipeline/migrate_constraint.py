import psycopg2
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
conn.autocommit = True
cur = conn.cursor()

# Add UNIQUE constraint on claim_id so ON CONFLICT works
try:
    cur.execute("ALTER TABLE claim_provenance ADD CONSTRAINT claim_provenance_claim_id_unique UNIQUE (claim_id);")
    print("Added UNIQUE constraint on claim_id.")
except psycopg2.errors.DuplicateTable:
    print("Constraint already exists. Skipping.")
except Exception as e:
    if 'already exists' in str(e).lower():
        print("Constraint already exists. Skipping.")
    else:
        print(f"Error: {e}")

cur.close()
conn.close()
print("Done.")
