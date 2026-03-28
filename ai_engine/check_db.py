import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
cur.execute("SELECT pipeline_stage, status, COUNT(*) FROM extracted_claims GROUP BY pipeline_stage, status;")
rows = cur.fetchall()
print("ROWS IN DB:", rows)

# Let's see if there are ANY claims we can reset.
cur.execute("UPDATE extracted_claims SET status='AUTO_APPROVE', pipeline_stage='STAGE_8_MUTATION_QUEUE' WHERE id IN (SELECT id FROM extracted_claims WHERE status='FAILED_MUTATION' LIMIT 5) RETURNING id;")
reset_rows = cur.fetchall()
conn.commit()
print("RESET IDs:", reset_rows)
