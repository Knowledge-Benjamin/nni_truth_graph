import os
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# Load database url relative to script location
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

# Path to the shared data directory mapped for Node.js delivery
EXPORT_PATH = os.path.join(os.path.dirname(__file__), '../../data/truth_graph_snapshot.csv')

def export_snapshot():
    """
    Executes a high-performance native Postgres COPY command to dump
    the requested knowledge graph data into a compressed/local CSV file
    for the Tier 1 B2B Enterprise API.
    """
    print(f"[{datetime.utcnow().isoformat()}] Starting B2B Enterprise bulk snapshot export...")
    try:
        # Ensure targeted directory exists
        os.makedirs(os.path.dirname(EXPORT_PATH), exist_ok=True)
        
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # We only export fully validated claims (GRAPH_COMMITTED) to external B2B clients
        query = """
            COPY (
                SELECT id, subject, predicate, object_entity, epistemic_score, 
                       temporal_anchor, spatial_anchor, quote_context, valid_from, valid_until, created_at
                FROM extracted_claims
                WHERE status = 'GRAPH_COMMITTED'
            ) TO STDOUT WITH CSV HEADER
        """
        
        # Extract straight to file pointer using psycopg2's custom copy_expert
        with open(EXPORT_PATH, 'w', encoding='utf-8') as f:
            cur.copy_expert(query, f)
            
        cur.close()
        conn.close()
        
        file_size = os.path.getsize(EXPORT_PATH) / (1024 * 1024)
        print(f"[{datetime.utcnow().isoformat()}] Snapshot successfully written to {EXPORT_PATH} ({file_size:.2f} MB)")
        
    except Exception as e:
        print(f"[ERROR] Failed to export snapshot: {e}")

if __name__ == "__main__":
    export_snapshot()
