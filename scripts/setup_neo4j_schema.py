import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Load from ai_engine/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

def setup_neo4j_schema():
    print("Connecting to Neo4j to setup Epistemic Graph schemas...")
    
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        
        with driver.session() as session:
            # 1. Entity Constraint
            print("- Setting up Entity constraints...")
            session.run("""
                CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE
            """)
            
            # 2. Claim Constraint
            print("- Setting up Claim constraints...")
            session.run("""
                CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim) REQUIRE c.id IS UNIQUE
            """)
            
            # 3. Source Evidence Constraint
            print("- Setting up Source Evidence constraints...")
            session.run("""
                CREATE CONSTRAINT source_url IF NOT EXISTS FOR (s:Source) REQUIRE s.url IS UNIQUE
            """)
            
            # 4. Indexes for faster matching during Cross-Reference stage
            print("- Setting up Indexes...")
            session.run("CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            session.run("CREATE INDEX claim_epistemic_idx IF NOT EXISTS FOR (c:Claim) ON (c.epistemic_score)")

        print("Neo4j Epistemic schema setup complete!")
        
    except Exception as e:
        print(f"Neo4j connection error: {e}")
    finally:
        if 'driver' in locals() and driver:
            driver.close()

if __name__ == "__main__":
    setup_neo4j_schema()
