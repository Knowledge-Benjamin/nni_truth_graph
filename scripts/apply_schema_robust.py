import os
import time
from dotenv import load_dotenv
from neo4j import GraphDatabase, TRUST_ALL_CERTIFICATES

# Load server env
load_dotenv('server/.env')

URI = os.getenv('NEO4J_URI')
USER = os.getenv('NEO4J_USER')
PASSWORD = os.getenv('NEO4J_PASSWORD')

print(f"Target URI: {URI}")

def apply_schema(driver):
    with driver.session() as session:
        print("Creating Fulltext Index...")
        session.run("CREATE FULLTEXT INDEX claim_statement_fulltext IF NOT EXISTS FOR (c:Claim) ON EACH [c.statement]")
        print("✅ Index created successfully!")

# Try standard
try:
    print("Attempting standard connection...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    driver.verify_connectivity()
    apply_schema(driver)
    driver.close()
    exit(0)
except Exception as e:
    print(f"Standard connection failed: {e}")

# Try relaxed SSL (common fix for Windows python envs with Aura)
try:
    print("Attempting relaxed SSL connection...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD), encrypted=True, trust=TRUST_ALL_CERTIFICATES)
    driver.verify_connectivity()
    apply_schema(driver)
    driver.close()
    print("✅ Success with relaxed SSL.")
    exit(0)
except Exception as e:
    print(f"Relaxed SSL connection failed: {e}")
    exit(1)
