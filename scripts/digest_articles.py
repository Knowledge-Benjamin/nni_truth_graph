import asyncio
import os
import json
import logging
import sys
import psycopg2
import signal
import trafilatura
from groq import Groq
from dotenv import load_dotenv

# Signal handlers for graceful shutdown in Docker containers
def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful container shutdown"""
    msg = "\n[SIGNAL-HANDLER] Received signal - flushing and exiting gracefully\n"
    sys.stdout.write(msg)
    sys.stderr.write(msg)
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

print("___SCRIPT_START___", flush=True)
sys.stdout.flush()
sys.stderr.flush()

try:
    print("___IMPORTING_MODULES___", flush=True)
    sys.stdout.flush()
    
    # Import our existing AI Engine components
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    print("___SYSPATH_UPDATED___", flush=True)
    sys.stdout.flush()
    
    from ai_engine.nlp_models import SemanticLinker
    print("___SEMANTICLINKER_IMPORTED___", flush=True)
    sys.stdout.flush()
    
except Exception as e:
    import traceback
    print("___IMPORT_ERROR___: " + str(e), flush=True)
    sys.stdout.flush()
    print(traceback.format_exc(), flush=True)
    sys.stdout.flush()
    sys.exit(1)

# Configure Logging with explicit unbuffered handler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)
logger = logging.getLogger(__name__)

# Force immediate flushing for all handlers
for handler in logging.root.handlers:
    handler.flush()
    if hasattr(handler, 'setFormatter'):
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

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
DB_CONNECT_TIMEOUT = 10  # seconds

class DigestEngine:
    def __init__(self):
        print("[INIT-1]", flush=True)
        sys.stdout.flush()
        
        try:
            env_count = len(os.environ)
            print("[INIT-2] env=" + str(env_count), flush=True)
            sys.stdout.flush()
            
            print("[INIT-3-DB-START]", flush=True)
            sys.stdout.flush()
            self.database_url = os.getenv("DATABASE_URL")
            print("[INIT-3-DB-DONE]", flush=True)
            sys.stdout.flush()
            
            print("[INIT-4-GQ-START]", flush=True)
            sys.stdout.flush()
            self.groq_api_key = os.getenv("GROQ_API_KEY")
            print("[INIT-4-GQ-DONE]", flush=True)
            sys.stdout.flush()
            
            if not self.groq_api_key:
                raise ValueError("GROQ_API_KEY missing")
            print("[INIT-5-GQ-OK]", flush=True)
            sys.stdout.flush()
            
            if not self.database_url:
                raise ValueError("DATABASE_URL missing")
            print("[INIT-6-DB-OK]", flush=True)
            sys.stdout.flush()
            
            print("[INIT-7-GRQ-INIT]", flush=True)
            sys.stdout.flush()
            self.groq_client = Groq(api_key=self.groq_api_key)
            print("[INIT-8-GRQ-DONE]", flush=True)
            sys.stdout.flush()
            
            print("[INIT-9-LNK-INIT]", flush=True)
            sys.stdout.flush()
            self.linker = SemanticLinker()
            print("[INIT-10-LNK-DONE]", flush=True)
            sys.stdout.flush()
            
            logger.info("[INIT] DigestEngine initialized successfully")
            
        except Exception as e:
            import traceback
            print("[ERROR] " + str(type(e).__name__) + ": " + str(e), flush=True)
            sys.stdout.flush()
            print(traceback.format_exc(), flush=True)
            sys.stdout.flush()
            raise
        
    def fetch_fresh_content(self, url):
        """Fetches fresh HTML and extracts text using Trafilatura."""
        try:
            logger.info(f"   📥 Fetching {url[:50]}...")
            # trafilatura.fetch_url does not accept timeout parameter
            # System connection timeout settings will apply instead
            downloaded = trafilatura.fetch_url(url)
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
                response_format={"type": "json_object"},
                timeout=30  # 30 second timeout for API response
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
            # Set statement timeout via SQL (Neon pooled connections don't support startup options)
            # Role-level timeout configured in Neon console:
            # ALTER ROLE neondb_owner SET statement_timeout = '70s';
            print(">>>SETTING_TIMEOUT<<<", flush=True)
            sys.stdout.flush()
            cur.execute("SET statement_timeout TO 70000")
            print(">>>TIMEOUT_SET<<<", flush=True)
            sys.stdout.flush()
            logger.info("✅ Database connection established")
            
            # 1. Get Articles that need digestion
            logger.info("📋 Fetching unprocessed articles...")
            print(">>>DB_FETCH_START<<<", flush=True)
            sys.stdout.flush()
            sys.stderr.flush()
            
            print(">>>DB_QUERY_PREP<<<", flush=True)
            sys.stdout.flush()
            
            try:
                print(">>>DB_TRY_START<<<", flush=True)
                sys.stdout.flush()
                
                query = """
                    SELECT id, url, title FROM articles 
                    WHERE processed_at IS NULL 
                    AND url IS NOT NULL
                    LIMIT %s;
                """
                print(">>>DB_QUERY_EXECUTE<<<", flush=True)
                sys.stdout.flush()
                
                # Execute query with application-level timeout to catch hanging queries
                async def execute_with_timeout():
                    try:
                        cur.execute(query, (BATCH_SIZE,))
                        print(">>>DB_QUERY_DONE<<<", flush=True)
                        sys.stdout.flush()
                        
                        print(">>>DB_FETCHALL_START<<<", flush=True)
                        sys.stdout.flush()
                        rows = cur.fetchall()
                        print(f">>>DB_FETCHALL_DONE_{len(rows)}<<<", flush=True)
                        sys.stdout.flush()
                        return rows
                    except Exception as e:
                        print(f">>>DB_EXECUTE_ERROR_{type(e).__name__}<<<", flush=True)
                        sys.stdout.flush()
                        raise
                
                rows = await asyncio.wait_for(execute_with_timeout(), timeout=75.0)
                logger.info(f"  [DB-4] Fetched {len(rows)} articles from database")
                sys.stdout.flush()
                
            except Exception as e:
                print(f">>>DB_FETCH_ERROR_TYPE_{type(e).__name__}<<<", flush=True)
                sys.stdout.flush()
                logger.error(f"❌ Database fetch failed: {type(e).__name__}: {e}")
                import traceback
                tb = traceback.format_exc()
                logger.error(f"Traceback: {tb}")
                print(f">>>DB_FETCH_ERROR_TRACE_{tb[:200]}<<<", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()
                raise
            
            if not rows:
                logger.info("✅ All articles processed.")
                return

            logger.info(f"🧠 Digesting {len(rows)} articles...")
            sys.stdout.flush()
            
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
                            downloaded = trafilatura.fetch_url(url)
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
                    result_json = await asyncio.wait_for(
                        loop.run_in_executor(None, self.extract_facts_with_llm, full_text),
                        timeout=60.0
                    )
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
    print("___MAIN_BLOCK_START___", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    
    logger.info("[__MAIN__] Script entry point reached")
    try:
        print("___ENGINE_INIT_START___", flush=True)
        sys.stdout.flush()
        logger.info("[__MAIN__] Creating DigestEngine...")
        engine = DigestEngine()
        print("___ENGINE_INIT_DONE___", flush=True)
        sys.stdout.flush()
        logger.info("[__MAIN__] ✅ DigestEngine created successfully")
        
        print("___PROCESS_BATCH_START___", flush=True)
        sys.stdout.flush()
        logger.info("[__MAIN__] Starting async process_batch...")
        asyncio.run(engine.process_batch())
        print("___PROCESS_BATCH_DONE___", flush=True)
        sys.stdout.flush()
        logger.info("[__MAIN__] ✅ process_batch completed")
        
        logger.info("=" * 80)
        logger.info("✅ Batch processing completed successfully")
        logger.info("=" * 80)
        
        # Explicit success exit code
        sys.exit(0)
    
    except Exception as e:
        import traceback
        full_error = traceback.format_exc()
        logger.error("=" * 80)
        logger.error("❌ CRITICAL ERROR IN DIGEST_ARTICLES")
        logger.error("=" * 80)
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        logger.error(f"\nFull Traceback:\n{full_error}")
        logger.error("=" * 80)
        
        # Also print to stderr so it appears in container logs
        print(f"\n___CRITICAL_ERROR___\n{full_error}\n", flush=True, file=sys.stderr)
        sys.stderr.flush()
        
        # Exit with error code
        sys.exit(1)
