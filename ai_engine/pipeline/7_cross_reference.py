# pyre-ignore-all-errors
"""
Stage 7: Epistemic Cross-Reference & Stance Detection Engine

For claims reaching STAGE_7_CROSS_REF, this engine:
1. Queries the Neo4j graph for existing claims with a matching subject.
2. Uses Groq to determine the epistemic STANCE between the new claim and
   any existing ones: NOVEL | SUPPORTS | CONTRADICTS | EVOLVES.
3. Updates the Epistemic Trust Score using contradiction weights from the graph.
4. Routes the claim to the final scoring queue (STAGE_8_SCORING).
"""

import os
import sys
import time
import psycopg2  # type: ignore
import math
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase  # type: ignore
from dotenv import load_dotenv  # type: ignore

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.epistemic_trust import EpistemicTrustScorer  # type: ignore
from ai_engine.core.logger import get_printer  # type: ignore
print = get_printer(7)  # Cyan

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
from ai_engine.core.llm_router import llm_pool  # type: ignore

neo4j_driver = GraphDatabase.driver(
    NEO4J_URI or "",
    auth=(NEO4J_USER or "", NEO4J_PASSWORD or "")
)
_scorer      = EpistemicTrustScorer()
# Cross-ref is LLM + Neo4j heavy; keep concurrency low on HF Spaces.
MAX_WORKERS = 2


def detect_stance(new_claim: dict, existing_claim: dict) -> str:
    """Asks Groq to classify the epistemic relationship between two claims."""
    prompt = f"""You are an epistemic stance detection engine for a Knowledge Graph.

Given an EXISTING claim already in the graph and a NEW claim, classify how the new claim relates to the existing one.

EXISTING: Subject="{existing_claim['subject']}", Predicate="{existing_claim['predicate']}", Object="{existing_claim['object']}", Score={existing_claim['score']:.2f}
NEW:      Subject="{new_claim['subject']}", Predicate="{new_claim['predicate']}", Object="{new_claim['object_entity']}", When="{new_claim['temporal_anchor']}", Where="{new_claim['spatial_anchor']}"

Classification rules:
- SUPPORTS:    New claim confirms or strengthens the existing claim.
- CONTRADICTS: New claim is logically incompatible with the existing claim.
- EVOLVES:     New claim is an update or refinement that supersedes the existing claim.
- NOVEL:       New claim has no meaningful epistemic relationship with the existing claim.

Reply with exactly one word: SUPPORTS, CONTRADICTS, EVOLVES, or NOVEL."""
    try:
        resp = llm_pool.chat_completions_create(
            model='TIER_LIGHT',
            messages=[
                {"role": "system", "content": "You are an epistemic stance detection engine. Reply with exactly one word: SUPPORTS, CONTRADICTS, EVOLVES, or NOVEL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        word = resp.choices[0].message.content.strip().upper().split()[0]
        return word if word in ("SUPPORTS", "CONTRADICTS", "EVOLVES", "NOVEL") else "NOVEL"
    except Exception as e:
        print(f"    [STANCE ERROR] {e}")
        return "NOVEL"


def cross_ref_worker(worker_id: int):
    try:
        for __phase, (__limit, __filter_clause) in enumerate([
            (100, "AND ru.metadata->>'investigation_id' IS NOT NULL"),
            (50, "AND ru.metadata->>'investigation_id' IS NULL")
        ]):
            items_processed = 0

            while items_processed < __limit:
                try:
                    claim_id = None
                    subj = None
                    pred = None
                    obj = None
                    temporal = None
                    spatial = None
                    conf = None
                    score = None
                    enrich_target = None
                    src_trust = None
                    ai_metadata = None

                    with psycopg2.connect(DATABASE_URL) as pg_conn:
                        with pg_conn.cursor() as cur:
                            cur.execute(f"""
                                SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
                                       ec.temporal_anchor, ec.spatial_anchor, ec.extraction_confidence, ec.epistemic_score,
                                       ec.enrichment_target_neo4j_id,
                                       s.epistemic_trust_score,
                                       ec.ai_metadata
                                FROM extracted_claims ec
                                JOIN raw_articles ra ON ec.article_id = ra.id
                                JOIN raw_urls ru     ON ra.url_id = ru.id
                                JOIN sources s       ON ru.source_id = s.id
                                WHERE ec.pipeline_stage = 'STAGE_7_CROSS_REF'
                                  AND ec.status = 'PROCESSING'
                                {__filter_clause}
                                    ORDER BY ec.id ASC
                                LIMIT 1
                                FOR UPDATE OF ec SKIP LOCKED;
                            """)
                            row = cur.fetchone()
                            if not row:
                                pg_conn.rollback()
                                break

                            claim_id, subj, pred, obj, temporal, spatial, conf, score, enrich_target, src_trust, ai_metadata = row
                            cur.execute("UPDATE extracted_claims SET pipeline_stage = 'STAGE_7_CROSS_REF_IN_PROGRESS' WHERE id = %s", (claim_id,))
                            pg_conn.commit()

                    if claim_id is None or subj is None or pred is None or obj is None:
                        break

                    import json
                    try:
                        ai_data = json.loads(ai_metadata) if ai_metadata else {}
                    except Exception:
                        ai_data = {}
                    epistemic_domain = ai_data.get("epistemic_domain", "EMPIRICAL")

                    new_claim = {"subject": subj, "predicate": pred, "object_entity": obj, "temporal_anchor": temporal, "spatial_anchor": spatial}

                    print(f"  [W-{worker_id}] Cross-Ref: [{pred}] {subj[:25]} -> {obj[:25]}")

                    stances = []
                    contradiction_weights = []
                    support_count = 0
                    final_matched_id = None

                    if enrich_target:
                        stances.append("ENRICHES")
                        final_matched_id = str(enrich_target)
                        support_count += 1
                        print(f"      -> [W-{worker_id}] ENRICHMENT pass-through. Target: {enrich_target}")
                    else:
                        try:
                            with neo4j_driver.session() as session:
                                records = session.run("""
                                    MATCH (c:Claim)
                                    WHERE (toLower(c.subject) CONTAINS toLower($subject)
                                       OR toLower(c.object) CONTAINS toLower($subject))
                                      AND coalesce(c.epistemic_domain, 'EMPIRICAL') = $domain
                                    RETURN c.id AS id, c.subject AS subject,
                                           c.predicate AS predicate, c.object AS object,
                                           c.epistemic_score AS score
                                    LIMIT 20
                                """, subject=subj[:50], domain=epistemic_domain).data()

                                for rec in records:
                                    if not rec.get("predicate"):
                                        continue
                                    existing = {
                                        "subject": rec["subject"] or "",
                                        "predicate": rec["predicate"] or "",
                                        "object": rec["object"] or "",
                                        "score": float(rec["score"] or 0.4)
                                    }
                                    stance = detect_stance(new_claim, existing)
                                    stances.append(stance)

                                    if stance in ("CONTRADICTS", "EVOLVES", "SUPPORTS") and final_matched_id is None:
                                        final_matched_id = str(rec["id"])

                                    if stance == "CONTRADICTS":
                                        contradiction_weights.append(existing["score"])
                                    elif stance in ("SUPPORTS", "EVOLVES"):
                                        support_count += 1

                        except Exception as neo_err:
                            print(f"    [NEO4J] {neo_err}")

                    if "ENRICHES" in stances:
                        final_stance = "ENRICHES"
                    elif "CONTRADICTS" in stances:
                        final_stance = "CONTRADICTS"
                    elif "EVOLVES" in stances:
                        final_stance = "EVOLVES"
                    elif "SUPPORTS" in stances:
                        final_stance = "SUPPORTS"
                    else:
                        final_stance = "NOVEL"

                    src_tier = 1 if src_trust >= 0.80 else (2 if src_trust >= 0.50 else 3)
                    new_score = _scorer.calculate_epistemic_score(
                        extraction_confidence=conf or 0.5,
                        source_tier=src_tier,
                        support_count=support_count,
                        contradiction_weights=contradiction_weights,
                        days_since_extracted=0,
                        historical_source_reliability=src_trust or 0.40,
                        media_synthetic_prob=ai_data.get("synthetic_probability")
                    )

                    routing = _scorer.determine_routing(new_score)

                    new_status = {
                        "AUTO_APPROVE": "AUTO_APPROVE",
                        "HUMAN_REVIEW": "HUMAN_REVIEW",
                        "AUTO_REJECT":  "AUTO_REJECT"
                    }.get(routing, "PROCESSING")

                    with psycopg2.connect(DATABASE_URL) as pg_conn:
                        with pg_conn.cursor() as cur:
                            cur.execute("""
                                UPDATE extracted_claims
                                SET epistemic_score = %s,
                                    status = %s,
                                    pipeline_stage = 'STAGE_8_MUTATION_QUEUE'
                                WHERE id = %s
                            """, (new_score, new_status, claim_id))

                            cur.execute("""
                                UPDATE claim_provenance
                                SET neo4j_stance = %s, neo4j_matched_claim_id = %s
                                WHERE claim_id = %s
                            """, (final_stance, final_matched_id, claim_id))
                            pg_conn.commit()

                    print(f"      -> [W-{worker_id}] {final_stance} | Score: {new_score:.3f} | Route: {routing}")
                    items_processed += 1
                    time.sleep(0.2)

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


def process_cross_ref_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 7: Cross-Reference & Stance Detection Engine (Single Pass)")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM extracted_claims
            WHERE pipeline_stage = 'STAGE_7_CROSS_REF' AND status = 'PROCESSING';
        """)
        row = cur.fetchone()
        pending = row[0] if row else 0
        cur.close()
        conn.close()

        if pending == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Queue empty. Exiting.")
            return

        workers = min(MAX_WORKERS, max(1, pending))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {pending} claims. Spinning {workers} stance-detection threads...")

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(cross_ref_worker, i) for i in range(workers)]  # type: ignore
            for f in futures:
                f.result()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cross-ref batch complete.")

    except KeyboardInterrupt:
        neo4j_driver.close()
        print("Stopping Cross-Reference Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    process_cross_ref_queue()
