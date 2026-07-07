# pyre-ignore-all-errors
"""
Stage 6: Claim Deduplication Engine (3-Layer Accuracy)

Layer 1 — Exact Fingerprint Hash
    SHA-256 of normalized(subject + predicate + object_entity).
    Catches verbatim duplicates with zero API cost.

Layer 2 — Vector Cosine Similarity (pgvector)
    Retrieves the article's 768D embedding from article_categories and
    compares against previously CANONICAL claim embeddings using cosine similarity.
    Only pairs crossing the threshold (default 0.94) advance to Layer 3.

Layer 3 — Groq LLM Semantic Judge
    Invoked ONLY for borderline cases that survived Layers 1 & 2.
    Reads full SPO + temporal context and determines DUPLICATE vs DISTINCT.
"""

import os
import sys
import time
import math
import hashlib
import psycopg2  # type: ignore
from concurrent.futures import ThreadPoolExecutor
from groq import RateLimitError  # type: ignore
from dotenv import load_dotenv  # type: ignore

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")
from ai_engine.core.groq_pool import groq_pool  # type: ignore
from ai_engine.core.logger import get_printer  # type: ignore
print = get_printer(6)  # Bright Red

# Dedup is relatively light but still touches the DB and LLM; keep threads small.
MAX_WORKERS     = 2
COSINE_THRESHOLD = 0.94   # Layer 2: minimum cosine similarity to advance to LLM judge

from ai_engine.core.epistemic_trust import EpistemicTrustScorer
_scorer = EpistemicTrustScorer()

def _add_corroboration(cur, canonical_id: int, duplicate_id: int):
    """
    Extracts the source and temporal metadata from a DUPLICATE claim
    and permanently binds it to the CANONICAL claim's Fossil Record.
    """
    cur.execute("""
        SELECT ec.quote_context, ec.extraction_confidence, ra.publish_date, s.epistemic_trust_score, ec.article_id, s.tier
        FROM extracted_claims ec
        JOIN raw_articles ra ON ec.article_id = ra.id
        JOIN raw_urls ru ON ra.url_id = ru.id
        JOIN sources s ON ru.source_id = s.id
        WHERE ec.id = %s
    """, (duplicate_id,))
    row = cur.fetchone()
    if not row: return
    
    quote, ex_conf, pub_date, trust, raw_art_id, src_tier = row
    tier = src_tier if src_tier else (1 if (trust or 0)>=0.80 else (2 if (trust or 0)>=0.50 else 3))
    
    # Ensure no duplicates in the corroboration array
    cur.execute("SELECT 1 FROM claim_corroborations WHERE claim_id=%s AND raw_article_id=%s", (canonical_id, raw_art_id))
    if cur.fetchone(): return
    
    cur.execute("""
        INSERT INTO claim_corroborations (claim_id, raw_article_id, quote_context, source_tier, source_trust, discovered_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (canonical_id, raw_art_id, quote, tier, trust or 0.40, pub_date))
    
    # Recalculate dynamic Epistemic Score
    cur.execute("SELECT discovered_at, source_tier, source_trust FROM claim_corroborations WHERE claim_id = %s", (canonical_id,))
    records = [{"timestamp": r[0], "source_tier": r[1], "source_trust": r[2]} for r in cur.fetchall()]
    
    cur.execute("SELECT extraction_confidence, ai_metadata FROM extracted_claims WHERE id = %s", (canonical_id,))
    c_conf_row = cur.fetchone()
    c_conf = c_conf_row[0] if c_conf_row else 0.5
    c_ai_metadata = c_conf_row[1] if c_conf_row and len(c_conf_row) > 1 else None
    
    import json
    try:
        c_ai_data = json.loads(c_ai_metadata) if c_ai_metadata else {}
    except Exception:
        c_ai_data = {}
    synth_prob = c_ai_data.get("synthetic_probability")
    
    new_score = _scorer.calculate_epistemic_score(
        extraction_confidence=c_conf, 
        source_tier=tier,
        corroboration_records=records,
        media_synthetic_prob=synth_prob
    )
    cur.execute("UPDATE extracted_claims SET epistemic_score = %s WHERE id = %s", (new_score, canonical_id))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1: Exact Fingerprint Hash
# ─────────────────────────────────────────────────────────────────────────────

def make_fingerprint(subject: str, predicate: str, obj: str) -> str:
    """SHA-256 of the normalized SPO triple."""
    normalized = f"{subject.strip().lower()}|{predicate.strip().lower()}|{obj.strip().lower()}"
    return hashlib.sha256(normalized.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2: Vector Cosine Similarity
# ─────────────────────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x**2 for x in a))
    mag_b = math.sqrt(sum(x**2 for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def get_article_embedding(cur, article_id: int) -> list[float] | None:
    """Fetch the vector embedding for the article this claim was extracted from."""
    cur.execute(
        "SELECT embedding FROM article_categories WHERE article_id = %s LIMIT 1;",
        (article_id,)
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        raw = row[0]
        # pgvector may return a list, string '[0.1,...]', or native object
        if isinstance(raw, str):
            import json
            raw = json.loads(raw.replace('(', '[').replace(')', ']'))
        return [float(v) for v in raw]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3: Groq LLM Semantic Judge
# ─────────────────────────────────────────────────────────────────────────────

def llm_are_same_fact(claim_a: dict, claim_b: dict) -> bool:
    """
    Invoked ONLY when layers 1 & 2 pass a borderline pair.
    Asks Groq to make the final duplicate vs distinct judgement.
    """
    prompt = f"""You are a precision fact deduplication engine for a Living Truth Knowledge Graph.

Determine if Claim A and Claim B represent EXACTLY the same underlying real-world fact.
Consider paraphrasing, abbreviations, and equivalent entity references.
A DUPLICATE means the same event/state described twice.
A DISTINCT means different facts even if related.

Claim A: Subject="{claim_a['subject']}", Predicate="{claim_a['predicate']}", Object="{claim_a['object_entity']}", When="{claim_a['temporal_anchor']}", Where="{claim_a['spatial_anchor']}"
Claim B: Subject="{claim_b['subject']}", Predicate="{claim_b['predicate']}", Object="{claim_b['object_entity']}", When="{claim_b['temporal_anchor']}", Where="{claim_b['spatial_anchor']}"

Reply with exactly one word: DUPLICATE or DISTINCT."""

    try:
        resp = groq_pool.chat_completions_create(
            model='TIER_LIGHT',
            messages=[
                {"role": "system", "content": "You are a precise fact deduplication engine. Reply with exactly one word: DUPLICATE or DISTINCT."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        return resp.choices[0].message.content.strip().upper().startswith("DUPLICATE")
    except Exception as e:
        print(f"    [LLM DEDUP ERROR] {e}")
        return False   # default to DISTINCT on failure — safety over false merges


def llm_is_enrichment(incoming_claim: dict, existing_claim: dict) -> bool:
    """
    Invoked when Layer 3 decides two claims are about the same fact.
    Determines if the INCOMING claim has strictly more detail than EXISTING.
    """
    prompt = f"""You are a precision fact analysis engine for a Knowledge Graph.

Determine if the INCOMING claim contains strictly greater factual detail (more specific temporal, spatial, or object descriptors) than the EXISTING claim.

EXISTING: Subject="{existing_claim['subject']}", Predicate="{existing_claim['predicate']}", Object="{existing_claim['object_entity']}", When="{existing_claim['temporal_anchor']}", Where="{existing_claim['spatial_anchor']}"
INCOMING: Subject="{incoming_claim['subject']}", Predicate="{incoming_claim['predicate']}", Object="{incoming_claim['object_entity']}", When="{incoming_claim['temporal_anchor']}", Where="{incoming_claim['spatial_anchor']}"

Reply with exactly one word: RICHER or SAME_DETAIL."""

    try:
        resp = groq_pool.chat_completions_create(
            model='TIER_LIGHT',
            messages=[
                {"role": "system", "content": "You are a precise fact analysis engine. Reply with exactly one word: RICHER or SAME_DETAIL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        return resp.choices[0].message.content.strip().upper().startswith("RICHER")
    except Exception as e:
        print(f"    [LLM ENRICH ERROR] {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DEDUP WORKER
# ─────────────────────────────────────────────────────────────────────────────

def dedup_worker(worker_id: int):
    try:
        items_processed = 0

        while items_processed < 50:
            try:
                claim_id = None
                subj = None
                pred = None
                obj = None
                temporal = None
                spatial = None
                conf = None
                score = None
                art_id = None

                with psycopg2.connect(DATABASE_URL) as pg_conn:
                    with pg_conn.cursor() as cur:
                        cur.execute("""
                            SELECT ec.id, ec.subject, ec.predicate, ec.object_entity, ec.temporal_anchor, ec.spatial_anchor,
                                   ec.extraction_confidence, ec.epistemic_score, ec.article_id
                            FROM extracted_claims ec
                            JOIN raw_articles ra ON ec.article_id = ra.id
                            JOIN raw_urls ru     ON ra.url_id = ru.id
                            WHERE ec.pipeline_stage = 'STAGE_6_DEDUP'
                              AND ec.status = 'PROCESSING'
                            ORDER BY CASE WHEN ru.metadata->>'investigation_id' IS NOT NULL THEN 0 ELSE 1 END, ec.id ASC
                            LIMIT 1
                            FOR UPDATE OF ec SKIP LOCKED;
                        """)
                        row = cur.fetchone()
                        if not row:
                            pg_conn.rollback()
                            break

                        claim_id, subj, pred, obj, temporal, spatial, conf, score, art_id = row
                        cur.execute("UPDATE extracted_claims SET pipeline_stage = 'STAGE_6_DEDUP_IN_PROGRESS' WHERE id = %s", (claim_id,))
                        pg_conn.commit()

                if claim_id is None or subj is None or pred is None or obj is None:
                    break

                claim_a = {"subject": subj, "predicate": pred, "object_entity": obj, "temporal_anchor": temporal, "spatial_anchor": spatial}

                print(f"  [W-{worker_id}] Dedup: [{pred}] {subj[:20]} -> {obj[:20]}")

                with psycopg2.connect(DATABASE_URL) as pg_conn:
                    with pg_conn.cursor() as cur:
                        fingerprint = make_fingerprint(subj, pred, obj)
                        cur.execute("""
                            SELECT id, epistemic_score, temporal_anchor, spatial_anchor
                            FROM extracted_claims
                            WHERE spo_fingerprint = %s
                              AND id != %s
                              AND status IN ('CANONICAL', 'AUTO_APPROVE', 'HUMAN_REVIEW', 'PROCESSING')
                            LIMIT 1;
                        """, (fingerprint, claim_id))
                        exact_match = cur.fetchone()

                        if exact_match:
                            canonical_id, c_score, c_temporal, c_spatial = exact_match
                            if (temporal or "").strip() == (c_temporal or "").strip() and (spatial or "").strip() == (c_spatial or "").strip():
                                if (score or 0) > (c_score or 0):
                                    cur.execute("""
                                        UPDATE extracted_claims SET status = 'DUPLICATE',
                                            pipeline_stage = 'STAGE_6_DEDUP_DONE' WHERE id = %s
                                    """, (canonical_id,))
                                    cur.execute("""
                                        UPDATE extracted_claims SET spo_fingerprint = %s,
                                            pipeline_stage = 'STAGE_7_CROSS_REF', status = 'PROCESSING'
                                        WHERE id = %s
                                    """, (fingerprint, claim_id))
                                    _add_corroboration(cur, claim_id, claim_id)
                                    _add_corroboration(cur, claim_id, canonical_id)
                                else:
                                    cur.execute("""
                                        UPDATE extracted_claims SET status = 'DUPLICATE',
                                            pipeline_stage = 'STAGE_6_DEDUP_DONE' WHERE id = %s
                                    """, (claim_id,))
                                    _add_corroboration(cur, canonical_id, canonical_id)
                                    _add_corroboration(cur, canonical_id, claim_id)
                                print(f"      -> [L1 EXACT HASH DUPLICATE] Merged with claim {canonical_id}.")
                                pg_conn.commit()
                                continue

                        cur.execute("UPDATE extracted_claims SET spo_fingerprint = %s WHERE id = %s", (fingerprint, claim_id))
                        emb_a = get_article_embedding(cur, art_id)
                        merged = False

                        if emb_a:
                            cur.execute("""
                                SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
                                       ec.temporal_anchor, ec.spatial_anchor, ec.epistemic_score, ec.article_id
                                FROM extracted_claims ec
                                WHERE ec.predicate = %s
                                  AND ec.id != %s
                                  AND ec.status IN ('CANONICAL', 'AUTO_APPROVE', 'HUMAN_REVIEW', 'PROCESSING')
                                  AND ec.article_id != %s
                                LIMIT 100;
                            """, (pred, claim_id, art_id))
                            candidates = cur.fetchall()

                            for cand in candidates:
                                cand_id, c_subj, c_pred, c_obj, c_temp, c_spatial, c_score, c_art_id = cand
                                emb_b = get_article_embedding(cur, c_art_id)
                                if not emb_b:
                                    continue

                                sim = cosine_similarity(emb_a, emb_b)
                                if sim < COSINE_THRESHOLD:
                                    continue

                                claim_b = {"subject": c_subj, "predicate": c_pred, "object_entity": c_obj, "temporal_anchor": c_temp, "spatial_anchor": c_spatial}
                                if llm_are_same_fact(claim_a, claim_b):
                                    if llm_is_enrichment(claim_a, claim_b):
                                        cur.execute("""
                                            UPDATE extracted_claims
                                            SET pipeline_stage = 'STAGE_7_CROSS_REF',
                                                status = 'PROCESSING',
                                                enrichment_target_neo4j_id = %s
                                            WHERE id = %s
                                        """, (str(cand_id), claim_id))
                                        print(f"      -> [L3 LLM ENRICHMENT] Rescued richer claim {claim_id}. Targets {cand_id}.")
                                    else:
                                        better_id = claim_id if (score or 0) >= (c_score or 0) else cand_id
                                        weaker_id = cand_id if better_id == claim_id else claim_id

                                        cur.execute("""
                                            UPDATE extracted_claims SET status = 'DUPLICATE',
                                                pipeline_stage = 'STAGE_6_DEDUP_DONE' WHERE id = %s
                                        """, (weaker_id,))
                                        cur.execute("""
                                            UPDATE extracted_claims
                                            SET pipeline_stage = 'STAGE_7_CROSS_REF',
                                                status = 'PROCESSING'
                                            WHERE id = %s
                                        """, (better_id,))
                                        _add_corroboration(cur, better_id, better_id)
                                        _add_corroboration(cur, better_id, weaker_id)
                                        print(f"      -> [L3 LLM DUPLICATE sim={sim:.2f}] Merged with claim {better_id}.")
                                    merged = True
                                    break

                        if not merged:
                            cur.execute("""
                                UPDATE extracted_claims
                                SET pipeline_stage = 'STAGE_7_CROSS_REF',
                                    status = 'PROCESSING'
                                WHERE id = %s
                            """, (claim_id,))
                            print(f"      -> [W-{worker_id}] UNIQUE. Routed to Stage 7.")

                        pg_conn.commit()
                    items_processed += 1
                    time.sleep(0.1)

            except Exception as loop_err:
                print(f"  [ERROR W-{worker_id}] {loop_err}. Rolling back to keep in queue.")
                try:
                    with psycopg2.connect(DATABASE_URL) as rollback_conn:
                        rollback_conn.rollback()
                except Exception:
                    pass
                time.sleep(10)
    except Exception as fatal:
        print(f"[FATAL W-{worker_id}] {fatal}")


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def process_dedup_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 6: Claim Deduplication Engine (3-Layer) (Single Pass)")

    # Ensure spo_fingerprint column exists
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS spo_fingerprint TEXT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_claims_fingerprint ON extracted_claims(spo_fingerprint);")
        cur.execute("ALTER TABLE extracted_claims ADD COLUMN IF NOT EXISTS enrichment_target_neo4j_id TEXT;")
        cur.close()
        conn.close()
        print("Schema ready (spo_fingerprint column ensured).")
    except Exception as e:
        print(f"Schema error: {e}")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) FROM extracted_claims
            WHERE pipeline_stage = 'STAGE_6_DEDUP' AND status = 'PROCESSING';
        """)
        row = cur.fetchone()
        pending = row[0] if row else 0
        cur.close()
        conn.close()

        if pending == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return

        workers = min(MAX_WORKERS, max(1, pending))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {pending} claims pending. Spinning {workers} dedup threads...")

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(dedup_worker, i) for i in range(workers)]  # type: ignore
            for f in futures:
                f.result()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Dedup batch complete.")

    except KeyboardInterrupt:
        print("Stopping Deduplication Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    process_dedup_queue()
