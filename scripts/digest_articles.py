import asyncio
import os
import json
import logging
import psycopg2
import trafilatura
from groq import Groq
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Import our existing AI Engine components
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from ai_engine.nlp_models import SemanticLinker

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Environment - try local .env file first, then system env vars are auto-available via os.getenv()
# CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars on Render
# It only loads from .env files. On Render, system env vars are set by render.yaml
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    logger.info(f"✅ Loaded .env from {env_path}")
else:
    logger.info("ℹ️ No .env file found - using system environment variables (Render deployment)")

# Constants
MAX_TOKENS = 8000  # Conservative limit for output
BATCH_SIZE = 5

class DigestEngine:
    def __init__(self):
        # CRITICAL FIX: Read environment variables at __init__ time (runtime), not module import time
        # This ensures Render's environment is fully initialized
        self.database_url = os.getenv("DATABASE_URL")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        
        # Debug logging to help troubleshoot environment variable loading
        logger.info(f"[DEBUG] DATABASE_URL: {'✅ SET' if self.database_url else '❌ NOT SET'}")
        logger.info(f"[DEBUG] GROQ_API_KEY: {'✅ SET (length: ' + str(len(self.groq_api_key)) + ')' if self.groq_api_key else '❌ NOT SET'}")
        
        if not self.groq_api_key:
            error_msg = """
            ❌ GROQ_API_KEY NOT SET AT RUNTIME
            
            Environment variables are read at initialization time.
            If you see this error on Render, it means the environment variable is not available.
            
            To fix this on Render:
            1. Go to https://render.com/dashboard
            2. Click on 'truth-graph-ai' service
            3. Click 'Environment' tab
            4. Find GROQ_API_KEY - click it and enter your API key from https://console.groq.com
            5. Redeploy the service
            
            To fix locally:
            - Create ai_engine/.env and add: GROQ_API_KEY=your_key_here
            """
            raise ValueError(error_msg)
        
        if not self.database_url:
            error_msg = """
            ❌ DATABASE_URL NOT SET AT RUNTIME
            
            To fix this on Render:
            1. Go to https://render.com/dashboard
            2. Click on 'truth-graph-ai' service
            3. Click 'Environment' tab
            4. Ensure DATABASE_URL is set with your PostgreSQL connection string
            5. Redeploy the service
            """
            raise ValueError(error_msg)
        
        try:
            logger.info(f"[DEBUG] Attempting to initialize Groq with API key length: {len(self.groq_api_key)}")
            self.groq_client = Groq(api_key=self.groq_api_key)
            logger.info("✅ Groq client initialized successfully")
        except Exception as e:
            import traceback
            full_error = traceback.format_exc()
            logger.error(f"❌ Failed to initialize Groq client: {e}")
            logger.error(f"Full traceback:\n{full_error}")
            raise ValueError(f"❌ Failed to initialize Groq client: {str(e)}\n\nFull error:\n{full_error}")
        
        try:
            self.linker = SemanticLinker() # Loads vector model (Local or API)
        except Exception as e:
            logger.warning(f"⚠️  SemanticLinker init failed: {e}")
            self.linker = None
        
    def fetch_fresh_content(self, url):
        """Fetches fresh HTML and extracts text using Trafilatura (Sync)."""
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None
            text = trafilatura.extract(downloaded, include_tables=False, include_comments=False)
            return text
        except Exception as e:
            logger.warning(f"Trafilatura fetch failed for {url}: {e}")
            return None

    def extract_facts_with_llm(self, text):
        """Sends text to Llama 3.3 to extract atomic facts JSON."""
        prompt = f"""
        You are an expert Knowledge Graph Engineer.
        Task: Extract strictly factual facts from the text below.
        
        CRITICAL RULES:
        1. ATOMIC: Each fact must be a single, standalone fact.
        2. RESOLVE PRONOUNS: "He" -> "Donald Trump". "The company" -> "Apple Inc.".
        3. DISAMBIGUATE ENTITIES: "Paris" -> "Paris, France" (if city) or "Paris Hilton" (if person).
        4. ACCURACY: Only extract what is explicitly stated.
        5. FORMAT: Return a valid JSON object with a "facts" key containing a list.
        
        TEXT:
        "{text[:80000]}" 
        
        OUTPUT FORMAT:
        {{
            "facts": [
                {{
                    "subject": "Entity Name (Disambiguated)",
                    "predicate": "action/verb",
                    "object": "target/detail (Disambiguated)",
                    "confidence": 0.95
                }}
            ]
        }}
        """
        
        try:
            chat_completion = self.groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a precise Knowledge Graph extractor. Output JSON only."},
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.0, # Zero temp for max determinism
                response_format={"type": "json_object"}
            )
            return json.loads(chat_completion.choices[0].message.content)
        except Exception as e:
            logger.error(f"Groq Extraction Failed: {e}")
            return {"facts": []} # Fallback

    async def process_batch(self):
        conn = psycopg2.connect(self.database_url)
        cur = conn.cursor()
        
        # 1. Get Articles that need digestion
        # processed_at IS NULL ensures we process queue
        cur.execute("""
            SELECT id, url, title FROM articles 
            WHERE processed_at IS NULL 
            AND url IS NOT NULL
            LIMIT 10;
        """)
        rows = cur.fetchall()
        
        if not rows:
            logger.info("✅ All articles processed.")
            return

        logger.info(f"🧠 Digesting {len(rows)} articles...")
        
        loop = asyncio.get_running_loop()
        
        for aid, url, title in rows:
            safe_title = title if title else "Unknown Title"
            logger.info(f"Processing {aid}: {safe_title[:30]}...")
            
            # A. Fetch Content & Metadata
            # We prefer Trafilatura (Fresh) over DB raw_text (Stale/Truncated)
            full_text = None
            date_found = None
            
            try:
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    full_text = trafilatura.extract(downloaded, include_tables=False, include_comments=False)
                    metadata = trafilatura.extract_metadata(downloaded)
                    if metadata and metadata.date:
                        date_found = metadata.date
            except Exception as e:
                logger.warning(f"Fetch/Extract error for {aid}: {e}")
            
            # Update Date if found
            if date_found:
                 logger.info(f"📅 Updating date for {aid}: {date_found}")
                 cur.execute("UPDATE articles SET published_date = %s WHERE id = %s", (date_found, aid))
                 conn.commit()

            if not full_text:
                FULL_TEXT_FROM_DB = False 
                # ... fall back to DB or skip logic ...
                # For simplicity in this script, we skip if Trafilatura fails, or add fallback logic
                logger.warning(f"⏩ Skipping {aid}: No fresh content available.")
                cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
                conn.commit()
                continue
                
            # B. Extract Facts (LLM)
            # Note: Groq call is sync in this SDK version, so run in executor
            result_json = await loop.run_in_executor(None, self.extract_facts_with_llm, full_text)
            
            facts_list = result_json.get('facts', [])
            # Fallback if top-level list
            if isinstance(result_json, list): facts_list = result_json
            
            # C. Vectorize & Deduplicate with Vector Search Gate
            fact_count = 0
            duplicate_count = 0
            
            for fact in facts_list:
                subj = fact.get('subject')
                pred = fact.get('predicate')
                obj = fact.get('object')
                conf = fact.get('confidence', 0.5)
                
                if not (subj and pred and obj): continue
                
                statement = f"{subj} {pred} {obj}"
                embedding = self.linker.get_embedding(statement)
                
                # DEDUPLICATION GATE: Check if semantically identical fact exists
                # Convert embedding to pgvector format
                if embedding is None:
                    embedding_str = None
                else:
                    embedding_str = '[' + ','.join(map(str, embedding)) + ']'
                
                # DEDUPLICATION GATE: Check if semantically identical fact exists
                existing_fact = None
                if embedding_str is not None:
                    cur.execute("""
                        SELECT id, subject, predicate, object 
                        FROM extracted_facts 
                        WHERE embedding <=> %s::vector < 0.05
                        LIMIT 1
                    """, (embedding_str,))
                    existing_fact = cur.fetchone()
                
                if existing_fact:
                    # Duplicate detected - Don't insert, just log
                    duplicate_count += 1
                    existing_id, ex_subj, ex_pred, ex_obj = existing_fact
                    logger.info(f"   🔁 Duplicate: '{statement}' → Existing Fact #{existing_id}")
                    # Note: We could increment a 'source_count' here if we had that column
                    continue
                
                # New Unique Fact - Insert
                cur.execute("""
                    INSERT INTO extracted_facts 
                    (article_id, subject, predicate, object, confidence, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                """, (aid, subj, pred, obj, conf, embedding_str))
                fact_count += 1
                
            # D. Mark Done
            cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
            conn.commit()
            
            if duplicate_count > 0:
                logger.info(f"✅ Article {aid}: {fact_count} new facts, {duplicate_count} duplicates skipped.")
            else:
                logger.info(f"✅ Article {aid}: Extracted {fact_count} facts.")
            
        cur.close()
        conn.close()

if __name__ == "__main__":
    engine = DigestEngine()
    asyncio.run(engine.process_batch())
