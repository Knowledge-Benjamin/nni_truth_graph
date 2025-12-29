import os
import sys
import time
import logging
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add parent directory to path to import ai_engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine.nlp_models import SemanticLinker, EntityExtractor

def get_embedding_with_retry(linker, text, max_retries=3):
    """Get embedding with exponential backoff retry for transient failures.
    
    Args:
        linker: SemanticLinker instance
        text: Claim text to embed
        max_retries: Maximum retry attempts
    
    Returns:
        Valid embedding or None if all retries fail
    """
    for attempt in range(max_retries):
        try:
            embedding = linker.get_embedding(text)
            
            # Success - return valid embedding
            if embedding is not None:
                return embedding
            
            # API returned None - retriable failure
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential: 1s, 2s, 4s
                logger.warning(f"Retry {attempt + 1}/{max_retries} in {wait_time}s...")
                time.sleep(wait_time)
        except Exception as e:
            logger.error(f"Embedding attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                raise
    
    return None  # All retries exhausted

def validate_embedding(embedding, expected_dim=384):
    """Validate that embedding is suitable for persistence.
    
    Args:
        embedding: Vector to validate
        expected_dim: Expected dimensionality
    
    Returns:
        (is_valid, error_message)
    """
    if embedding is None:
        return False, "Embedding is None"
    
    if not isinstance(embedding, list):
        return False, f"Embedding is not a list (type: {type(embedding)})"
    
    if len(embedding) != expected_dim:
        return False, f"Invalid dimension: {len(embedding)} (expected {expected_dim})"
    
    if all(v == 0.0 for v in embedding):
        return False, "Zero vector detected (corrupted data)"
    
    # Check for NaN or inf values
    if any(not isinstance(v, (int, float)) or v != v or abs(v) == float('inf') for v in embedding):
        return False, "Contains non-finite values (NaN/Inf)"
    
    return True, None

load_dotenv('server/.env')

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

# Initialize NLP Models
linker = SemanticLinker()
extractor = EntityExtractor()

def link_claims():
    logger.info("🔌 Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        # 1. Find claims without embeddings
        logger.info("🔍 Finding un-embedded claims...")
        result = session.run("""
            MATCH (c:Claim)
            WHERE c.embedding IS NULL
            RETURN c.id AS id, c.statement AS statement
            LIMIT 50
        """)
        
        claims = list(result)
        if not claims:
            logger.info("✅ All claims processed. No new links needed.")
            return

        logger.info(f"⚡ Processing {len(claims)} claims...")
        
        successful = 0
        skipped = 0
        
        for record in claims:
            claim_id = record['id']
            statement = record['statement']
            logger.info(f"   > Processing: {statement[:50]}...")
            
            # A. Generate Embedding with Retry
            embedding = get_embedding_with_retry(linker, statement)
            
            # B. Validate Embedding
            is_valid, error_msg = validate_embedding(embedding)
            if not is_valid:
                logger.warning(f"     ⚠️ Skipped: {error_msg}")
                skipped += 1
                continue
            
            # B. Extract Entities
            entities = extractor.extract_unique_entities(statement)
            
            
            # C. Update Claim Node (Only if embedding is valid)
            session.run("""
                MATCH (c:Claim {id: $id})
                SET c.embedding = $embedding
            """, id=claim_id, embedding=embedding)
            
            successful += 1
            
            # D. Link Entities
            for ent in entities:
                session.run("""
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET e.type = $type
                    WITH e
                    MATCH (c:Claim {id: $id})
                    MERGE (c)-[:MENTIONS]->(e)
                """, name=ent['text'], type=ent['type'], id=claim_id)
            
            # E. Find Similar Claims (Vector Search)
            # Using Neo4j Vector Index 'claim_embeddings'
            try:
                similar_result = session.run("""
                    CALL db.index.vector.queryNodes('claim_embeddings', 5, $embedding)
                    YIELD node, score
                    WHERE node.id <> $id AND score > 0.85
                    MATCH (c:Claim {id: $id})
                    MERGE (c)-[r:SIMILAR_TO]-(node)
                    ON CREATE SET r.score = score
                    RETURN node.statement, score
                """, id=claim_id, embedding=embedding)
                
                link_count = 0
                for sim in similar_result:
                    logger.info(f"     🔗 Linked to: {sim['node.statement'][:40]}... (Score: {sim['score']:.2f})")
                    link_count += 1
                    
                if link_count == 0:
                    logger.debug(f"     No similar claims found")
            except Exception as e:
                logger.warning(f"     ⚠️ Vector search failed: {e}")

    driver.close()
    logger.info(f"✅ Batch complete. Processed: {successful}, Skipped: {skipped}, Total: {len(claims)}")

if __name__ == "__main__":
    link_claims()
