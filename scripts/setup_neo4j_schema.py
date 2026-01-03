"""
Neo4j Schema Setup
Creates indexes and constraints for Truth Graph optimal performance.
Run this AFTER successful connection test.
"""

import os
from dotenv import load_dotenv
from db_utils import get_neo4j_driver, close_neo4j_driver

load_dotenv('server/.env')

print("=" * 60)
print("Neo4j Schema Setup - Creating Indexes & Constraints")
print("=" * 60)

try:
    driver = get_neo4j_driver()
    
    with driver.session() as session:
        print("\n🔄 Creating indexes and constraints...\n")
        
        # 1. Unique constraint on Fact IDs
        print("1. Creating unique constraint on Fact IDs...")
        try:
            session.run("CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE")
            print("   [OK] Fact ID constraint created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 2. Text index for fact search (Fulltext for relevance)
        print("2. Creating fulltext index for fact search...")
        try:
            # Creating FULLTEXT index
            session.run("CREATE FULLTEXT INDEX fact_statement_fulltext IF NOT EXISTS FOR (f:Fact) ON EACH [f.text]")
            print("   [OK] Fact fulltext index created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 3. Index for article IDs
        print("3. Creating index for article IDs...")
        try:
            session.run("CREATE INDEX article_id_idx IF NOT EXISTS FOR (a:Article) ON (a.id)")
            print("   [OK] Article ID index created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 4. Index for source URLs
        print("4. Creating index for source URLs...")
        try:
            session.run("CREATE INDEX source_url_idx IF NOT EXISTS FOR (s:Source) ON (s.url)")
            print("   [OK] Source URL index created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 5. Index for entity names
        print("5. Creating index for entity names...")
        try:
            session.run("CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            print("   [OK] Entity name index created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 6. Index for fact confidence scores
        print("6. Creating index for fact confidence...")
        try:
            session.run("CREATE INDEX fact_confidence_idx IF NOT EXISTS FOR (f:Fact) ON (f.confidence)")
            print("   [OK] Fact confidence index created")
        except Exception as e:
            print(f"   [WARN]  {e}")
        
        # 7. Verify all indexes
        print("\n📊 Verifying indexes...")
        result = session.run("SHOW INDEXES")
        index_count = 0
        for record in result:
            index_count += 1
            print(f"   - {record['name']}: {record['type']} on {record['labelsOrTypes']}")
        
        print(f"\n[OK] Total indexes created: {index_count}")
        print("\n" + "=" * 60)
        print("[OK] Schema setup complete! Database ready for backfill.")
        print("=" * 60)
    
    close_neo4j_driver()
    
except Exception as e:
    print(f"\n[ERROR] Schema setup failed: {e}")
    print("\nMake sure connection test passes first:")
    print("  python scripts/test_neo4j_connection.py")
