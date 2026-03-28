import os
import json
import psycopg2
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

def add_local_sources():
    print("Connecting to PostgreSQL to inject local trusted sources...")
    
    file_path = os.path.join(os.path.dirname(__file__), '../data/trusted_sources.json')
    if not os.path.exists(file_path):
        print(f"Error: Could not find {file_path}")
        return
        
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    sources = data.get('trusted_sources', [])
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        inserted = 0
        for source in sources:
            name = source.get("name")
            url = source.get("url")
            category = source.get("category", "General")
            
            # The trust score in the JSON is 0-10, we need it as 0.0-1.0
            raw_trust = source.get("trust_score", 5)
            trust_score = raw_trust / 10.0
            
            domain = urlparse(url).netloc
            if domain.startswith("www."):
                domain = domain[4:]
                
            try:
                cursor.execute("""
                    INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id;
                """, (name, url, domain, category, trust_score))
                
                if cursor.fetchone():
                    inserted += 1
            except Exception as e:
                print(f"Error inserting {name}: {e}")
                
        print(f"Successfully injected {inserted} new local sources from JSON file into the database!")
        
    except psycopg2.Error as e:
        print(f"PostgreSQL connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    add_local_sources()
