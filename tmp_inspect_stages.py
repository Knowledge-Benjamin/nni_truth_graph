import os
from dotenv import load_dotenv
import psycopg2

load_dotenv(r'c:\Users\TempAdmin\Desktop\nni_truth_graph\ai_engine\.env')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
conn.autocommit = True
cur = conn.cursor()

print('raw_urls statuses:')
cur.execute("SELECT status, COUNT(*) FROM raw_urls GROUP BY status ORDER BY status")
for row in cur.fetchall():
    print(row)

print('\nraw_articles statuses:')
cur.execute("SELECT status, COUNT(*) FROM raw_articles GROUP BY status ORDER BY status")
for row in cur.fetchall():
    print(row)

print('\nextracted_claims stage/status sample:')
cur.execute("SELECT pipeline_stage, status, COUNT(*) FROM extracted_claims GROUP BY pipeline_stage, status ORDER BY pipeline_stage, status LIMIT 40")
for row in cur.fetchall():
    print(row)

conn.close()
