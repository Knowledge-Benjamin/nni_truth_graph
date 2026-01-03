from dotenv import load_dotenv
from db_utils import get_neo4j_driver, close_neo4j_driver

# Load server env
load_dotenv('server/.env')

def apply_schema(driver):
    with driver.session() as session:
        print("Creating Fulltext Index...")
        session.run("CREATE FULLTEXT INDEX fact_statement_fulltext IF NOT EXISTS FOR (f:Fact) ON EACH [f.text]")
        print("✅ Index created successfully!")

# Use centralized driver (handles connection pooling and errors)
try:
    print("Connecting to Neo4j...")
    driver = get_neo4j_driver()
    driver.verify_connectivity()
    apply_schema(driver)
    close_neo4j_driver()
    print("✅ Schema applied successfully!")
except Exception as e:
    print(f"❌ Connection failed: {e}")
    exit(1)
