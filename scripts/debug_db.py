
import os
import psycopg2
from dotenv import load_dotenv

# Load env variables
load_dotenv(os.path.join(os.path.dirname(__file__), '../server/.env'))
NEON_DB = os.getenv("DATABASE_URL")

def inspect_db():
    if not NEON_DB:
        print("❌ DATABASE_URL is missing in server/.env")
        return

    try:
        print(f"Connecting to DB...")
        conn = psycopg2.connect(NEON_DB)
        cur = conn.cursor()
        print("✅ Connected!")

        # 1. Check tables
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = cur.fetchall()
        print(f"\nTables found: {[t[0] for t in tables]}")

        if ('articles',) in tables or ('Articles',) in tables:
            table_name = 'articles' if ('articles',) in tables else 'Articles'
            
            # 2. Count total rows
            cur.execute(f'SELECT count(*) FROM "{table_name}";')
            count = cur.fetchone()[0]
            print(f"\nTotal rows in '{table_name}': {count}")

            if count > 0:
                # 3. Check distinct statuses with repr to see whitespace
                try:
                    cur.execute(f'SELECT DISTINCT status FROM "{table_name}";')
                    statuses = cur.fetchall()
                    print(f"Distinct statuses (raw): {[repr(s[0]) for s in statuses]}")
                    
                    # Check for content nulls
                    cur.execute(f'SELECT count(*) FROM "{table_name}" WHERE content IS NULL or content = \'\';')
                    null_content = cur.fetchone()[0]
                    print(f"Rows with NULL/Empty content: {null_content}")
                    
                    # Check matching rows query manually
                    print("Testing match query...")
                    cur.execute(f"SELECT count(*) FROM \"{table_name}\" WHERE TRIM(UPPER(status)) = 'PUBLISHED' AND content IS NOT NULL;")
                    match_count = cur.fetchone()[0]
                    print(f"Rows matching TRIM(UPPER(status))='PUBLISHED': {match_count}")

                except Exception as e:
                    print(f"Could not check statuses: {e}")

                # 4. Check one row to see column names and sample data
                cur.execute(f'SELECT * FROM "{table_name}" LIMIT 1;')
                row = cur.fetchone()
                colnames = [desc[0] for desc in cur.description]
                print(f"\nSample Row Columns: {colnames}")
                # print(f"Sample Row Data: {row}")
                
                 # 5. Check "publishedAt" column existence casing
                print("\nChecking publishedAt column...")
                lower_pub = "publishedat" in colnames
                camel_pub = "publishedAt" in colnames
                print(f"Contains 'publishedat'? {lower_pub}")
                print(f"Contains 'publishedAt'? {camel_pub}")

        conn.close()

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    inspect_db()
