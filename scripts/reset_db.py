
import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Load env variables from server/.env
load_dotenv(os.path.join(os.path.dirname(__file__), '../server/.env'))

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

if not URI:
    print("❌ Error: NEO4J_URI not found in server/.env")
    sys.exit(1)

def reset_db():
    print("="*60)
    print(f"🔌 Connection Debug")
    print(f"   URI: {URI}")
    print(f"   User: {USER}")
    print(f"   Password: {'*' * len(PASSWORD) if PASSWORD else 'NOT SET'}")
    print("="*60)

    try:
        driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
        print("   Verifying connectivity...")
        driver.verify_connectivity()
        print("✅ Connectivity Verified!")
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        print("   Try checking your internet connection or firewall.")
        return
    
    confirm = input("\n⚠️  WARNING: This will DELETE ALL DATA in the database. Type 'DELETE' to confirm: ")
    if confirm.strip() != "DELETE":
        print("❌ Reset cancelled.")
        driver.close()
        return

    try:
        with driver.session() as session:
            print("🗑️  Deleting all nodes and relationships...")
            # Delete in batches to avoid transaction timeouts on large graphs
            result = session.run("""
                CALL {
                    MATCH (n)
                    DETACH DELETE n
                } IN TRANSACTIONS OF 1000 ROWS
            """)
            print("✅ Database successfully wiped.")
            
            # Re-create indexes immediately
            print("⚡ Re-creating indexes...")
            session.run("CREATE FULLTEXT INDEX claim_statement_fulltext IF NOT EXISTS FOR (c:Claim) ON EACH [c.statement]")
            
            # Create Vector Index (Standard)
            try:
                session.run("""
                  CREATE VECTOR INDEX claim_embeddings IF NOT EXISTS
                  FOR (c:Claim) ON (c.embedding)
                  OPTIONS {indexConfig: {
                    `vector.dimensions`: 384,
                    `vector.similarity_function`: 'cosine'
                  }}
                """)
                print("✅ Indexes restored.")
            except Exception as e:
                print(f"⚠️ Vector index creation warning: {e}")

    except Exception as e:
        print(f"❌ Error during reset: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    reset_db()
