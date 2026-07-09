# pyre-ignore-all-errors
import os
import time
import json
import sys
from typing import List
from pydantic import BaseModel  # type: ignore

# Make console logging safe on Windows terminals that use cp1252 instead of UTF-8.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

# Resolve up to the ai_engine directory so 'utils.groq_pool' becomes importable
current_dir = os.path.dirname(os.path.abspath(__file__))
ai_engine_dir = os.path.dirname(current_dir)
if ai_engine_dir not in sys.path:
    sys.path.insert(0, ai_engine_dir)




from dotenv import load_dotenv  # type: ignore
from neo4j import GraphDatabase  # type: ignore

# Load environment logic mimicking hf_pool / server environment
env_path = os.path.join(ai_engine_dir, '.env')
load_dotenv(env_path)
load_dotenv()  # also load from current-working directory for safety

from core.llm_router import llm_pool as groq_pool  # type: ignore  # unified multi-provider LLM router

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

class OntologyResponse(BaseModel):
    is_a: List[str]
    part_of: List[str]
    epistemic_domain: str

ONTOLOGY_PROMPT = """You are an elite, purely deterministic Ontology Classification Engine.
Your sole purpose is to classify the provided Entity name into its direct hypernyms (IS_A), physical/conceptual containers (PART_OF), and its core Epistemic Domain.

Rules:
1. "is_a": The broader class the entity belongs to (e.g., Apple IS_A Fruit). Capitalize the first letter.
2. "part_of": A strictly physical, geographical, or structural container the entity resides inside (e.g., Paris PART_OF France, Finger PART_OF Hand). Capitalize the first letter.
3. If an entity is too abstract to be "part of" something, leave the array empty [].
4. "epistemic_domain": You MUST classify the entity into one of these EXACT four ontological domains:
   - 'EMPIRICAL': Science, History, Physical Geography, Fact-checked items (e.g. Earth, Carbon, World War 2).
   - 'THEOLOGICAL': Religious texts, divinities, spiritual concepts (e.g. God, Bible, Quran, Angels).
   - 'PHILOSOPHICAL': Normative claims, ethics, subjective reasoning, abstract theories.
   - 'LEXICAL': Language tools, dictionaries (e.g. Adjective).

FORMATTING INSTRUCTION:
You are a JSON-only API. Output ONLY valid JSON matching the exact schema requested. No conversational text, no markdown code blocks (do not wrap in ```json), no preambles, and no trailing comments.
"""

def fetch_orphaned_entities(limit=50):
    """
    Find Entities that have NO outgoing standard hierarchical relationships (IS_A or PART_OF).
    It ensures we do not re-process nodes that have already been classified.
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE NOT (e)-[:IS_A|PART_OF]->()
            AND e.name <> 'World'
            AND (e.ontology_last_attempt IS NULL OR e.ontology_last_attempt < datetime() - duration({minutes: 10}))
            AND coalesce(e.ontology_failure_count, 0) < 3
            WITH e, rand() as r
            ORDER BY r
            LIMIT $limit
            RETURN e.name as name
        """, limit=limit)
        return [record["name"] for record in result]

def build_ontology_edges(entity_name, classification):
    """
    Execute cypher to natively bind the orphaned entity to its newly discovered parent classes
    """
    domain = classification.get("epistemic_domain", "EMPIRICAL")
    with driver.session() as session:
        # 0. Set the NOMA Epistemic Domain
        session.run("""
            MATCH (e:Entity {name: $name})
            SET e.epistemic_domain = $domain
        """, name=entity_name, domain=domain)
        
        # 1. Forge IS_A ties
        for parent_class in classification.get("is_a", []):
            session.run("""
                MATCH (child:Entity {name: $child_name})
                MERGE (parent:Entity {name: $parent_name})
                  ON CREATE SET parent.created_at = datetime(), parent.mention_count = 1
                MERGE (child)-[:IS_A]->(parent)
                MERGE (parent)-[:SUBCLASS_OF]->(child)
            """, child_name=entity_name, parent_name=parent_class)
            
        # 2. Forge PART_OF / CONTAINS ties
        for container in classification.get("part_of", []):
            session.run("""
                MATCH (child:Entity {name: $child_name})
                MERGE (parent:Entity {name: $parent_name})
                  ON CREATE SET parent.created_at = datetime(), parent.mention_count = 1
                MERGE (child)-[:PART_OF]->(parent)
                MERGE (parent)-[:CONTAINS]->(child)
            """, child_name=entity_name, parent_name=container)


def mark_ontology_attempt(entity_name, succeeded=False):
    """Mark an ontology attempt to avoid retry loops on repeatedly failing entities."""
    with driver.session() as session:
        if succeeded:
            session.run(
                """
                MATCH (e:Entity {name: $name})
                SET e.ontology_last_attempt = datetime(),
                    e.ontology_last_success = datetime(),
                    e.ontology_failure_count = 0
                """,
                name=entity_name,
            )
        else:
            session.run(
                """
                MATCH (e:Entity {name: $name})
                SET e.ontology_last_attempt = datetime(),
                    e.ontology_failure_count = coalesce(e.ontology_failure_count, 0) + 1
                """,
                name=entity_name,
            )

def run_evaluation_cycle():
    """Main daemon loop for continuously evaluating the graph."""
    print("[Ontology Daemon] Waking up to scan for orphaned Entities...")
    orphans = fetch_orphaned_entities()
    
    if not orphans:
        print("[Ontology Daemon] No orphaned entities found. Sleeping.")
        return False
        
    print(f"[Ontology Daemon] Found {len(orphans)} orphans. Beginning LLM Classification...")
    for idx, entity_name in enumerate(orphans):
        messages = [
            {"role": "system", "content": ONTOLOGY_PROMPT},
            {"role": "user", "content": f"Classify the entity: {entity_name}"}
        ]
        
        try:
            print(f"  [{idx+1}/{len(orphans)}] Classifying: '{entity_name}'...")

            # Temperature 0.0 for strict deterministic categorization using Pydantic validation via instructor
            ontology_data: OntologyResponse = groq_pool.chat_completions_create(
                messages=messages,
                model="TIER_HEAVY",
                temperature=0.0,
                response_model=OntologyResponse
            )

            # Defensive guard for malformed provider responses
            if not ontology_data or not hasattr(ontology_data, 'is_a') or not hasattr(ontology_data, 'part_of'):
                raise ValueError(f"Invalid ontology response object: {ontology_data}")

            print(f"    -> IS_A: {ontology_data.is_a} | PART_OF: {ontology_data.part_of} | DOMAIN: {ontology_data.epistemic_domain}")

            # Convert the validated Pydantic object to a standard dict for the build logic
            build_ontology_edges(entity_name, ontology_data.model_dump())

            # Mark success so this entity is not reprocessed immediately
            mark_ontology_attempt(entity_name, succeeded=True)

        except Exception as e:
            # Add extra handling for empty/invalid choices results from a provider response
            if 'list index out of range' in str(e) or 'choices' in str(e):
                print(f"    [Error] Provider returned malformed LLM output for '{entity_name}': {e}")

                # Log the live router metrics for a debug snapshot
                try:
                    print(f"    [Debug] LLM router universal pool size: {len(groq_pool.clients_universal)}")
                    cooled = sum(1 for c in groq_pool.clients_universal if c.is_cooling_down())
                    print(f"    [Debug] Cooling keys: {cooled} / {len(groq_pool.clients_universal)}")
                except Exception as debug_e:
                    print(f"    [Debug] Could not collect groq_pool internal debug info: {debug_e}")
            else:
                print(f"    [Error] Failed to classify '{entity_name}': {e}")

            # Track failures so the scheduler can back off and avoid starvation.
            mark_ontology_attempt(entity_name, succeeded=False)
            
    return True

if __name__ == "__main__":
    print("==================================================")
    print("= TRUTH GRAPH UNIVERSAL ONTOLOGY ENGINE STARTED  =")
    print("==================================================")
    while True:
        processed_any = run_evaluation_cycle()
        if not processed_any:
            # Sleep 15 seconds before scanning again
            time.sleep(15)
        else:
            # Brief cooldown to prevent spamming
            time.sleep(2)
