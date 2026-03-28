import os
import sys
import json
import time
import uuid
import urllib.request
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

try:
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except Exception as e:
    print(f"Failed to connect to Neo4j: {e}")
    sys.exit(1)

def _embed_text(text: str) -> list | None:
    """Generate 768-D embeddings leveraging the resilient HuggingFace rotating token pool."""
    print(f"      [Debug] Requesting embedding for: '{text[:30]}...'")
    try:
        res = hf_pool.embed(text)
        print(f"      [Debug] Embedding complete.")
        return res
    except Exception as e:
        print(f"      [Debug] Embedding failed: {e}")
        return None

# ==============================================================================
# 1. BIBLE INGESTION
# ==============================================================================
def ingest_bible():
    print("\n==================================================")
    print("= STARTING BIBLE INGESTION (KJV)")
    print("==================================================")
    
    # Download JSON if missing
    json_path = os.path.join(os.path.dirname(__file__), 'en_kjv.json')
    if not os.path.exists(json_path):
        print(f"Downloading KJV JSON to {json_path}...")
        url = "https://raw.githubusercontent.com/thiagobodruk/bible/master/json/en_kjv.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("Downloaded.")
        except Exception as e:
            print(f"Failed to download JSON: {e}")
            return
            
    with open(json_path, 'r', encoding='utf-8-sig') as f:
        books = json.load(f)
        
    for book_data in books:
        book_name = book_data.get('name', 'Unknown Book')
        chapters = book_data.get('chapters', [])
        
        print(f"\n--- Ingesting Book: {book_name} (Total {len(chapters)} chapters) ---")

        for c_idx, chapter in enumerate(chapters):
            chapter_num = c_idx + 1
            print(f"  Processing Chapter {chapter_num} ({len(chapter)} verses)...")
            
            for v_idx, verse_text in enumerate(chapter):
                verse_num = v_idx + 1
                subject_name = f"{book_name} {chapter_num}:{verse_num}"
                
                with neo4j_driver.session() as session:
                    # Resumption Check: Is this verse already ingested?
                    existing = session.run("MATCH (c:Claim {subject: $subject}) RETURN count(c) as count", subject=subject_name).single()
                    if existing and existing["count"] > 0:
                        continue 
                    
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
                              
                            MERGE (text:Entity {name: 'Bible'})
                              ON CREATE SET text.type = 'Religious Text', text.created_at = datetime()
                              
                            MERGE (book:Entity {name: $book_name})
                              ON CREATE SET book.type = 'Book', book.created_at = datetime()

                            MERGE (src:Source {name: $source_name})
                              ON CREATE SET src.epistemic_trust = 1.0, src.tier = 'Tier 1'
                              
                            MERGE (a:Article {url: $url})
                              ON CREATE SET a.title = $article_title, a.created_at = datetime()
                              
                            WITH a, src, s, o, text, book
                            MERGE (a)-[:PUBLISHED_BY]->(src)
                            MERGE (text)-[:CONTAINS]->(book)
                            MERGE (book)-[:PART_OF]->(text)
                            MERGE (book)-[:CONTAINS]->(s)
                            MERGE (s)-[:PART_OF]->(book)
                            
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
                            book_name=book_name,
                            url=f"bible://kjv/{book_name.lower().replace(' ', '')}"
                        )
                    except Exception as e:
                        print(f"      - Error merging {subject_name}: {e}")
                if verse_num % 10 == 0:
                     print(f"    ...merged up to verse {verse_num}")
            print(f"  [OK] Chapter {chapter_num} Complete.")
    print("Bible Ingestion 100% Complete.")


# ==============================================================================
# 2. DICTIONARY INGESTION
# ==============================================================================
def ingest_dictionary():
    print("\n==================================================")
    print("= STARTING DICTIONARY INGESTION")
    print("==================================================")
    
    json_path = os.path.join(os.path.dirname(__file__), 'dictionary.json')
    if not os.path.exists(json_path):
        print(f"Downloading Webster's Dictionary to {json_path}...")
        url = "https://raw.githubusercontent.com/matthewreagan/WebstersEnglishDictionary/master/dictionary.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("Downloaded.")
        except Exception as e:
            print(f"Failed to download Dictionary: {e}")
            return

    with open(json_path, 'r', encoding='utf-8') as f:
        dictionary_data = json.load(f)
        
    print(f"Loaded {len(dictionary_data)} Words from the Dictionary.")
    
    count = 0
    for word, definition in dictionary_data.items():
        count += 1
        word = str(word).strip()
        definition = str(definition).strip()
        if not definition: 
            continue

        with neo4j_driver.session() as session:
            # Resumption Check
            existing = session.run("MATCH (c:Claim {subject: $subject, predicate: 'IS_DEFINED_AS'}) RETURN count(c) as count", subject=word).single()
            if existing and existing["count"] > 0:
                if count % 1000 == 0:
                    print(f"  ...Skipping already processed words (up to {count})")
                continue 

            embedding = _embed_text(definition)
            
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
                        predicate: 'IS_DEFINED_AS',
                        object: $object
                    })
                      ON CREATE SET
                        claim.id = $id,
                        claim.epistemic_score = 1.0,
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
                    MERGE (s)-[:PREDICATE {type: 'IS_DEFINED_AS', epistemic_score: 1.0, is_current: true}]->(o)
                """,
                    subject=word,
                    object=definition[:2000],  # Limit object size if it's too huge
                    id=str(uuid.uuid4()),
                    quote=definition,
                    article_title="Webster's English Dictionary",
                    source_name="Webster's English Dictionary",
                    embedding=embedding,
                    url=f"dictionary://webster/{word.lower().replace(' ', '')}"
                )
                print(f"  [OK] Ingested Word: {word}")
            except Exception as e:
                print(f"      - Error merging '{word}': {e}")
                
    print("Dictionary Ingestion 100% Complete.")

# ==============================================================================
# 3. ENCYCLOPEDIA INGESTION
# ==============================================================================
def ingest_encyclopedia():
    print("\n==================================================")
    print("= STARTING ENCYCLOPEDIA INGESTION (Geography)")
    print("==================================================")
    
    json_path = os.path.join(os.path.dirname(__file__), 'capitals.json')
    if not os.path.exists(json_path):
        print(f"Downloading World Capitals Dataset to {json_path}...")
        url = "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-capital-city.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("Downloaded.")
        except Exception as e:
            print(f"Failed to download Encyclopedia: {e}")
            return

    with open(json_path, 'r', encoding='utf-8') as f:
        geo_data = json.load(f)
        
    print(f"Loaded {len(geo_data)} Countries from the Encyclopedia.")
    
    count = 0
    for item in geo_data:
        country = item.get('country')
        city = item.get('city')
        
        if not country or not city:
            continue
            
        count += 1
        phrase = f"The capital city of {country} is {city}."
        
        with neo4j_driver.session() as session:
            # Resumption Check
            existing = session.run("MATCH (c:Claim {subject: $subject, predicate: 'HAS_CAPITAL_CITY'}) RETURN count(c) as count", subject=country).single()
            if existing and existing["count"] > 0:
                if count % 20 == 0:
                    print(f"  ...Skipping already processed countries (up to {count})")
                continue 

            embedding = _embed_text(phrase)
            
            try:
                session.run("""
                    MERGE (s:Entity {name: $subject})
                      ON CREATE SET s.created_at = datetime(), s.mention_count = 1
                      ON MATCH  SET s.mention_count = s.mention_count + 1
                      
                    MERGE (o:Entity {name: $object})
                      ON CREATE SET o.created_at = datetime(), o.mention_count = 1
                      ON MATCH  SET o.mention_count = o.mention_count + 1
                      
                    MERGE (src:Source {name: $source_name})
                      ON CREATE SET src.epistemic_trust = 0.9, src.tier = 'Tier 2'
                      
                    MERGE (a:Article {url: $url})
                      ON CREATE SET a.title = $article_title, a.created_at = datetime()
                    WITH a, src, s, o
                    MERGE (a)-[:PUBLISHED_BY]->(src)
                    
                    MERGE (claim:Claim {
                        subject: $subject,
                        predicate: 'HAS_CAPITAL_CITY',
                        object: $object
                    })
                      ON CREATE SET
                        claim.id = $id,
                        claim.epistemic_score = 0.9,
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
                    MERGE (s)-[:PREDICATE {type: 'HAS_CAPITAL_CITY', epistemic_score: 0.9, is_current: true}]->(o)
                """,
                    subject=country,
                    object=city,
                    id=str(uuid.uuid4()),
                    quote=phrase,
                    article_title="World Geography Encyclopedia",
                    source_name="Open Encyclopedia Data",
                    embedding=embedding,
                    url=f"encyclopedia://geography/capitals"
                )
                print(f"  [OK] Ingested Fact: {phrase}")
            except Exception as e:
                print(f"      - Error merging '{country}': {e}")
                
    print("Encyclopedia Ingestion 100% Complete.")


# ==============================================================================
# 4. QURAN INGESTION (English Translation)
# ==============================================================================
def ingest_quran():
    print("\n==================================================")
    print("= STARTING QURAN INGESTION (English)")
    print("==================================================")

    json_path = os.path.join(os.path.dirname(__file__), 'quran_en.json')
    if not os.path.exists(json_path):
        print(f"Downloading Quran (English) to {json_path}...")
        url = "https://raw.githubusercontent.com/risan/quran-json/main/dist/quran.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("Downloaded.")
        except Exception as e:
            print(f"Failed to download Quran JSON: {e}")
            return

    # Download English translations separately
    en_path = os.path.join(os.path.dirname(__file__), 'quran_en_translation.json')
    if not os.path.exists(en_path):
        print(f"Downloading Quran English translations to {en_path}...")
        # This file has surah -> verses -> text_en
        url = "https://raw.githubusercontent.com/semarketir/quranjson/master/source/surah/surah_1.json"
        # We'll fetch all 114 surahs one by one from the semarketir API - they're small
        all_translations = {}
        try:
            for i in range(1, 115):
                url = f"https://raw.githubusercontent.com/semarketir/quranjson/master/source/surah/surah_{i}.json"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                surah_data = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
                all_translations[str(i)] = surah_data
                if i % 10 == 0:
                    print(f"  Downloaded surah {i}/114...")
            with open(en_path, 'w') as f:
                json.dump(all_translations, f)
            print("Downloaded all translations.")
        except Exception as e:
            print(f"  Skipping English translations (will use Arabic only): {e}")
            all_translations = {}
    else:
        with open(en_path, 'r') as f:
            all_translations = json.load(f)

    with open(json_path, 'r', encoding='utf-8') as f:
        quran_surahs = json.load(f)

    print(f"Loaded {len(quran_surahs)} Surahs from the Quran.")

    for s_idx, surah_data in enumerate(quran_surahs):
        surah_num = s_idx + 1
        surah_name = surah_data.get('transliteration', f'Surah {surah_num}')
        verses = surah_data.get('verses', [])
        print(f"\n--- Surah {surah_num}: {surah_name} ({len(verses)} ayahs) ---")

        surah_translations = all_translations.get(str(surah_num), {})

        for v_idx, verse in enumerate(verses):
            verse_num = verse.get('id', v_idx + 1)
            # Prefer English translation, fall back to Arabic text
            verse_text = surah_translations.get(f"verse_{verse_num}", verse.get('text', ''))
            subject_name = f"{surah_name} {surah_num}:{verse_num}"

            with neo4j_driver.session() as session:
                existing = session.run("MATCH (c:Claim {subject: $subject}) RETURN count(c) as count", subject=subject_name).single()
                if existing and existing["count"] > 0:
                    continue

                embedding = _embed_text(verse_text)
                try:
                    session.run("""
                        MERGE (s:Entity {name: $subject})
                          ON CREATE SET s.created_at = datetime(), s.mention_count = 1
                          ON MATCH  SET s.mention_count = s.mention_count + 1
                        MERGE (o:Entity {name: $object})
                          ON CREATE SET o.created_at = datetime(), o.mention_count = 1
                          ON MATCH  SET o.mention_count = o.mention_count + 1
                        MERGE (text:Entity {name: 'Quran'})
                          ON CREATE SET text.type = 'Religious Text', text.created_at = datetime()
                        MERGE (surah:Entity {name: $surah_name})
                          ON CREATE SET surah.type = 'Surah', surah.created_at = datetime()
                        MERGE (src:Source {name: $source_name})
                          ON CREATE SET src.epistemic_trust = 1.0, src.tier = 'Tier 1'
                        MERGE (a:Article {url: $url})
                          ON CREATE SET a.title = $article_title, a.created_at = datetime()
                        WITH a, src, s, o, text, surah
                        MERGE (a)-[:PUBLISHED_BY]->(src)
                        MERGE (text)-[:CONTAINS]->(surah)
                        MERGE (surah)-[:PART_OF]->(text)
                        MERGE (surah)-[:CONTAINS]->(s)
                        MERGE (s)-[:PART_OF]->(surah)
                        MERGE (claim:Claim {subject: $subject, predicate: $predicate, object: $object})
                          ON CREATE SET
                            claim.id = $id, claim.epistemic_score = 1.0,
                            claim.extraction_confidence = 1.0, claim.is_verifiable = true,
                            claim.created_at = datetime(), claim.is_current = true,
                            claim.lifecycle = 'ACTIVE', claim.quote_context = $quote,
                            claim.article_title = $article_title,
                            claim.source_name = $source_name, claim.embedding = $embedding
                        WITH claim, s, o, a
                        MERGE (claim)-[:HAS_SUBJECT]->(s)
                        MERGE (claim)-[:HAS_OBJECT]->(o)
                        MERGE (claim)-[:EXTRACTED_FROM]->(a)
                        MERGE (s)-[:PREDICATE {type: $predicate, epistemic_score: 1.0, is_current: true}]->(o)
                    """,
                        subject=subject_name, predicate="STATES", object=verse_text,
                        id=str(uuid.uuid4()), quote=verse_text,
                        article_title=f"Quran - {surah_name}",
                        source_name="Quran (English Translation)",
                        embedding=embedding,
                        surah_name=surah_name,
                        url=f"quran://en/{surah_num}/{verse_num}"
                    )
                except Exception as e:
                    print(f"      - Error merging {subject_name}: {e}")
            if verse_num % 10 == 0:
                print(f"    ...merged up to ayah {verse_num}")
        print(f"  [OK] Surah {surah_num} Complete.")
    print("Quran Ingestion 100% Complete.")


# ==============================================================================
# 5. BHAGAVAD GITA INGESTION
# ==============================================================================
def ingest_bhagavad_gita():
    print("\n==================================================")
    print("= STARTING BHAGAVAD GITA INGESTION")
    print("==================================================")

    json_path = os.path.join(os.path.dirname(__file__), 'bhagavad_gita.json')
    if not os.path.exists(json_path):
        print(f"Fetching Bhagavad Gita from Gita API...")
        all_chapters = []
        try:
            for chapter_num in range(1, 19):  # 18 chapters
                url = f"https://gita-api.vercel.app/eng/chapters/{chapter_num}/verses"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                chapter_verses = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
                all_chapters.append({"chapter": chapter_num, "verses": chapter_verses})
                print(f"  Fetched Chapter {chapter_num} ({len(chapter_verses)} verses)")
                time.sleep(0.5)  # Be polite to the API
            with open(json_path, 'w') as f:
                json.dump(all_chapters, f)
            print("Saved Bhagavad Gita locally.")
        except Exception as e:
            print(f"Failed to download Bhagavad Gita: {e}")
            return

    with open(json_path, 'r') as f:
        chapters = json.load(f)

    print(f"Loaded Bhagavad Gita with {len(chapters)} chapters.")

    for chapter_data in chapters:
        chapter_num = chapter_data.get('chapter')
        verses = chapter_data.get('verses', [])
        print(f"\n--- Chapter {chapter_num} ({len(verses)} verses) ---")

        for verse in verses:
            verse_num = verse.get('verse_number', verse.get('verseNumber', '?'))
            verse_text = verse.get('text', verse.get('translation', verse.get('meaning', '')))
            if not verse_text:
                continue
            subject_name = f"Gita {chapter_num}:{verse_num}"

            with neo4j_driver.session() as session:
                existing = session.run("MATCH (c:Claim {subject: $subject}) RETURN count(c) as count", subject=subject_name).single()
                if existing and existing["count"] > 0:
                    continue

                embedding = _embed_text(verse_text)
                try:
                    session.run("""
                        MERGE (s:Entity {name: $subject})
                          ON CREATE SET s.created_at = datetime(), s.mention_count = 1
                          ON MATCH  SET s.mention_count = s.mention_count + 1
                        MERGE (o:Entity {name: $object})
                          ON CREATE SET o.created_at = datetime(), o.mention_count = 1
                          ON MATCH  SET o.mention_count = o.mention_count + 1
                        MERGE (text:Entity {name: 'Bhagavad Gita'})
                          ON CREATE SET text.type = 'Religious Text', text.created_at = datetime()
                        MERGE (chapter:Entity {name: $chapter_name})
                          ON CREATE SET chapter.type = 'Chapter', chapter.created_at = datetime()
                        MERGE (src:Source {name: $source_name})
                          ON CREATE SET src.epistemic_trust = 1.0, src.tier = 'Tier 1'
                        MERGE (a:Article {url: $url})
                          ON CREATE SET a.title = $article_title, a.created_at = datetime()
                        WITH a, src, s, o, text, chapter
                        MERGE (a)-[:PUBLISHED_BY]->(src)
                        MERGE (text)-[:CONTAINS]->(chapter)
                        MERGE (chapter)-[:PART_OF]->(text)
                        MERGE (chapter)-[:CONTAINS]->(s)
                        MERGE (s)-[:PART_OF]->(chapter)
                        MERGE (claim:Claim {subject: $subject, predicate: $predicate, object: $object})
                          ON CREATE SET
                            claim.id = $id, claim.epistemic_score = 1.0,
                            claim.extraction_confidence = 1.0, claim.is_verifiable = true,
                            claim.created_at = datetime(), claim.is_current = true,
                            claim.lifecycle = 'ACTIVE', claim.quote_context = $quote,
                            claim.article_title = $article_title,
                            claim.source_name = $source_name, claim.embedding = $embedding
                        WITH claim, s, o, a
                        MERGE (claim)-[:HAS_SUBJECT]->(s)
                        MERGE (claim)-[:HAS_OBJECT]->(o)
                        MERGE (claim)-[:EXTRACTED_FROM]->(a)
                        MERGE (s)-[:PREDICATE {type: $predicate, epistemic_score: 1.0, is_current: true}]->(o)
                    """,
                        subject=subject_name, predicate="STATES", object=verse_text,
                        id=str(uuid.uuid4()), quote=verse_text,
                        article_title=f"Bhagavad Gita - Chapter {chapter_num}",
                        source_name="Bhagavad Gita",
                        embedding=embedding,
                        chapter_name=f"Bhagavad Gita Chapter {chapter_num}",
                        url=f"gita://en/{chapter_num}/{verse_num}"
                    )
                except Exception as e:
                    print(f"      - Error merging {subject_name}: {e}")
        print(f"  [OK] Chapter {chapter_num} Complete.")
    print("Bhagavad Gita Ingestion 100% Complete.")


# ==============================================================================
# 6. COUNTRY FACTS INGESTION (Population, Religion, Language, Currency)
# ==============================================================================
def _ingest_country_dataset(filename, url, predicate, value_key, source_name, phrase_template):
    """Generic helper to ingest a single country-json dataset."""
    json_path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(json_path):
        print(f"  Downloading {filename}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("  Downloaded.")
        except Exception as e:
            print(f"  FAILED: {e}")
            return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"  Loaded {len(data)} records from {filename}")
    for item in data:
        country = item.get('country')
        value = item.get(value_key)
        if not country or not value:
            continue

        subject_name = str(country).strip()
        object_name  = str(value).strip()
        phrase = phrase_template.format(country=subject_name, value=object_name)

        with neo4j_driver.session() as session:
            existing = session.run(
                "MATCH (c:Claim {subject: $subject, predicate: $predicate}) RETURN count(c) as count",
                subject=subject_name, predicate=predicate
            ).single()
            if existing and existing["count"] > 0:
                continue

            embedding = _embed_text(phrase)
            try:
                session.run("""
                    MERGE (s:Entity {name: $subject})
                      ON CREATE SET s.created_at = datetime(), s.mention_count = 1
                      ON MATCH  SET s.mention_count = s.mention_count + 1
                    MERGE (o:Entity {name: $object})
                      ON CREATE SET o.created_at = datetime(), o.mention_count = 1
                      ON MATCH  SET o.mention_count = o.mention_count + 1
                    MERGE (src:Source {name: $source_name})
                      ON CREATE SET src.epistemic_trust = 0.9, src.tier = 'Tier 2'
                    MERGE (a:Article {url: $url})
                      ON CREATE SET a.title = $article_title, a.created_at = datetime()
                    WITH a, src, s, o
                    MERGE (a)-[:PUBLISHED_BY]->(src)
                    MERGE (claim:Claim {subject: $subject, predicate: $predicate, object: $object})
                      ON CREATE SET
                        claim.id = $id, claim.epistemic_score = 0.9,
                        claim.extraction_confidence = 1.0, claim.is_verifiable = true,
                        claim.created_at = datetime(), claim.is_current = true,
                        claim.lifecycle = 'ACTIVE', claim.quote_context = $quote,
                        claim.article_title = $article_title,
                        claim.source_name = $source_name, claim.embedding = $embedding
                    WITH claim, s, o, a
                    MERGE (claim)-[:HAS_SUBJECT]->(s)
                    MERGE (claim)-[:HAS_OBJECT]->(o)
                    MERGE (claim)-[:EXTRACTED_FROM]->(a)
                    MERGE (s)-[:PREDICATE {type: $predicate, epistemic_score: 0.9, is_current: true}]->(o)
                """,
                    subject=subject_name, predicate=predicate, object=object_name,
                    id=str(uuid.uuid4()), quote=phrase,
                    article_title="World Country Facts",
                    source_name=source_name, embedding=embedding,
                    url=f"geography://samayo/{filename}"
                )
                print(f"  [OK] {phrase}")
            except Exception as e:
                print(f"      - Error: {e}")


COUNTRY_DATASETS = [
    {
        "filename": "country_religion.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-religion.json",
        "predicate": "HAS_MAJORITY_RELIGION",
        "value_key": "religion",
        "source_name": "Open Country Data",
        "phrase_template": "The majority religion of {country} is {value}."
    },
    {
        "filename": "country_population.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-population.json",
        "predicate": "HAS_POPULATION",
        "value_key": "population",
        "source_name": "Open Country Data",
        "phrase_template": "The population of {country} is {value}."
    },
    {
        "filename": "country_language.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-languages.json",
        "predicate": "HAS_OFFICIAL_LANGUAGE",
        "value_key": "languages",
        "source_name": "Open Country Data",
        "phrase_template": "The official language(s) of {country} are {value}."
    },
    {
        "filename": "country_currency.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-currency-code.json",
        "predicate": "HAS_CURRENCY",
        "value_key": "currency",
        "source_name": "Open Country Data",
        "phrase_template": "The currency of {country} is {value}."
    },
    {
        "filename": "country_area.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-surface-area.json",
        "predicate": "HAS_AREA_KM2",
        "value_key": "area",
        "source_name": "Open Country Data",
        "phrase_template": "The area of {country} is {value} square kilometers."
    },
    {
        "filename": "country_continent.json",
        "url": "https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-continent.json",
        "predicate": "IS_LOCATED_IN_CONTINENT",
        "value_key": "continent",
        "source_name": "Open Country Data",
        "phrase_template": "{country} is located in {value}."
    },
]

def ingest_country_facts():
    print("\n==================================================")
    print("= STARTING COUNTRY FACTS INGESTION")
    print("==================================================")
    for dataset in COUNTRY_DATASETS:
        print(f"\n  >> Processing: {dataset['predicate']}")
        _ingest_country_dataset(**dataset)
    print("Country Facts Ingestion 100% Complete.")


# ==============================================================================
# 7. NOBEL PRIZE WINNERS
# ==============================================================================
def ingest_nobel_prizes():
    print("\n==================================================")
    print("= STARTING NOBEL PRIZE WINNERS INGESTION")
    print("==================================================")

    json_path = os.path.join(os.path.dirname(__file__), 'nobel_prizes.json')
    if not os.path.exists(json_path):
        print(f"Downloading Nobel Prize data to {json_path}...")
        url = "https://api.nobelprize.org/v1/prize.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response:
                with open(json_path, 'wb') as f:
                    f.write(response.read())
            print("Downloaded.")
        except Exception as e:
            print(f"Failed to download Nobel Prizes: {e}")
            return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    prizes = data.get('prizes', [])
    print(f"Loaded {len(prizes)} Nobel Prize years.")

    for prize in prizes:
        year = prize.get('year', '?')
        category = prize.get('category', '?').title()
        laureates = prize.get('laureates', [])

        for laureate in laureates:
            firstname = laureate.get('firstname', '')
            surname = laureate.get('surname', '')
            name = f"{firstname} {surname}".strip()
            motivation = laureate.get('motivation', f'won the Nobel Prize in {category}').strip('"')
            if not name:
                continue

            subject_name = name
            object_name = f"Nobel Prize in {category} ({year})"
            phrase = f"{name} won the {object_name}. Motivation: {motivation}"

            with neo4j_driver.session() as session:
                existing = session.run(
                    "MATCH (c:Claim {subject: $subject, predicate: 'WON_NOBEL_PRIZE'}) RETURN count(c) as count",
                    subject=subject_name
                ).single()
                if existing and existing["count"] > 0:
                    continue

                embedding = _embed_text(phrase)
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
                        MERGE (claim:Claim {subject: $subject, predicate: 'WON_NOBEL_PRIZE', object: $object})
                          ON CREATE SET
                            claim.id = $id, claim.epistemic_score = 1.0,
                            claim.extraction_confidence = 1.0, claim.is_verifiable = true,
                            claim.created_at = datetime(), claim.is_current = true,
                            claim.lifecycle = 'ACTIVE', claim.quote_context = $quote,
                            claim.article_title = $article_title,
                            claim.source_name = $source_name, claim.embedding = $embedding
                        WITH claim, s, o, a
                        MERGE (claim)-[:HAS_SUBJECT]->(s)
                        MERGE (claim)-[:HAS_OBJECT]->(o)
                        MERGE (claim)-[:EXTRACTED_FROM]->(a)
                        MERGE (s)-[:PREDICATE {type: 'WON_NOBEL_PRIZE', epistemic_score: 1.0, is_current: true}]->(o)
                    """,
                        subject=subject_name, object=object_name,
                        id=str(uuid.uuid4()), quote=phrase,
                        article_title="Nobel Prize Laureates",
                        source_name="Nobel Prize Official API",
                        embedding=embedding,
                        url="https://api.nobelprize.org/v1/prize.json"
                    )
                    print(f"  [OK] {name} → {object_name}")
                except Exception as e:
                    print(f"      - Error merging '{name}': {e}")
    print("Nobel Prize Ingestion 100% Complete.")


# ==============================================================================
# 8. WORLD LEADERS (Past & Present) — via Wikidata SPARQL
# ==============================================================================
def ingest_world_leaders():
    import urllib.parse

    print("\n==================================================")
    print("= STARTING WORLD LEADERS INGESTION (Wikidata)")
    print("==================================================")

    json_path = os.path.join(os.path.dirname(__file__), 'world_leaders.json')

    if not os.path.exists(json_path):
        print("Querying Wikidata SPARQL for all heads of state (past & present)...")
        # Query all sovereign countries' heads of state with start/end dates
        query = """
SELECT ?countryLabel ?headLabel ?start ?end WHERE {
  ?country wdt:P31 wd:Q6256 .
  ?country p:P35 ?statement .
  ?statement ps:P35 ?head .
  OPTIONAL { ?statement pq:P580 ?start }
  OPTIONAL { ?statement pq:P582 ?end }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
ORDER BY ?countryLabel ?start
"""
        url = (
            "https://query.wikidata.org/sparql?query="
            + urllib.parse.quote(query)
            + "&format=json"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "NNI-TruthGraph/1.0", "Accept": "application/json"}
        )
        try:
            response = urllib.request.urlopen(req, timeout=60)
            data = json.loads(response.read().decode("utf-8"))
            records = data["results"]["bindings"]
            # Normalize into a clean list
            clean = []
            for r in records:
                clean.append({
                    "country":    r.get("countryLabel", {}).get("value", ""),
                    "leader":     r.get("headLabel",    {}).get("value", ""),
                    "start":      r.get("start",        {}).get("value", "")[:10],  # YYYY-MM-DD
                    "end":        r.get("end",          {}).get("value", "")[:10],
                })
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=2)
            print(f"Downloaded {len(clean)} leader records. Saved to {json_path}")
        except Exception as e:
            print(f"Failed to query Wikidata: {e}")
            return
    else:
        with open(json_path, "r", encoding="utf-8") as f:
            clean = json.load(f)
        print(f"Loaded {len(clean)} leader records from local cache.")

    count = 0
    for record in clean:
        country = record.get("country", "").strip()
        leader  = record.get("leader",  "").strip()
        start   = record.get("start",   "").strip()
        end     = record.get("end",     "").strip()

        if not country or not leader:
            continue

        count += 1
        # Build a rich human-readable period string
        period = ""
        if start and end:
            period = f"{start[:4]}–{end[:4]}"
        elif start:
            period = f"{start[:4]}–present"
        else:
            period = "Unknown period"

        subject_name = leader
        object_name  = f"{country} ({period})"
        phrase = f"{leader} served as head of state of {country} from {period}."
        if start:
            phrase += f" Term started: {start}."
        if end:
            phrase += f" Term ended: {end}."

        with neo4j_driver.session() as session:
            # Resumption check — use (leader + country + start) as unique key
            existing = session.run(
                """MATCH (c:Claim {subject: $subject, predicate: 'LED_COUNTRY', object: $object})
                   RETURN count(c) as count""",
                subject=subject_name, object=object_name
            ).single()
            if existing and existing["count"] > 0:
                if count % 100 == 0:
                    print(f"  ...skipped up to record {count}")
                continue

            embedding = _embed_text(phrase)
            try:
                session.run("""
                    MERGE (s:Entity {name: $leader})
                      ON CREATE SET s.created_at = datetime(), s.mention_count = 1, s.type = 'Person'
                      ON MATCH  SET s.mention_count = s.mention_count + 1

                    MERGE (o:Entity {name: $country})
                      ON CREATE SET o.created_at = datetime(), o.mention_count = 1, o.type = 'Country'
                      ON MATCH  SET o.mention_count = o.mention_count + 1

                    MERGE (src:Source {name: $source_name})
                      ON CREATE SET src.epistemic_trust = 0.95, src.tier = 'Tier 1'

                    MERGE (a:Article {url: $url})
                      ON CREATE SET a.title = $article_title, a.created_at = datetime()
                    WITH a, src, s, o
                    MERGE (a)-[:PUBLISHED_BY]->(src)

                    MERGE (claim:Claim {subject: $leader, predicate: 'LED_COUNTRY', object: $object})
                      ON CREATE SET
                        claim.id = $id,
                        claim.epistemic_score = 0.95,
                        claim.extraction_confidence = 1.0,
                        claim.is_verifiable = true,
                        claim.created_at = datetime(),
                        claim.is_current = true,
                        claim.lifecycle = 'ACTIVE',
                        claim.quote_context = $quote,
                        claim.article_title = $article_title,
                        claim.source_name = $source_name,
                        claim.embedding = $embedding,
                        claim.term_start = $start,
                        claim.term_end = $end

                    WITH claim, s, o, a
                    MERGE (claim)-[:HAS_SUBJECT]->(s)
                    MERGE (claim)-[:HAS_OBJECT]->(o)
                    MERGE (claim)-[:EXTRACTED_FROM]->(a)
                    MERGE (s)-[:PREDICATE {
                        type: 'LED_COUNTRY',
                        epistemic_score: 0.95,
                        is_current: true,
                        term_start: $start,
                        term_end: $end
                    }]->(o)
                """,
                    leader=subject_name,
                    country=country,
                    object=object_name,
                    id=str(uuid.uuid4()),
                    quote=phrase,
                    article_title=f"World Leaders — {country}",
                    source_name="Wikidata Open Knowledge Base",
                    embedding=embedding,
                    start=start,
                    end=end,
                    url=f"https://www.wikidata.org/wiki/Special:EntityData"
                )
                print(f"  [OK] {leader} → {country} ({period})")
            except Exception as e:
                print(f"      - Error merging '{leader}': {e}")

    print(f"World Leaders Ingestion 100% Complete. ({count} records processed)")


# ==============================================================================
# MAIN: Run all ingestion jobs in sequence
# ==============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  NNI Truth Graph — Baseline Knowledge Seeder")
    print("  This process runs continuously until all datasets")
    print("  are fully ingested. It will auto-pause on rate limits")
    print("  and auto-resume. Safe to restart at any time.")
    print("=" * 60)

    # --- Run all ingestion jobs in priority order ---

    # 1. Religious Texts (highest epistemic trust)
    ingest_bible()
    ingest_quran()
    ingest_bhagavad_gita()

    # 2. Language & Definitions
    ingest_dictionary()

    # 3. World Geography & Demographics
    ingest_encyclopedia()    # Capital cities
    ingest_country_facts()   # Religion, Population, Language, Currency, Area, Continent

    # 4. Historical Records
    ingest_world_leaders()   # Past & present heads of state with terms
    ingest_nobel_prizes()    # Nobel Prize laureates since 1901

    print("\n" + "=" * 60)
    print("  ALL DATASETS COMPLETE.")
    print("  Closing Neo4j Driver Connection...")
    print("=" * 60)
    neo4j_driver.close()
    print("Unified Ingestion System Terminating. Goodnight.")
