"""
Database Cleanup Script: Remove claims with corrupted (zero-vector) embeddings.

Run this BEFORE running link_claims.py after deploying the fixes.
This ensures the database is clean and ready for valid embeddings.
"""

import os
import logging
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv('server/.env')

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

def cleanup_corrupted_embeddings():
    """Remove claims with zero or invalid embeddings without deleting the claims themselves."""
    logger.info("🔌 Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    try:
        with driver.session() as session:
            # Find claims with zero embeddings
            logger.info("🔍 Scanning for corrupted embeddings...")
            result = session.run("""
                MATCH (c:Claim)
                WHERE c.embedding IS NOT NULL
                WITH c, c.embedding AS emb
                WHERE size(emb) = 384 AND ALL(x IN emb WHERE x = 0.0)
                RETURN count(c) AS zero_count
            """)
            
            zero_count = result.single()['zero_count']
            logger.info(f"📊 Found {zero_count} claims with zero embeddings")
            
            if zero_count > 0:
                # Remove zero embeddings (keep claims, just remove corrupted data)
                logger.info("🧹 Removing corrupted embeddings...")
                result = session.run("""
                    MATCH (c:Claim)
                    WHERE c.embedding IS NOT NULL
                    WITH c, c.embedding AS emb
                    WHERE size(emb) = 384 AND ALL(x IN emb WHERE x = 0.0)
                    SET c.embedding = NULL
                    RETURN count(c) AS cleaned_count
                """)
                
                cleaned_count = result.single()['cleaned_count']
                logger.info(f"✅ Cleaned {cleaned_count} corrupted embeddings")
                logger.info("💡 These claims will be reprocessed in the next link_claims.py run")
            else:
                logger.info("✅ No corrupted embeddings found - database is clean")
            
            # Also check for and remove any invalid dimension embeddings
            logger.info("🔍 Checking for invalid dimension embeddings...")
            result = session.run("""
                MATCH (c:Claim)
                WHERE c.embedding IS NOT NULL
                WITH c, c.embedding AS emb
                WHERE size(emb) <> 384
                RETURN count(c) AS invalid_dim_count
            """)
            
            invalid_count = result.single()['invalid_dim_count']
            if invalid_count > 0:
                logger.warning(f"⚠️ Found {invalid_count} claims with invalid embedding dimensions")
                session.run("""
                    MATCH (c:Claim)
                    WHERE c.embedding IS NOT NULL
                    WITH c, c.embedding AS emb
                    WHERE size(emb) <> 384
                    SET c.embedding = NULL
                """)
                logger.info(f"✅ Removed {invalid_count} invalid dimension embeddings")
    
    finally:
        driver.close()
        logger.info("🔌 Connection closed")

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Database Cleanup - Remove Corrupted Embeddings")
    logger.info("=" * 60)
    cleanup_corrupted_embeddings()
    logger.info("=" * 60)
    logger.info("✅ Cleanup complete. Database is ready for link_claims.py")
    logger.info("=" * 60)
