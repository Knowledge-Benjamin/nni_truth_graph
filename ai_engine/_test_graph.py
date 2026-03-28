import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
load_dotenv('.env')

driver = GraphDatabase.driver(os.getenv('NEO4J_URI'), auth=(os.getenv('NEO4J_USER'), os.getenv('NEO4J_PASSWORD')))
with driver.session() as session:
    name = "Nasa"
    showAll = False
    
    q = """
    MATCH (focal:Entity {name: $name})

    // 1-hop: Claims that have this entity as subject or object
    OPTIONAL MATCH (c:Claim)-[:HAS_SUBJECT|HAS_OBJECT]->(focal)
    WHERE $showAll = true OR c.is_current = true

    // Linked entities via the claim
    OPTIONAL MATCH (c)-[:HAS_SUBJECT]->(subj:Entity)
    OPTIONAL MATCH (c)-[:HAS_OBJECT]->(obj:Entity)

    // Evidence attached to each claim
    OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)

    // Stance edges between claims
    OPTIONAL MATCH (c)-[stance:EVOLVES|CORROBORATED_BY|CONTRADICTS]->(other:Claim)
    WHERE $showAll = true OR other.is_current = true

    WITH focal,
            collect(DISTINCT c)    AS claims,
            collect(DISTINCT subj) AS subjects,
            collect(DISTINCT obj)  AS objects,
            collect(DISTINCT ev)   AS evidences,
            collect(DISTINCT {
                fromId:      c.id,
                toId:        other.id,
                type:        type(stance)
            }) AS stanceEdges

    RETURN focal.name AS focal,
            size(claims) AS claim_count,
            size(subjects) AS subj_count,
            size(objects) AS obj_count,
            size(evidences) AS ev_count,
            size(stanceEdges) AS stance_count
    """
    res = session.run(q, name=name, showAll=showAll)
    rec = res.single()
    if rec:
        print(f"Focal: {rec['focal']}")
        print(f"Claims: {rec['claim_count']}")
        print(f"Subj: {rec['subj_count']}")
        print(f"Obj: {rec['obj_count']}")
        print(f"Evid: {rec['ev_count']}")
        print(f"Stance: {rec['stance_count']}")
    else:
        print("Not found")
driver.close()
