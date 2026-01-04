import psycopg2
import os
import logging
import datetime
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Environment - try local .env file first, then fall back to system env vars
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()  # Load from system environment (Render)

DATABASE_URL = os.getenv("DATABASE_URL")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

class ProvenanceHunter:
    def __init__(self):
        self.conn = psycopg2.connect(DATABASE_URL)
        self.cur = self.conn.cursor()

    def get_fact_date(self, fact_id):
        """Fetch published_date of the article containing the fact."""
        self.cur.execute("""
            SELECT a.published_date 
            FROM articles a 
            JOIN extracted_facts f ON f.article_id = a.id 
            WHERE f.id = %s
        """, (fact_id,))
        res = self.cur.fetchone()
        return res[0] if res else None

    def check_external_provenance(self, text, current_date):
        """Checks if the text existed on the web BEFORE current_date."""
        if not SERPER_API_KEY or not current_date:
            return None
        
        # Date Format for Google: "before:YYYY-MM-DD"
        date_str = current_date.strftime("%Y-%m-%d") if isinstance(current_date, datetime.date) else str(current_date)[:10]
        query = f'"{text}" before:{date_str}'
        
        url = "https://google.serper.dev/search"
        payload = {"q": query}
        headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            results = response.json()
            if results.get("organic"):
                # Found an older result!
                top = results["organic"][0]
                return {
                    "url": top.get("link"),
                    "title": top.get("title"),
                    "date": top.get("date", "Unknown")
                }
        except Exception as e:
            logger.error(f"Serper Error: {e}")
        return None

    def hunt(self):
        logger.info("🕵️ Starting Provenance Hunt...")
        
        # 1. Get Unchecked Facts
        self.cur.execute("""
            SELECT id, subject, predicate, object, embedding 
            FROM extracted_facts 
            WHERE checked_at IS NULL AND embedding IS NOT NULL
            LIMIT 50;
        """)
        candidates = self.cur.fetchall()
        
        if not candidates:
            logger.info("✅ No new facts to verify.")
            return

        for cid, subj, pred, obj, emb in candidates:
            statement = f"{subj} {pred} {obj}"
            my_date = self.get_fact_date(cid)
            
            if not my_date:
                logger.warning(f"⚠️ Fact {cid} has no date. Skipping.")
                continue

            # 2. Vector Search for Similar Facts
            # Find neighbors within strict distance (0.15 ~= 85% similarity)
            self.cur.execute("""
                SELECT id, embedding <=> %s::vector as dist
                FROM extracted_facts
                WHERE id != %s 
                AND embedding <=> %s::vector < 0.15
                ORDER BY dist ASC LIMIT 10;
            """, (emb, cid, emb))
            
            neighbors = self.cur.fetchall()
            
            original_id = cid
            is_original = True
            earliest_date = my_date
            
            # 3. Time Travel Analysis
            if neighbors:
                logger.info(f"🔎 Fact {cid} has {len(neighbors)} semantic neighbors.")
                for nid, dist in neighbors:
                    # Check neighbor date
                    n_date = self.get_fact_date(nid)
                    if n_date and n_date < earliest_date:
                        earliest_date = n_date
                        original_id = nid
                        is_original = False
            
            # 4. External Reality Check
            external_url = None
            external_source_db_id = None
            
            if is_original:
                external_hit = self.check_external_provenance(statement, my_date)
                if external_hit:
                    logger.info(f"🚨 Debunked! Fact {cid} found externally: {external_hit['url']}")
                    is_original = False
                    external_url = external_hit['url']
                    
                    # --- UNIFIED GRAPH LOGIC ---
                    # Insert this external source as a "Reference Article" so we can link to it in Neo4j
                    try:
                        # Check exist
                        self.cur.execute("SELECT id FROM articles WHERE url = %s", (external_url,))
                        ex_row = self.cur.fetchone()
                        
                        if ex_row:
                            external_source_db_id = ex_row[0]
                        else:
                            # Insert New
                            self.cur.execute("""
                                INSERT INTO articles (url, title, published_date, is_reference)
                                VALUES (%s, %s, %s, TRUE)
                                RETURNING id
                            """, (external_url, external_hit['title'], external_hit['date']))
                            external_source_db_id = self.cur.fetchone()[0]
                            self.conn.commit()
                            logger.info(f"🔗 Created Reference Node ID {external_source_db_id} for external source.")
                            
                    except Exception as e:
                        logger.error(f"Failed to create reference node: {e}")
                        self.conn.rollback()

            # 5. Determine Original Source ID
            final_source_id = None
            
            if external_source_db_id:
                final_source_id = external_source_db_id # We found an older external source
            elif not is_original and original_id != cid:
                 # It's an internal echo. Point to the original fact's article.
                 self.cur.execute("SELECT article_id FROM extracted_facts WHERE id = %s", (original_id,))
                 parent_row = self.cur.fetchone()
                 if parent_row:
                     final_source_id = parent_row[0]
            elif is_original:
                # I am the original (so far), so point to MY article.
                self.cur.execute("SELECT article_id FROM extracted_facts WHERE id = %s", (cid,))
                my_row = self.cur.fetchone()
                if my_row:
                    final_source_id = my_row[0]

            # 6. Update Record
            prov_id_val = original_id if (not is_original and not external_url) else None 
            
            self.cur.execute("""
                UPDATE extracted_facts 
                SET is_original = %s, 
                    provenance_id = %s, 
                    external_source_url = %s,
                    original_source_id = %s,
                    checked_at = NOW() 
                WHERE id = %s;
            """, (is_original, prov_id_val, external_url, final_source_id, cid))
            
            label = "ORIGINAL" if is_original else f"ECHO of {original_id}"
            logger.info(f"✅ Fact {cid}: {label}")
            
            self.conn.commit()

        self.cur.close()
        self.conn.close()

if __name__ == "__main__":
    hunter = ProvenanceHunter()
    hunter.hunt()
