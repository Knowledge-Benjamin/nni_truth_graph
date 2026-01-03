import psycopg2
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Environment
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
HF_TOKEN = os.getenv("HF_TOKEN")  # Hugging Face API token

# DeBERTa-MNLI API endpoint (Hugging Face Inference API)
# Switching to 'ynie/roberta-large-snli_mnli_fever_anli_R1_R2_R3-nli' (Pure NLI / Text Classification)
DEBERTA_API = "https://router.huggingface.co/hf-inference/models/ynie/roberta-large-snli_mnli_fever_anli_R1_R2_R3-nli"

def query_deberta(premise, hypothesis):
    """
    Query DeBERTa-MNLI to check if hypothesis contradicts premise.
    Returns: {'entailment': 0.X, 'neutral': 0.Y, 'contradiction': 0.Z}
    """
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # RoBERTa-based models use </s></s> as separator
    payload = {
        "inputs": f"{premise} </s></s> {hypothesis}"
    }
    
    try:
        response = requests.post(DEBERTA_API, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"❌ API Error {response.status_code}: {response.text}")
            return None
            
        result = response.json()
        
        # Parse DeBERTa output - usually a list of list of scores/labels
        # Format can be [[{'label': 'ENTAILMENT', 'score': 0.9}, ...]]
        if isinstance(result, list) and isinstance(result[0], list):
            result = result[0]
            
        scores = {item['label'].lower(): item['score'] for item in result}
        return scores
    except Exception as e:
        logger.error(f"DeBERTa Connection Failed: {e}")
        return None

def detect_contradictions():
    """
    Nightly job to detect contradictions between new facts and high-value existing facts.
    Strategy:
    1. Get "High-Value" facts (facts from trusted sources with trust_score >= 0.8)
    2. Get today's new facts
    3. For each new fact, find semantically similar candidates (vector search)
    4. Run DeBERTa-MNLI to check for contradictions
    5. Store contradiction in dedicated table
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        logger.info("🔍 Starting Contradiction Detection (DeBERTa-MNLI)...")
        
        # 1. Get High-Value Facts (from trusted sources, created > 7 days ago = established truth)
        cur.execute("""
            SELECT f.id, f.subject, f.predicate, f.object, f.embedding
            FROM extracted_facts f
            JOIN articles a ON f.article_id = a.id
            WHERE a.trust_score >= 0.8
              AND f.created_at < NOW() - INTERVAL '7 days'
              AND f.is_original = TRUE
            LIMIT 500
        """)
        
        high_value_facts = cur.fetchall()
        logger.info(f"📚 Loaded {len(high_value_facts)} high-value established facts.")
        
        # 2. Get New Facts (created in last 24 hours)
        cur.execute("""
            SELECT f.id, f.subject, f.predicate, f.object, f.embedding
            FROM extracted_facts f
            WHERE f.created_at >= NOW() - INTERVAL '1 day'
              AND f.is_original = TRUE
            LIMIT 100
        """)
        
        new_facts = cur.fetchall()
        
        if not new_facts:
            logger.info("✅ No new facts to check.")
            return
        
        logger.info(f"🆕 Checking {len(new_facts)} new facts...")
        
        contradiction_count = 0
        
        # 3. For each new fact, compare with similar established facts
        for new_id, new_subj, new_pred, new_obj, new_emb in new_facts:
            new_text = f"{new_subj} {new_pred} {new_obj}"
            
            # Convert embedding for vector search
            # Ensure it's a flat list string: "[0.1,0.2,...]"
            if isinstance(new_emb, str):
                embedding_str = new_emb
            else:
                embedding_str = str(list(new_emb)).replace(' ', '') # Remove spaces for cleaner SQL
            
            # Find semantically similar facts (not identical, but related)
            cur.execute("""
                SELECT id, subject, predicate, object
                FROM extracted_facts
                WHERE id != %s
                  AND embedding <=> %s::vector BETWEEN 0.05 AND 0.3
                  AND is_original = TRUE
                LIMIT 10
            """, (new_id, embedding_str))
            
            candidates = cur.fetchall()
            
            if not candidates:
                continue
            
            # 4. Run DeBERTa-MNLI on candidates
            for old_id, old_subj, old_pred, old_obj in candidates:
                old_text = f"{old_subj} {old_pred} {old_obj}"
                
                logger.info(f"   Comparing:\n      New: {new_text}\n      Old: {old_text}")
                
                scores = query_deberta(old_text, new_text)
                
                if not scores:
                    continue
                
                contradiction_score = scores.get('contradiction', 0.0)
                
                if contradiction_score > 0.7:  # High confidence contradiction
                    logger.warning(f"   🚨 CONTRADICTION DETECTED (confidence: {contradiction_score:.2f})")
                    
                    # Store contradiction
                    cur.execute("""
                        INSERT INTO extracted_contradictions (fact_id_1, fact_id_2, confidence, detected_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (fact_id_1, fact_id_2) DO NOTHING
                    """, (new_id, old_id, contradiction_score))
                    
                    contradiction_count += 1
        
        conn.commit()
        logger.info(f"✅ Contradiction Detection Complete. Found {contradiction_count} contradictions.")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    detect_contradictions()
