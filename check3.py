import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT pipeline_stage, status, COUNT(*) FROM extracted_claims WHERE status = 'AUTO_APPROVE' GROUP BY pipeline_stage, status;")
for row in cur.fetchall(): print(row)
