import os
import sys
import time
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Add parent directory to path to import ai_engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine.nlp_models import SemanticLinker, EntityExtractor

load_dotenv('server/.env')

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

# Initialize NLP Models
linker = SemanticLinker()
extractor = EntityExtractor()

def link_claims():
    print("🔌 Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        # 1. Find claims without embeddings
        print("🔍 Finding un-embedded claims...")
        result = session.run("""
            MATCH (c:Claim)
            WHERE c.embedding IS NULL
            RETURN c.id AS id, c.statement AS statement
            LIMIT 50
        """)
        
        claims = list(result)
        if not claims:
            print("✅ All claims processed. No new links needed.")
            return

        print(f"⚡ Processing {len(claims)} claims...")
        
        for record in claims:
            claim_id = record['id']
            statement = record['statement']
            print(f"   > Processing: {statement[:50]}...")
            
            # A. Generate Embedding
            embedding = linker.get_embedding(statement)
            
            # B. Extract Entities
            entities = extractor.extract_unique_entities(statement)
            
            # C. Update Claim Node
            session.run("""
                MATCH (c:Claim {id: $id})
                SET c.embedding = $embedding
            """, id=claim_id, embedding=embedding)
            
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
                
                for sim in similar_result:
                    print(f"     🔗 Linked to: {sim['node.statement'][:40]}... (Score: {sim['score']:.2f})")
            except Exception as e:
                print(f"     ⚠️ Vector search failed (Index missing?): {e}")

    driver.close()
    print("✅ Batch complete.")

if __name__ == "__main__":
    link_claims()
