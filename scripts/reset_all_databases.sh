#!/bin/bash
# ============================================================================
# COMPLETE DATABASE RESET - PostgreSQL + Neo4j
# WARNING: This DELETES ALL DATA. Use only for full reset/reboot.
# ============================================================================

set -e

echo "🔥 COMPLETE DATABASE RESET STARTING..."
echo ""
echo "This will:"
echo "  1. Drop and recreate all PostgreSQL tables"
echo "  2. Clear all Neo4j nodes and relationships"
echo "  3. Recreate all schemas from scratch"
echo ""
echo "Data WILL BE LOST. Type 'yes' to continue or Ctrl+C to abort:"
read -p "> " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

cd "$(dirname "$0")/.."

# 1. Reset PostgreSQL
echo ""
echo "🗑️  Resetting PostgreSQL..."
python3 scripts/truncate_dbs.py

# 2. Reset Neo4j
echo ""
echo "🗑️  Resetting Neo4j..."
python3 << 'EOF'
import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), 'ai_engine/.env'))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
with driver.session() as session:
    session.run("MATCH (n) DETACH DELETE n")
    print("✓ Neo4j cleared")
driver.close()
EOF

# 3. Recreate PostgreSQL schema
echo ""
echo "📋 Recreating PostgreSQL schemas..."
python3 scripts/setup_postgres_schema.py

# 4. Recreate Neo4j schema
echo ""
echo "📋 Recreating Neo4j schemas..."
python3 scripts/setup_neo4j_schema.py

echo ""
echo "✅ COMPLETE RESET FINISHED"
echo "Databases ready for fresh start."
echo ""
echo "Next steps:"
echo "  1. Restart the Node.js server: docker-compose restart server"
echo "  2. Server will auto-create graph_outbox on bootup"
echo "  3. Ingest data via ai_engine/scripts/seed_*.py"
