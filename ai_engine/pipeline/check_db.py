import psycopg2
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM extracted_claims WHERE status = 'PROCESSING' AND pipeline_stage = 'STAGE_4_RESOLUTION'")
print('Pending Claims:', cur.fetchone()[0])
