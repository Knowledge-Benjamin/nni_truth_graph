import psycopg2
import json

# Neon DB connection
conn_string = "postgresql://neondb_owner:npg_b2sNTig0IBmZ@ep-summer-fog-adlvchlm-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require"

try:
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    
    # Get list of tables
    print("=== TABLES ===")
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cur.fetchall()
    for table in tables:
        print(f"  - {table[0]}")
    
    print("\n=== ARTICLES TABLE SCHEMA ===")
    cur.execute("""
        SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'articles'
        ORDER BY ordinal_position;
    """)
    columns = cur.fetchall()
    for col in columns:
        print(f"  {col[0]}: {col[1]}" + (f"({col[2]})" if col[2] else "") + f" | Nullable: {col[3]}")
    
    print("\n=== SAMPLE DATA (First 2 Articles) ===")
    cur.execute("SELECT * FROM articles LIMIT 2;")
    sample = cur.fetchall()
    colnames = [desc[0] for desc in cur.description]
    
    for row in sample:
        print("\nArticle:")
        for i, val in enumerate(row):
            print(f"  {colnames[i]}: {str(val)[:100]}...")  # Truncate long values
    
    print("\n=== STATISTICS ===")
    cur.execute("SELECT COUNT(*) FROM articles;")
    count = cur.fetchone()[0]
    print(f"Total Articles: {count}")
    
    cur.close()
    conn.close()
    print("\n✅ Connection successful!")
    
except Exception as e:
    print(f"❌ Error: {e}")
