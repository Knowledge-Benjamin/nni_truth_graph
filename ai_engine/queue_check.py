import os
import psycopg2
from dotenv import load_dotenv

load_dotenv('.env')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
queries = [
    ('raw_urls','SELECT status, count(*) FROM raw_urls GROUP BY status'),
    ('raw_articles','SELECT status, count(*) FROM raw_articles GROUP BY status'),
    ('extracted_claims','SELECT pipeline_stage, status, count(*) FROM extracted_claims GROUP BY pipeline_stage, status')
]
for name,q in queries:
    print('---', name)
    cur.execute(q)
    for r in cur.fetchall():
        print(r)
cur.close()
conn.close()
