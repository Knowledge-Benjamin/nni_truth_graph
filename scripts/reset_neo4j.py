#!/usr/bin/env python3
"""Reset Neo4j database - Delete all nodes and relationships."""

import os
import sys
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '../server/.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

from db_utils import get_neo4j_driver

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def reset_neo4j():
    """Delete all nodes and relationships from Neo4j."""
    driver = None
    try:
        logger.info("Connecting to Neo4j...")
        driver = get_neo4j_driver()
        
        with driver.session() as session:
            logger.info("Deleting all nodes and relationships...")
            result = session.run("MATCH (n) DETACH DELETE n")
            
            logger.info("[SUCCESS] Neo4j database reset complete")
            logger.info("All nodes and relationships deleted")
            
    except Exception as e:
        logger.error(f"[ERROR] Failed to reset Neo4j: {type(e).__name__}: {str(e)}")
        return False
    finally:
        if driver:
            driver.close()
            logger.info("Neo4j driver closed")
    
    return True

if __name__ == "__main__":
    success = reset_neo4j()
    sys.exit(0 if success else 1)
