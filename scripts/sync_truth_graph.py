import os
import logging
from dotenv import load_dotenv
import json
import subprocess
from datetime import datetime, date
import sys

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'sync_truth_graph.log'))
    ]
)
logger = logging.getLogger(__name__)

# Load Environment - try local .env file first, then system env vars are auto-available
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

# Use shared database utilities
from db_utils import get_pg_connection, release_pg_connection, get_neo4j_driver, cleanup

TEMP_FILE = os.path.join(os.path.dirname(__file__), 'temp_graph_data.json')
JS_SCRIPT = os.path.join(os.path.dirname(__file__), 'push_to_neo4j.js')

class DateEncoder(json.JSONEncoder):
    """Handle datetime serialization."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

class TruthGraphSyncer:
    def __init__(self):
        self.pg_conn = get_pg_connection()
        self.pg_cur = self.pg_conn.cursor()
        self.neo4j_driver = get_neo4j_driver()

    def fetch_pg_data(self):
        """Fetch Facts (Original Claims) and Evidence (Articles) from Postgres.
           Applies Strict QC Gates:
           1. Facts must be PROVENANCE CHECKED (checked_at IS NOT NULL).
           2. Articles must be CLASSIFIED (in article_topics) OR be References.
        """
        logger.info("Fetching Verified Truth from Postgres (With Quality Gates)...")
        
        try:
            # 1. Get Canonical Facts (Facts that are Originals + Verified)
            self.pg_cur.execute("""
                SELECT id, subject, predicate, object, confidence, embedding 
                FROM extracted_facts 
                WHERE is_original = TRUE 
                  AND checked_at IS NOT NULL
            """)
            col_names = [desc[0] for desc in self.pg_cur.description]
            facts = [dict(zip(col_names, row)) for row in self.pg_cur.fetchall()]
            logger.info(f"[OK] Fetched {len(facts)} original verified facts")
            
            # 2. Get Relationships (Only for Valid Facts)
            self.pg_cur.execute("""
                SELECT f.id, f.article_id, f.provenance_id, f.is_original
                FROM extracted_facts f
                JOIN articles a ON f.article_id = a.id
                WHERE f.article_id IS NOT NULL
                  AND (f.is_original = TRUE OR f.provenance_id IS NOT NULL)
            """)
            col_names = [desc[0] for desc in self.pg_cur.description]
            assertions = [dict(zip(col_names, row)) for row in self.pg_cur.fetchall()]
            logger.info(f"[OK] Fetched {len(assertions)} fact-article assertions")
            
            # 3. Get Articles (Metadata) - MUST BE CLASSIFIED or REFERENCES
            self.pg_cur.execute("""
                SELECT DISTINCT a.id, a.title, a.url, a.published_date, a.is_reference 
                FROM articles a
                LEFT JOIN article_topics t ON a.id = t.article_id
                WHERE 
                    (a.processed_at IS NOT NULL AND t.topic_id IS NOT NULL) -- Normal: Processed & Classified
                    OR 
                    (a.is_reference = TRUE) -- References: Always accepted (ground truth)
            """)
            col_names = [desc[0] for desc in self.pg_cur.description]
            articles = [dict(zip(col_names, row)) for row in self.pg_cur.fetchall()]
            logger.info(f"[OK] Fetched {len(articles)} classified/reference articles")
            
            # Validation: Check data integrity
            if not facts:
                logger.warning("[WARNING] No verified facts found. Quality Gate 1 may be blocking all data.")
            if not articles:
                logger.warning("[WARNING] No classified articles found. Quality Gate 2 may be blocking all data.")
            if facts and articles and not assertions:
                logger.warning("[WARNING] Facts and articles exist but no assertions found.")
            
            return facts, articles, assertions
            
        except Exception as e:
            logger.error(f"[ERROR] Error fetching data from PostgreSQL: {type(e).__name__}: {str(e)}")
            raise

    def sync(self):
        try:
            logger.info("=" * 80)
            logger.info("STARTING GRAPH SYNCHRONIZATION")
            logger.info("=" * 80)
            
            # Step 1: Fetch data from PostgreSQL
            facts, articles, assertions = self.fetch_pg_data()
            
            # Step 2: Validate data
            logger.info("\n[DATA] Summary:")
            logger.info(f"  • {len(facts)} original verified facts")
            logger.info(f"  • {len(articles)} classified/reference articles")
            logger.info(f"  • {len(assertions)} fact-article assertions")
            
            if not facts or not articles:
                logger.error("[ERROR] SYNC ABORTED: Insufficient data to sync")
                logger.error("   Facts: Quality Gate 1 (provenance checked, original) may be blocking")
                logger.error("   Articles: Quality Gate 2 (classified or reference) may be blocking")
                return False
            
            # Step 3: Prepare payload
            payload = {
                "facts": facts,
                "articles": articles,
                "assertions": assertions
            }
            
            logger.info(f"\n[SERIALIZE] Serializing Data...")
            with open(TEMP_FILE, 'w') as f:
                json.dump(payload, f, cls=DateEncoder)
            logger.info(f"[OK] Wrote {os.path.getsize(TEMP_FILE)} bytes to {TEMP_FILE}")
            
            # Step 4: Execute Node.js bridge
            logger.info("\n[LAUNCH] Launching Node.js Bridge for Neo4j Push...")
            
            # Verify JS script exists
            if not os.path.exists(JS_SCRIPT):
                logger.error(f"[ERROR] JavaScript script not found: {JS_SCRIPT}")
                return False
            
            # Execute JS script
            # Force UTF-8 encoding to avoid Windows CP1252 errors with emojis/special chars
            result = subprocess.run(
                ["node", JS_SCRIPT], 
                capture_output=True, 
                text=True, 
                encoding='utf-8',
                errors='replace'
            )
            
            print("\n" + "=" * 80)
            print("NODE.JS OUTPUT")
            print("=" * 80)
            print(result.stdout)
            if result.stderr:
                print("\nSTDERR:")
                print(result.stderr)
            print("=" * 80 + "\n")
            
            if result.returncode != 0:
                logger.error(f"[ERROR] Node.js Script Failed with return code {result.returncode}")
                logger.error(f"Error output:\n{result.stderr}")
                return False
            else:
                logger.info("[SUCCESS] Graph Sync Bridge Completed Successfully")
                
            # Step 5: Cleanup
            if os.path.exists(TEMP_FILE):
                os.remove(TEMP_FILE)
                logger.info(f"[OK] Cleaned up temporary file: {TEMP_FILE}")
            
            logger.info("\n" + "=" * 80)
            logger.info("[SUCCESS] GRAPH SYNCHRONIZATION COMPLETE")
            logger.info("=" * 80)
            return True
            
        except Exception as e:
            logger.error(f"[ERROR] SYNC FAILED: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
            
        finally:
            try:
                self.pg_cur.close()
                release_pg_connection(self.pg_conn)
                cleanup()
            except Exception as e:
                logger.warning(f"Warning during cleanup: {type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    syncer = TruthGraphSyncer()
    syncer.sync()
