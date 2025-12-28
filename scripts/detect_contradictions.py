"""
Contradiction Detection Script
Finds claims that cite the same sources but with opposite stances (SUPPORT vs CONTRADICT).
Creates CONTRADICTS relationships between such claims.
"""

import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Load environment
load_dotenv('server/.env')

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

def detect_contradictions():
    print("🔍 Detecting contradictions...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        # Find claims citing same source with opposite stances
        result = session.run("""
            MATCH (c1:Claim)-[r1:CITED_BY]->(s:Source)<-[r2:CITED_BY]-(c2:Claim)
            WHERE c1.id < c2.id
              AND r1.stance = 'SUPPORT' 
              AND r2.stance = 'CONTRADICT'
            WITH c1, c2, s, r1, r2
            MERGE (c1)-[con:CONTRADICTS {
                evidence_url: s.url,
                detected: datetime(),
                severity: abs(r1.confidence - r2.confidence)
            }]-(c2)
            RETURN c1.statement AS claim1, 
                   c2.statement AS claim2, 
                   s.publisher AS source
        """)
        
        count = 0
        for record in result:
            print(f"  ⚠️  Contradiction detected:")
            print(f"     Claim A: {record['claim1'][:60]}...")
            print(f"     Claim B: {record['claim2'][:60]}...")
            print(f"     Source: {record['source']}")
            count += 1
        
        if count == 0:
            print("  ✅ No contradictions found.")
        else:
            print(f"\n  🔗 Created {count} contradiction relationships.")
    
    driver.close()

if __name__ == "__main__":
    detect_contradictions()
