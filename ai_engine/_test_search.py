import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
load_dotenv('.env')

driver = GraphDatabase.driver(os.getenv('NEO4J_URI'), auth=(os.getenv('NEO4J_USER'), os.getenv('NEO4J_PASSWORD')))
with driver.session() as session:
    res = session.run("MATCH (e:Entity) WHERE toLower(e.name) CONTAINS 'trump' RETURN e.name AS name, e.mention_count AS cnt ORDER BY cnt DESC")
    names = [(r['name'], r['cnt']) for r in res]
    print(f'Total entities matching trump: {len(names)}')
    for n in names[:10]:
        print(f'  - {n[0]}: {n[1]}')
    
    print('\nTotal entities in DB overall:')
    res2 = session.run("MATCH (e:Entity) RETURN count(e) AS cnt")
    print(res2.single()['cnt'])
driver.close()
