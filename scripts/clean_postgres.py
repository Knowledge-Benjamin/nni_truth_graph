import os
import psycopg2
from dotenv import load_dotenv

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

def clean_postgres():
    print("Connecting to PostgreSQL to clean databases...")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Drop the tables in reverse order of creation due to foreign keys
        print("- Dropping extracted_claims...")
        cursor.execute("DROP TABLE IF EXISTS extracted_claims CASCADE;")
        
        print("- Dropping raw_articles...")
        cursor.execute("DROP TABLE IF EXISTS raw_articles CASCADE;")
        
        print("- Dropping raw_urls...")
        cursor.execute("DROP TABLE IF EXISTS raw_urls CASCADE;")
        
        print("PostgreSQL cleanup complete! The staging databases have been wiped.")
        
    except psycopg2.Error as e:
        print(f"PostgreSQL connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    clean_postgres()
