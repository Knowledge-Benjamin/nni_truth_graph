# pyre-ignore-all-errors
"""
Stage 8: Neo4j Graph Mutation Engine
Writes fully-resolved, epistemic-scored claims into the Neo4j graph.
Entity names are canonicalised via the EntityDisambiguator before every MERGE.
"""

import os
import sys
import time
import psycopg2  # type: ignore
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase  # type: ignore
from dotenv import load_dotenv  # type: ignore
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.logger import get_printer  # type: ignore
from ai_engine.core.groq_pool import groq_pool  # type: ignore
from ai_engine.core.inference_pool import inference_pool as hf_pool  # type: ignore
from ai_engine.core.entity_disambiguator import get_disambiguator  # type: ignore
print = get_printer(8)  # Yellow

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

HF_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"

def _embed_text(text: str) -> list | None:
    """768-dim embedding via shared InferencePool endpoint."""
    try:
        # Use the configured inference pooling endpoint; avoid passing raw model ID as URL.
        return hf_pool.embed(text)
    except Exception:
        return None

neo4j_driver = GraphDatabase.driver(
    NEO4J_URI or "",
    auth=(NEO4J_USER or "", NEO4J_PASSWORD or "")
)
MAX_WORKERS  = 5

# ── Entity Disambiguator — shared across all worker threads ──────────────────
_disambiguator = get_disambiguator(
    neo4j_driver=neo4j_driver,
    groq_pool=groq_pool,
    embed_fn=_embed_text,
)


def write_claim_to_graph(session, claim: dict):
    """MERGE all nodes and relationships for one atomic claim.
    Entity names are resolved to canonical form before any MERGE.
    """
    # ── Canonicalise entity names before writing ───────────────────────────
    claim["subject"]       = _disambiguator.resolve(claim["subject"])
    claim["object_entity"] = _disambiguator.resolve(claim["object_entity"])

    # 1. Entity nodes
    session.run("""
        MERGE (s:Entity {name: $subject})
          ON CREATE SET s.created_at = datetime(), s.mention_count = 1
          ON MATCH  SET s.mention_count = s.mention_count + 1
    """, subject=claim["subject"])

    session.run("""
        MERGE (o:Entity {name: $object})
          ON CREATE SET o.created_at = datetime(), o.mention_count = 1
          ON MATCH  SET o.mention_count = o.mention_count + 1
    """, object=claim["object_entity"])

    # 2. Source node
    session.run("""
        MERGE (src:Source {name: $source_name})
          ON CREATE SET src.epistemic_trust = $trust,
                        src.tier = $tier,
                        src.created_at = datetime()
          ON MATCH  SET src.epistemic_trust = CASE
                          WHEN $trust > src.epistemic_trust THEN $trust
                          ELSE src.epistemic_trust END
    """, source_name=claim["source_name"],
         trust=float(claim["source_trust"] or 0.4),
         tier=claim["source_tier"])

    # 3. Article node + link to Source
    session.run("""
        MERGE (a:Article {url: $url})
          ON CREATE SET a.title = $title,
                        a.created_at = datetime(),
                        a.content_sha256 = $sha256,
                        a.snapshot_path = $snap
          ON MATCH SET  a.content_sha256 = CASE
                          WHEN $sha256 IS NOT NULL AND $sha256 <> '' THEN $sha256
                          ELSE a.content_sha256 END
        WITH a
        MATCH (src:Source {name: $source_name})
        MERGE (a)-[:PUBLISHED_BY]->(src)
    """, url=claim["article_url"] or "",
         title=claim["article_title"] or "",
         source_name=claim["source_name"],
         sha256=claim.get("content_sha256") or "",
         snap=claim.get("snapshot_path") or "")

    # 4. Claim node + entity links + direct SPO edge
    session.run("""
        MERGE (c:Claim {
            subject:   $subject,
            predicate: $predicate,
            object:    $object,
            temporal:  $temporal,
            spatial:   $spatial
        })
          ON CREATE SET
            c.id                    = $claim_id,
            c.epistemic_domain      = $epistemic_domain,
            c.epistemic_score       = $score,
            c.extraction_confidence = $conf,
            c.is_verifiable         = $verifiable,
            c.spo_fingerprint       = $fingerprint,
            c.created_at            = datetime(),
            c.valid_from            = datetime(),
            c.valid_until           = null,
            c.is_current            = true,
            c.lifecycle             = 'ACTIVE',
            c.verdict               = 'UNVERIFIED',
            c.quote_context         = $quote,
            c.article_title         = $article_title,
            c.source_url            = $article_url,
            c.source_name           = $source_name,
            c.publish_date          = $publish_date
          ON MATCH SET
            c.epistemic_score = CASE
              WHEN $score > c.epistemic_score THEN $score
              ELSE c.epistemic_score END
        WITH c
        MATCH (s:Entity {name: $subject}), (o:Entity {name: $object})
        MERGE (c)-[:HAS_SUBJECT]->(s)
        MERGE (c)-[:HAS_OBJECT]->(o)
        MERGE (s)-[r:PREDICATE {type: $predicate, temporal: $temporal, spatial: $spatial}]->(o)
        SET r.epistemic_score = $score,
            r.is_current = true,
            r.discovered_by_agent = $agent,
            r.source_sha256 = $sha256,
            r.verified_by_red_teamer = false,
            r.red_team_verdict = 'PENDING'
        WITH c
        MATCH (a:Article {url: $article_url})
        MERGE (c)-[:EXTRACTED_FROM]->(a)
    """,
        claim_id=str(claim["id"]),
        epistemic_domain=claim.get("epistemic_domain", "EMPIRICAL"),
        subject=claim["subject"],
        predicate=claim["predicate"],
        object=claim["object_entity"],
        temporal=claim["temporal_anchor"] or "",
        spatial=claim.get("spatial_anchor") or "",
        score=float(claim["epistemic_score"] or 0.5),
        conf=float(claim["extraction_confidence"] or 0.5),
        verifiable=bool(claim["is_verifiable"]),
        fingerprint=claim.get("spo_fingerprint") or "",
        quote=claim.get("quote_context") or "",
        article_title=claim.get("article_title") or "",
        article_url=claim.get("article_url") or "",
        source_name=claim.get("source_name") or "",
        publish_date=str(claim.get("publish_date") or ""),
        agent="OSINT_SYNTHETIC_AGENT" if claim.get("synthetic_osint") else "WEB_SCRAPER",
        sha256=claim.get("content_sha256") or "")

    # 5. Evidence node — the raw text snippet that supports the claim
    if claim.get("quote_context"):
        session.run("""
            MATCH (c:Claim {id: $claim_id})
            MERGE (e:Evidence {
                claim_id:  $claim_id,
                source_url: $src_url
            })
              ON CREATE SET
                e.raw_text       = $quote,
                e.article_title  = $title,
                e.published_by   = $src_name,
                e.epistemic_conf = $conf,
                e.created_at     = datetime()
            MERGE (c)-[:SUPPORTED_BY]->(e)
        """, claim_id=str(claim["id"]),
             src_url=claim["article_url"] or "",
             quote=str(claim.get("quote_context") or "")[:2000],  # type: ignore
             title=claim.get("article_title") or "",
             src_name=claim.get("source_name") or "",
             conf=float(claim.get("extraction_confidence") or 0.5))

    # 6. Timeline node — groups all claims about the same subject+predicate over time
    session.run("""
        MERGE (tl:Timeline {
            subject:   $subject,
            predicate: $predicate
        })
          ON CREATE SET tl.created_at = datetime()
          ON MATCH  SET tl.updated_at = datetime()
        WITH tl
        MATCH (c:Claim {id: $claim_id})
        MERGE (tl)-[:CONTAINS {epistemic_score: $score,
                                valid_from: $valid_from}]->(c)
    """, subject=claim["subject"], predicate=claim["predicate"],
         claim_id=str(claim["id"]),
         score=float(claim["epistemic_score"] or 0.5),
         valid_from=datetime.now(timezone.utc).isoformat())

    # 5. Provenance: FIRST_REPORTED_BY (if discovered internet source differs)
    if claim.get("internet_original_url") and \
       claim["internet_original_url"] != claim["article_url"]:
        session.run("""
            MERGE (orig:Source {url: $orig_url})
              ON CREATE SET orig.name = $orig_name, orig.created_at = datetime()
            WITH orig
            MATCH (c:Claim {id: $claim_id})
            MERGE (c)-[:FIRST_REPORTED_BY {date: $orig_date}]->(orig)
        """,
            orig_url=claim["internet_original_url"],
            orig_name=claim.get("internet_original_source") or "Unknown",
            claim_id=str(claim["id"]),
            orig_date=str(claim.get("internet_original_date") or ""))

    # 6. Stance relationships to existing matched claims
    stance = claim.get("neo4j_stance") or "NOVEL"
    rel_map = {
        "CORROBORATES": "CORROBORATED_BY",
        "CONTRADICTS":  "CONTRADICTS",
        "EVOLVES":      "EVOLVES",
        "DUPLICATE":    "DUPLICATE_OF",
        "SUPPORTS":     "SUPPORTS",
        "ENRICHES":     "ENRICHES",
        "DEBUNKS":      "DEBUNKS",
    }
    rel_type = rel_map.get(stance)
    if rel_type and claim.get("neo4j_matched_claim_id"):
        try:
            session.run(f"""
                MATCH (c:Claim {{id: $cid}})
                MATCH (e:Claim {{id: $eid}})
                MERGE (c)-[:{rel_type} {{similarity: $sim}}]->(e)
            """,
                cid=str(claim["id"]),
                eid=str(claim["neo4j_matched_claim_id"]),
                sim=float(claim.get("neo4j_similarity") or 0.0))
        except Exception:
            pass

    # 7. Map the Fossil Record (Corroboration Matrix)
    if claim.get("corroborations"):
        for cor in claim["corroborations"]:
            if not cor.get("url"): continue
            
            session.run("""
                MERGE (src:Source {name: $src_name})
                  ON CREATE SET src.tier = $tier, src.epistemic_trust = $trust, src.created_at = datetime()
                MERGE (a:Article {url: $url})
                  ON CREATE SET a.title = $title, a.created_at = datetime()
                MERGE (a)-[:PUBLISHED_BY]->(src)
                
                WITH a
                MATCH (c:Claim {id: $claim_id})
                MERGE (c)-[:CORROBORATED_BY {timestamp: $date, quote: $quote}]->(a)
            """, 
                claim_id=str(claim["id"]),
                src_name=cor["source_name"] or "Unknown",
                tier=int(cor["source_tier"] or 3),
                trust=float(cor["source_trust"] or 0.4),
                url=cor["url"],
                title=cor["title"],
                date=cor["date"],
                quote=cor["quote"][:2000]
            )

    # 8. Mutate Media node for Visual Provenance
    if claim.get("media"):
        session.run("""
            MERGE (m:Media {url: $media_url})
              ON CREATE SET m.phash = $phash,
                            m.created_at = datetime()
            WITH m
            MATCH (c:Claim {id: $claim_id})
            MERGE (c)-[:SUPPORTED_BY {
                synthetic_probability: $synthetic_prob,
                cross_modal_similarity: $cross_modal_sim
            }]->(m)
        """, media_url=claim["media"]["url"],
             phash=claim["media"].get("phash") or "",
             synthetic_prob=claim["media"].get("synthetic_probability") or 0.0,
             cross_modal_sim=claim.get("cross_modal_similarity"),
             claim_id=str(claim["id"]))

    # 9. Graph Attribution (Investigation Tracking)
    inv_id = claim.get("investigation_id")
    if inv_id:
        session.run("""
            MATCH (c:Claim {id: $claim_id})
            MERGE (inv:Investigation {id: $inv_id})
              ON CREATE SET inv.created_at = datetime()
            MERGE (c)-[:DISCOVERED_DURING]->(inv)
            SET c.discovered_by = $agent
        """, claim_id=str(claim["id"]),
             inv_id=int(inv_id),
             agent="OSINT_SYNTHETIC_AGENT" if claim.get("synthetic_osint") else "WEB_SCRAPER")


def mutation_worker(worker_id: int):
    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        items_processed = 0

        while items_processed < 50:
            try:
                with pg_conn.cursor() as cur:
                    cur.execute("""
                        SELECT ec.id, ec.subject, ec.predicate, ec.object_entity,
                               ec.temporal_anchor, ec.spatial_anchor, ec.extraction_confidence,
                               ec.epistemic_score, ec.is_verifiable,
                               ec.spo_fingerprint, ec.quote_context,
                               ra.title, ra.publish_date, ru.url,
                               s.name, s.epistemic_trust_score,
                               cp.internet_original_url, cp.internet_original_source,
                               cp.internet_original_date, cp.neo4j_stance,
                               cp.neo4j_matched_claim_id, cp.neo4j_similarity,
                               ec.ai_metadata, ra.id,
                               ru.metadata->>'investigation_id' as inv_id,
                               ru.metadata->>'synthetic_osint' as synthetic_osint,
                               ra.content_sha256, ra.snapshot_path
                        FROM extracted_claims ec
                        JOIN raw_articles ra  ON ec.article_id = ra.id
                        JOIN raw_urls ru      ON ra.url_id = ru.id
                        JOIN sources s        ON ru.source_id = s.id
                        LEFT JOIN claim_provenance cp ON cp.claim_id = ec.id
                        WHERE ec.pipeline_stage = 'STAGE_8_MUTATION_QUEUE'
                          AND (
                            ec.status IN ('AUTO_APPROVE', 'PROCESSING')
                            OR (
                              ec.status = 'FAILED_MUTATION'
                              AND COALESCE((ec.ai_metadata->>'mutation_retries')::int, 0) < 3
                            )
                          )
                        ORDER BY
                          CASE WHEN ec.status = 'FAILED_MUTATION' THEN 1 ELSE 0 END,
                          ec.id
                        LIMIT 1
                        FOR UPDATE OF ec SKIP LOCKED;
                    """)
                    row = cur.fetchone()
                    if not row:
                        pg_conn.rollback()
                        break

                    (claim_id, subj, pred, obj, temporal, spatial, conf, score,
                     verifiable, fingerprint, quote,
                     art_title, pub_date, art_url,
                     src_name, src_trust,
                     orig_url, orig_src, orig_date,
                     stance, matched_id, similarity, ai_metadata, raw_article_id,
                     inv_id, synthetic_osint,
                     content_sha256, snapshot_path) = row

                    import json
                    try:
                        ai_data = json.loads(ai_metadata) if ai_metadata else {}
                    except Exception:
                        ai_data = {}
                    epistemic_domain = ai_data.get("epistemic_domain", "EMPIRICAL")

                    src_tier = 1 if (src_trust or 0) >= 0.80 \
                               else (2 if (src_trust or 0) >= 0.50 else 3)
                               
                    # --- Aggregate Temporal Provenance Matrix (Fossil Record) ---
                    cur.execute("""
                        SELECT cc.quote_context, cc.discovered_at, 
                               ra.title, ru.url, 
                               s.name, s.tier, s.epistemic_trust_score
                        FROM claim_corroborations cc
                        JOIN raw_articles ra ON cc.raw_article_id = ra.id
                        JOIN raw_urls ru ON ra.url_id = ru.id
                        JOIN sources s ON ru.source_id = s.id
                        WHERE cc.claim_id = %s
                    """, (claim_id,))
                    corrobs = []
                    for c_row in cur.fetchall():
                        corrobs.append({
                            "quote": c_row[0] or "",
                            "date": str(c_row[1] or ""),
                            "title": c_row[2] or "",
                            "url": c_row[3] or "",
                            "source_name": c_row[4] or "",
                            "source_tier": c_row[5] or 3,
                            "source_trust": c_row[6] or 0.40
                        })

                    # --- Visual Media Support ---
                    cur.execute("""
                        SELECT media_url, phash, synthetic_probability
                        FROM media_provenance
                        WHERE raw_article_id = %s
                    """, (raw_article_id,))
                    m_row = cur.fetchone()
                    media_support = None
                    if m_row:
                        media_support = {
                            "url": m_row[0],
                            "phash": m_row[1],
                            "synthetic_probability": float(m_row[2] or 0.0)
                        }

                    claim = dict(
                        id=claim_id, subject=subj, predicate=pred,
                        object_entity=obj, temporal_anchor=temporal, spatial_anchor=spatial,
                        extraction_confidence=conf, epistemic_score=score,
                        is_verifiable=verifiable, spo_fingerprint=fingerprint,
                        quote_context=quote,
                        article_title=art_title, publish_date=pub_date,
                        article_url=art_url, source_name=src_name,
                        source_trust=src_trust, source_tier=src_tier,
                        internet_original_url=orig_url,
                        internet_original_source=orig_src,
                        internet_original_date=orig_date,
                        neo4j_stance=stance,
                        neo4j_matched_claim_id=matched_id,
                        neo4j_similarity=similarity,
                        epistemic_domain=epistemic_domain,
                        cross_modal_similarity=ai_data.get("cross_modal_similarity"),
                        corroborations=corrobs,
                        media=media_support,
                        investigation_id=inv_id,
                        synthetic_osint=(synthetic_osint == 'true' or synthetic_osint == True),
                        content_sha256=content_sha256,
                        snapshot_path=snapshot_path
                    )

                    print(f"  [W-{worker_id}] Mutating: [{pred}] {subj[:22]} -> {obj[:22]}")  # type: ignore

                    # --- Red Teamer (Forensic Validator) Check ---
                    if inv_id and quote and len(quote) > 10:
                        try:
                            # Use groq_pool directly to validate the epistemic logic
                            response = groq_pool.chat_completions_create(
                                model="TIER_HEAVY",
                                messages=[
                                    {
                                        "role": "system", 
                                        "content": (
                                            "You are a Forensic Validator (Red Teamer) for an OSINT system. "
                                            "Your job is to prevent hallucinations and false linkages. "
                                            "Review the extracted claim below. Does the provided quote actually "
                                            "prove this relationship beyond a reasonable doubt? Output EXACTLY one word: "
                                            "'VALID' or 'REJECTED'."
                                        )
                                    },
                                    {
                                        "role": "user",
                                        "content": (
                                            f"Subject: {subj}\n"
                                            f"Predicate: {pred}\n"
                                            f"Object: {obj}\n"
                                            f"Evidence Quote: {quote}"
                                        )
                                    }
                                ],
                                response_model=None, # Raw string response
                                temperature=0.0
                            )
                            ans = response.strip().upper()
                            if "REJECTED" in ans:
                                cur.execute("""
                                    UPDATE extracted_claims
                                    SET status = 'RED_TEAM_REJECTED',
                                        pipeline_stage = 'COMPLETE',
                                        ai_metadata = ai_metadata || '{"red_team_rejected": true}'::jsonb
                                    WHERE id = %s
                                """, (claim_id,))

                                # ── Re-queue the entities as high-priority leads ──────────
                                # The claim was rejected but the entity is still worth
                                # investigating — push it back with priority 90.
                                if inv_id:
                                    try:
                                        int_inv_id = int(inv_id)
                                        for requeue_entity in {subj, obj}:
                                            if requeue_entity and len(requeue_entity) > 2:
                                                cur.execute("""
                                                    INSERT INTO investigation_leads
                                                        (investigation_id, entity_name, lead_type, priority, status)
                                                    VALUES (%s, %s, 'GENERAL', 90, 'PENDING')
                                                    ON CONFLICT (investigation_id, entity_name)
                                                    DO UPDATE SET priority = GREATEST(investigation_leads.priority, 90),
                                                                  status = CASE
                                                                    WHEN investigation_leads.status = 'EXPLORED'
                                                                    THEN 'PENDING'
                                                                    ELSE investigation_leads.status
                                                                  END
                                                """, (int_inv_id, requeue_entity))
                                        print(f"      -> [W-{worker_id}] Requeued entities for re-investigation.")
                                    except Exception as rq_e:
                                        print(f"      -> [W-{worker_id}] Re-queue failed: {rq_e}")

                                pg_conn.commit()
                                print(f"      -> [W-{worker_id}] RED TEAM REJECTED. Sanity check failed.")
                                items_processed += 1
                                continue
                        except Exception as e:
                            print(f"      -> [W-{worker_id}] Red Teamer check failed: {e}. Proceeding.")

                    try:
                        with neo4j_driver.session() as session:
                            write_claim_to_graph(session, claim)

                        cur.execute("""
                            UPDATE extracted_claims
                            SET status = 'GRAPH_COMMITTED',
                                pipeline_stage = 'COMPLETE'
                            WHERE id = %s
                        """, (claim_id,))
                        pg_conn.commit()

                        # ── Write Red Teamer VALID verdict onto the Neo4j [:PREDICATE] edge ───
                        # Only do this if a Red Teamer check was actually performed (inv_id set).
                        if inv_id and quote and len(quote) > 10:
                            try:
                                with neo4j_driver.session() as session:
                                    session.run("""
                                        MATCH (s:Entity {name: $subject})-[r:PREDICATE]->(o:Entity {name: $object})
                                        WHERE r.type = $predicate
                                        SET r.verified_by_red_teamer = true,
                                            r.red_team_verdict = 'VALID',
                                            r.red_team_verified_at = datetime()
                                    """, subject=subj, object=obj, predicate=pred)
                            except Exception as ve:
                                print(f"      -> [W-{worker_id}] Red Teamer verdict write failed: {ve}")

                        print(f"      -> [W-{worker_id}] Committed. Score={score:.3f}")

                    except Exception as ne:
                        import json as _json
                        try:
                            _ai = _json.loads(ai_metadata) if ai_metadata else {}
                        except Exception:
                            _ai = {}
                        retries = _ai.get('mutation_retries', 0) + 1
                        _ai['mutation_retries'] = retries
                        _ai['last_mutation_error'] = str(ne)[:200]

                        if retries >= 3:
                            # Escalate to dead-letter — permanently visible in dashboard
                            cur.execute("""
                                UPDATE extracted_claims
                                SET status = 'DEAD_LETTER',
                                    pipeline_stage = 'STAGE_8_MUTATION_QUEUE',
                                    ai_metadata = ai_metadata || %s::jsonb
                                WHERE id = %s
                            """, (_json.dumps({"mutation_retries": retries, "last_mutation_error": str(ne)[:200]}), claim_id))
                            print(f"      -> [W-{worker_id}] DEAD_LETTER after {retries} attempts: {ne}")
                        else:
                            # Re-queue for retry with incremented counter
                            cur.execute("""
                                UPDATE extracted_claims
                                SET status = 'FAILED_MUTATION',
                                    pipeline_stage = 'STAGE_8_MUTATION_QUEUE',
                                    ai_metadata = ai_metadata || %s::jsonb
                                WHERE id = %s
                            """, (_json.dumps({"mutation_retries": retries, "last_mutation_error": str(ne)[:200]}), claim_id))
                            print(f"      -> [W-{worker_id}] FAILED (attempt {retries}/3): {ne}")
                        pg_conn.commit()
                    items_processed += 1
                    time.sleep(0.05)

            except Exception as le:
                print(f"  [ERROR W-{worker_id}] {le}")
                pg_conn.rollback()
                time.sleep(2)

        pg_conn.close()
    except Exception as fe:
        print(f"[FATAL W-{worker_id}] {fe}")


def process_mutation_queue():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          "Starting Stage 8: Neo4j Graph Mutation Engine (Single Pass)")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM extracted_claims
            WHERE pipeline_stage = 'STAGE_8_MUTATION_QUEUE'
              AND status IN ('AUTO_APPROVE', 'PROCESSING');
        """)
        row = cur.fetchone()
        pending = row[0] if row else 0
        cur.close()
        conn.close()

        if pending == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  "Queue empty. Exiting.")
            return

        workers = min(MAX_WORKERS, max(1, pending))
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
              f"{pending} claims. Spinning {workers} graph-write threads...")

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(mutation_worker, i) for i in range(workers)]  # type: ignore
            for f in futures:
                f.result()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Mutation batch done.")

    except KeyboardInterrupt:
        neo4j_driver.close()
        print("Stopping Graph Mutation Engine.")
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    process_mutation_queue()
