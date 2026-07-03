import os
from dotenv import load_dotenv
load_dotenv(dotenv_path='ai_engine/.env')
import psycopg2
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute("SELECT status, COUNT(*) FROM raw_articles GROUP BY status ORDER BY COUNT(*) DESC;")
for row in cur.fetchall():
    print(row)
cur.execute("SELECT COUNT(*) FROM raw_articles WHERE status = 'PENDING_EXTRACTION';")
print('PENDING_EXTRACTION:', cur.fetchone()[0])
conn.close()
