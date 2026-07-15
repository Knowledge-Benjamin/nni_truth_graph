import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT (ru.metadata->>'investigation_id') IS NOT NULL as is_inv, COUNT(*) FROM raw_urls ru GROUP BY is_inv;")
for row in cur.fetchall(): print(row)
