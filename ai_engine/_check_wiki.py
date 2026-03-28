import os
import sys
import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, 'ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
with conn.cursor() as cur:
    cur.execute("""
        SELECT status, COUNT(*) 
        FROM raw_articles 
        WHERE author = 'Wikipedia Contributors' 
        GROUP BY status
    """)
    rows = cur.fetchall()
    print("Wikipedia Articles Status Breakdown:")
    total = 0
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
        total += r[1]
    print(f"Total Wikipedia articles: {total}")
    
    cur.execute("""
        SELECT ec.status, ec.pipeline_stage, COUNT(*)
        FROM extracted_claims ec
        JOIN raw_articles ra ON ec.article_id = ra.id
        WHERE ra.author = 'Wikipedia Contributors'
        GROUP BY ec.status, ec.pipeline_stage
    """)
    rows = cur.fetchall()
    print("\nWikipedia Claims Status Breakdown:")
    claim_total = 0
    for r in rows:
        print(f"  Stage: {r[1]}, Status: {r[0]}, Count: {r[2]}")
        claim_total += r[2]
    print(f"Total Wikipedia Claims: {claim_total}")

conn.close()
