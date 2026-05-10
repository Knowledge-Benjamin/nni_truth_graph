"""
Stage 10: Claim Revalidation Daemon

Runs on a 24-hour schedule. For every ACTIVE claim older than REVALIDATION_THRESHOLD_DAYS:
  1. Generates a fresh Serper search for the claim's SPO fingerprint.
  2. Parses results for date signals.
  3. If a contradicting or superseding article is found:
     → Fires it into the Stage 1 ingestion queue (raw_urls).
     → The new article will flow through all pipeline stages and Stage 9
       will handle the EVOLVES/CONTRADICTS resolution.
  4. If corroborating articles found:
     → Boosts epistemic_score + refreshes support_count.
     → Resets freshness timer on the claim.
  5. If no corroboration found after 90 days → decrements trust score
     and marks claim as STALE.
"""

import os
import sys
import time
import json
import math
import requests
import psycopg2
from datetime import datetime, timezone, timedelta
from neo4j import GraphDatabase
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))

from ai_engine.core.logger import get_printer
print = get_printer(10)  # Green

DATABASE_URL   = os.getenv("DATABASE_URL")
NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

from ai_engine.core.groq_pool import groq_pool
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

SERPER_URL                 = "https://google.serper.dev/search"
REVALIDATION_THRESHOLD_DAYS = 30   # Claims older than this get revalidated
STALE_THRESHOLD_DAYS        = 90   # Claims with no corroboration become STALE
RUN_INTERVAL_HOURS          = 24   # How often the daemon runs


def serper_search(query: str) -> list[dict]:
    try:
        headers  = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload  = {"q": query, "num": 10,
                    "tbs": "qdr:m"}  # Results from the last month
        resp     = requests.post(SERPER_URL, headers=headers,
                                 json=payload, timeout=10)
        return resp.json().get("organic", [])
    except Exception as e:
        print(f"    [SERPER ERROR] {e}")
        return []


def llm_assess_revalidation(claim_spo: str, search_snippet: str) -> str:
    """
    Returns: CORROBORATES | SUPERSEDES | CONTRADICTS | UNRELATED
    """
    prompt = f"""You are a fact-checking engine for a Living Truth Knowledge Graph.

Original claim: "{claim_spo}"
Newly found web snippet: "{search_snippet[:500]}"

Does the snippet:
- CORROBORATES: confirm the claim is still true
- SUPERSEDES: describe an updated state that replaces the claim
- CONTRADICTS: directly oppose the claim's truth
- UNRELATED: not meaningfully related

Reply with exactly one word."""
    try:
        resp = groq_pool.chat_completions_create(
            model='TIER_LIGHT',
            messages=[
                {"role": "system", "content": "You are a fact-checking engine. Reply with exactly one word: CORROBORATES, SUPERSEDES, CONTRADICTS, or UNRELATED."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        word = resp.choices[0].message.content.strip().upper().split()[0]
        return word if word in ("CORROBORATES", "SUPERSEDES",
                                "CONTRADICTS", "UNRELATED") else "UNRELATED"
    except Exception:
        return "UNRELATED"


def fire_new_ingestion(pg_cur, url: str, source_name: str,
                       revalidation_of_claim_id: int):
    """Queue a newly discovered URL for full pipeline processing."""
    try:
        pg_cur.execute("""
            INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
            VALUES (%s, %s, %s, 'Revalidation', 0.50)
            ON CONFLICT (url) DO NOTHING RETURNING id;
        """, (source_name, url, url.split('/')[2] if '/' in url else url))
        row = pg_cur.fetchone()
        if not row:
            pg_cur.execute("SELECT id FROM sources WHERE url = %s", (url,))
            row = pg_cur.fetchone()
        if not row:
            return
        source_id = row[0]

        pg_cur.execute("""
            INSERT INTO raw_urls (source_id, url, metadata, status)
            VALUES (%s, %s, %s, 'PENDING_SCRAPE')
            ON CONFLICT (url) DO NOTHING;
        """, (source_id, url, json.dumps({
            "origin": "revalidation",
            "revalidating_claim_id": revalidation_of_claim_id
        })))
        print(f"      [INGESTION FIRED] {url[:60]}")
    except Exception as e:
        print(f"      [FIRE ERROR] {e}")


def revalidate_claim(pg_cur, claim_id: int, subject: str,
                     predicate: str, obj: str, created_at,
                     lifecycle: str):
    """Run one claim through the revalidation process."""
    days_old = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).days
    spo_text = f"{subject} {predicate.replace('_',' ')} {obj}"

    print(f"  [Revalidating | {days_old}d old] [{predicate}] "
          f"{subject[:20]} -> {obj[:20]}")

    results = serper_search(spo_text)
    corroborations = 0
    fired_new = False

    for r in results[:5]:
        snippet  = r.get("snippet", "")
        link     = r.get("link", "")
        verdict  = llm_assess_revalidation(spo_text, snippet)

        if verdict == "CORROBORATES":
            corroborations += 1
        elif verdict in ("SUPERSEDES", "CONTRADICTS") and link:
            fire_new_ingestion(pg_cur, link,
                               r.get("source", "Unknown"),
                               claim_id)
            fired_new = True

    # Apply results
    if corroborations > 0:
        # Still true — boost score and reset freshness clock
        pg_cur.execute("""
            UPDATE extracted_claims
            SET epistemic_score = LEAST(1.0, epistemic_score + %s),
                valid_from      = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (min(0.05, corroborations * 0.01), claim_id))
        print(f"      -> CORROBORATED ({corroborations}x). Score boosted.")

    elif days_old > STALE_THRESHOLD_DAYS and not fired_new:
        # No supporting evidence after 90 days → STALE
        pg_cur.execute("""
            UPDATE extracted_claims
            SET lifecycle       = 'STALE',
                epistemic_score = GREATEST(0.0, epistemic_score - 0.05)
            WHERE id = %s
        """, (claim_id,))
        # Mirror in Neo4j
        try:
            with neo4j_driver.session() as session:
                session.run("""
                    MATCH (c:Claim {id: $cid})
                    SET c.lifecycle = 'STALE',
                        c.epistemic_score = c.epistemic_score - 0.05
                """, cid=str(claim_id))
        except Exception:
            pass
        print(f"      -> STALE after {days_old} days with no corroboration.")

    else:
        print(f"      -> No signal. Claim remains ACTIVE.")

    time.sleep(1.5)  # Serper rate limit


def revalidation_sweep():
    """Full sweep of all ACTIVE claims older than REVALIDATION_THRESHOLD_DAYS."""
    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = False
        pg_cur  = pg_conn.cursor()

        threshold = datetime.now(timezone.utc) - \
                    timedelta(days=REVALIDATION_THRESHOLD_DAYS)

        pg_cur.execute("""
            SELECT id, subject, predicate, object_entity, valid_from, lifecycle
            FROM extracted_claims
            WHERE lifecycle = 'ACTIVE'
              AND pipeline_stage = 'COMPLETE'
              AND status = 'GRAPH_COMMITTED'
              AND valid_from < %s
            ORDER BY valid_from ASC
            LIMIT 100;
        """, (threshold,))
        rows = pg_cur.fetchall()

        print(f"[Revalidation] {len(rows)} claims due for revalidation.")

        for row in rows:
            claim_id, subj, pred, obj, valid_from, lifecycle = row
            revalidate_claim(pg_cur, claim_id, subj, pred, obj,
                             valid_from, lifecycle)
            pg_conn.commit()

        pg_cur.close()
        pg_conn.close()
        return len(rows)

    except Exception as e:
        print(f"[Stage 10 ERROR] {e}")
        return 0


def run_revalidation_daemon():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          "Starting Stage 10: Claim Revalidation Daemon (Single Pass) ")

    try:
        count = revalidation_sweep()
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
              f"Revalidation cycle done. {count} claims checked.")
    except KeyboardInterrupt:
        neo4j_driver.close()
        print("Stopping Revalidation Daemon.")
    except Exception as e:
        print(f"Fatal error: {e}")


if __name__ == "__main__":
    run_revalidation_daemon()
