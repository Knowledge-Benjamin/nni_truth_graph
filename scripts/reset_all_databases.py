#!/usr/bin/env python3
"""
COMPLETE DATABASE RESET - PostgreSQL + Neo4j
WARNING: This DELETES ALL DATA. Use only for full reset/reboot.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

print("=" * 70)
print("🔥 COMPLETE DATABASE RESET")
print("=" * 70)
print()
print("This will:")
print("  1. Drop and recreate all PostgreSQL tables")
print("  2. Clear all Neo4j nodes and relationships")
print("  3. Recreate all schemas from scratch")
print()
print("⚠️  Data WILL BE LOST.")
print()
confirm = input("Type 'yes' to continue or Ctrl+C to abort: ").strip()
if confirm != "yes":
    print("Aborted.")
    sys.exit(1)

print()

# ─── 1. PostgreSQL Reset ──────────────────────────────────────────────────────
print("🗑️  Resetting PostgreSQL...")
try:
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Drop all tables in cascade
    cursor.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname = 'public';
    """)
    tables = cursor.fetchall()
    if tables:
        table_list = ', '.join([t[0] for t in tables])
        cursor.execute(f"DROP TABLE IF EXISTS {table_list} CASCADE;")
        print(f"  ✓ Dropped {len(tables)} tables")
    else:
        print("  ✓ No tables to drop")
    
    cursor.close()
    conn.close()
except Exception as e:
    print(f"  ✗ PostgreSQL error: {e}")
    sys.exit(1)

# ─── 2. Neo4j Reset ──────────────────────────────────────────────────────────
print("🗑️  Resetting Neo4j...")
try:
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        result = session.run("MATCH (n) DETACH DELETE n")
        nodes_deleted = result.consume().counters.nodes_deleted
        print(f"  ✓ Deleted {nodes_deleted} nodes")
    driver.close()
except Exception as e:
    print(f"  ✗ Neo4j error: {e}")
    sys.exit(1)

# ─── 3. Recreate PostgreSQL Schema ─────────────────────────────────────────
print()
print("📋 Recreating PostgreSQL schema...")
try:
    # Import and run the setup script
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "setup_postgres_schema",
        os.path.join(os.path.dirname(__file__), "setup_postgres_schema.py")
    )
    setup_pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_pg)
    setup_pg.setup_postgres_schema()
except Exception as e:
    print(f"  ✗ PostgreSQL schema error: {e}")
    sys.exit(1)

# ─── 4. Recreate Neo4j Schema ─────────────────────────────────────────────
print()
print("📋 Recreating Neo4j schema...")
try:
    spec = importlib.util.spec_from_file_location(
        "setup_neo4j_schema",
        os.path.join(os.path.dirname(__file__), "setup_neo4j_schema.py")
    )
    setup_neo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_neo)
    setup_neo.setup_neo4j_schema()
except Exception as e:
    print(f"  ✗ Neo4j schema error: {e}")
    sys.exit(1)

print()
print("=" * 70)
print("✅ COMPLETE RESET FINISHED")
print("=" * 70)
print()
print("Databases ready for fresh start.")
print()
print("Next steps:")
print("  1. Restart the Node.js server")
print("  2. Server will auto-create graph_outbox on bootup")
print("  3. Ingest data via ai_engine/scripts/seed_*.py")
