"""
ai_engine/orchestrator/harvester.py
─────────────────────────────────────────────────────────────────────────────
Harvester: After the pipeline processes URLs tagged with an investigation_id,
this module reads the resulting extracted_claims, uses the LLM to score their
relevance and novelty, and inserts new high-value leads into investigation_leads.

Also tracks novel_discoveries to support Diminishing Returns termination.
"""

import os
import sys
import json
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from ai_engine.core.llm_router import llm_pool


class ScoredLead(BaseModel):
    entity_name: str
    lead_type: str = Field(description="EMAIL | IP | DOMAIN | WALLET | PERSON | ORGANISATION | GENERAL")
    priority: int  = Field(description="0-100, higher = more relevant to the investigation goal")
    relevance_reason: str = Field(description="One sentence: why this entity is relevant")


class HarvestResult(BaseModel):
    leads: list[ScoredLead] = Field(
        description="List of entities worth exploring as new leads. Filter out generic entities."
    )
    goal_progress_summary: str = Field(
        description=(
            "1-2 sentences summarising what was learned in this harvest batch "
            "and how close we are to answering the investigation goal."
        )
    )
    goal_achieved: bool = Field(
        description="True ONLY if you are highly confident the investigation goal has been answered."
    )


def run_harvester(
    investigation_id: int,
    investigation_target: str,
    goal_type: str,
    exhaust_predicate: str | None,
    pg_conn,
    neo4j_driver=None,
) -> dict:
    """
    Reads pipeline-completed claims for this investigation, scores novelty,
    and inserts new leads. Also mines the existing Neo4j graph for neighbor
    entities of already-explored leads, seeding them without waiting on pipeline.
    Returns a summary dict.
    """

    # ── Phase 0: Graph-based lead expansion (fast, no LLM needed) ───────────
    # For every EXPLORED lead, pull its Neo4j neighbors and insert them as
    # PENDING leads. This uses existing internal knowledge immediately.
    graph_inserted = 0
    if neo4j_driver:
        try:
            with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT entity_name FROM investigation_leads
                    WHERE investigation_id = %s AND status = 'EXPLORED'
                    ORDER BY priority DESC LIMIT 30
                    """,
                    (investigation_id,)
                )
                explored_leads = [r["entity_name"] for r in cur.fetchall()]

                cur.execute(
                    "SELECT entity_name FROM investigation_leads WHERE investigation_id = %s",
                    (investigation_id,)
                )
                known_entities = {r["entity_name"] for r in cur.fetchall()}

            for entity_name in explored_leads:
                try:
                    with neo4j_driver.session() as session:
                        result = session.run(
                            """
                            MATCH (e:Entity)
                            WHERE toLower(e.name) = toLower($name)
                               OR toLower(e.name) CONTAINS toLower($name)
                            WITH e LIMIT 1
                            MATCH (e)-[r]-(neighbor:Entity)
                            WHERE neighbor.name IS NOT NULL
                            WITH neighbor, type(r) AS rel_type,
                                 coalesce(neighbor.mention_count, 0) AS pop
                            ORDER BY pop DESC
                            RETURN neighbor.name AS name, rel_type, pop
                            LIMIT 50
                            """,
                            {"name": entity_name}
                        )
                        neighbors = list(result)

                    with pg_conn.cursor() as cur:
                        for rec in neighbors:
                            neighbor_name = rec["name"]
                            if not neighbor_name or neighbor_name in known_entities:
                                continue
                            pop = rec["pop"] or 0
                            priority = min(92, 35 + min(57, int(pop / 5)))
                            cur.execute(
                                """
                                INSERT INTO investigation_leads
                                    (investigation_id, entity_name, lead_type, priority, status, context)
                                VALUES (%s, %s, 'GENERAL', %s, 'PENDING', %s)
                                ON CONFLICT (investigation_id, entity_name) DO NOTHING
                                """,
                                (investigation_id, neighbor_name, priority, f"Discovered in graph as a neighbor of {entity_name} with relationship: {rec['rel_type']}")
                            )
                            known_entities.add(neighbor_name)
                            graph_inserted += 1
                    pg_conn.commit()
                except Exception as ne:
                    print(f"[Harvester] Neo4j neighbor fetch for '{entity_name}' failed: {ne}")

            if graph_inserted > 0:
                print(f"[Harvester] Graph expansion: +{graph_inserted} leads from Neo4j neighbors.")
        except Exception as ge:
            print(f"[Harvester] Graph expansion failed (non-fatal): {ge}")

    # ── Pull completed claims for this investigation ─────────────────────────
    # Claims are linked via raw_urls.metadata->>'investigation_id'
    with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ec.subject, ec.predicate, ec.object_entity,
                   ec.epistemic_score, ec.extraction_confidence,
                   ru.metadata->>'lead_entity' AS source_lead
            FROM extracted_claims ec
            JOIN raw_articles ra ON ra.id = ec.article_id
            JOIN raw_urls     ru ON ru.id = ra.url_id
            WHERE (ru.metadata->>'investigation_id')::int = %s
              AND ec.status = 'GRAPH_COMMITTED'
              AND ec.id > COALESCE(
                  (SELECT (findings->>'last_harvested_claim_id')::int FROM investigations WHERE id = %s),
                  0
              )
            ORDER BY ec.id ASC
            LIMIT 200
            """,
            (investigation_id, investigation_id)
        )
        raw_claims = cur.fetchall()

    if not raw_claims:
        return {"new_leads": 0, "goal_achieved": False, "summary": "No new committed claims to harvest."}

    last_claim_id = int(raw_claims[-1]["id"]) if "id" in raw_claims[0] else None

    # Collect candidate entities from all claims
    candidate_entities: set[str] = set()
    for c in raw_claims:
        if c["subject"]:
            candidate_entities.add(c["subject"])
        if c["object_entity"]:
            candidate_entities.add(c["object_entity"])

    # ── Get already-known leads to avoid re-adding them ─────────────────────
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT entity_name FROM investigation_leads WHERE investigation_id = %s",
            (investigation_id,)
        )
        known_entities = {row[0] for row in cur.fetchall()}

    novel_candidates = [e for e in candidate_entities if e not in known_entities]

    if not novel_candidates:
        return {"new_leads": 0, "goal_achieved": False, "summary": "No novel entities found in batch."}

    # Build a compact claims summary for the LLM
    claims_summary = "\n".join(
        f"- {c['subject']} {c['predicate']} {c['object_entity']} "
        f"(confidence={c['extraction_confidence']:.2f})"
        for c in raw_claims[:50]  # cap context size
    )
    entities_list = "\n".join(f"- {e}" for e in novel_candidates[:80])

    # ── LLM: Score and filter leads ─────────────────────────────────────────
    harvest_result: HarvestResult = llm_pool.chat_completions_create(
        model="TIER_HEAVY",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the Lead Detective reviewing freshly extracted intelligence. "
                    "Your job is to identify which newly discovered entities are worth "
                    "investigating further as leads. Filter out generic or irrelevant entities "
                    "(countries, vague phrases, common words). Assign priority 0-100."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Investigation goal: {investigation_target}\n"
                    f"Goal type: {goal_type}\n"
                    f"Predicate of interest: {exhaust_predicate or 'N/A'}\n\n"
                    f"Recently extracted claims:\n{claims_summary}\n\n"
                    f"Novel entities not yet in our lead queue:\n{entities_list}\n\n"
                    "Score and filter the novel entities. Only include genuinely relevant leads."
                ),
            },
        ],
        response_model=HarvestResult,
        temperature=0.1,
    )

    # ── Persist new leads ────────────────────────────────────────────────────
    inserted = 0
    with pg_conn.cursor() as cur:
        for lead in harvest_result.leads:
            if lead.priority < 20:
                continue  # discard low-relevance noise
            cur.execute(
                """
                INSERT INTO investigation_leads
                    (investigation_id, entity_name, lead_type, priority, status, context)
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
                ON CONFLICT (investigation_id, entity_name) DO NOTHING
                """,
                (investigation_id, lead.entity_name, lead.lead_type, lead.priority, lead.relevance_reason)
            )
            inserted += 1

        # Advance the harvest watermark and update novel_discoveries
        extra_update = ""
        if last_claim_id:
            cur.execute(
                """
                UPDATE investigations
                SET findings = findings || %s::jsonb,
                    novel_discoveries = novel_discoveries + %s
                WHERE id = %s
                """,
                (
                    Json({"last_harvested_claim_id": last_claim_id,
                          "last_harvest_summary": harvest_result.goal_progress_summary}),
                    inserted,
                    investigation_id,
                )
            )

    pg_conn.commit()

    print(
        f"[Harvester] Investigation #{investigation_id}: "
        f"{inserted} new leads inserted, goal_achieved={harvest_result.goal_achieved}"
    )
    return {
        "new_leads":    inserted,
        "goal_achieved": harvest_result.goal_achieved,
        "summary":      harvest_result.goal_progress_summary,
    }
