# pyre-ignore-all-errors
"""
article_worker.py — Enterprise 7-Layer Knowledge Article Daemon

Architecture Layers Implemented:
  1. Importance-Driven Hierarchy: Classifies claims into Foundational vs Supporting sections.
  2. Corroboration Evidence Pyramid: Uses duplicate tracking to weigh highly corroborated facts.
  3. Multi-Pass Generation: Generates the article progressively section-by-section.
  4. Entity Link Graph: Prompts enforce `[[Entity Name]]` markdown hyperlinking.
  5. Confidence-Stratified Rendering: Article persists as a structured JSON object.
  6. Incremental Updates: MD5 hashing of claim UUIDs prevents regenerating unchanged sections.
  7. Evidence Trail: Incorporates Badges (e.g., 🥇 Highly Corroborated) and preserves full source links.
"""

import os
import sys
import time
import json
import math
import hashlib
import logging
import psycopg2  # type: ignore
import re
from datetime import datetime, timezone
from neo4j import GraphDatabase  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing import Optional, Dict, List, Any
from dotenv import load_dotenv  # type: ignore

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

from ai_engine.core.groq_pool import groq_pool  # type: ignore

logging.basicConfig(level=logging.INFO, format='[ArticleWorker] %(message)s')
log = logging.getLogger(__name__)

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

BATCH_SIZE  = 20
MIN_CLAIMS  = 3
CYCLE_SLEEP = 600  # 10 minutes

# 1. Importance Hierarchy definitions
SECTION_MAP = {
    "Foundational Overview": ["IS_A", "SUBCLASS_OF", "IS_TYPE_OF", "CATEGORIZED_AS", "WAS_BORN_IN", "FOUNDED_IN", "STARTED_IN"],
    "Structural Relationships": ["IS_PART_OF", "CONTAINS", "PART_OF", "HAS_COMPONENT", "CONSISTS_OF"],
    "Geographic Context": ["IS_LOCATED_IN", "HAS_CAPITAL", "BORDERS", "IS_IN", "LOCATED_AT"],
    "Demographics & Culture": ["HAS_POPULATION", "HAS_LANGUAGE", "HAS_CURRENCY", "HAS_RELIGION", "HAS_GOVERNMENT"],
    "Historical Events": ["HAPPENED_IN", "OCCURRED_IN", "ENDED_IN", "DISSOLVED_IN", "SIGNED_IN"],
    "Ecosystem Interactions": ["IS_RELATED_TO", "ASSOCIATED_WITH", "REFERENCED_BY", "CITED_BY", "USED_BY"]
}

# --- Database & Search Helpers ---

def get_stale_entities(neo4j_session, batch_size: int) -> list[dict]:
    result = neo4j_session.run("""
        MATCH (e:Entity)
        WHERE (
            e.article IS NULL
            OR e.article_stale = true
        )
        AND coalesce(e.article_last_attempt, datetime({year:1970, month:1, day:1})) < datetime() - duration({minutes: 10})
        AND coalesce(e.article_failure_count, 0) < 3
        WITH e, coalesce(e.search_count, 0) AS popularity, coalesce(e.mention_count, 0) AS mc
        ORDER BY popularity DESC, e.article_stale DESC, mc DESC
        LIMIT $batch_size
        RETURN e.name AS name, e.mention_count AS mentions,
               popularity,
               e.article_generated_at AS last_gen,
               e.article AS existing_article
    """, batch_size=batch_size)
    return [dict(r) for r in result]


def mark_article_attempt(neo4j_session, entity_name: str, succeeded: bool):
    if succeeded:
        neo4j_session.run("""
            MATCH (e:Entity {name: $name})
            SET e.article_last_attempt = datetime(),
                e.article_last_success = datetime(),
                e.article_failure_count = 0,
                e.article_stale = false
        """, name=entity_name)
    else:
        neo4j_session.run("""
            MATCH (e:Entity {name: $name})
            SET e.article_last_attempt = datetime(),
                e.article_failure_count = coalesce(e.article_failure_count, 0) + 1,
                e.article_stale = true
        """, name=entity_name)


def fetch_entity_claims(entity_name: str) -> list[dict]:
    pg_conn = psycopg2.connect(DATABASE_URL)
    with pg_conn.cursor() as cur:
        # Layer 2: Corroboration Engine included via scalar subquery
        cur.execute("""
            SELECT
                ec.id            AS claim_uuid,
                ec.subject,
                ec.predicate,
                ec.object_entity AS object,
                ec.temporal_anchor,
                ec.spatial_anchor,
                ec.quote_context,
                ec.epistemic_score,
                COALESCE((SELECT COUNT(*) FROM extracted_claims sub WHERE sub.spo_fingerprint = ec.spo_fingerprint AND ec.spo_fingerprint IS NOT NULL), 1) AS corroboration_count,
                ra.title         AS article_title,
                ra.publish_date  AS publish_date,
                ru.url           AS source_url,
                s.name           AS source_name,
                cp.internet_original_url   AS original_url,
                cp.internet_original_source AS original_source,
                ec.status        AS claim_status,
                ec.ai_metadata
            FROM extracted_claims ec
            JOIN raw_articles ra ON ec.article_id = ra.id
            JOIN raw_urls     ru ON ra.url_id = ru.id
            JOIN sources       s ON ru.source_id = s.id
            LEFT JOIN claim_provenance cp ON cp.claim_id = ec.id
            WHERE (LOWER(ec.subject) = LOWER(%s) OR LOWER(ec.object_entity) = LOWER(%s))
              AND ec.status IN ('GRAPH_COMMITTED', 'AUTO_APPROVE', 'CONTRADICTED')
              AND ec.article_incorporated IS NOT TRUE
            ORDER BY corroboration_count DESC, ec.epistemic_score DESC
            LIMIT 300;
        """, (entity_name, entity_name))
        cols = [d[0] for d in cur.description]
        claims = [dict(zip(cols, row)) for row in cur.fetchall()]

        if claims:
            # Inject Fossil Record temporal evolution matrix into the LLM payload
            ids = tuple(c['claim_uuid'] for c in claims)
            cur.execute("""
                SELECT cc.claim_id, cc.discovered_at, s.name, cc.quote_context
                FROM claim_corroborations cc
                JOIN raw_articles ra ON cc.raw_article_id = ra.id
                JOIN raw_urls ru ON ra.url_id = ru.id
                JOIN sources s ON ru.source_id = s.id
                WHERE cc.claim_id IN %s
                ORDER BY cc.discovered_at ASC
            """, (ids,))
            
            from collections import defaultdict
            cormap = defaultdict(list)
            for row in cur.fetchall():
                cormap[row[0]].append({"date": row[1], "source_name": row[2], "quote": row[3]})
                
            for c in claims:
                tl = cormap.get(c['claim_uuid'], [])
                if tl:
                    c["corroboration_timeline"] = tl
                    c["corroboration_count"] = max(c.get("corroboration_count", 1), len(tl))
                
                # Parse visual AI metadata
                try:
                    c['synthetic_prob'] = json.loads(c.get('ai_metadata') or '{}').get('synthetic_probability')
                except Exception:
                    c['synthetic_prob'] = None
                    
        pg_conn.close()
        return claims


def fetch_relevant_excerpts(entity_name: str, top_k: int = 4) -> str:
    """
    Core semantic extraction: Uses PostgreSQL pgvector <-> to find the most contextually relevant
    source texts for the entity to provide expansive journalistic depth for the LLM. 
    """
    from ai_engine.core.inference_pool import inference_pool  # type: ignore
    try:
        entity_embedding = inference_pool.embed(entity_name)
    except Exception as e:
        log.error(f"    [Embedding Error] Failed to fetch vector: {e}")
        return ""
        
    if not entity_embedding:
        return ""

    embedding_literal = f"[{','.join(str(f) for f in entity_embedding)}]"

    pg_conn = psycopg2.connect(DATABASE_URL)
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT ra.title, LEFT(ra.raw_text, 40000) AS excerpt
            FROM article_categories ac
            JOIN raw_articles ra ON ac.article_id = ra.id
            WHERE ra.status IN ('EXTRACTED', 'PENDING_EXTRACTION')
            ORDER BY ac.embedding <-> %s::vector
            LIMIT %s;
        """, (embedding_literal, top_k))
        
        excerpts = []
        for row in cur.fetchall():
            title, text = row
            if text:
                excerpts.append(f"--- Context Source (Full/Chunked Text): {title} ---\n{text.strip()}")
        pg_conn.close()
        return "\n\n".join(excerpts)


def generate_section_hash(claims: list[dict]) -> str:
    uuids = sorted([str(c['claim_uuid']) for c in claims])
    return hashlib.md5("".join(uuids).encode()).hexdigest()


def group_claims(claims: list[dict]) -> dict[str, list[dict]]:
    sections = {k: [] for k in SECTION_MAP.keys()}
    sections["Contradictions & Disputes"] = []
    sections["Other Details"] = []

    for c in claims:
        # Layer 7: Evidence Trail Flagging
        if c.get("claim_status") == "CONTRADICTED" or c.get("stance") == "CONTRADICTS":
            sections["Contradictions & Disputes"].append(c)
            continue
            
        pred = (c.get("predicate") or "").upper()
        placed = False
        for sec_name, keywords in SECTION_MAP.items():
            if any(pred == kw or pred.startswith(kw) for kw in keywords):
                sections[sec_name].append(c)  # type: ignore[index]
                placed = True
                break
        if not placed:
            sections["Other Details"].append(c)  # type: ignore[index]

    return {k: v for k, v in sections.items() if len(v) > 0}

# --- LLM Processing (Layer 3 & 7) ---

class SectionGeneration(BaseModel):
    paragraph: str = Field(description="The synthesized markdown paragraph for this section.")

def generate_section(entity_name: str, section_name: str, claims: list[dict], existing_content: Optional[str] = None, article_excerpts: Optional[str] = None) -> str:
    """Pass 2 & 4: Generates the synthesized narrative for a single module using Groq."""
    
    lines = [
        f"You are the master encyclopedic synthesis engine for the entity: [[{entity_name}]].",
        f"Write the '{section_name}' section for its expansive, authoritative wiki article.",
        "Your mission is to weave the atomic facts provided below into a rich, natural, and highly readable journalistic narrative.",
        "Do not simply list or regurgitate the facts transactionally. Connect them logically, add contextual depth, use elegant transitions, and employ an authoritative tone.",
        "Make the article feel engaging, insightful, and impressively in-depth, as if written by a Pulitzer-winning historian.",
        "\nSTRICT RULES:",
        "1. ALWAYS wrap entity names or important nouns in [[Entity Name]] brackets for hyperlinks.",
        "2. EVERY SINGLE FACT must be cited with its `[REF:<uuid>]` immediately following the claim in the sentence.",
        "3. Incorporate the provided corroboration badges (e.g. 🥇) exactly as shown before the fact, naturally integrated within the prose.",
        "4. DO NOT hallucinate. Only use the facts provided. Your primary job is to articulate these facts beautifully.",
        "5. OUTPUT FORMAT: You are a JSON-only API. Return only the raw JSON object matching the schema exactly. Do NOT wrap output in ```json code blocks, do NOT include preambles, and do NOT add trailing comments."
    ]
    
    if existing_content:
        lines.append(f"\n[EXISTING TEXT TO EXPAND]\n{existing_content}\n")
        
    if article_excerpts:
        lines.append("\n[BACKGROUND SOURCE EXCERPTS FOR DEPTH (DO NOT CITE THESE)]")
        lines.append(article_excerpts)
        
    lines.append("\n[VERIFIED FACT BASE (YOU MUST CITE THESE)]")
    for c in claims[:40]:  # type: ignore[misc]
        # Corroboration layering
        badge = "🥇 " if c.get('corroboration_count', 1) >= 3 else "🥈 " if c.get('corroboration_count', 1) == 2 else ""
        stmt = f"{badge}[REF:{c['claim_uuid']}] {c['subject']} {c['predicate'].replace('_', ' ').lower()} {c['object']}"
        if c.get('temporal_anchor'): stmt += f" (when: {c['temporal_anchor']})"
        if c.get('spatial_anchor'):  stmt += f" (where: {c['spatial_anchor']})"
        
        # --- NEW: EVOLUTION TIMELINE INJECTION (Historiography Engine) ---
        if c.get('synthetic_prob') is not None and c.get('synthetic_prob') > 0.85:
            stmt += f"\n   *Visual Evidence Warning:* 🚨 Detected as likely AI-Generated / Deepfake ({round(c['synthetic_prob']*100)}% synthetic certainty)"
        elif c.get('synthetic_prob') is not None and c.get('synthetic_prob') < 0.15:
            stmt += f"\n   *Visual Evidence:* ✅ Authentic visual media verified"
            
        if c.get('corroboration_timeline'):
            stmt += "\n   *Information Evolution Timeline:*"
            for idx, corr in enumerate(c['corroboration_timeline'][:5]): # Cap at 5 to maintain context window length
                date_str = str(corr['date'])[:10] if corr['date'] else "Unknown Date"
                quote = str(corr.get('quote') or "").strip()[:200]
                stmt += f"\n     - Update {idx+1}: On {date_str}, {corr['source_name']} reported: \"{quote}\""
        elif c.get('quote_context'):
            stmt += f"\n   *Context snippet from source:* \"{c['quote_context'].strip()[:300]}\""
        lines.append(stmt)

    prompt = "\n".join(lines)
    
    try:
        resp = groq_pool.chat_completions_create(
            model='TIER_HEAVY',
            messages=[{"role": "user", "content": prompt}],
            response_model=SectionGeneration,
            temperature=0.4
        )
        if isinstance(resp, dict):
            return resp.get("paragraph", "")
        return resp.paragraph
    except Exception as e:
        log.error(f"  [LLM Error in '{section_name}'] {e}")
        return ""


def validate_refs(raw_text: str, valid_uuids: set) -> tuple[str, list[str]]:
    used_refs = []
    cleaned_lines = []
    for para in raw_text.split('\n'):
        if not para.strip() or para.startswith('#') or para.startswith('[[') or len(para) < 5:
            cleaned_lines.append(para.strip())
            continue
        
        matches = re.findall(r'\[REF:([a-zA-Z0-9\-]+)\]', para)
        if matches:
            valid_in_para = [r for r in matches if r in valid_uuids]
            if valid_in_para:
                used_refs.extend(valid_in_para)
                cleaned_lines.append(para.strip())
        else:
            if '.' in para: pass # Drop hallucinated unreferenced sentences
            else: cleaned_lines.append(para.strip())
            
    return '\n'.join(cleaned_lines), list(set(used_refs))

# --- Main Orchestration ---

def process_entity(entity: dict, neo4j_session):
    name = entity['name']
    log.info(f"  Processing entity: '{name}'")

    claims = fetch_entity_claims(name)
    if len(claims) < MIN_CLAIMS:
        log.info(f"    Skipping '{name}' — only {len(claims)} claim(s)")
        mark_article_attempt(neo4j_session, name, succeeded=False)
        return

    # Layer 5: Base Incremental Object
    existing_json = {}
    if entity.get("existing_article"):
        try:
            existing_json = json.loads(entity["existing_article"])
        except: pass

    sections = group_claims(claims)
    updated_json = {}
    total_used_uuids = set()
    valid_uuids = {str(c['claim_uuid']) for c in claims}
    
    # Pre-load previous references if they exist
    all_references = existing_json.get("_references", [])
    
    any_section_changed = False
    
    for sec_name, sec_claims in sections.items():
        sec_hash = generate_section_hash(sec_claims)
        
        # Layer 6: Incremental Skip
        existing_sec = existing_json.get(sec_name, {})  # type: ignore[misc]
        if existing_sec and existing_sec.get("hash") == sec_hash:  # type: ignore[misc]
            log.info(f"    -> [SKIPPED] '{sec_name}' (No new claims, MD5 matched)")
            updated_json[sec_name] = existing_sec
            for u in existing_sec.get("used_uuids", []):  # type: ignore[misc]
                total_used_uuids.add(u)
            continue
            
        # Semantic Extraction
        article_excerpts = fetch_relevant_excerpts(name)
            
        # Generation Mode
        log.info(f"    -> [GENERATING] '{sec_name}' using {len(sec_claims)} claims...")
        raw_output = generate_section(name, sec_name, sec_claims, existing_content=existing_sec.get("content"), article_excerpts=article_excerpts)  # type: ignore[misc]
        
        if raw_output:
            cleaned_text, used_refs = validate_refs(raw_output, valid_uuids)
            if cleaned_text.strip():
                updated_json[sec_name] = {
                    "hash": sec_hash,
                    "content": cleaned_text,
                    "used_uuids": used_refs,
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }
                for u in used_refs: total_used_uuids.add(u)
                any_section_changed = True

    if not any_section_changed:
        log.info(f"    No changes required for '{name}'.")
        mark_article_attempt(neo4j_session, name, succeeded=True)
        return

    # Compilation & Formatting
    claim_map = {c['claim_uuid']: c for c in claims}
    for u in total_used_uuids:
        if u in claim_map:
            c = claim_map[u]
            # Avoid dupe references
            if not any(r['uuid'] == u for r in all_references):
                all_references.append({  # type: ignore[misc]
                    "uuid": u,
                    "source_name": c.get("original_source") or c.get("source_name") or "Unknown",
                    "source_url":  c.get("original_url") or c.get("source_url") or "",
                    "publish_date": str(c.get("publish_date") or ""),
                    "article_title": c.get("article_title") or "",
                    "quote_context": c.get("quote_context") or "",
                    "epistemic_score": float(c.get("epistemic_score") or 0),
                    "stance":       c.get("stance") or "ORIGINAL",
                    "corroboration_count": c.get("corroboration_count", 1)
                })

    updated_json["_references"] = sorted(all_references, key=lambda x: x.get('corroboration_count', 0), reverse=True)
    
    # Commit structures
    serialized_article = json.dumps(updated_json, default=str)
    serialized_refs = json.dumps(updated_json["_references"], default=str)
    
    neo4j_session.run("""
        MATCH (e:Entity {name: $name})
        SET e.article               = $article,
            e.article_references    = $refs,
            e.article_generated_at  = datetime(),
            e.article_stale         = false,
            e.article_claim_count   = $claim_count,
            e.article_last_success  = datetime(),
            e.article_failure_count = 0
    """, name=name, article=serialized_article, refs=serialized_refs, claim_count=len(updated_json.get("_references", [])))
    
    log.info(f"  → Stored Enterprise JSON for '{name}' ({len(updated_json)-1} sections generated/retained).")
    
    if total_used_uuids:
        pg_conn = psycopg2.connect(DATABASE_URL)
        with pg_conn.cursor() as cur:
            int_uuids = [int(u) for u in total_used_uuids]
            cur.execute("UPDATE extracted_claims SET article_incorporated = TRUE WHERE id = ANY(%s)", (int_uuids,))
        pg_conn.commit()
        pg_conn.close()


def run_article_daemon():
    log.info("Starting Enterprise Multi-Pass Living Article Worker")
    try:
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except Exception as e:
        log.error(f"Failed to connect to databases: {e}")
        raise

    cycle = 0
    while True:
        cycle += 1
        log.info(f"\n[{datetime.now(timezone.utc).isoformat()}] Cycle #{cycle}")
        try:
            with neo4j_driver.session() as session:
                stale_entities = get_stale_entities(session, BATCH_SIZE)
                
            if not stale_entities:
                log.info("  No stale entities found. Sleeping...")
            else:
                with neo4j_driver.session() as session:
                    for entity in stale_entities:
                        try:
                            process_entity(entity, session)
                            time.sleep(1)
                        except Exception as e:
                            log.error(f"  Failed processing '{entity.get('name')}': {e}")
        except Exception as e:
            log.error(f"Cycle error: {e}")
            pass
            
        time.sleep(CYCLE_SLEEP)

if __name__ == "__main__":
    run_article_daemon()
