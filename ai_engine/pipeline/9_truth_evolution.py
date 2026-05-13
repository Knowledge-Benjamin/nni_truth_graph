# pyre-ignore-all-errors
"""
Stage 9: Truth Evolution Engine

Runs continuously. For every GRAPH_COMMITTED claim that Stage 7 classified as
EVOLVES or CONTRADICTS, this engine:

EVOLVES path:
  1. Finds the older matching claim in Neo4j.
  2. Sets: valid_until = now, is_current = false, lifecycle = SUPERSEDED.
  3. Creates a (:SUPERSEDES {effective_date}) edge from new → old.
  4. Updates the PREDICATE edge: marks old one is_current=false.

CONTRADICTS path:
  1. Sets both claims' lifecycle = DISPUTED.
  2. Creates (:CONTRADICTS) edge between them.
  3. Lowers epistemic_score of both by the contradiction weight.
  4. Routes both to HUMAN_REVIEW in PostgreSQL.

Source Trust Feedback (inline):
  After handling each evolution/contradiction, updates the source's
  epistemic_trust_score in PostgreSQL based on prediction accuracy.
"""

import os
import sys
import time
import psycopg2  # type: ignore
from datetime import datetime, timezone
import math
from neo4j import GraphDatabase # type: ignore
from dotenv import load_dotenv # type: ignore

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.epistemic_trust import EpistemicTrustScorer  # type: ignore
from ai_engine.core.logger import get_printer  # type: ignore
print = get_printer(9)  # Magenta

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

neo4j_driver = GraphDatabase.driver(
    NEO4J_URI or "",
    auth=(NEO4J_USER or "", NEO4J_PASSWORD or "")
)
_scorer      = EpistemicTrustScorer()

SLEEP_INTERVAL = 20  # seconds between sweeps


# ─────────────────────────────────────────────────────────────────────────────
# EVOLVES handler
# ─────────────────────────────────────────────────────────────────────────────

def handle_evolves(session, pg_cur, new_claim: dict, matched_id: str, similarity: float):
    """
    Retire the old claim and link new → old with SUPERSEDES.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Retire old claim in Neo4j
    session.run("""
        MATCH (old:Claim {id: $old_id})
        SET old.valid_until  = datetime($now),
            old.is_current   = false,
            old.lifecycle    = 'SUPERSEDED'
        WITH old
        MATCH (new:Claim {id: $new_id})
        MERGE (new)-[:SUPERSEDES {effective_date: datetime($now),
                                   similarity: $sim}]->(old)
    """, old_id=str(matched_id), new_id=str(new_claim["id"]),
         now=now_iso, sim=float(similarity or 0))

    # 2. Retire old PREDICATE edge — mark is_current=false
    session.run("""
        MATCH (s:Entity)-[r:PREDICATE]->(o:Entity)
        WHERE r.type = $predicate AND r.is_current = true
        SET r.is_current = false, r.valid_until = $now
    """, predicate=new_claim["predicate"], now=now_iso)

    # 3. Mark old PG claim as SUPERSEDED
    pg_cur.execute("""
        UPDATE extracted_claims
        SET lifecycle   = 'SUPERSEDED',
            valid_until = %s
        WHERE id::text = %s
    """, (datetime.now(timezone.utc), str(matched_id)))

    print(f"    [EVOLVES] Claim {matched_id} retired. "
          f"Claim {new_claim['id']} is now ACTIVE truth.")

    # 4. Boost source trust (correctly predicted evolution)
    adjust_source_trust(pg_cur, new_claim.get("source_id"), +0.01)


# ─────────────────────────────────────────────────────────────────────────────
# ENRICHES handler
# ─────────────────────────────────────────────────────────────────────────────

def handle_enriches(session, pg_cur, new_claim: dict, matched_id: str, similarity: float):
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Retire old claim in Neo4j
    session.run("""
        MATCH (old:Claim {id: $old_id})
        SET old.valid_until  = datetime($now),
            old.is_current   = false,
            old.lifecycle    = 'ENRICHED_BY'
    """, old_id=str(matched_id), now=now_iso)

    # 2. Retire old PREDICATE edge
    session.run("""
        MATCH (s:Entity)-[r:PREDICATE]->(o:Entity)
        WHERE r.type = $predicate AND r.is_current = true
        SET r.is_current = false, r.valid_until = datetime($now)
    """, predicate=new_claim["predicate"], now=now_iso)

    # 3. Mark old PG claim as ENRICHED_BY
    pg_cur.execute("""
        UPDATE extracted_claims
        SET lifecycle   = 'ENRICHED_BY',
            valid_until = %s
        WHERE id::text = %s
    """, (datetime.now(timezone.utc), str(matched_id)))

    print(f"    [ENRICHES] Claim {matched_id} enriched by {new_claim['id']}. Old retired.")

    # 4. Boost source trust (for providing better detail)
    adjust_source_trust(pg_cur, new_claim.get("source_id"), +0.01)


# ─────────────────────────────────────────────────────────────────────────────
# CONTRADICTS handler
# ─────────────────────────────────────────────────────────────────────────────

def handle_contradicts(session, pg_cur, new_claim: dict,
                       matched_id: str, matched_score: float):
    """
    Asymmetric debunking model.

    AUTO-RESOLVE (score_diff >= 0.15):
      - Higher-scored claim = FACT_CHECK (the debunker)
      - Lower-scored claim  = DEBUNKED   (the false claim)
      - Creates [:DEBUNKS] edge debunker → false_claim
      - Sets verdict on both Claim nodes
      - Closes the :Controversy node with resolution metadata
      - Asymmetric trust: false-claim source −0.10, debunker source +0.02

    AMBIGUOUS (score_diff < 0.15):
      - Both claims remain DISPUTED
      - Creates [:CONTRADICTS] edge
      - :Controversy node stays open (HUMAN_REVIEW_PENDING)
      - Both sources penalised −0.05, both routed to HUMAN_REVIEW
    """
    now_iso   = datetime.now(timezone.utc).isoformat()
    new_score = float(new_claim["epistemic_score"] or 0.4)
    score_diff = abs(new_score - matched_score)

    # Look up the matched claim's PostgreSQL source_id so we can adjust its trust
    matched_source_id = None
    try:
        pg_cur.execute("""
            SELECT ru.source_id FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls ru     ON ra.url_id = ru.id
            WHERE ec.id::text = %s
        """, (str(matched_id),))
        m_src_row = pg_cur.fetchone()
        matched_source_id = m_src_row[0] if m_src_row else None
    except Exception:
        pass

    if score_diff >= 0.15:
        # ── AUTO-RESOLVE: determine which side is the debunker ────────────────
        if new_score > matched_score:
            debunker_id  = str(new_claim["id"])
            false_id     = str(matched_id)
            debunker_src = new_claim.get("source_id")
            false_src    = matched_source_id
        else:
            debunker_id  = str(matched_id)
            false_id     = str(new_claim["id"])
            debunker_src = matched_source_id
            false_src    = new_claim.get("source_id")

        # 1. Tag both Claim nodes + create directed DEBUNKS edge
        session.run("""
            MATCH (debunker:Claim {id: $debunker_id})
            MATCH (false_claim:Claim {id: $false_id})
            SET false_claim.verdict    = 'DEBUNKED',
                false_claim.lifecycle  = 'DEBUNKED',
                false_claim.is_current = false,
                false_claim.valid_until = datetime($now),
                debunker.verdict       = 'FACT_CHECK'
            MERGE (debunker)-[:DEBUNKS {
                debunked_at:   datetime($now),
                auto_resolved: true,
                confidence:    $confidence
            }]->(false_claim)
        """, debunker_id=debunker_id, false_id=false_id,
             now=now_iso, confidence=round(score_diff, 3))

        # 2. Controversy node — resolved
        session.run("""
            MERGE (cv:Controversy {subject: $subject, predicate: $predicate})
              ON CREATE SET cv.created_at   = datetime($now),
                            cv.claim_count  = 2,
                            cv.open         = false
              ON MATCH  SET cv.claim_count  = cv.claim_count + 1
            SET cv.resolved          = true,
                cv.verdict           = 'AUTO_RESOLVED',
                cv.resolved_by       = 'auto_score',
                cv.resolution_date   = datetime($now)
            WITH cv
            MATCH (debunker:Claim  {id: $debunker_id})
            MATCH (false_claim:Claim {id: $false_id})
            MERGE (cv)-[:INCLUDES {role: 'FACT_CHECK'}]->(debunker)
            MERGE (cv)-[:INCLUDES {role: 'DEBUNKED'}]->(false_claim)
        """, subject=new_claim["subject"], predicate=new_claim["predicate"],
             now=now_iso, debunker_id=debunker_id, false_id=false_id)

        # 3. Mark debunked claim in PostgreSQL
        pg_cur.execute("""
            UPDATE extracted_claims
            SET status    = 'DEBUNKED',
                lifecycle = 'DEBUNKED',
                valid_until = %s
            WHERE id::text = %s
        """, (datetime.now(timezone.utc), false_id))

        # 4. Asymmetric trust: penalise false reporter, reward debunker
        adjust_source_trust(pg_cur, false_src,    -0.10)
        adjust_source_trust(pg_cur, debunker_src, +0.02)

        print(f"    [DEBUNKS] {debunker_id} debunks {false_id} "
              f"(score_diff={score_diff:.3f}, auto-resolved).")

    else:
        # ── AMBIGUOUS: route both to human review ─────────────────────────────
        # 1. Mark both DISPUTED + create symmetric CONTRADICTS edge
        session.run("""
            MATCH (new:Claim {id: $new_id})
            MATCH (old:Claim {id: $old_id})
            SET new.lifecycle = 'DISPUTED',
                old.lifecycle = 'DISPUTED',
                new.epistemic_score = GREATEST(0.0, new.epistemic_score - 0.10),
                old.epistemic_score = GREATEST(0.0, old.epistemic_score - 0.10)
            MERGE (new)-[:CONTRADICTS {detected_at: datetime($now)}]->(old)
        """, new_id=str(new_claim["id"]), old_id=str(matched_id), now=now_iso)

        # 2. Controversy node — open, awaiting human verdict
        session.run("""
            MERGE (cv:Controversy {subject: $subject, predicate: $predicate})
              ON CREATE SET cv.created_at  = datetime($now),
                            cv.claim_count = 2,
                            cv.open        = true,
                            cv.resolved    = false,
                            cv.verdict     = 'HUMAN_REVIEW_PENDING'
              ON MATCH  SET cv.claim_count = cv.claim_count + 1,
                            cv.updated_at  = datetime($now)
            WITH cv
            MATCH (new:Claim {id: $new_id})
            MATCH (old:Claim {id: $old_id})
            MERGE (cv)-[:INCLUDES {role: 'DISPUTED'}]->(new)
            MERGE (cv)-[:INCLUDES {role: 'DISPUTED'}]->(old)
        """, subject=new_claim["subject"], predicate=new_claim["predicate"],
             now=now_iso, new_id=str(new_claim["id"]), old_id=str(matched_id))

        # 3. Route both to HUMAN_REVIEW in PostgreSQL
        pg_cur.execute("""
            UPDATE extracted_claims
            SET status    = 'HUMAN_REVIEW',
                lifecycle = 'DISPUTED'
            WHERE id = %s OR id::text = %s
        """, (new_claim["id"], str(matched_id)))

        # 4. Symmetric trust penalty for publishing conflicting information
        adjust_source_trust(pg_cur, new_claim.get("source_id"), -0.05)
        adjust_source_trust(pg_cur, matched_source_id,          -0.05)

        print(f"    [CONTRADICTS] Claims {new_claim['id']} & {matched_id} "
              f"ambiguous (score_diff={score_diff:.3f}) → HUMAN_REVIEW.")


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE TRUST FEEDBACK
# ─────────────────────────────────────────────────────────────────────────────

def adjust_source_trust(pg_cur, source_id, delta: float):
    """
    Clamp trust to [0.0, 1.0] after applying delta.
    Prescribed thresholds:
      RETRACTED claim      → -0.05
      CONTRADICTS detected → -0.05
      CORROBORATED (5x)   → +0.02
      EVOLVES correct     → +0.01
    """
    if source_id is None:
        return
    pg_cur.execute("""
        UPDATE sources
        SET epistemic_trust_score = LEAST(1.0, GREATEST(0.0,
              epistemic_trust_score + %s)),
            trust_updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (delta, source_id))


def handle_retracted(session, pg_cur, claim_id: int, source_id: int):
    """
    When a source officially retracts a claim — called externally from
    the Human Review API after a reviewer marks a claim RETRACTED.
    """
    session.run("""
        MATCH (c:Claim {id: $claim_id})
        SET c.lifecycle    = 'RETRACTED',
            c.is_current   = false,
            c.valid_until  = datetime()
        WITH c
        MERGE (c)-[:RETRACTED {retracted_at: datetime()}]->(:RetractedFlag)
    """, claim_id=str(claim_id))

    pg_cur.execute("""
        UPDATE extracted_claims
        SET lifecycle = 'RETRACTED', valid_until = CURRENT_TIMESTAMP,
            status = 'RETRACTED'
        WHERE id = %s
    """, (claim_id,))

    # Heavy source trust penalty for retracted content
    adjust_source_trust(pg_cur, source_id, -0.05)
    print(f"    [RETRACTED] Claim {claim_id} retracted. Source trust -0.05.")


def handle_confirmed(session, pg_cur, claim_id: int, confirming_source_url: str,
                     corroboration_count: int):
    """
    When a third-party official source confirms a claim.
    At 5+ corroborations, boost source trust by +0.02.
    """
    session.run("""
        MERGE (conf:Source {url: $src_url})
          ON CREATE SET conf.name = $src_url, conf.created_at = datetime()
        WITH conf
        MATCH (c:Claim {id: $claim_id})
        MERGE (c)-[:CONFIRMED_BY {confirmed_at: datetime()}]->(conf)
        SET c.epistemic_score = LEAST(1.0, c.epistemic_score + 0.02)
    """, claim_id=str(claim_id), src_url=confirming_source_url)

    if corroboration_count >= 5:
        pg_cur.execute("""
            UPDATE sources s
            SET epistemic_trust_score = LEAST(1.0, epistemic_trust_score + 0.02),
                trust_updated_at = CURRENT_TIMESTAMP
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls ru ON ra.url_id = ru.id
            WHERE ru.source_id = s.id AND ec.id = %s
        """, (claim_id,))
        print(f"    [CONFIRMED x{corroboration_count}] Source trust +0.02.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SWEEP
# ─────────────────────────────────────────────────────────────────────────────

def evolution_sweep():
    """Single sweep over all GRAPH_COMMITTED claims with stance != NOVEL/ORIGINAL."""
    pg_conn = None  # guard: ensure rollback in except is always safe
    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = False
        pg_cur = pg_conn.cursor()

        pg_cur.execute("""
            SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
                   ec.epistemic_score, ec.temporal_anchor, ec.spatial_anchor,
                   cp.neo4j_stance, cp.neo4j_matched_claim_id, cp.neo4j_similarity,
                   ru.source_id
            FROM extracted_claims ec
            JOIN claim_provenance cp ON cp.claim_id = ec.id
            JOIN raw_articles ra     ON ec.article_id = ra.id
            JOIN raw_urls ru         ON ra.url_id = ru.id
            WHERE ec.pipeline_stage = 'COMPLETE'
              AND ec.lifecycle = 'ACTIVE'
              AND cp.neo4j_stance IN ('EVOLVES', 'CONTRADICTS', 'CORROBORATES', 'ENRICHES')
              AND ec.status = 'GRAPH_COMMITTED'
            LIMIT 50;
        """)
        rows = pg_cur.fetchall()

        if not rows:
            return 0

        print(f"  [Stage 9] Processing {len(rows)} stance-resolved claims...")

        with neo4j_driver.session() as session:
            for row in rows:
                (claim_id, subj, pred, obj, score, temporal, spatial,
                 stance, matched_id, similarity, source_id) = row

                claim = dict(id=claim_id, subject=subj, predicate=pred,
                             object_entity=obj, epistemic_score=score,
                             temporal_anchor=temporal, spatial_anchor=spatial, source_id=source_id)

                if stance == "EVOLVES" and matched_id:
                    handle_evolves(session, pg_cur, claim,
                                   matched_id, similarity)

                elif stance == "ENRICHES" and matched_id:
                    handle_enriches(session, pg_cur, claim,
                                   matched_id, similarity)

                elif stance == "CONTRADICTS" and matched_id:
                    # Get matched claim's score from Neo4j
                    rec = session.run(
                        "MATCH (c:Claim {id: $id}) RETURN c.epistemic_score AS s",
                        id=str(matched_id)
                    ).single()
                    matched_score = float(rec["s"] or 0.4) if rec else 0.4
                    handle_contradicts(session, pg_cur, claim,
                                       matched_id, matched_score)

                elif stance == "CORROBORATES":
                    # Stage 6 (deduplication) already inserted the claim_corroborations
                    # row and recalculated epistemic_score. Stage 9 only needs to apply
                    # the minor source trust nudge to reward corroborating sources.
                    # DO NOT re-boost epistemic_score here — that causes double-counting.
                    adjust_source_trust(pg_cur, source_id, +0.005)
                    print(f"    [CORROBORATES] Claim {matched_id} acknowledged. Source trust +0.005.")

                # Mark as evolution-processed to avoid re-processing
                pg_cur.execute("""
                    UPDATE extracted_claims SET lifecycle = 'EVOLUTION_PROCESSED'
                    WHERE id = %s
                """, (claim_id,))

        pg_conn.commit()
        pg_cur.close()
        pg_conn.close()
        return len(rows)

    except Exception as e:
        print(f"  [Stage 9 ERROR] {e}")
        try:
            if pg_conn:
                pg_conn.rollback()
        except Exception:
            pass
        return 0


def run_evolution_engine():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          "Starting Stage 9: Truth Evolution Engine (Single Pass)")

    try:
        processed = evolution_sweep()
        if processed:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Evolution sweep done — {processed} claims processed.")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  "No stance-resolved claims. Exiting.")
    except KeyboardInterrupt:
        neo4j_driver.close()
        print("Stopping Truth Evolution Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    run_evolution_engine()
