import asyncio
import os
import json
import logging
import psycopg2
import trafilatura
from groq import Groq
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
BATCH_SIZE = 10
FETCH_TIMEOUT = 15  # seconds
DB_CONNECT_TIMEOUT = 10  # seconds

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
        """Fetches fresh HTML and extracts text using Trafilatura with timeout."""
        try:
            logger.info(f"   📥 Fetching {url[:50]}...")
            # trafilatura.fetch_url can hang, use timeout
            downloaded = trafilatura.fetch_url(url, timeout=FETCH_TIMEOUT)
            if not downloaded:
                logger.warning(f"   ⚠️  No content downloaded from {url}")
                return None
            text = trafilatura.extract(downloaded, include_tables=False, include_comments=False)
            if text:
                logger.info(f"   ✅ Extracted {len(text)} chars")
            return text
        except Exception as e:
            logger.warning(f"   ❌ Trafilatura fetch failed for {url}: {e}")
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
                temperature=0.0,  # Zero temp for max determinism
                response_format={"type": "json_object"}
            )
            return json.loads(chat_completion.choices[0].message.content)
        except Exception as e:
            logger.error(f"Groq Extraction Failed: {e}")
            return {"facts": []}  # Fallback

    async def process_batch(self):
        """Process batch of articles, extract facts, deduplicate."""
        conn = None
        cur = None
        
        try:
            logger.info("🔄 Connecting to database...")
            conn = psycopg2.connect(self.database_url, connect_timeout=DB_CONNECT_TIMEOUT)
            cur = conn.cursor()
            logger.info("✅ Database connection established")
            
            # 1. Get Articles that need digestion
            logger.info("📋 Fetching unprocessed articles...")
            cur.execute("""
                SELECT id, url, title FROM articles 
                WHERE processed_at IS NULL 
                AND url IS NOT NULL
                LIMIT %s;
            """, (BATCH_SIZE,))
            rows = cur.fetchall()
            
            if not rows:
                logger.info("✅ All articles processed.")
                return

            logger.info(f"🧠 Digesting {len(rows)} articles...")
            
            loop = asyncio.get_running_loop()
            
            # Process each article
            for aid, url, title in rows:
                safe_title = title if title else "Unknown Title"
                logger.info(f"Processing {aid}: {safe_title[:30]}...")
                
                # A. Fetch Content & Metadata
                full_text = None
                date_found = None
                
                try:
                    full_text = self.fetch_fresh_content(url)
                    
                    # Try to extract metadata if we got content
                    if full_text:
                        try:
                            downloaded = trafilatura.fetch_url(url, timeout=FETCH_TIMEOUT)
                            if downloaded:
                                metadata = trafilatura.extract_metadata(downloaded)
                                if metadata and metadata.date:
                                    date_found = metadata.date
                        except Exception as e:
                            logger.warning(f"   ⚠️  Metadata extraction failed: {e}")
                
                except Exception as e:
                    logger.warning(f"   ❌ Content fetch error for {aid}: {e}")
                
                # Update date if found
                if date_found:
                    try:
                        logger.info(f"📅 Updating date for {aid}: {date_found}")
                        cur.execute("UPDATE articles SET published_date = %s WHERE id = %s", (date_found, aid))
                        conn.commit()
                    except Exception as e:
                        logger.warning(f"   ⚠️  Failed to update date: {e}")

                # Skip if no content
                if not full_text:
                    logger.warning(f"⏩ Skipping {aid}: No fresh content available.")
                    try:
                        cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
                        conn.commit()
                    except Exception as e:
                        logger.warning(f"   ⚠️  Failed to mark as processed: {e}")
                    continue
                
                # B. Extract Facts (LLM)
                logger.info(f"   🤖 Extracting facts from article {aid}...")
                try:
                    result_json = await loop.run_in_executor(None, self.extract_facts_with_llm, full_text)
                except Exception as e:
                    logger.error(f"   ❌ LLM extraction failed for {aid}: {e}")
                    try:
                        cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
                        conn.commit()
                    except:
                        pass
                    continue
                
                # Parse facts
                facts_list = result_json.get('facts', [])
                if isinstance(result_json, list):
                    facts_list = result_json
                
                # C. Vectorize & Deduplicate
                fact_count = 0
                duplicate_count = 0
                
                for fact in facts_list:
                    try:
                        subj = fact.get('subject', '').strip()
                        pred = fact.get('predicate', '').strip()
                        obj = fact.get('object', '').strip()
                        conf = float(fact.get('confidence', 0.5))
                        
                        if not (subj and pred and obj):
                            continue
                        
                        statement = f"{subj} {pred} {obj}"
                        
                        # Get embedding for deduplication
                        embedding = None
                        embedding_str = None
                        try:
                            if self.linker:
                                embedding = self.linker.get_embedding(statement)
                                if embedding:
                                    embedding_str = '[' + ','.join(map(str, embedding)) + ']'
                        except Exception as e:
                            logger.warning(f"   ⚠️  Embedding generation failed: {e}")
                        
                        # Check for duplicates
                        existing_fact = None
                        if embedding_str:
                            try:
                                cur.execute("""
                                    SELECT id, subject, predicate, object 
                                    FROM extracted_facts 
                                    WHERE embedding <=> %s::vector < 0.05
                                    LIMIT 1
                                """, (embedding_str,))
                                existing_fact = cur.fetchone()
                            except Exception as e:
                                logger.warning(f"   ⚠️  Dedup check failed: {e}")
                        
                        if existing_fact:
                            # Duplicate detected
                            duplicate_count += 1
                            existing_id = existing_fact[0]
                            logger.info(f"   🔁 Duplicate: '{statement}' → Existing Fact #{existing_id}")
                            continue
                        
                        # New Unique Fact - Insert
                        try:
                            cur.execute("""
                                INSERT INTO extracted_facts 
                                (article_id, subject, predicate, object, confidence, embedding)
                                VALUES (%s, %s, %s, %s, %s, %s::vector)
                            """, (aid, subj, pred, obj, conf, embedding_str))
                            fact_count += 1
                        except Exception as e:
                            logger.warning(f"   ⚠️  Failed to insert fact: {e}")
                    
                    except Exception as e:
                        logger.warning(f"   ⚠️  Error processing fact: {e}")
                        continue
                
                # Mark article as processed
                try:
                    cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to mark article processed: {e}")
                
                # Log results
                if duplicate_count > 0:
                    logger.info(f"✅ Article {aid}: {fact_count} new facts, {duplicate_count} duplicates.")
                else:
                    logger.info(f"✅ Article {aid}: Extracted {fact_count} facts.")
        
        except Exception as e:
            logger.error(f"❌ Batch processing failed: {e}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise
        
        finally:
            # Clean up connections
            if cur:
                try:
                    cur.close()
                except Exception as e:
                    logger.warning(f"Failed to close cursor: {e}")
            if conn:
                try:
                    conn.close()
                    logger.info("✅ Database connection closed")
                except Exception as e:
                    logger.warning(f"Failed to close connection: {e}")

if __name__ == "__main__":
    engine = DigestEngine()
    asyncio.run(engine.process_batch())
