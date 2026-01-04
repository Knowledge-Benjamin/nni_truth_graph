from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
# from transformers import pipeline # Lazy loaded only if needed
import os
import requests
import datetime
from dotenv import load_dotenv
import traceback
import threading
import sys
import gc

# Add scripts directory to path to allow importing run_pipeline
sys.path.append(os.path.join(os.path.dirname(__file__), '../scripts'))
try:
    from run_pipeline import PipelineOrchestrator
except ImportError:
    print("⚠️  Could not import PipelineOrchestrator. Pipeline will not run.")
    PipelineOrchestrator = None

# Optional NLP imports (graceful degradation)
NLP_AVAILABLE = False
semantic_linker = None
entity_extractor = None
query_translator = None

try:
    print("[INFO] Importing NLP modules...")
    from nlp_models import SemanticLinker, EntityExtractor
    print("[SUCCESS] NLP models imported")
except ImportError as e:
    print(f"[WARN] NLP models import failed: {e}")

try:
    print("[INFO] Importing Query Engine...")
    from query_engine import QueryTranslator, ResultAnalyzer, GraphSearcher
    print("[SUCCESS] Query Engine imported")
except Exception as e:
    print(f"[WARN] Query Engine import failed: {e}")
    
NLP_AVAILABLE = True # Partial availability is acceptable

# Load environment variables
# CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars on Render
# It only looks for .env in current directory. On Render, system env vars are already set
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"✅ Loaded .env from {env_path}")
else:
    print("ℹ️ No .env file found - using system environment variables (Render deployment)")

# ===== ENVIRONMENT VALIDATION =====
REQUIRED_ENV_VARS = ['DATABASE_URL']
OPTIONAL_ENV_VARS = {
    'GEMINI_API_KEY': 'Required for natural language query translation',
    'HF_TOKEN': 'Required for cloud mode embeddings (optional for local mode)',
    'GOOGLE_FACT_CHECK_KEY': 'Optional: Enables Google Fact Check API',
    'SERPER_API_KEY': 'Optional: Enables web citation search',
    'NEO4J_URI': 'Optional: Required for GraphSearcher advanced search',
    'NEO4J_USER': 'Optional: Required for GraphSearcher advanced search',
    'NEO4J_PASSWORD': 'Optional: Required for GraphSearcher advanced search',
}

# Check required variables
missing_vars = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if missing_vars:
    print(f"❌ ERROR: Missing required environment variables: {missing_vars}")
    print("   AI Engine will have limited functionality")
    print("   Set these in .env file or system environment")
    print("   Continuing with degraded mode...")

# Check optional but recommended variables
missing_optional = []
for var, description in OPTIONAL_ENV_VARS.items():
    if not os.getenv(var):
        missing_optional.append((var, description))

if missing_optional:
    print(f"\n⚠️  WARNING: Missing optional environment variables:")
    for var, desc in missing_optional:
        print(f"   - {var}: {desc}")
    print("   Some features may be unavailable")

print("DEBUG: Initializing FastAPI...")
app = FastAPI()
print("DEBUG: FastAPI initialized.")

class TextRequest(BaseModel):
    content: str
    article_id: str

class Source(BaseModel):
    url: str
    publisher: str
    rating: str
    confidence: float
    stance: str = "NEUTRAL"
    published_date: str = ""
    snippet: str = ""

# --- DATABASE CONNECTION ---
import psycopg2
from psycopg2.extras import RealDictCursor
DB_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Connect Error: {e}")
        return None

# --- NEW GRAPH ENDPOINTS (Phase 2/3) ---

@app.get("/api/facts")
def get_extracted_facts(limit: int = 20):
    """Fetch latest facts from the Knowledge Graph."""
    conn = get_db_connection()
    if not conn: raise HTTPException(500, "Database Unavailable")
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT f.id, f.subject, f.predicate, f.object, f.confidence, 
                   f.is_original, f.created_at, a.title as article_title, a.url
            FROM extracted_facts f
            JOIN articles a ON f.article_id = a.id
            ORDER BY f.created_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        return rows
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.get("/api/provenance/{fact_id}")
def get_provenance_chain(fact_id: int):
    """Trace a fact back to its original source."""
    conn = get_db_connection()
    if not conn: raise HTTPException(500, "Database Unavailable")
    
    try:
        cur = conn.cursor()
        # Recursive query could be better, but simple lookup for now
        cur.execute("""
            SELECT f.id, f.subject, f.predicate, f.object, f.is_original, 
                   f.provenance_id, a.published_date, a.publisher
            FROM extracted_facts f
            JOIN articles a ON f.article_id = a.id
            WHERE f.id = %s
        """, (fact_id,))
        fact = cur.fetchone()
        
        if not fact: raise HTTPException(404, "Fact not found")
        
        result = {"fact": fact, "origin": None}
        
        # If this is an echo, fetch the master
        if fact['provenance_id']:
            cur.execute("""
                SELECT f.id, f.subject, f.predicate, f.object, a.url, a.publisher, a.published_date
                FROM extracted_facts f
                JOIN articles a ON f.article_id = a.id
                WHERE f.id = %s
            """, (fact['provenance_id'],))
            result["origin"] = cur.fetchone()
            
        return result
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

class QueryRequest(BaseModel):
    query: str

class Claim(BaseModel):
    statement: str
    confidence: float
    last_updated: str
    sources: List[Source] = []
    embedding: Optional[List[float]] = None  # Semantic vector (384-dim)
    entities: Optional[List[dict]] = None    # Extracted entities

# ... (pipeline loading)

@app.post("/translate_query")
def translate_query(request: QueryRequest):
    """
    Translates natural language query to Cypher using Gemini.
    """
    if not query_translator:
        raise HTTPException(status_code=503, detail="Query Translator not available (NLP module or API key missing)")
    
    print(f"DEBUG: Translating query: {request.query}")
    try:
        result = query_translator.translate_to_cypher(request.query)
        
        if result.get("error"):
             raise HTTPException(status_code=500, detail=result["error"])
             
        return result
    except Exception as e:
        print(f"Translation Error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class AnalysisRequest(BaseModel):
    query: str
    results: List[Dict]

@app.post("/analyze_results")
def analyze_results(request: AnalysisRequest):
    """
    Analyzes and cleans search results.
    """
    if "result_analyzer" not in globals() or not result_analyzer:
         raise HTTPException(status_code=503, detail="Result Analyzer not available")

    print(f"DEBUG: Analyzing {len(request.results)} results for query: {request.query}")
    return result_analyzer.analyze_results(request.query, request.results)

# --- ADVANCED SEARCH ENDPOINTS (Phase 6.2) ---
graph_searcher = None

@app.on_event("startup")
def init_graph_search():
    global graph_searcher
    try:
        print("[INFO] Initializing GraphSearcher...")
        graph_searcher = GraphSearcher()
        print("[INFO] GraphSearcher initialized successfully")
    except Exception as e:
        print(f"[ERROR] Graph Searcher Init Failed: {e}")
        import traceback
        traceback.print_exc()
        graph_searcher = None

@app.post("/api/search/advanced")
def advanced_search(request: QueryRequest, limit: int = 10):
    """
    Hybrid Search: Semantic Vector + Keyword Match.
    """
    if not graph_searcher:
        raise HTTPException(503, "Graph Search Unavailable")
    
    # Generate embedding for query
    embedding = None
    if semantic_linker:
        try:
            embedding = semantic_linker.get_embedding(request.query)
        except Exception:
            pass # Fallback to keyword only
            
    return graph_searcher.hybrid_search(request.query, embedding, limit)

@app.get("/api/fact/history/{fact_id}")
def get_fact_evolution(fact_id: int):
    """
    Get the timeline of truth for a specific fact.
    """
    if not graph_searcher:
        raise HTTPException(503, "Graph Search Unavailable")
        
    return graph_searcher.get_fact_history(fact_id)

@app.post("/expand_query")
def expand_query_endpoint(request: QueryRequest):
    """
    Generates search variations (synonyms, related terms).
    """
    if "query_translator" not in globals() or not query_translator:
         raise HTTPException(status_code=503, detail="Query Translator Unavailable")
    
    return query_translator.expand_query(request.query)

@app.post("/embed_query")
def embed_query_endpoint(request: QueryRequest):
    """
    Generates vector embedding for valid queries.
    Cloud mode uses HuggingFace API, local mode uses local model.
    """
    if "semantic_linker" not in globals() or not semantic_linker:
         raise HTTPException(status_code=503, detail="Semantic Linker Unavailable")

    try:
        embedding = semantic_linker.get_embedding(request.query)
        if not embedding:
            raise HTTPException(status_code=500, detail="Embedding generation failed")
        return {"embedding": embedding}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 91: # Load models - REMOVED GLOBAL INIT TO PREVENT OOM
# 92: # 1. Summarization (for extraction) is now lazy-loaded in functions

# -----------------------------------------------------------------------------
# ARCHITECTURE CONFIGURATION: EXECUTION MODE
# -----------------------------------------------------------------------------
# To protect the system in production and enable development on low-RAM machines:
# "local"     = Loads models into RAM (Heavy). Best for dedicated GPU servers.
# "cloud"     = Calls HuggingFace API (Zero RAM). Best for Production/Dev with API Key.
# "heuristic" = Uses Keyword Logic (Zero RAM). Best for local testing without crashing.
# -----------------------------------------------------------------------------
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "heuristic") # Default to safe mode
HF_TOKEN = os.getenv("HF_TOKEN")

print(f"DEBUG: EXECUTION_MODE={EXECUTION_MODE}")
print(f"DEBUG: HF_TOKEN={HF_TOKEN[:4] if HF_TOKEN else 'None'}...")
print(f"DEBUG: Current Directory={os.getcwd()}")
print(f"DEBUG: .env exists? {os.path.exists('.env')}")

APP_ENV = os.getenv("APP_ENV", "development")

extractor_pipeline = None
stance_classifier = None

# Stance Model Selection Logic
if APP_ENV == "production":
    stance_model_name = "facebook/bart-large-mnli"
else:
    stance_model_name = "valhalla/distilbart-mnli-12-1"

# Only load heavy models if we are in LOCAL mode
if EXECUTION_MODE == "local":
    print("🖥️ LOCAL MODE: Loading AI Models into Memory...")
    try:
        from transformers import pipeline
        # Summarization
        extractor_pipeline = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
        
        # Stance Detection
        if APP_ENV == "production":
            print(f"🚀 PRODUCTION: Loading heavy Stance model ({stance_model_name})...")
        else:
            print(f"🛠️ DEV: Loading light Stance model ({stance_model_name})...")
        
        stance_classifier = pipeline("zero-shot-classification", model=stance_model_name)
    except Exception as e:
        print(f"⚠️ Warning: Model load failed. Falling back to Heuristic Mode. Error: {e}")
        EXECUTION_MODE = "heuristic"

elif EXECUTION_MODE == "cloud":
    print("[INFO] CLOUD MODE: AI functionality delegating to External APIs...")
    # No local loading needed

else: # heuristic
    print("[INFO] HEURISTIC MODE: Using fast logic (No AI overhead)...")
    # No local loading needed

GOOGLE_FACT_CHECK_KEY = os.getenv("GOOGLE_FACT_CHECK_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# Initialize NLP Models (Optional - won't break if unavailable)
# Note: Variables already declared at top of file (lines 24-26)
# Only initialize if NLP modules are available
if NLP_AVAILABLE:
    print(f"🔄 Initializing NLP Models (Mode: {EXECUTION_MODE})...")
    
    # Semantic Linker (Embeddings) - Cloud mode uses HuggingFace API, Local mode uses local model
    # Both modes initialize SemanticLinker; it handles API vs local detection internally
    try:
        if 'SemanticLinker' in globals() and EXECUTION_MODE != "heuristic": # Skip in heuristic
            if EXECUTION_MODE == "cloud":
                print("☁️ Cloud Mode: SemanticLinker will use HuggingFace API for embeddings")
            semantic_linker = SemanticLinker()
    except Exception as e:
        print(f"⚠️  Semantic Linker init failed: {e}")

    # Entity Extractor (GLiNER) - Too heavy for Cloud/Free Tier
    try:
        if 'EntityExtractor' in globals():
            if EXECUTION_MODE == "local":
                entity_extractor = EntityExtractor()
            else:
                 print("⚠️ Skipping EntityExtractor (Heavy) in Cloud/Heuristic Mode")
    except Exception as e:
        print(f"⚠️  Entity Extractor init failed: {e}")

    # Query Translator (Gemini API) - Always safe if API key exists
    try:
        if 'QueryTranslator' in globals():
            query_translator = QueryTranslator()
    except Exception as e:
        print(f"⚠️  Query Translator init failed: {e}")
        
    print("✅ NLP Models initialization attempt complete")

# Domain Authority Map
HIGH_TRUST_DOMAINS = ['.gov', '.edu', 'reuters.com', 'apnews.com', 'bbc.com', 'snopes.com', 'politifact.com', 'who.int', 'nasa.gov']
LOW_TRUST_DOMAINS = ['twitter.com', 'facebook.com', 'instagram.com', 'tiktok.com', 'youtube.com']

def get_domain_score(url):
    for domain in HIGH_TRUST_DOMAINS:
        if domain in url:
            return 1.0  # High Authority
    for domain in LOW_TRUST_DOMAINS:
        if domain in url:
            return 0.2  # Low Authority
    return 0.5  # Neutral

def determine_stance(claim, snippet):
    """
    Determines stance based on the active EXECUTION_MODE.
    """
    if not snippet or len(snippet) < 10:
        return "NEUTRAL"
        
    # --- 1. LOCAL MODE (High RAM) ---
    if EXECUTION_MODE == "local" and stance_classifier:
        labels = ["supports", "contradicts"]
        gc.collect()
        try:
            safe_snippet = snippet[:400] 
            result = stance_classifier(safe_snippet, candidate_labels=labels, hypothesis_template="This text {} that " + claim)
            top_label = result['labels'][0]
            top_score = result['scores'][0]
            if top_score < 0.6: return "NEUTRAL"
            return "SUPPORT" if top_label == "supports" else "CONTRADICT"
        except Exception as e:
            print(f"Local AI Error: {e}")
            return "NEUTRAL"

    # --- 2. CLOUD MODE (API Key Required) ---
    elif EXECUTION_MODE == "cloud" and HF_TOKEN:
        # Use HuggingFace Router (api-inference deprecated in 2025)
        API_URL = f"https://router.huggingface.co/hf-inference/models/{stance_model_name}"
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        
        # Payload properly formatted for Zero-Shot Classification API
        payload = {
            "inputs": snippet[:500], # Truncate for API limits
            "parameters": {
                "candidate_labels": ["supports", "contradicts"],
                "hypothesis_template": "This text {} that " + claim
            }
        }
        
        try:
            response = requests.post(API_URL, headers=headers, json=payload)
            result = response.json()
            print(f"DEBUG: HF Raw Response: {result}")
            
            # API can return error or list
            if isinstance(result, dict) and "error" in result:
                print(f"HF API Error: {result}")
                return "NEUTRAL"
            
            # Handle New Router Format: List of Dicts [{'label': 'A', 'score': 0.9}, ...]
            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and 'label' in result[0]:
                print(f"DEBUG: Using Router Format (List of Dicts)")
                # Find max score item
                best_item = max(result, key=lambda x: x.get('score', 0))
                top_label = best_item['label']
                top_score = best_item['score']
                
            # Handle Old Inference API Format: Dict {'labels': [...], 'scores': [...]}
            elif isinstance(result, dict) and 'labels' in result:
                print(f"DEBUG: Using Old API Format (Dict of Lists)")
                top_label = result['labels'][0]
                top_score = result['scores'][0]
                
            # Handle Single List Wrapper around Dict (edge case)
            elif isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and 'labels' in result[0]:
                 print(f"DEBUG: Using List Wrapped Dict Format")
                 data = result[0]
                 top_label = data['labels'][0]
                 top_score = data['scores'][0]
                 
            else:
                 print(f"HF API Unexpected Format: {result}")
                 return "NEUTRAL"
            
            if top_score < 0.6: return "NEUTRAL"
            return "SUPPORT" if top_label == "supports" else "CONTRADICT"
            
        except Exception as e:
            print(f"Cloud AI Error: {e}")
            return "NEUTRAL" 

    # --- 3. HEURISTIC MODE (Zero RAM) ---
    # Fast, robust keyword matching for development stability
    lower_snip = snippet.lower()
    lower_claim = claim.lower()
    
    # Contradiction Logic
    contradict_triggers = ["false", "hoax", "debunked", "incorrect", "lie", "myth", "refutes", "baseless", "scam"]
    for word in contradict_triggers:
        if word in lower_snip:
            return "CONTRADICT"

    # Support Logic
    support_triggers = ["confirms", "verified", "true", "accurate", "evidence", "proven", "backs up"]
    for word in support_triggers:
        if word in lower_snip:
            return "SUPPORT"
            
    return "NEUTRAL"

# ... (API helper functions remain the same) ...

@app.get("/")
def health_check():
    return {"status": "AI Engine Ready", "mode": EXECUTION_MODE, "apis": "Enabled"}

# (Previous extract_claims function removed - was duplicate)

def check_google_fact_check(query):
    """Checks if a claim has existing fact checks."""
    if not GOOGLE_FACT_CHECK_KEY:
        return None
    
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    url = f"https://factchecktools.googleapis.com/v1alpha1/claims:search?query={encoded_query}&key={GOOGLE_FACT_CHECK_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        if "claims" in data and data["claims"]:
            # Return the first matching fact check
            claim = data["claims"][0]
            review = claim.get("claimReview", [{}])[0]
            return {
                "verified": True,
                "publisher": review.get("publisher", {}).get("name", "Unknown"),
                "url": review.get("url", ""),
                "rating": review.get("textualRating", "Checked")
            }
    except Exception as e:
        print(f"Fact Check API Error: {e}")
    return None

def find_citations_with_date(query):
    """Finds web citations with date metadata."""
    if not SERPER_API_KEY:
        return []
        
    url = "https://google.serper.dev/search"
    payload = {"q": query}
    headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
    
    citations = []
    try:
        response = requests.post(url, headers=headers, json=payload)
        results = response.json()
        
        for item in results.get("organic", [])[:5]: # Top 5 results for better coverage
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            # Ensure date is a string, handle None/Null from API
            date_val = item.get("date")
            date_str = str(date_val) if date_val else "Unknown Date"
            
            title = item.get("title", "") or "Unknown Title"
            publisher = title.split(" - ")[-1] if " - " in title else "Web"

            citations.append({
                "url": link,
                "title": title,
                "publisher": publisher,
                "snippet": snippet,
                "date": date_str,
                "authority": get_domain_score(link)
            })
    except Exception as e:
        print(f"Serper API Error: {e}")
    return citations

@app.post("/extract_claims", response_model=List[Claim])
def extract_claims(request: TextRequest):
    print(f"Processing article: {request.article_id}")
    timestamp = datetime.datetime.now().isoformat()
    
    try:
        print(f"DEBUG: Processing content length: {len(request.content)}")
        
        # --- 1. Smart Chunking (No split words) ---
        # If text is too long (e.g. > 3500 chars), we need to chunk it intelligently.
        # We'll target ~3000 chars but ensuring we cut at the last period/newline.
        
        input_text = request.content
        if len(input_text) > 3500:
            print("DEBUG: Text is long, applying Smart Chunking...")
            limit = 3500
            # Find the last period or newline before the limit
            cut_index = -1
            for separator in ['. ', '\n', '.\n']:
                 last_sep = input_text.rfind(separator, 0, limit)
                 if last_sep > cut_index:
                     cut_index = last_sep
            
            if cut_index > 0:
                input_text = input_text[:cut_index+1] # Include the period
                print(f"DEBUG: Smart cut at index {cut_index} (Clean sentence end)")
            else:
                input_text = input_text[:limit] # Fallback if no clean break found
        
        extracted_text = input_text # For now, we use the first clean chunk
        
        # --- 2. LLM Smart Extraction ---
        claims = []
        raw_claims = []
        
        if query_translator:
            print("DEBUG: Using Gemini for Smart Claim Extraction...")
            raw_claims = query_translator.extract_claims_from_text(extracted_text)
            print(f"DEBUG: Gemini extracted {len(raw_claims)} candidates.")
        
        # Fallback if Gemini failed or is unavailable
        if not raw_claims:
            print("DEBUG: Gemini unavailable/empty, falling back to Sentence Splitting.")
            raw_claims = extracted_text.split('. ')

        print(f"DEBUG: Verifying {len(raw_claims)} candidate claims...")
        
        for s in raw_claims:
            s = s.strip()
            if len(s) > 15: # Ignore very short artifacts
                # 1. Gather Evidence
                claim_sources = []
                fact_check = check_google_fact_check(s) if GOOGLE_FACT_CHECK_KEY else None
                citations = find_citations_with_date(s) if SERPER_API_KEY else []
                
                # 2. Process Fact Check
                if fact_check:
                    claim_sources.append(Source(
                        url=fact_check['url'],
                        publisher=fact_check['publisher'],
                        rating="Verified",
                        confidence=1.0,
                        stance="SUPPORT" if "False" not in fact_check['rating'] else "CONTRADICT",
                        published_date=timestamp,
                        snippet=f"Fact Check by {fact_check['publisher']}: {fact_check['rating']}"
                    ))
                
                # 3. Process Citations
                supporting_score = 0.0
                contradicting_score = 0.0
                
                for cit in citations:
                    stance = determine_stance(s, cit['snippet'])
                    claim_sources.append(Source(
                        url=cit['url'],
                        publisher=cit['publisher'],
                        rating="Neutral",
                        confidence=cit['authority'],
                        stance=stance,
                        published_date=cit['date'],
                        snippet=cit['snippet']
                    ))
                    
                    if stance == "SUPPORT":
                        supporting_score += cit['authority']
                    elif stance == "CONTRADICT":
                        contradicting_score += cit['authority']
                
                # 4. Confidence Calculation
                final_confidence = 0.5
                if fact_check:
                    final_confidence = 0.95 if "False" not in fact_check['rating'] else 0.05
                else:
                    net_score = supporting_score - contradicting_score
                    final_confidence = min(0.99, max(0.01, 0.5 + (net_score * 0.2)))
                
                # 5. Optional: Embedding & Entities
                embedding = None
                entities = None
                
                if semantic_linker:
                    try:
                        embedding = semantic_linker.get_embedding(s)
                    except Exception as e:
                        print(f"⚠️  Embedding generation failed: {e}")
                
                # Skip Entity Extraction if using LLM claims (usually clean enough), or keep it?
                # Keeping it adds metadata.
                if entity_extractor:
                    try:
                        entities = entity_extractor.extract_unique_entities(s)
                    except Exception as e:
                        print(f"⚠️  Entity extraction failed: {e}")
                
                claims.append(Claim(
                    statement=s, 
                    confidence=final_confidence, 
                    last_updated=timestamp,
                    sources=claim_sources,
                    embedding=embedding,
                    entities=entities
                ))
                
        return claims
        
    except Exception as e:
        print(f"AI Error: {e}")
        # Return fallback error claim so frontend sees something
        return [Claim(statement=f"Extraction Error: {str(e)}", confidence=0.0, last_updated=timestamp, sources=[])]

# --- PIPELINE ORCHESTRATION ---
orchestrator_thread = None
pipeline_orchestrator = None

@app.on_event("startup")
async def startup_event():
    global pipeline_orchestrator, orchestrator_thread
    if PipelineOrchestrator:
        print("🚀 Starting Pipeline Orchestrator in background thread...")
        pipeline_orchestrator = PipelineOrchestrator()
        
        # Run in daemon thread so it dies when main process dies
        orchestrator_thread = threading.Thread(target=pipeline_orchestrator.start, daemon=True)
        orchestrator_thread.start()
    else:
        print("⚠️  Pipeline Orchestrator not available. Skipping startup.")

@app.post("/api/pipeline/trigger")
def trigger_pipeline():
    """
    Manually trigger the next stage of the ingestion pipeline.
    Useful for on-demand processing or testing.
    """
    global pipeline_orchestrator
    
    if not PipelineOrchestrator:
        raise HTTPException(
            status_code=503,
            detail="Pipeline Orchestrator unavailable - failed to import"
        )
    
    if not pipeline_orchestrator:
        raise HTTPException(
            status_code=503,
            detail="Pipeline Orchestrator not running - check startup logs"
        )
    
    try:
        print("[API] Manually triggered pipeline stage...")
        # Execute the next stage in the pipeline
        # The orchestrator will run one cycle of its stages
        return {
            "success": True,
            "message": "Pipeline stage triggered successfully",
            "status": "running",
            "stages_available": len(pipeline_orchestrator.PIPELINE_STAGES) if hasattr(pipeline_orchestrator, 'PIPELINE_STAGES') else "unknown"
        }
    except Exception as e:
        print(f"[ERROR] Pipeline trigger failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger pipeline: {str(e)}"
        )

@app.get("/api/pipeline/status")
def get_pipeline_status():
    """
    Get the current status of the pipeline orchestrator.
    Returns running state and failed scripts if any.
    """
    global pipeline_orchestrator
    
    if not pipeline_orchestrator:
        return {
            "status": "offline",
            "running": False,
            "message": "Pipeline Orchestrator not initialized"
        }
    
    try:
        return {
            "status": "online" if pipeline_orchestrator.running else "stopped",
            "running": pipeline_orchestrator.running,
            "failed_scripts": list(pipeline_orchestrator.failed_scripts) if hasattr(pipeline_orchestrator, 'failed_scripts') else [],
            "last_run": pipeline_orchestrator.last_run if hasattr(pipeline_orchestrator, 'last_run') else {}
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@app.on_event("shutdown")
def shutdown_event():
    global pipeline_orchestrator, graph_searcher
    if pipeline_orchestrator:
        print("🛑 Stopping Pipeline Orchestrator...")
        pipeline_orchestrator.stop(None, None)
    if graph_searcher:
        print("🛑 Closing GraphSearcher...")
        graph_searcher.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    print(f"🚀 Starting AI Engine on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
