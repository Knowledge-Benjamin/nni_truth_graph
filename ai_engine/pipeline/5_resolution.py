"""
Stage 5: Provenance Discovery & Neo4j Cross-Reference Engine

For each PROCESSING claim this stage:
1. Builds a semantic search fingerprint from the claim's SPO triple.
2. Uses Serper API (Google Search) to hunt for the earliest occurrence of that fact on the internet.
3. Validates candidate URLs against the Wayback Machine CDX API for true first-seen timestamps.
4. Cross-references the claim against existing Neo4j graph nodes via embedding cosine similarity.
5. Classifies the relationship: ORIGINAL | CORROBORATES | CONTRADICTS | DUPLICATE | EVOLVES
6. If a true original source is found outside our graph, fires a new URL into the Stage 1 queue.
7. Updates the claim's pipeline_stage and the epistemic score based on findings.
"""

import os
import sys
import time
import math
import json
import requests  # type: ignore[import]
import psycopg2  # type: ignore[import]
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

print("[INIT] Loading Neo4j driver...")
from neo4j import GraphDatabase  # type: ignore[import]

print("[INIT] Loading dotenv...")
from dotenv import load_dotenv  # type: ignore[import]

# Force UTF-8 output to avoid Windows charmap errors on Unicode characters
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

# Ensure the project root is on the path so we can import ai_engine.core
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

print("[INIT] Importing core modules (this may block if testing API keys)...")
from ai_engine.core.epistemic_trust import EpistemicTrustScorer  # type: ignore[import]
from ai_engine.core.logger import get_printer  # type: ignore[import]
print("[INIT] Importing inference_pool...")
from ai_engine.core.inference_pool import inference_pool as hf_pool  # type: ignore[import]
print = get_printer(5)  # Bright Green

print("[INIT] Instantiating Scorer...")
_scorer = EpistemicTrustScorer()

print("[INIT] Loading dotenv for DB urls...")
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

print("[INIT] Loading DATABASE_URL...")
DATABASE_URL   = os.getenv("DATABASE_URL")
print("[INIT] Loading SEARXNG_URL...")
SEARXNG_URL    = os.getenv("SEARXNG_URL", "https://knowledgebenji-searchserver.hf.space")
print("[INIT] Loading GROQ_API_KEY...")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
print("[INIT] Loading NEO4J_URI...")
NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
print("[INIT] Loading NEO4J_USER...")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
print("[INIT] Loading NEO4J_PASSWORD...")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type  # type: ignore[import]

def embed_text(text: str) -> Optional[list]:
    """Returns a 768-dim embedding via HF Inference API (key-rotating pool)."""
    try:
        return hf_pool.embed(text)
    except Exception as e:
        print(f"      [Embed Error] {e}")
        return None

print("[INIT] Importing groq_pool...")
from ai_engine.core.groq_pool import groq_pool  # type: ignore[import]

print("[INIT] Initializing Neo4j Driver globally...")
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

llm_client   = groq_pool
# Keep resolution concurrency low on HF Spaces to reduce Serper/HF load.
# Increased from 2 to 10 per request.
MAX_WORKERS = 4

WAYBACK_CDX = os.getenv(
    "WAYBACK_CDX_URL",
    f"{os.getenv('CC_PROXY_BASE', 'https://cc-proxy-qvem3ril2q-uc.a.run.app')}/wayback/cdx/search/cdx"
)
SEARXNG_SEARCH_URL = f"{SEARXNG_URL}/search"
CC_PROXY_BASE = os.getenv("CC_PROXY_BASE", "https://cc-proxy-qvem3ril2q-uc.a.run.app")

_cc_index_url_cache: str | None = None

def _get_cc_index_url() -> str | None:
    """Fetch and cache the latest Common Crawl CDX index URL via proxy (one-time lookup)."""
    global _cc_index_url_cache
    if _cc_index_url_cache:
        return _cc_index_url_cache
    try:
        headers = {'User-Agent': 'KnowledgeBenjiTruthGraphBot/1.0 (Contact: admin@example.com)'}
        timeout = int(os.getenv('CC_PROXY_TIMEOUT', '10'))
        resp = requests.get(f"{CC_PROXY_BASE}/collinfo.json", headers=headers, timeout=timeout)
        resp.raise_for_status()
        raw = resp.json()
        if raw:
            # Proxy rewrites cdx-api to relative paths, so prepend the proxy base
            raw_path = raw[0].get('cdx-api', '')
            if raw_path.startswith('/'):
                _cc_index_url_cache = f"{CC_PROXY_BASE}{raw_path}"
            else:
                # Fully qualified — replace index.commoncrawl.org with the proxy
                _cc_index_url_cache = raw_path.replace("https://index.commoncrawl.org", CC_PROXY_BASE)
    except requests.exceptions.RequestException as e:
        print(f"  [CC] Network error fetching collinfo via proxy: {e}")
        # Leave cache unset; caller will treat as unavailable
        return None
    except Exception as e:
        print(f"  [CC] Failed to fetch collinfo via proxy: {e}")
    return _cc_index_url_cache

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x**2 for x in a))
    mag_b = math.sqrt(sum(x**2 for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0

@retry(
    wait=wait_exponential(multiplier=1.5, min=4, max=20),
    stop=stop_after_attempt(4),
    retry=retry_if_exception_type(Exception)
)
def searxng_search(query: str, date_before: Optional[str] = None) -> list[dict]:
    """
    Searches via our self-hosted SearXNG instance.
    date_before: ISO date string 'YYYY-MM-DD'.
    """
    # Note: SearXNG time ranges are mostly: day, week, month, year. 
    # Exact before-date is engine specific. We'll specify format=json
    payload = {
        "q": query,
        "format": "json",
        "engines": "google,bing,brave,qwant"
    }
    headers = {
        "User-Agent": "KnowledgeBenjiTruthGraphBot/1.0 (Contact: admin@example.com)",
        "Accept": "application/json",
    }
    # If date_before is strict, SearXNG doesn't seamlessly support "before X date" out of the box
    # across all engines, but time_range="year" can be used if recent. 
    # For now, we rely on Wayback CDX for exact timestamp validation anyway.
    
    resp = requests.post(SEARXNG_SEARCH_URL, data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])

import threading

_archive_lock = threading.Lock()
_last_archive_call = 0.0
ARCHIVE_RATE_LIMIT = 60.0 / 45.0  # 45 requests per minute

def _apply_rate_limit():
    global _last_archive_call
    with _archive_lock:
        now = time.time()
        elapsed = now - _last_archive_call
        if elapsed < ARCHIVE_RATE_LIMIT:
            time.sleep(ARCHIVE_RATE_LIMIT - elapsed)
        _last_archive_call = time.time()

@retry(
    wait=wait_exponential(multiplier=1.5, min=2, max=6),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((requests.exceptions.RequestException, Exception))
)
def wayback_first_seen(url: str) -> datetime | None:
    """Query CDX API for the earliest archived snapshot of a URL. Retries on timeout."""
    try:
        _apply_rate_limit()
        headers = {'User-Agent': 'KnowledgeBenjiTruthGraphBot/1.0 (Contact: admin@example.com)'}
        timeout = int(os.getenv('WAYBACK_TIMEOUT', '60'))
        params = {
            "url": url, "output": "json", "fl": "timestamp",
            "limit": 1, "from": "19900101", "filter": "statuscode:200"
        }
        try:
            resp = requests.get(WAYBACK_CDX, params=params, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            # Network issues (timeout, connection error, DNS). Log and return None to avoid crashing worker.
            print(f"  [WAYBACK NETWORK] {e} -- treating as 'not archived' for now")
            return None

        if not resp.text.strip():
            return None # URL not archived

        try:
            data = resp.json()
        except ValueError:
            return None # Invalid JSON usually means not archived

        if data and len(data) > 1:
            ts = data[1][0]  # Skip header row
            return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return None
    except Exception as e:
        # Unexpected errors: log and return None rather than raising to keep worker alive
        print(f"  [WAYBACK ERROR] Unexpected exception: {e}")
        return None

@retry(
    wait=wait_exponential(multiplier=1.5, min=2, max=6),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((requests.exceptions.RequestException, Exception))
)
def common_crawl_first_seen(url: str) -> datetime | None:
    """Query Common Crawl Index API for the earliest archived snapshot. Retries on timeout."""
    try:
        _apply_rate_limit()
        headers = {'User-Agent': 'KnowledgeBenjiTruthGraphBot/1.0 (Contact: admin@example.com)'}
        
        latest_index = _get_cc_index_url()
        if not latest_index:
            return None  # Proxy unavailable or still booting up
        
        _apply_rate_limit()
        params = {"url": url, "output": "json", "limit": 1}
        resp = requests.get(latest_index, params=params, headers=headers, timeout=30)
        
        if resp.status_code == 200 and resp.text.strip():
            first_line = resp.text.strip().split('\n')[0]
            data = json.loads(first_line)
            ts = data.get("timestamp")
            if ts:
                return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return None
    except (requests.exceptions.RequestException, Exception) as e:
        print(f"  [CC ERROR] {repr(e)} | Retrying {url[:30]}...")
        raise e

def fire_new_ingestion(pg_conn, url: str, source_name: str):
    """Insert a newly discovered original source URL into the Stage 1 queue."""
    try:
        with pg_conn.cursor() as cur:
            # Upsert a 'DISCOVERED' source record
            cur.execute("""
                INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                VALUES (%s, %s, %s, 'Discovered', 0.50)
                ON CONFLICT (url) DO NOTHING
                RETURNING id;
            """, (source_name, url, url.split('/')[2] if '/' in url else url))
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM sources WHERE url = %s", (url,))
                row = cur.fetchone()
            source_id = row[0]

            # Queue the URL for scraping
            cur.execute("""
                INSERT INTO raw_urls (source_id, url, metadata, status)
                VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                ON CONFLICT (url) DO NOTHING;
            """, (source_id, url, json.dumps({"origin": "provenance_discovery"})))
            pg_conn.commit()
            print(f"      -> [FIRED INGESTION] New original source queued: {url[:60]}")  # type: ignore[index]
    except Exception as e:
        pg_conn.rollback()
        print(f"      [INGESTION FIRE ERROR] {e}")

# ─────────────────────────────────────────────────────────────────────────────
# NEO4J CROSS-REFERENCE
# ─────────────────────────────────────────────────────────────────────────────

def neo4j_cross_reference(subject: str, predicate: str, obj: str, claim_embedding: list[float]) -> dict:
    """
    Query Neo4j for existing claims similar to this one.
    Primary path: Neo4j 5.x vector index (single round-trip HNSW query).
    Fallback: Python cosine loop over predicate-filtered scan (used if index not yet built).
    Returns the best matching claim and the classification.
    """
    result: dict = {"stance": "ORIGINAL", "matched_claim_id": None, "similarity": 0.0, "contradiction_weights": []}

    try:
        with neo4j_driver.session() as session:

            # ── PRIMARY PATH: Neo4j 5.x vector index ──────────────────────────
            # Requires: CREATE VECTOR INDEX claim_embedding_idx IF NOT EXISTS
            #   FOR (c:Claim) ON c.embedding
            #   OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}
            records = []
            use_vector_index = False
            try:
                vq = session.run("""
                    CALL db.index.vector.queryNodes('claim_embedding_idx', 5, $embedding)
                    YIELD node AS c, score
                    WHERE c.predicate = $predicate
                    RETURN c.id AS id, c.subject AS subj, c.object AS obj,
                           c.embedding AS emb, c.epistemic_score AS es,
                           score AS vector_score
                """, embedding=claim_embedding, predicate=predicate).data()
                if vq:
                    records = vq
                    use_vector_index = True
            except Exception:
                # Index not yet created — fall back to full Python cosine scan
                pass

            # ── FALLBACK: predicate-filtered Python cosine loop ───────────────
            if not use_vector_index:
                records = session.run("""
                    MATCH (c:Claim)
                    WHERE c.predicate = $predicate AND c.embedding IS NOT NULL
                    RETURN c.id AS id, c.subject AS subj, c.object AS obj,
                           c.embedding AS emb, c.epistemic_score AS es
                    LIMIT 200
                """, predicate=predicate).data()

            best_sim = 0.0
            best_id  = None
            best_es  = 0.0
            best_rec = None

            for rec in records:
                # Vector index already gives us score; fallback computes cosine manually
                if use_vector_index:
                    sim = float(rec.get("vector_score", 0.0))
                else:
                    if rec.get("emb") is None:
                        continue
                    neo_emb = rec["emb"]
                    if len(neo_emb) != len(claim_embedding):
                        continue
                    sim = cosine_similarity(claim_embedding, neo_emb)

                if sim > best_sim:
                    best_sim = sim
                    best_id  = rec["id"]
                    best_es  = rec.get("es") or 0.4
                    best_rec = rec

            if best_sim >= 0.95:
                # Very high similarity — same claim, invert predicate meaning to detect contradiction
                # Use Groq to do semantic stance detection on the objects
                stance_prompt = f"""
You are a logical stance detector. Compare these two claim objects:
Claim A object: "{obj}"
Claim B object: "{best_rec['obj'] if best_rec else ''}"

Given they share subject "{subject}" and predicate "{predicate}", are they:
- DUPLICATE (semantically identical)
- CONTRADICTS (logically opposite or mutually exclusive)
- CORROBORATES (supports the same conclusion)
- EVOLVES (Claim A is a newer update to Claim B)

Reply with exactly one word: DUPLICATE, CONTRADICTS, CORROBORATES, or EVOLVES.
"""
                try:
                    stance_resp = llm_client.chat_completions_create(
                        model='TIER_HEAVY',
                        messages=[
                            {"role": "system", "content": "You are a logical stance detector. Reply with exactly one word: DUPLICATE, CONTRADICTS, CORROBORATES, or EVOLVES."},
                            {"role": "user", "content": stance_prompt}
                        ],
                        temperature=0.0
                    )
                    stance_word = stance_resp.choices[0].message.content.strip().upper().split()[0]
                    if stance_word in ("DUPLICATE", "CONTRADICTS", "CORROBORATES", "EVOLVES"):
                        result["stance"] = stance_word
                except Exception:
                    result["stance"] = "CORROBORATES" if best_sim >= 0.98 else "ORIGINAL"

                result["matched_claim_id"] = best_id
                result["similarity"] = round(best_sim, 4)  # type: ignore[call-overload]
                if result["stance"] == "CONTRADICTS":
                    result["contradiction_weights"].append(best_es)

            elif best_sim >= 0.80:
                result["stance"] = "CORROBORATES"
                result["matched_claim_id"] = best_id
                result["similarity"] = round(best_sim, 4)  # type: ignore[call-overload]

    except Exception as e:
        print(f"  [NEO4J ERROR] {e}")

    return result

# ─────────────────────────────────────────────────────────────────────────────
# MAIN WORKER
# ─────────────────────────────────────────────────────────────────────────────

def resolution_worker(worker_id: int):
    try:
        # ── Two-phase queue: investigation items first, background second ──
        # Phase 2 (background items) is skipped when an investigation is active
        # so downstream investigation content is not starved by background work.
        phases = [
            (100, "AND ru.metadata->>'investigation_id' IS NOT NULL"),
        ]
        try:
            with psycopg2.connect(DATABASE_URL) as _inv_check:
                with _inv_check.cursor() as _inv_cur:
                    _inv_cur.execute("SELECT COUNT(*) FROM investigations WHERE status = 'ACTIVE'")
                    _active_inv = _inv_cur.fetchone()[0]
        except Exception:
            _active_inv = 0
            
        if _active_inv == 0:
            phases.append((20, "AND ru.metadata->>'investigation_id' IS NULL"))
            
        for __phase, (__limit, __filter_clause) in enumerate(phases):
            items_processed = 0

            while items_processed < __limit:
                try:
                    claim_id = None
                    subject = None
                    predicate = None
                    obj = None
                    temporal = None
                    spatial = None
                    extr_conf = None
                    epist_score = None
                    pub_date = None
                    art_title = None
                    ingest_url = None
                    src_trust = None
                    ai_metadata = None

                    with psycopg2.connect(DATABASE_URL) as claim_conn:
                        with claim_conn.cursor() as cur:
                            cur.execute(f"""
                                SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
                                       ec.temporal_anchor, ec.spatial_anchor, ec.extraction_confidence, ec.epistemic_score,
                                       ra.publish_date, ra.title, ru.url, s.epistemic_trust_score, ec.ai_metadata
                                FROM extracted_claims ec
                                JOIN raw_articles ra ON ec.article_id = ra.id
                                JOIN raw_urls ru     ON ra.url_id = ru.id
                                JOIN sources s       ON ru.source_id = s.id
                                WHERE ec.status = 'PROCESSING'
                                  AND ec.pipeline_stage = 'STAGE_4_RESOLUTION'
                                {__filter_clause}
                                    ORDER BY ec.id ASC
                                LIMIT 1
                                FOR UPDATE OF ec SKIP LOCKED;
                            """)
                            row = cur.fetchone()
                            if not row:
                                claim_conn.rollback()
                                break

                            (claim_id, subject, predicate, obj, temporal, spatial,
                             extr_conf, epist_score, pub_date, art_title, ingest_url, src_trust, ai_metadata) = row
                            cur.execute(
                                "UPDATE extracted_claims SET pipeline_stage = 'STAGE_5_RESOLUTION_IN_PROGRESS' WHERE id = %s",
                                (claim_id,),
                            )
                            claim_conn.commit()

                    if claim_id is None or subject is None or predicate is None or obj is None:
                        break

                    print(f"  [W-{worker_id}] Resolving: [{predicate}] {subject[:30]} → {obj[:30]}")

                    # ── Build search fingerprint ──────────────────────────────
                    spo_text     = f"{subject} {predicate.replace('_',' ')} {obj}"
                    claim_embed  = embed_text(spo_text)

                    # ── A. Internet Provenance Hunt ───────────────────────────
                    pub_date_str = pub_date.strftime("%Y-%m-%d") if pub_date else None
                    try:
                        search_results = searxng_search(spo_text, date_before=pub_date_str)
                    except Exception as e:
                        print(f"  [SEARXNG SKIP] Search exhausted retries ({type(e).__name__}). Continuing without internet sources.")
                        search_results = []

                    original_url   = None
                    original_date  = pub_date
                    original_name  = "Unknown"
                    is_our_url_original = True  # assume ours is original until proven otherwise

                    for result in search_results[:5]:  # type: ignore[index]
                        candidate_url  = result.get("url", "")
                        candidate_date_str = result.get("publishedDate", "")
                        candidate_source   = result.get("engine") or result.get("title") or "Unknown Source"

                        # Parse Date - SearXNG formats vary depending on engine
                        candidate_date = None
                        if candidate_date_str:
                            import re
                            rel = re.match(r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', candidate_date_str, re.I)
                            if rel:
                                n, unit = int(rel.group(1)), rel.group(2).lower()
                                deltas = {
                                    'second': timedelta(seconds=n), 'minute': timedelta(minutes=n),
                                    'hour': timedelta(hours=n), 'day': timedelta(days=n),
                                    'week': timedelta(weeks=n), 'month': timedelta(days=n*30),
                                    'year': timedelta(days=n*365)
                                }
                                candidate_date = (datetime.now(timezone.utc) - deltas.get(unit, timedelta(0)))
                            else:
                                for fmt in ("%b %d, %Y", "%Y-%m-%d", "%B %d, %Y", "%d %b %Y"):
                                    try:
                                        candidate_date = datetime.strptime(candidate_date_str, fmt).replace(tzinfo=timezone.utc)
                                        break
                                    except ValueError:
                                        continue

                        # Try Wayback Machine and Common Crawl (Dual Archive Validation)
                        wayback_date = None
                        cc_date = None
                        try:
                            wayback_date = wayback_first_seen(candidate_url)
                        except Exception:
                            pass

                        try:
                            cc_date = common_crawl_first_seen(candidate_url)
                        except Exception:
                            pass

                        # Take the absolute earliest verified date we have found
                        valid_dates = [d for d in [wayback_date, cc_date, candidate_date] if d is not None]
                        earliest = min(valid_dates) if valid_dates else None

                        # Normalize pub_date timezone for comparison
                        local_pub = pub_date
                        if local_pub and local_pub.tzinfo is None:
                            local_pub = local_pub.replace(tzinfo=timezone.utc)

                        if earliest:
                            if local_pub is None:
                                if original_url is None:
                                    original_url  = candidate_url
                                    original_date = earliest
                                    original_name = candidate_source
                            elif earliest < local_pub:
                                is_our_url_original = False
                                original_url  = candidate_url
                                original_date = earliest
                                original_name = candidate_source

                    # ── B. Neo4j Cross-Reference ──────────────────────────────
                    neo4j_result = {"stance": "ORIGINAL", "matched_claim_id": None,
                                    "similarity": 0.0, "contradiction_weights": []}
                    if claim_embed:
                        neo4j_result = neo4j_cross_reference(subject, predicate, obj, list(claim_embed))  # type: ignore[arg-type]

                    final_stance = neo4j_result["stance"]

                    if not is_our_url_original and final_stance == "ORIGINAL":
                        final_stance = "CORROBORATES"

                    # ── C. Re-score with new intelligence ────────────────
                    days_old = (datetime.now(timezone.utc) - original_date).days if original_date else 0
                    support_count = 1 if final_stance == "CORROBORATES" else 0

                    try:
                        ai_data = json.loads(ai_metadata) if ai_metadata else {}
                    except Exception:
                        ai_data = {}
                    media_synth_prob = ai_data.get("synthetic_probability")

                    new_score = _scorer.calculate_epistemic_score(
                        extraction_confidence=extr_conf,
                        source_tier=1 if src_trust >= 0.80 else (2 if src_trust >= 0.50 else 3),
                        support_count=support_count,
                        contradiction_weights=neo4j_result.get("contradiction_weights", []), # type: ignore
                        days_since_extracted=days_old,
                        historical_source_reliability=src_trust,
                        media_synthetic_prob=media_synth_prob
                    )

                    routing = _scorer.determine_routing(new_score)

                    # ── D. Persist results ───────────────────────────────────
                    # Routing decision:
                    #   AUTO_APPROVE  → STAGE_6_DEDUP as PROCESSING (S7 does the definitive rescore)
                    #   HUMAN_REVIEW  → STAGE_HELD_FOR_REVIEW (admin reviews via /api/human-review)
                    #   AUTO_REJECT   → STAGE_HELD_FOR_REVIEW (terminal, never touches graph)
                    # HUMAN_REVIEW/AUTO_REJECT must NOT enter STAGE_6_DEDUP — S6 only processes
                    # PROCESSING items, so they would sit there permanently, invisible to the
                    # terminator's in-flight count and never surfaced to the human-review queue.
                    if routing in ("HUMAN_REVIEW", "AUTO_REJECT"):
                        out_stage = "STAGE_HELD_FOR_REVIEW"
                        out_status = routing          # preserve HUMAN_REVIEW / AUTO_REJECT
                    else:
                        out_stage = "STAGE_6_DEDUP"
                        out_status = "PROCESSING"     # AUTO_APPROVE flattened; S7 rescores

                    with psycopg2.connect(DATABASE_URL) as write_conn:
                        with write_conn.cursor() as cur:
                            cur.execute("""
                                UPDATE extracted_claims
                                SET epistemic_score  = %s,
                                    status           = %s,
                                    pipeline_stage   = %s
                                WHERE id = %s
                            """, (new_score, out_status, out_stage, claim_id))

                            cur.execute("""
                                INSERT INTO claim_provenance
                                    (claim_id, internet_original_url, internet_original_source,
                                     internet_original_date, is_our_source_original,
                                     neo4j_stance, neo4j_matched_claim_id, neo4j_similarity)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (claim_id) DO UPDATE SET
                                    internet_original_url = EXCLUDED.internet_original_url,
                                    neo4j_stance = EXCLUDED.neo4j_stance;
                            """, (
                                claim_id, original_url, original_name,
                                original_date, is_our_url_original,
                                final_stance,
                                neo4j_result["matched_claim_id"],
                                neo4j_result["similarity"]
                            ))
                        write_conn.commit()

                    print(f"      -> [W-{worker_id}] Stance: {final_stance} | Score: {new_score:.2f} | Route: {routing}")

                    # ── E. Fire new ingestion if original is not ours ────────
                    if not is_our_url_original and original_url and original_url != ingest_url:
                        with psycopg2.connect(DATABASE_URL) as ingest_conn:
                            fire_new_ingestion(ingest_conn, original_url, original_name)

                    items_processed += 1
                    time.sleep(1.5)  # Respect Serper rate limits

                except Exception as loop_err:
                    print(f"  [ERROR W-{worker_id} Loop] {loop_err}. Rolling back to keep in queue.")
                    try:
                        with psycopg2.connect(DATABASE_URL) as rollback_conn:
                            rollback_conn.rollback()
                    except Exception:
                        pass
                    time.sleep(10)
    except Exception as fatal_e:
        print(f"[FATAL W-{worker_id}] {fatal_e}")


def process_resolution_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 5: Provenance Discovery & Neo4j Cross-Reference Engine (Single Pass)")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checking STAGE_4_RESOLUTION queue...")
        cur.execute("""
            SELECT COUNT(*) FROM extracted_claims
            WHERE status = 'PROCESSING' AND pipeline_stage = 'STAGE_4_RESOLUTION';
        """)
        row = cur.fetchone()
        pending = row[0] if row else 0
        cur.close()
        conn.close()

        if pending == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return

        workers = min(MAX_WORKERS, max(1, pending))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {pending} claims pending. Spinning {workers} provenance threads...")

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(resolution_worker, i) for i in range(workers)]  # type: ignore[arg-type]
            for f in futures:
                f.result()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch complete.")

    except KeyboardInterrupt:
        print("Stopping Resolution Engine.")
        neo4j_driver.close()
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    process_resolution_queue()
