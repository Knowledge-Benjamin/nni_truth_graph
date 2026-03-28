import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
cur.execute("UPDATE extracted_claims SET status='AUTO_APPROVE', pipeline_stage='STAGE_8_MUTATION_QUEUE' WHERE status='GRAPH_COMMITTED' LIMIT 5;")
conn.commit()
print("Successfully reset 5 claims for Mutation test.")
