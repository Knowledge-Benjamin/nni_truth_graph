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

# Keep extraction concurrency low on HF Spaces to minimize LLM/CPU load.
MAX_WORKERS = 5

class AtomicClaim(BaseModel):
    subject: str = Field(description="The normalized main entity. MUST be a proper noun, specific event name, or core concept (e.g., 'Donald Trump', 'Knowledge', 'World War II'). Do NOT use descriptive statements. Max 3-5 words. MUST resolve all pronouns (anaphoric resolution) e.g., 'He' -> 'John Smith'.")
    predicate: str = Field(description="The action or relationship. Use a strict standardized ontology (e.g., IS_A, REPORTED, INCREASED, DECREASED, LAUNCHED, DISCOVERED, GOT_PHD).")
    object_entity: str = Field(description="The target entity, concept, or value of the predicate. MUST be a standalone noun, entity name, or specific value. Do NOT use descriptive statements.")
    temporal_anchor: str = Field(description="When does this claim apply according to the text? e.g., '2026-02-24', 'Future', 'Ongoing', 'In the 1990s', '27th Feb 2025'.")
    spatial_anchor: str = Field(description="Where did this event or fact take place? e.g., 'Mexico', 'MIT University in Mexico', 'New York City'. Leave empty if not applicable.")
    is_verifiable: bool = Field(description="True if this claim can be objectively proven true or false. False if it is subjective opinion or unprovable.")
    quote_context: str = Field(description="A brief verbatim snippet from the article that supports this claim. CRITICAL: use only single-quote characters (') inside this field — NEVER raw double-quote characters (\"). Replace any double quotes in the source text with single quotes.")
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
You are a JSON-only API. Output ONLY valid JSON matching the exact schema requested. No conversational text, no markdown code blocks (do not wrap in ```json), no preambles, and no trailing comments.

Article Title: {title}
Author: {author}
Date: {date}

Content:
{text}
"""

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
        conn = psycopg2.connect(DATABASE_URL)
        items_processed = 0
        
        while items_processed < 20:
            try:
                with conn.cursor() as cursor:
                    # FOR UPDATE SKIP LOCKED ensures concurrency
                    cursor.execute("""
                        SELECT a.id, a.title, a.author, a.publish_date, a.raw_text, s.epistemic_trust_score, u.metadata
                        FROM raw_articles a
                        JOIN raw_urls u ON a.url_id = u.id
                        JOIN sources s ON u.source_id = s.id
                        WHERE a.status = 'PENDING_EXTRACTION' 
                        LIMIT 1 
                        FOR UPDATE SKIP LOCKED;
                    """)
                    
                    row = cursor.fetchone()
                    if not row:
                        conn.rollback()
                        break 
                        
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

                    CHUNK_SIZE = 12000
                    OVERLAP = 1000
                    
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
                    chunks = []
                    
                    if len(text_to_process) <= CHUNK_SIZE:
                        chunks = [text_to_process]
                    else:
                        for i in range(0, len(text_to_process), CHUNK_SIZE - OVERLAP):
                            chunks.append(text_to_process[i:i + CHUNK_SIZE])
                            if len(chunks) >= 8:  # Max 8 chunks (~90k characters) per article
                                break
                    
                    if not chunks:
                        chunks = [""]

                    all_claims_extracted = []
                    extraction_failed_permanently = False
                    retryable_failure = False

                    for chunk_idx, chunk_text in enumerate(chunks):
                        if len(chunks) > 1:
                            print(f"      -> Processing chunk {chunk_idx+1}/{len(chunks)}...")
                        prompt = generate_extraction_prompt(title, author, date_context, chunk_text)
                        
                        try:
                            response_obj = groq_pool.chat_completions_create(
                                model='TIER_HEAVY',
                                messages=[
                                    {"role": "system", "content": "You are a specialized Knowledge Graph extraction engine."},
                                    {"role": "user", "content": prompt}
                                ],
                                response_model=ClaimExtractionList,
                                max_retries=3,
                                temperature=0.1,
                            )
                            all_claims_extracted.extend(response_obj.claims)
                        except Exception as e:
                            print(f"      [LLM/JSON ERROR Chunk {chunk_idx+1}] {type(e).__name__}: {str(e)[:200]}...")
                            err_str = str(e).lower()
                            is_rate_limit = '429' in err_str or 'rate limit' in err_str or 'cooling' in err_str or 'too many requests' in err_str
                            is_incomplete_output = 'incompleteoutputexception' in err_str or 'max_tokens' in err_str or 'length limit' in err_str
                            
                            if is_rate_limit or is_incomplete_output:
                                retryable_failure = True
                            else:
                                extraction_failed_permanently = True
                            break

                    if retryable_failure or extraction_failed_permanently:
                        conn.rollback()
                        if retryable_failure:
                            print(f"      [RETRYABLE] Not incrementing attempts for {article_id}. Will retry later.")
                        else:
                            with conn.cursor() as upd:
                                upd.execute("""
                                    UPDATE raw_articles
                                    SET extraction_attempts = COALESCE(extraction_attempts, 0) + 1,
                                        status = CASE WHEN COALESCE(extraction_attempts, 0) + 1 >= 3 THEN 'FAILED_EXTRACTION' ELSE status END
                                    WHERE id = %s
                                """, (article_id,))
                                conn.commit()
                        time.sleep(10)
                        continue

                    # Insert successful extractions
                    for claim in all_claims_extracted:
                        clean_subj = str(claim.subject or "")[:70]
                        clean_pred = str(claim.predicate or "")[:70]
                        clean_obj = str(claim.object_entity or "")[:70]
                        
                        if not clean_subj or not clean_pred or not clean_obj:
                            continue

                        # Execute Zero-Shot Visual Holographic Math
                        claim_str = f"{clean_subj} {clean_pred} {clean_obj}"
                        cross_modal_sim, synth_prob = verify_cross_modal(claim_str, article_id, cursor)
                        
                        adjusted_conf = float(claim.extraction_confidence or 0.5)
                        if cross_modal_sim is not None:
                            if cross_modal_sim < 0.15:
                                adjusted_conf *= 0.4  # Harsh penalty for Contextual Hallucination (Clickbait)
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
                        
                        cursor.execute("""
                            INSERT INTO extracted_claims (
                                article_id, subject, predicate, object_entity,
                                temporal_anchor, spatial_anchor, is_verifiable, quote_context,
                                extraction_confidence, epistemic_score, status, pipeline_stage,
                                model_version, prompt_version, ai_metadata
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROCESSING',
                                      'STAGE_4_RESOLUTION', %s, %s, %s)
                        """, (
                            article_id, clean_subj, clean_pred, clean_obj,
                            str(claim.temporal_anchor or "")[:255],
                            str(claim.spatial_anchor or "")[:255], bool(claim.is_verifiable), str(claim.quote_context or ""),
                            float(claim.extraction_confidence or 0.5), preliminary_score,
                            'gemma-4-heavy-tier/router', PROMPT_VERSION, ai_metadata
                        ))

                    cursor.execute("UPDATE raw_articles SET status = 'EXTRACTED' WHERE id = %s", (article_id,))
                    print(f"      -> [SUCCESS W-{worker_id}] Extracted {len(all_claims_extracted)} total atomic claims.")
                    conn.commit()
                    items_processed = items_processed + 1  # type: ignore
                    time.sleep(2) # Reduced sleep thanks to Groq pool rotation
            except Exception as e:
                print(f"  [ERROR W-{worker_id} Loop] {e}")
                conn.rollback()
                time.sleep(2)
        
        conn.close()
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
            
        workers_to_use = min(MAX_WORKERS, max(1, pending_count))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Found {pending_count} pending articles. Spinning up {workers_to_use} threads...")
        
        with ThreadPoolExecutor(max_workers=workers_to_use) as executor:
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
