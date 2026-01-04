"""
Unified Contradiction Detection Script
Replaces both detect_contradictions.py (Neo4j) and provides enhanced detection for detect_contradictions_deberta.py

Strategy: PostgreSQL-Only (RECOMMENDED)
- Uses DeBERTa NLI for accurate contradiction detection
- Processes BOTH recent AND historical facts
- Syncs PostgreSQL contradictions to Neo4j for visualization

Features:
- Backfill support (process all facts or just recent)
- Dual-stage processing (high-value facts + new facts)
- PostgreSQL ↔ Neo4j synchronization
- Comprehensive logging and error recovery
"""

import psycopg2
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [CONTRADICTION_DETECTION] - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("contradiction_detection.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load Environment - try local .env file first, then system env vars are auto-available
# CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars on Render
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    logger.info(f"✅ Loaded .env from {env_path}")
else:
    logger.info("ℹ️ No .env file found - using system environment variables (Render deployment)")

DATABASE_URL = os.getenv("DATABASE_URL")
HF_TOKEN = os.getenv("HF_TOKEN")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Import Neo4j driver
try:
    from neo4j import GraphDatabase
    neo4j_available = True
except ImportError:
    logger.warning("⚠️  Neo4j driver not installed - Neo4j sync will be skipped")
    neo4j_available = False

# DeBERTa NLI Model endpoint
DEBERTA_API = "https://router.huggingface.co/hf-inference/models/ynie/roberta-large-snli_mnli_fever_anli_R1_R2_R3-nli"


def query_deberta(premise, hypothesis, max_retries=3):
    """
    Query DeBERTa-MNLI to check if hypothesis contradicts premise.
    
    Args:
        premise: Base fact statement
        hypothesis: Fact to check against premise
        max_retries: Number of retry attempts on failure
    
    Returns:
        dict: {'entailment': 0.X, 'neutral': 0.Y, 'contradiction': 0.Z}
        or None on failure
    """
    if not HF_TOKEN:
        logger.error("❌ HF_TOKEN not set - DeBERTa API unavailable")
        return None
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {
        "inputs": f"{premise} </s></s> {hypothesis}"
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(DEBERTA_API, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                # Parse DeBERTa output
                if isinstance(result, list) and isinstance(result[0], list):
                    result = result[0]
                
                scores = {item['label'].lower(): item['score'] for item in result}
                return scores
                
            elif response.status_code == 429:  # Rate limited
                logger.warning(f"🔄 Rate limited on attempt {attempt+1}/{max_retries}, retrying...")
                import time
                time.sleep(2 ** (attempt + 1))
                continue
            else:
                logger.error(f"❌ API Error {response.status_code}: {response.text}")
                return None
                
        except requests.Timeout:
            logger.warning(f"⏱️  Timeout on attempt {attempt+1}/{max_retries}")
            if attempt == max_retries - 1:
                return None
            import time
            time.sleep(1)
            continue
        except Exception as e:
            logger.error(f"❌ DeBERTa API Error: {e}")
            return None
    
    return None


def get_neo4j_driver():
    """Initialize Neo4j driver if credentials available."""
    if not neo4j_available or not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
        return None
    
    try:
        # For neo4j+s:// URIs, encryption is in the scheme
        # Just pass URI and auth - no additional SSL config to avoid conflicts
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        logger.info("✅ Neo4j connection verified")
        return driver
    except Exception as e:
        logger.warning(f"⚠️  Neo4j connection failed: {e}")
        return None


def sync_to_neo4j(neo4j_driver, contradictions):
    """
    Sync PostgreSQL contradictions to Neo4j for visualization.
    
    Args:
        neo4j_driver: Neo4j driver instance
        contradictions: List of contradiction tuples
    """
    if not neo4j_driver:
        logger.warning("⚠️  Skipping Neo4j sync (driver unavailable)")
        return
    
    try:
        with neo4j_driver.session() as session:
            synced_count = 0
            
            for fact1_id, fact2_id, score in contradictions:
                try:
                    result = session.run("""
                        MATCH (f1:Fact {id: $f1}), (f2:Fact {id: $f2})
                        MERGE (f1)-[c:CONTRADICTS {
                            score: $score,
                            detected_at: datetime(),
                            method: 'DeBERTa'
                        }]-(f2)
                        RETURN COUNT(*) AS created
                    """, {
                        'f1': fact1_id,
                        'f2': fact2_id,
                        'score': float(score)
                    })
                    
                    synced_count += 1
                    
                except Exception as e:
                    logger.warning(f"⚠️  Failed to sync contradiction {fact1_id}-{fact2_id}: {e}")
                    continue
            
            if synced_count > 0:
                logger.info(f"✅ Synced {synced_count} contradictions to Neo4j")
    
    except Exception as e:
        logger.error(f"❌ Neo4j sync failed: {e}")


def detect_contradictions(backfill=False, days_back=None):
    """
    Unified contradiction detection with optional backfill.
    
    Args:
        backfill: If True, reprocess ALL facts (not just recent)
        days_back: If set, reprocess facts created in last N days
    
    Returns:
        tuple: (new_contradictions_count, failed_checks)
    """
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        logger.info("🔍 Starting Unified Contradiction Detection (DeBERTa-MNLI)...")
        
        # ===== STAGE 1: GET HIGH-VALUE FACTS =====
        # High-value = from trusted sources, created > 7 days ago (established truth)
        
        logger.info("📚 Stage 1: Loading high-value baseline facts...")
        
        cur.execute("""
            SELECT f.id, f.subject, f.predicate, f.object, f.embedding, a.trust_score
            FROM extracted_facts f
            JOIN articles a ON f.article_id = a.id
            WHERE a.trust_score >= 0.8
              AND f.created_at < NOW() - INTERVAL '7 days'
              AND f.is_original = TRUE
            ORDER BY f.created_at DESC
            LIMIT 500
        """)
        
        high_value_facts = cur.fetchall()
        logger.info(f"📚 Loaded {len(high_value_facts)} high-value baseline facts")
        
        if not high_value_facts:
            logger.warning("⚠️  No high-value facts found for comparison")
        
        # ===== STAGE 2: GET FACTS TO CHECK =====
        # Determine which facts to check based on backfill mode
        
        if backfill:
            logger.info("🔄 BACKFILL MODE: Processing ALL facts (no time limit)")
            cur.execute("""
                SELECT f.id, f.subject, f.predicate, f.object, f.embedding, a.trust_score
                FROM extracted_facts f
                JOIN articles a ON f.article_id = a.id
                WHERE f.is_original = TRUE
                ORDER BY f.created_at DESC
            """)
        elif days_back:
            logger.info(f"🔄 Processing facts from last {days_back} days...")
            cur.execute("""
                SELECT f.id, f.subject, f.predicate, f.object, f.embedding, a.trust_score
                FROM extracted_facts f
                JOIN articles a ON f.article_id = a.id
                WHERE f.is_original = TRUE
                  AND f.created_at >= NOW() - INTERVAL '%s days'
                ORDER BY f.created_at DESC
            """, (days_back,))
        else:
            # Default: Last 24 hours (new facts)
            logger.info("🆕 Processing NEW facts (last 24 hours)...")
            cur.execute("""
                SELECT f.id, f.subject, f.predicate, f.object, f.embedding, a.trust_score
                FROM extracted_facts f
                JOIN articles a ON f.article_id = a.id
                WHERE f.is_original = TRUE
                  AND f.created_at >= NOW() - INTERVAL '1 day'
                ORDER BY f.created_at DESC
            """)
        
        facts_to_check = cur.fetchall()
        
        if not facts_to_check:
            logger.info("✅ No facts to check. Exiting.")
            return (0, 0)
        
        logger.info(f"🔎 Checking {len(facts_to_check)} facts...")
        
        # ===== STAGE 3: RUN CONTRADICTION DETECTION =====
        
        new_contradictions = []
        failed_checks = 0
        
        for check_fact in facts_to_check:
            check_id, check_subj, check_pred, check_obj, _, _ = check_fact
            check_text = f"{check_subj} {check_pred} {check_obj}"
            
            for base_fact in high_value_facts:
                base_id, base_subj, base_pred, base_obj, _, _ = base_fact
                base_text = f"{base_subj} {base_pred} {base_obj}"
                
                # Skip if same fact
                if check_id == base_id:
                    continue
                
                # Query DeBERTa
                scores = query_deberta(base_text, check_text)
                
                if scores is None:
                    failed_checks += 1
                    continue
                
                # Check if contradiction score > threshold
                contradiction_score = scores.get('contradiction', 0)
                
                if contradiction_score > 0.7:
                    new_contradictions.append((
                        check_id,
                        base_id,
                        float(contradiction_score)
                    ))
                    logger.debug(f"⚠️  Contradiction detected: {check_id} vs {base_id} (score: {contradiction_score:.2f})")
        
        logger.info(f"🔎 Contradiction detection complete: {len(new_contradictions)} found, {failed_checks} failed checks")
        
        # ===== STAGE 4: SAVE TO POSTGRESQL =====
        
        if new_contradictions:
            logger.info("💾 Saving contradictions to PostgreSQL...")
            
            save_count = 0
            for fact1_id, fact2_id, score in new_contradictions:
                try:
                    cur.execute("""
                        INSERT INTO contradiction_relationships 
                        (fact1_id, fact2_id, contradiction_score, detected_at, detection_method)
                        VALUES (%s, %s, %s, NOW(), 'DeBERTa-MNLI')
                        ON CONFLICT (fact1_id, fact2_id) 
                        DO UPDATE SET 
                            contradiction_score = EXCLUDED.contradiction_score,
                            updated_at = NOW()
                    """, (fact1_id, fact2_id, score))
                    save_count += 1
                    
                except psycopg2.Error as e:
                    logger.warning(f"⚠️  Failed to save contradiction {fact1_id}-{fact2_id}: {e}")
                    continue
            
            conn.commit()
            logger.info(f"✅ Saved {save_count} contradictions to PostgreSQL")
        
        # ===== STAGE 5: SYNC TO NEO4J =====
        
        if new_contradictions:
            neo4j_driver = get_neo4j_driver()
            sync_to_neo4j(neo4j_driver, new_contradictions)
            if neo4j_driver:
                neo4j_driver.close()
        
        return (len(new_contradictions), failed_checks)
    
    except Exception as e:
        logger.error(f"❌ Contradiction detection failed: {e}")
        return (0, 0)
    
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def backfill_historical_contradictions():
    """
    Backfill script to process ALL historical facts.
    Run once to populate contradiction_relationships for entire dataset.
    """
    logger.info("=" * 80)
    logger.info("BACKFILL MODE: Processing ALL historical facts")
    logger.info("=" * 80)
    
    new_count, failed = detect_contradictions(backfill=True)
    
    logger.info("=" * 80)
    logger.info(f"✅ Backfill Complete: {new_count} contradictions found, {failed} failed checks")
    logger.info("=" * 80)
    
    return new_count


if __name__ == "__main__":
    import sys
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--backfill":
            # Process all facts
            backfill_historical_contradictions()
        elif sys.argv[1] == "--days":
            # Process last N days
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            logger.info(f"Processing facts from last {days} days...")
            count, failed = detect_contradictions(days_back=days)
            logger.info(f"✅ Found {count} contradictions ({failed} failed checks)")
        else:
            print("Usage:")
            print("  python detect_contradictions_unified.py           # Process last 24 hours (default)")
            print("  python detect_contradictions_unified.py --backfill # Process all facts")
            print("  python detect_contradictions_unified.py --days N   # Process last N days")
    else:
        # Default: Process last 24 hours
        count, failed = detect_contradictions()
        logger.info(f"✅ Found {count} contradictions ({failed} failed checks)")
