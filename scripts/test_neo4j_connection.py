"""
Test Neo4j Aura Connection
Validates cloud database connectivity before proceeding with backfill.
"""

from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

# Load server environment variables
load_dotenv('server/.env')

NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USER = os.getenv('NEO4J_USER')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')

print("=" * 60)
print("Neo4j Aura Connection Test")
print("=" * 60)
print(f"\nURI: {NEO4J_URI}")
print(f"User: {NEO4J_USER}")
print(f"Password: {NEO4J_PASSWORD[:4]}..." if NEO4J_PASSWORD else "Password: None")

try:
    print("\n🔄 Connecting to Neo4j Aura...")
    
    # Aura-specific driver configuration
    driver = GraphDatabase.driver(
        NEO4J_URI, 
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        max_connection_lifetime=3600,
        max_connection_pool_size=50,
        connection_acquisition_timeout=120
    )
    
    # Test connection with explicit database
    with driver.session(database="neo4j") as session:
        result = session.run("RETURN 'Connection successful!' as message, datetime() as time")
        record = result.single()
        
        print(f"\n✅ SUCCESS!")
        print(f"   {record['message']}")
        print(f"   Server time: {record['time']}")
        
        # Get database info
        result = session.run("""
            CALL dbms.components() YIELD name, versions, edition
            RETURN name, versions[0] as version, edition
        """)
        for record in result:
            print(f"   {record['name']}: {record['version']} ({record['edition']})")
        
        # Check existing data
        result = session.run("MATCH (n) RETURN COUNT(n) as node_count")
        count = result.single()['node_count']
        print(f"\n📊 Current graph size: {count} nodes")
        
        if count == 0:
            print("   ✅ Empty graph - ready for backfill!")
        else:
            print(f"   ⚠️  Graph has existing data")
    
    driver.close()
    print("\n" + "=" * 60)
    print("✅ Neo4j Aura is ready!")
    print("=" * 60)
    
except Exception as e:
    print(f"\n❌ Connection failed: {e}")
    print(f"\nFull error: {type(e).__name__}")
    import traceback
    traceback.print_exc()
    print("\nTroubleshooting:")
    print("1. Verify instance is 'Running' in console.neo4j.io")
    print("2. Check credentials match exactly")
    print("3. Ensure URI starts with neo4j+s://")
    print("4. Try upgrading driver: pip install --upgrade neo4j")
