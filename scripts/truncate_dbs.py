import os
import psycopg2
from neo4j import GraphDatabase
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, 'ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

def truncate_postgres():
    print("Truncating PostgreSQL databases...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        print("- Truncating tables via CASCADE (empties everything)...")
        # CASCADE will truncate all tables that reference these tables.
        # Sources -> raw_urls -> raw_articles -> extracted_claims -> claim_provenance
        cursor.execute("TRUNCATE TABLE sources, raw_urls, raw_articles, article_categories, extracted_claims, graph_outbox RESTART IDENTITY CASCADE;")
        
        print("PostgreSQL tables truncated successfully!")
    except psycopg2.Error as e:
        print(f"PostgreSQL connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

def wipe_neo4j():
    print("Wiping Neo4j Graph...")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            # Delete all nodes and relationships
            session.run("MATCH (n) DETACH DELETE n")
            print("Neo4j wiped successfully!")
        driver.close()
    except Exception as e:
        print(f"Neo4j connection error: {e}")

if __name__ == "__main__":
    print(f"WARNING: This will permanently wipe all staging data in PostgreSQL and the entire Neo4j graph.")
    print(f"Schemas and constraints will be preserved. Sources will be re-seeded automatically.")
    
    truncate_postgres()
    wipe_neo4j()
    
    # Re-seed sources immediately after truncation so the pipeline doesn't
    # hit FK constraint violations on raw_urls.source_id on the next run.
    print("\nRe-seeding sources...")
    import sys
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
    from add_local_sources import add_local_sources
    from add_extended_sources import add_extended_sources
    add_local_sources()
    add_extended_sources()
    print("\nDone! Databases are clean and sources are re-seeded.")
