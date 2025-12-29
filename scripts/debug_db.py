import os
import psycopg2
from dotenv import load_dotenv

# Hardcoded for debugging as requested by user
NEON_DB = "postgresql://neondb_owner:npg_b2sNTig0IBmZ@ep-summer-fog-adlvchlm-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def inspect_db():
    try:
        print(f"Connecting to DB...")
        conn = psycopg2.connect(NEON_DB)
        cur = conn.cursor()
        print("✅ Connected!")

        # 1. Inspect Schema (Columns and Types)
        cur.execute("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name = 'articles';
        """)
        columns = cur.fetchall()
        print("\n[SCHEMA] 'articles' table columns:")
        for col in columns:
            print(f" - {col[0]}: {col[1]} (udt: {col[2]})")

        # 2. Check content of 'status' and 'content' for the 16 rows
        cur.execute('SELECT id, status, length(content), "publishedAt" FROM articles;')
        rows = cur.fetchall()
        print(f"\n[DATA] Found {len(rows)} rows:")
        for r in rows:
            print(f" - ID: {r[0]} | Status: '{r[1]}' (Type: {type(r[1])}) | Content Len: {r[2]} | Published: {r[3]}")

        # 3. Test exact queries
        print("\n[TEST] Testing Filters:")
        
        # Test 1: Case sensitive exact match
        cur.execute("SELECT count(*) FROM articles WHERE status = 'PUBLISHED'")
        print(f" - status = 'PUBLISHED': {cur.fetchone()[0]}")

        # Test 2: Lowercase exact match
        cur.execute("SELECT count(*) FROM articles WHERE status = 'published'")
        print(f" - status = 'published': {cur.fetchone()[0]}")

        # Test 3: Upper with Cast (Handling Enums)
        try:
            cur.execute("SELECT count(*) FROM articles WHERE UPPER(status::text) = 'PUBLISHED'")
            print(f" - UPPER(status::text) = 'PUBLISHED': {cur.fetchone()[0]}")
        except Exception as e:
            print(f" - UPPER(status::text) failed: {e}")
            conn.rollback()

        # Test 4: The Failing Query (TRIM(UPPER(status)))
        try:
            cur.execute("SELECT count(*) FROM articles WHERE TRIM(UPPER(status::text)) = 'PUBLISHED' AND content IS NOT NULL")
            print(f" - TRIM(UPPER(status::text)) = 'PUBLISHED' + content: {cur.fetchone()[0]}")
        except Exception as e:
            print(f" - TRIM(UPPER) failed: {e}")
            conn.rollback()

        conn.close()

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    inspect_db()
