import os
import sys
import json
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.hf_pool import hf_pool

NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

HF_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"
HF_EMBED_URL   = f"https://router.huggingface.co/hf-inference/models/{HF_EMBED_MODEL}/pipeline/feature-extraction"

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def _embed_text(text: str) -> list | None:
    """Generate 768-D embeddings leveraging the resilient HuggingFace rotating token pool."""
    print(f"      [Debug] Requesting embedding for: '{text[:30]}...'")
    try:
        res = hf_pool.embed(text, HF_EMBED_URL)
        print(f"      [Debug] Embedding complete.")
        return res
    except Exception as e:
        print(f"      [Debug] Embedding failed: {e}")
        return None

def ingest_book(book_data: dict):
    """
    Ingests one Book of the Bible chapter by chapter.
    """
    book_name = book_data.get('name', 'Unknown Book')
    chapters = book_data.get('chapters', [])
    
    print(f"\n--- Ingesting Book: {book_name} (Total {len(chapters)} chapters) ---")

    for c_idx, chapter in enumerate(chapters):
        chapter_num = c_idx + 1
        print(f"  Processing Chapter {chapter_num} ({len(chapter)} verses)...")
        
        for v_idx, verse_text in enumerate(chapter):
            verse_num = v_idx + 1
            subject_name = f"{book_name} {chapter_num}:{verse_num}"  # "Genesis 1:1"
            
            with neo4j_driver.session() as session:
                # --- Resumption Logic Check ---
                # Verify if this Exact Chapter:Verse Claim is already completely processed
                existing = session.run("MATCH (c:Claim {subject: $subject}) RETURN count(c) as count", subject=subject_name).single()
                if existing and existing["count"] > 0:
                    continue  # Skip embedding and Neo4j writing. It's already done!
                
                # We only reach here if the verse is completely new.
                # Embed the verse for Hybrid Semantic Search
                embedding = _embed_text(verse_text)
                
                claim = {
                    "id": str(uuid.uuid4()),
                    "subject": subject_name,

                "predicate": "STATES",
                "object": verse_text,
                "score": 1.0, 
                "embedding": embedding,
                "quote": verse_text,
                "source": "King James Version (KJV)",
                "article_title": book_name
            }
            
                try:
                    session.run("""
                        MERGE (s:Entity {name: $subject})
                          ON CREATE SET s.created_at = datetime(), s.mention_count = 1
                          ON MATCH  SET s.mention_count = s.mention_count + 1
                          
                        MERGE (o:Entity {name: $object})
                          ON CREATE SET o.created_at = datetime(), o.mention_count = 1
                          ON MATCH  SET o.mention_count = o.mention_count + 1
                          
                        MERGE (src:Source {name: $source_name})
                          ON CREATE SET src.epistemic_trust = 1.0, src.tier = 'Tier 1'
                          
                        MERGE (a:Article {url: $url})
                          ON CREATE SET a.title = $article_title, a.created_at = datetime()
                        WITH a, src, s, o
                        MERGE (a)-[:PUBLISHED_BY]->(src)
                        
                        MERGE (claim:Claim {
                            subject: $subject,
                            predicate: $predicate,
                            object: $object
                        })
                          ON CREATE SET
                            claim.id = $id,
                            claim.epistemic_score = $score,
                            claim.extraction_confidence = 1.0,
                            claim.is_verifiable = true,
                            claim.created_at = datetime(),
                            claim.is_current = true,
                            claim.lifecycle = 'ACTIVE',
                            claim.quote_context = $quote,
                            claim.article_title = $article_title,
                            claim.source_name = $source_name,
                            claim.embedding = $embedding
                            
                        WITH claim, s, o, a
                        MERGE (claim)-[:HAS_SUBJECT]->(s)
                        MERGE (claim)-[:HAS_OBJECT]->(o)
                        MERGE (claim)-[:EXTRACTED_FROM]->(a)
                        MERGE (s)-[:PREDICATE {type: 'STATES', epistemic_score: 1.0, is_current: true}]->(o)
                    """,
                        subject=claim["subject"],
                        predicate=claim["predicate"],
                        object=claim["object"],
                        id=claim["id"],
                        score=claim["score"],
                        quote=claim["quote"],
                        article_title=claim["article_title"],
                        source_name=claim["source"],
                        embedding=claim["embedding"],
                        url=f"bible://kjv/{book_name.lower().replace(' ', '')}"
                    )
                except Exception as e:
                    print(f"      - Error merging {subject_name}: {e}")
            if verse_num % 10 == 0:
                 print(f"    ...merged up to verse {verse_num}")
        print(f"  ✓ Chapter {chapter_num} Complete.")

if __name__ == "__main__":
    json_path = os.path.join(os.path.dirname(__file__), 'en_kjv.json')
    
    if not os.path.exists(json_path):
        print(f"Bible JSON file not found at {json_path}")
        sys.exit(1)
        
    with open(json_path, 'r', encoding='utf-8-sig') as f:
        books = json.load(f)
        
    print(f"Loaded {len(books)} Books of the KJV Bible.")
    
    print("WARNING: This script will ingest actual claim nodes. Press Ctrl+C within 5 seconds to cancel...")
    time.sleep(5)
    
    for book in books:
        ingest_book(book)
        
    print("Closing Neo4j Driver Connection...")
    neo4j_driver.close()
    print("Done.")
