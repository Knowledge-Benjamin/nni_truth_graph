import psycopg2
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
conn.autocommit = True
cur = conn.cursor()

# Create table if it doesn't exist at all
cur.execute("""
CREATE TABLE IF NOT EXISTS claim_provenance (
    id SERIAL PRIMARY KEY,
    claim_id INTEGER UNIQUE REFERENCES extracted_claims(id) ON DELETE CASCADE
);
""")

columns = {
    "internet_original_url": "TEXT",
    "internet_original_source": "TEXT",
    "internet_original_date": "TIMESTAMPTZ",
    "is_our_source_original": "BOOLEAN",
    "neo4j_stance": "VARCHAR(50)",
    "neo4j_matched_claim_id": "INTEGER",
    "neo4j_similarity": "FLOAT"
}

for col, data_type in columns.items():
    try:
        cur.execute(f"ALTER TABLE claim_provenance ADD COLUMN {col} {data_type};")
        print(f"Added column: {col}")
    except psycopg2.errors.DuplicateColumn:
        print(f"Column {col} already exists. Skipping.")

cur.close()
conn.close()
print("Migration completed successfully.")
