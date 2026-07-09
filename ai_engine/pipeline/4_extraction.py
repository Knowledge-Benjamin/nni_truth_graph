# pyre-ignore-all-errors
import os
import re
import time
import psycopg2  # type: ignore
import json
from pydantic import BaseModel, Field  # type: ignore
from threading import current_thread
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv  # type: ignore

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ai_engine.core.epistemic_trust import EpistemicTrustScorer  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.logger import get_printer  # type: ignore
print = get_printer(4)  # Bright Magenta

# Force the 70B environment key check
if not os.getenv("GROQ_API_KEY_70B"):
    print("[WARNING] GROQ_API_KEY_70B is not set. Extraction will fall back to the 8B pool or fail.")

DATABASE_URL = os.getenv("DATABASE_URL")
from ai_engine.core.groq_pool import groq_pool  # type: ignore
scorer = EpistemicTrustScorer()

from typing import Optional

# Keep extraction concurrency low on HF Spaces to minimize LLM/CPU load.
MAX_WORKERS = 1

class AtomicClaim(BaseModel):
    subject: str = Field(description="The normalized main entity. MUST be a proper noun, specific event name, or core concept (e.g., 'Donald Trump', 'Knowledge', 'World War II'). Do NOT use descriptive statements. Max 3-5 words. MUST resolve all pronouns (anaphoric resolution) e.g., 'He' -> 'John Smith'.")
    predicate: str = Field(description="The action or relationship. Use a strict standardized ontology (e.g., IS_A, REPORTED, INCREASED, DECREASED, LAUNCHED, DISCOVERED, GOT_PHD).")
    object_entity: Optional[str] = Field(None, description="The target entity, concept, or value of the predicate. MUST be a standalone noun, entity name, or specific value. Do NOT use descriptive statements.")
    temporal_anchor: Optional[str] = Field(None, description="When does this claim apply according to the text? e.g., '2026-02-24', 'Future', 'Ongoing', 'In the 1990s', '27th Feb 2025'.")
    spatial_anchor: Optional[str] = Field(None, description="Where did this event or fact take place? e.g., 'Mexico', 'MIT University in Mexico', 'New York City'. Leave empty if not applicable.")
    is_verifiable: bool = Field(description="True if this claim can be objectively proven true or false. False if it is subjective opinion or unprovable.")
    quote_context: Optional[str] = Field(None, description="A brief verbatim snippet from the article that supports this claim. CRITICAL: use only single-quote characters (') inside this field — NEVER raw double-quote characters (\"). Replace any double quotes in the source text with single quotes.")
    extraction_confidence: float = Field(description="Score from 0.0 to 1.0 indicating how confident you are in this extraction. Penalize vague language.")
    epistemic_domain: str = Field(description="The Non-Overlapping Magisteria domain. MUST be exactly one of: 'EMPIRICAL' (science/news/history), 'THEOLOGICAL' (religion/divinity), 'PHILOSOPHICAL', or 'LEXICAL'.")

class ClaimExtractionList(BaseModel):
    """Root wrapper object containing all extracted atomic claims."""
    claims: list[AtomicClaim]

PROMPT_VERSION = "v2.1-gemma-strict"

def generate_extraction_prompt(title, author, date, text):
    return f"""
You are a world-class Knowledge Graph extraction engine for the Living Truth Graph.
Your task is to comprehensively analyze the following article and extract standalone, verifiable facts as atomic claims.

STRICT CONSTRAINTS (CRITICAL):
1. Stand-alone facts: Each claim MUST make perfect sense out of context.
2. Strict Entity Normalization (Nouns Only): `subject` and `object_entity` MUST be pure names or nouns (e.g., 'Donald Trump', 'MIT University', 'Knowledge'). Do NOT extract full sentences or descriptive statements. Max 3-5 words.
3. Pronoun resolution (Anaphoric): You MUST completely replace "he", "she", "it", "they", "the company" with the actual normalized entity name from the text.
4. Predicate Ontology: Standardize relationships. (e.g., OWNED_BY, ACQUIRED, STATED, DEVELOPED, GOT_PHD).
5. Temporal & Spatial Anchors: Pinpoint the temporal context if mentioned (When: "27th Feb 2025") and the spatial context (Where: "Mexico" or "MIT University").
6. Verifiability constraint: Only extract claims that can be objectively proven true or false.
7. Contradiction Pre-computation: Capture nuance that might contradict other facts.
8. Epistemic Domain: Categorize the claim into its NOMA magisterium ('EMPIRICAL', 'THEOLOGICAL', 'PHILOSOPHICAL', 'LEXICAL'). Most news is 'EMPIRICAL'.

FORMATTING INSTRUCTION:
You are a JSON-only API. Output ONLY a valid JSON object. No conversational text, no markdown code blocks (do not wrap in ```json), no preambles.
Extract all standalone, verifiable facts from this chunk.
Your output must EXACTLY match this structure:
{{
  "claims": [
    {{
      "subject": "Entity Name",
      "predicate": "ACTION_VERB",
      "object_entity": "Target Entity",
      "temporal_anchor": "Time or Date",
      "spatial_anchor": "Location",
      "is_verifiable": true,
      "quote_context": "'Exact snippet with single quotes only'",
      "extraction_confidence": 0.9,
      "epistemic_domain": "EMPIRICAL"
    }}
  ]
}}

Article Title: {title}
Author: {author}
Date: {date}

Content:
{text}
"""


from typing import Optional


def normalize_extraction_json(raw_text: Optional[str]) -> str:
    if raw_text is None:
        return ""
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)

    raw_text = raw_text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.I)
    raw_text = re.sub(r"\s*```$", "", raw_text, flags=re.I)
    raw_text = raw_text.strip()

    start_positions = [pos for pos in (raw_text.find("{"), raw_text.find("[")) if pos != -1]
    if start_positions:
        start = min(start_positions)
        end = max(raw_text.rfind("}"), raw_text.rfind("]"))
        if end >= start:
            raw_text = raw_text[start:end+1]

    raw_text = re.sub(r",\s*(?=[}\]])", "", raw_text)
    return raw_text


def load_extraction_json(raw_text: Optional[str]):
    raw_text = normalize_extraction_json(raw_text)
    if not raw_text:
        raise ValueError("Empty response after normalization")
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as json_err:
        import ast
        raw_text_python = re.sub(r"\bnull\b", "None", raw_text)
        raw_text_python = re.sub(r"\btrue\b", "True", raw_text_python)
        raw_text_python = re.sub(r"\bfalse\b", "False", raw_text_python)
        
        # Aggressively try to close the array and object if they were left open
        if raw_text_python.count('[') > raw_text_python.count(']'):
            raw_text_python += ']'
        if raw_text_python.count('{') > raw_text_python.count('}'):
            raw_text_python += '}'
            
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] JSON fallback repaired string:\n{raw_text_python}")
        try:
            return ast.literal_eval(raw_text_python)
        except SyntaxError:
            # Last ditch effort to close it correctly assuming structure: {"claims": [ ... ]}
            if raw_text_python.strip().endswith("}") and '"claims": [' in raw_text_python:
                raw_text_python = raw_text_python + "]}"
            return ast.literal_eval(raw_text_python)


def verify_cross_modal(claim_text: str, article_id: int, cursor) -> tuple[float | None, float | None]:
    """Calculates Cosine Similarity + Returns Model Synthetic Probability if Hero Image exists."""
    try:
        cursor.execute("SELECT clip_embedding, synthetic_probability FROM media_provenance WHERE raw_article_id = %s", (article_id,))
        row = cursor.fetchone()
        if not row or not row[0]: # Try to fetch pgvector object
            return None, None
            
        synth_prob = float(row[1]) if row[1] is not None else None
            
        import requests
        VISION_URL = os.getenv("VISION_INFERENCE_URL", "http://localhost:7860")
        resp = requests.post(f"{VISION_URL}/embed_text", json={"texts": [claim_text]}, timeout=5)
        if resp.status_code == 200:
            text_embed = resp.json()["embeddings"][0]
            
            # Execute natively in pgvector using Cosine Distance <=>
            cursor.execute("""
                SELECT 1 - (clip_embedding <=> %s::vector) AS similarity
                FROM media_provenance
                WHERE raw_article_id = %s
            """, (text_embed, article_id))
            sim_row = cursor.fetchone()
            if sim_row and sim_row[0] is not None:
                return float(sim_row[0]), synth_prob
                
        return None, synth_prob
    except Exception as e:
        print(f"      [VISION ERROR] Cross-modal math failed: {e}")
    return None, None

def extraction_worker(worker_id):
    try:
        for __phase, (__limit, __filter_clause) in enumerate([
            (100, "AND ru.metadata->>'investigation_id' IS NOT NULL"),
            (20, "AND ru.metadata->>'investigation_id' IS NULL")
        ]):
            items_processed = 0
        
            while items_processed < __limit:
                try:
                    # 1. Fetch Job and Immediately Close Connection
                    conn = psycopg2.connect(DATABASE_URL)
                    with conn.cursor() as cursor:
                        # Mark as PROCESSING_EXTRACTION to prevent other workers from grabbing it,
                        # while releasing the FOR UPDATE lock immediately upon commit.
                        cursor.execute(f"""
                            WITH selected AS (
                                SELECT a.id FROM raw_articles a
                                JOIN raw_urls ru ON a.url_id = ru.id
                                WHERE a.status = 'PENDING_EXTRACTION'
                                {__filter_clause}
                                    ORDER BY a.id ASC
                                LIMIT 1 FOR UPDATE OF a SKIP LOCKED
                            ),
                            updated AS (
                                UPDATE raw_articles SET status = 'PROCESSING_EXTRACTION'
                                WHERE id = (SELECT id FROM selected)
                                RETURNING id, title, author, publish_date, raw_text, url_id
                            )
                            SELECT u.id, u.title, u.author, u.publish_date, u.raw_text, s.epistemic_trust_score, urls.metadata
                            FROM updated u
                            JOIN raw_urls urls ON u.url_id = urls.id
                            JOIN sources s ON urls.source_id = s.id;
                        """)
                    
                        row = cursor.fetchone()
                
                    if not row:
                        conn.rollback()
                        conn.close()
                        break 
                
                    conn.commit()
                    conn.close()
                    
                    article_id, title, author, pub_date, raw_text, trust_score, raw_metadata = row
                    print(f"  [W-{worker_id}] Extracting Claims from: {title[:50]}...")

                    # Pass pub_date as the context
                    date_context = str(pub_date) if pub_date else "Unknown"
                
                    # Determine source tier based on epistemic_trust_score
                    source_tier = 3
                    if trust_score and trust_score >= 0.80:
                        source_tier = 1
                    elif trust_score and trust_score >= 0.50:
                        source_tier = 2
                    
                    trust_val = trust_score if trust_score is not None else 0.40

                    # Use very small chunks so the model naturally finishes generation quickly, avoiding the 5-min timeout limit.
                    CHUNK_SIZE = 800
                    OVERLAP = 100
                
                    text_to_process = raw_text or ""
                
                    # --- NEW: VLM SCENE EXTRACTION ---
                    visual_scene_desc = ""
                    meta_dict = raw_metadata if isinstance(raw_metadata, dict) else (json.loads(raw_metadata) if hasattr(raw_metadata, 'strip') else {})
                    keyframes = meta_dict.get('video_keyframes', [])
                    if keyframes:
                        print(f"      -> [VISION CORE] Video keyframes detected! Asking VLM to narrate the visual scene...")
                        try:
                            vlm_content = [{"type": "text", "text": "You are a forensic analyst. Describe exactly what is happening in this sequence of video frames chronologically. Mention identities, events, and any text visible on screen. Keep it highly descriptive but concise."}]
                            for kf in keyframes[:4]: # Max 4 to prevent payload bloat
                                vlm_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{kf}"}})  # type: ignore[arg-type]
                            
                            vlm_resp = groq_pool.chat_completions_create(
                                model='TIER_VISION', # Abstract tier routing
                                messages=[{"role": "user", "content": vlm_content}],
                                max_retries=2,
                                temperature=0.1
                            )
                            # Raw text responses don't use Pydantic models so we pluck the text directly
                            visual_scene_desc = vlm_resp.choices[0].message.content
                            print(f"      -> [VLM SUCCESS] Scene Narration: {visual_scene_desc[:60]}...")
                            text_to_process += f"\n\n[VISUAL FORENSIC NARRATIVE]\n{visual_scene_desc}"
                        except Exception as vlm_e:
                            print(f"      -> [VLM FAILED] Could not extract visual narrative: {vlm_e}")
                        
                    # Smart Chunking based on sentences to avoid cutting mid-sentence
                    TARGET_CHUNK_SIZE = 500
                    chunks = []
                
                    if text_to_process.startswith("[SUMMARY]"):
                        chunks.append(text_to_process)
                    else:
                        sentences = text_to_process.split(". ")
                        current_chunk = ""
                        for sentence in sentences:
                            sentence = sentence.strip()
                            if not sentence:
                                continue
                            if not sentence.endswith("."):
                                sentence += "."
                        
                            if len(current_chunk) + len(sentence) > TARGET_CHUNK_SIZE and current_chunk:
                                chunks.append(current_chunk.strip())
                                current_chunk = sentence + " "
                            else:
                                current_chunk += sentence + " "
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        
                        # Safety cap on chunks
                        chunks = chunks[:20]
                
                    if not chunks:
                        chunks = [""]

                    extraction_failed_permanently = False
                    retryable_failure = False

                    # We no longer hold save_conn open across the inference loop.
                    inserted_count = 0
                    duplicate_count = 0
                    all_article_claims = []
                    if True:
                        for chunk_idx, chunk_text in enumerate(chunks):
                            if len(chunks) > 1:
                                print(f"      -> Processing chunk {chunk_idx+1}/{len(chunks)}...")
                            prompt = generate_extraction_prompt(title, author, date_context, chunk_text)

                            CHUNK_RETRIES = 3
                            chunk_attempt = 0
                            chunk_succeeded = False

                            while chunk_attempt < CHUNK_RETRIES and not chunk_succeeded:
                                chunk_attempt += 1
                                try:
                                    response_obj = groq_pool.chat_completions_create(
                                        model='TIER_HEAVY',
                                        messages=[
                                            {"role": "system", "content": "You are a specialized Knowledge Graph extraction engine. You must output ONLY valid JSON without any markdown formatting or code blocks."},
                                            {"role": "user", "content": prompt}
                                        ],
                                        temperature=0.1,
                                        max_tokens=4000,
                                        response_format={"type": "json_object"}
                                    )
                                    raw_text = groq_pool.extract_text_from_response(response_obj)
                                    if raw_text is None:
                                        if isinstance(response_obj, dict):
                                            # All providers exhausted / returned safe empty fallback — skip chunk, don't waste retries
                                            print(f"      [SKIP] Chunk {chunk_idx+1}: all LLM providers unavailable, skipping chunk.")
                                            chunk_succeeded = True  # treat as graceful skip so we move on
                                            break
                                        raise ValueError(f"Unable to extract text from provider response: {type(response_obj)}")

                                    parsed_json = load_extraction_json(raw_text)
                                    if "claims" in parsed_json:
                                        claim_list = ClaimExtractionList(claims=parsed_json["claims"])
                                        # Store claims in memory to commit transactionally later
                                        all_article_claims.extend([(chunk_idx, c) for c in claim_list.claims])
                                    
                                        try:
                                            claims_serializable = [c.dict() if hasattr(c, 'dict') else c for c in claim_list.claims]
                                            claims_preview = json.dumps(claims_serializable, ensure_ascii=False)
                                            max_preview = int(os.getenv('CLAIM_PREVIEW_MAX', '4000'))
                                            if len(claims_preview) > max_preview:
                                                claims_preview = claims_preview[:max_preview] + '...'
                                            print(f"      [CLAIMS PARSED] Article {article_id} Chunk {chunk_idx+1} Claims: {claims_preview}")
                                        except Exception as _e:
                                            pass
                                    chunk_succeeded = True
                                except Exception as e:
                                    print(f"      [LLM/JSON ERROR Chunk {chunk_idx+1} Attempt {chunk_attempt}] {type(e).__name__}: {str(e)[:200]}...")
                                    err_str = str(e).lower()
                                    is_rate_limit = '429' in err_str or 'rate limit' in err_str or 'cooling' in err_str or 'too many requests' in err_str
                                    is_incomplete_output = 'incompleteoutputexception' in err_str or 'max_tokens' in err_str or 'length limit' in err_str

                                    if is_rate_limit or is_incomplete_output:
                                        retryable_failure = True
                                        break
                                    # If we still have retries left, try again after brief backoff
                                    if chunk_attempt < CHUNK_RETRIES:
                                        time.sleep(2)
                                        continue
                                    else:
                                        extraction_failed_permanently = True
                                        print(f"      [CHUNK FAILED] Chunk {chunk_idx+1} failed after {CHUNK_RETRIES} attempts.")
                                        break

                            if retryable_failure or extraction_failed_permanently:
                                break

                    if not retryable_failure and not extraction_failed_permanently and all_article_claims:
                        save_conn = None
                        try:
                            save_conn = psycopg2.connect(DATABASE_URL)
                            save_conn.autocommit = False
                            cursor = save_conn.cursor()
                            for _chunk_idx, claim in all_article_claims:
                                clean_subj = str(claim.subject or "")[:70]
                                clean_pred = str(claim.predicate or "")[:70]
                                clean_obj = str(claim.object_entity or "")[:70]

                                if not clean_subj or not clean_pred or not clean_obj:
                                    continue

                                claim_str = f"{clean_subj} {clean_pred} {clean_obj}"
                                cross_modal_sim, synth_prob = verify_cross_modal(claim_str, article_id, cursor)

                                adjusted_conf = float(claim.extraction_confidence or 0.5)
                                if cross_modal_sim is not None:
                                    if cross_modal_sim < 0.15:
                                        adjusted_conf *= 0.4
                                        print(f"      -> [VISION CONTRADICTION] Cross-Modal Sim: {cross_modal_sim:.3f} | Conf dropped to {adjusted_conf:.2f}")
                                    elif cross_modal_sim > 0.85:
                                        adjusted_conf = min(1.0, adjusted_conf * 1.25)
                                        print(f"      -> [VISION SUPREMACY] True Match Sim: {cross_modal_sim:.3f} | Conf boosted to {adjusted_conf:.2f}")

                                preliminary_score = scorer.calculate_epistemic_score(
                                    extraction_confidence=adjusted_conf,
                                    source_tier=source_tier,
                                    support_count=0,
                                    contradiction_weights=[],
                                    days_since_extracted=0,
                                    historical_source_reliability=trust_val,
                                    media_synthetic_prob=synth_prob
                                )

                                ai_metadata = json.dumps({
                                    "extraction_confidence": claim.extraction_confidence,
                                    "adjusted_visual_confidence": adjusted_conf,
                                    "cross_modal_similarity": cross_modal_sim,
                                    "synthetic_probability": synth_prob,
                                    "is_verifiable": claim.is_verifiable,
                                    "epistemic_domain": getattr(claim, "epistemic_domain", "EMPIRICAL")
                                })

                                THREAT_KEYWORDS = ["explosion", "strike", "kidnapping", "breach", "fire", "attack", "casualty", "riot", "terror", "lockdown"]
                                is_threat = any(kw in clean_pred.lower() for kw in THREAT_KEYWORDS) or any(kw in clean_obj.lower() for kw in THREAT_KEYWORDS)

                                if is_threat:
                                    cursor.execute("SELECT 1 FROM extracted_claims WHERE subject = %s AND predicate = %s AND object_entity = %s LIMIT 1", (clean_subj, clean_pred, clean_obj))
                                    if not cursor.fetchone():
                                        print(f"      [🚨 THREAT PRIORITY ALERT] Zero-Day Threat Detected: {clean_subj} {clean_pred} {clean_obj}")
                                        webhook_url = os.getenv("THREAT_ALERT_WEBHOOK")
                                        if webhook_url:
                                            try:
                                                import requests
                                                requests.post(webhook_url, json={
                                                    "text": f"🚨 UNVERIFIED THREAT DETECTED: {clean_subj} {clean_pred} {clean_obj}\nSource trust: {trust_val}\nRequires immediate human review."
                                                }, timeout=3)
                                            except Exception as alert_e:
                                                print(f"      [Alert Error] {alert_e}")

                                cursor.execute("SAVEPOINT claim_insert")
                                try:
                                    cursor.execute(f"""
                                        INSERT INTO extracted_claims (
                                            article_id, subject, predicate, object_entity,
                                            temporal_anchor, spatial_anchor, is_verifiable, quote_context,
                                            extraction_confidence, epistemic_score, status, pipeline_stage,
                                            model_version, prompt_version, ai_metadata, investigation_id
                                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROCESSING',
                                                  'STAGE_4_RESOLUTION', %s, %s, %s, %s)
                                        ON CONFLICT (article_id, subject, predicate, object_entity) DO NOTHING
                                        RETURNING id
                                    """, (
                                        article_id, clean_subj, clean_pred, clean_obj,
                                        str(claim.temporal_anchor or "")[:255],
                                        str(claim.spatial_anchor or "")[:255], bool(claim.is_verifiable), str(claim.quote_context or ""),
                                        float(claim.extraction_confidence or 0.5), preliminary_score,
                                        'gemma-4-heavy-tier/router', PROMPT_VERSION, ai_metadata,
                                        meta_dict.get("investigation_id")
                                    ))
                                    ins_row = cursor.fetchone()
                                    if ins_row:
                                        inserted_count += 1
                                    else:
                                        duplicate_count += 1
                                except Exception as db_e:
                                    err_str_db = str(db_e)
                                    cursor.execute("ROLLBACK TO SAVEPOINT claim_insert")
                                    print(f"      [DB ERROR] Insert failed for article {article_id}: {err_str_db}")
                                    if "no unique or exclusion constraint" in err_str_db.lower():
                                        try:
                                            cursor.execute("DROP INDEX IF EXISTS idx_claim_unique;")
                                            cursor.execute("CREATE UNIQUE INDEX idx_claim_unique ON extracted_claims(article_id, subject, predicate, object_entity);")
                                        except Exception:
                                            pass
                                finally:
                                    try:
                                        cursor.execute("RELEASE SAVEPOINT claim_insert")
                                    except Exception:
                                        pass

                            # Single transactional commit for ALL chunks — atomic
                            save_conn.commit()
                            cursor.close()
                            save_conn.close()
                        except Exception as fatal_db_e:
                            print(f"      [FATAL DB ERROR] Transaction failed for article {article_id}: {fatal_db_e}")
                            extraction_failed_permanently = True
                            if save_conn:
                                try:
                                    save_conn.rollback()
                                    save_conn.close()
                                except Exception:
                                    pass

                    # (save_conn is now closed per-chunk)
                    pass
                    # Update article status based on outcome
                    try:
                        upd_conn = psycopg2.connect(DATABASE_URL)
                        with upd_conn.cursor() as ucur:
                            if retryable_failure:
                                print(f"      [RETRYABLE] Not incrementing attempts for {article_id}. Will retry later.")
                                ucur.execute("UPDATE raw_articles SET status = 'PENDING_EXTRACTION' WHERE id = %s", (article_id,))
                            elif extraction_failed_permanently:
                                ucur.execute("""
                                    UPDATE raw_articles
                                    SET extraction_attempts = COALESCE(extraction_attempts, 0) + 1,
                                        status = CASE WHEN COALESCE(extraction_attempts, 0) + 1 >= 3 THEN 'FAILED_EXTRACTION' ELSE 'PENDING_EXTRACTION' END
                                    WHERE id = %s
                                """, (article_id,))
                                print(f"      [FAILED] Article {article_id} marked as failed attempt.")
                            else:
                                ucur.execute("UPDATE raw_articles SET status = 'EXTRACTED' WHERE id = %s", (article_id,))
                        upd_conn.commit()
                        upd_conn.close()
                    except Exception as e:
                        print(f"      [DB ERROR] Could not update article status: {e}")

                    print(f"      -> [SUCCESS W-{worker_id}] Inserted {inserted_count} atomic claims for article {article_id}.")

                    items_processed = items_processed + 1  # type: ignore
                    time.sleep(2) # Reduced sleep thanks to Groq pool rotation
                except Exception as e:
                    print(f"  [ERROR W-{worker_id} Loop] {e}")
                    time.sleep(2)
        
    except Exception as fatal_e:
        print(f"[FATAL W-{worker_id}] {fatal_e}")

def process_extraction_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 4: Atomic Claim Extraction Engine (Single Pass)")

    # Ensure extraction_attempts column exists (added for retry-cap tracking)
    try:
        _conn = psycopg2.connect(DATABASE_URL)
        _conn.autocommit = True
        _cur = _conn.cursor()
        _cur.execute("ALTER TABLE raw_articles ADD COLUMN IF NOT EXISTS extraction_attempts INTEGER DEFAULT 0;")
        # Ensure uniqueness to prevent duplicate claim inserts (article + triple)
        try:
            _cur.execute("DROP INDEX IF EXISTS idx_claim_unique;")
            _cur.execute("""
                DELETE FROM extracted_claims a
                USING extracted_claims b
                WHERE a.article_id = b.article_id
                  AND a.subject = b.subject
                  AND a.predicate = b.predicate
                  AND a.object_entity = b.object_entity
                  AND a.id > b.id
            """)
            duplicates_removed = _cur.rowcount
            if duplicates_removed:
                print(f"[Stage 4] Removed {duplicates_removed} duplicate extracted_claim rows before creating unique index.")
            _cur.execute("CREATE UNIQUE INDEX idx_claim_unique ON extracted_claims(article_id, subject, predicate, object_entity);")
        except Exception as idx_err:
            print(f"[Stage 4] Could not create unique claim index: {idx_err}")
        _cur.close()
        _conn.close()
    except Exception as _e:
        print(f"[Stage 4] Schema migration warning: {_e}")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM raw_articles WHERE status = 'PENDING_EXTRACTION';")
        count_row = cursor.fetchone()
        pending_count = count_row[0] if count_row else 0
        
        cursor.close()
        conn.close()
        
        if pending_count == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return
            
        workers_to_use = 1  # Single worker: model has 1 slot, multiple workers only cause timeout pile-ups
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Found {pending_count} pending articles. Spinning up {workers_to_use} thread...")
        
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(extraction_worker, i) for i in range(workers_to_use)]  # type: ignore
            for f in futures:
                f.result()
                
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch complete.")
        
    except KeyboardInterrupt:
        print("Stopping Extraction Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    process_extraction_queue()
