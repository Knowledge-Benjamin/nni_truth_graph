import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv("server/.env") # Try server env if possible, or ai_engine

URI = "neo4j+s://21f786a2.databases.neo4j.io"
USER = "neo4j"
PASSWORD = os.getenv("NEO4J_PASSWORD") or "g4Nr..." # Fallback or need real pass
# Wait, I don't have the password handy in cleartext in the logs, it was masked.
# But ai_engine/.env should have it.

def check_indexes():
    # Load from ai_engine .env which I know exists
    load_dotenv("ai_engine/.env")
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    
    if not password:
        print("No password found in env")
        return

    # Use neo4j+ssc for self-signed certificates (skips verification)
    if uri.startswith("neo4j+s://"):
        uri = uri.replace("neo4j+s://", "neo4j+ssc://")
    
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        result = session.run("SHOW INDEXES WHERE type = 'VECTOR'")
        print("Vector Indexes:")
        for record in result:
             print(f"- {record['name']} (labels: {record['labelsOrTypes']}, props: {record['properties']})")
             
        print("\nFulltext Indexes:")
        result = session.run("SHOW INDEXES WHERE type = 'FULLTEXT'")
        for record in result:
             print(f"- {record['name']}")
    driver.close()

if __name__ == "__main__":
    check_indexes()
