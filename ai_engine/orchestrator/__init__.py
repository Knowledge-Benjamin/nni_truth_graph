"""
ai_engine/orchestrator/__init__.py
─────────────────────────────────────────────────────────────────────────────
Main Orchestrator Loop — The Lead Detective.

Called by worker.py on the S11 interval. For every ACTIVE investigation:
  1. Run triage if this is the first tick (no leads yet)
  2. Spawn up to MAX_CONCURRENT_AGENTS parallel sub-agent threads
     each claiming a PENDING lead and injecting URLs
  3. Run the harvester to read completed pipeline output and score new leads
  4. Run the terminator to check all four stopping conditions
  5. If terminated, write the final summary and mark COMPLETED

Backward compatibility: touches ONLY new tables (investigations, investigation_leads)
and raw_urls.metadata (additive JSONB field). No existing schema is modified.
"""

import os
import sys
import time
import json
import threading
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../.env'))

DATABASE_URL = os.getenv("DATABASE_URL")
SEARXNG_URL  = os.getenv("SEARXNG_URL", "")

from .triage    import triage_target, persist_triage
from .lead_agent import run_lead_agent
from .harvester  import run_harvester
from .terminator import check_termination, complete_investigation
from .report_writer import run_report_tick
from ai_engine.core.license_manager import validate_license

_has_run_startup_recovery = False


def _get_or_create_searxng_source(pg_conn) -> int:
    """Ensures a 'SearXNG OSINT Ingest' source row exists and returns its ID."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
            VALUES ('SearXNG OSINT Ingest', %s, 'searxng.local', 'OSINT', 0.5)
            ON CONFLICT (url) DO NOTHING;
            """,
            (SEARXNG_URL or "https://searxng.local",)
        )
        cur.execute(
            "SELECT id FROM sources WHERE url = %s",
            (SEARXNG_URL or "https://searxng.local",)
        )
        row = cur.fetchone()
    pg_conn.commit()
    return row[0] if row else 1


def _inject_initial_queries(investigation_id: int, queries: list, pg_conn, searxng_source_id: int) -> None:
    """Executes the triage's initial queries immediately and injects URLs into raw_urls."""
    import requests
    def create_searxng_headers(secret: str = "") -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
            headers["X-API-KEY"] = secret
        return headers

    def build_searxng_params(query: str, time_range: str | None = None) -> dict:
        params = {"q": query, "format": "json", "engines": "google,bing"}
        if time_range:
            params["time_range"] = time_range
        return params

    def extract_searxng_html_links(html_text: str) -> list[str]:
        import re
        urls = []
        for match in re.finditer(r'<article[^>]*class=["\'][^"\']*result[^"\']*["\'][^>]*>(.*?)</article>', html_text, re.S | re.I):
            article_html = match.group(1)
            href_match = re.search(r'href=["\'](https?://[^"\']+)["\']', article_html, re.I)
            if href_match:
                urls.append(href_match.group(1))
        if not urls:
            urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', html_text, re.I)
        return urls

    for q in queries:
        try:
            resp = requests.get(
                f"{SEARXNG_URL.rstrip('/')}/search",
                params=build_searxng_params(q),
                headers=create_searxng_headers(os.getenv("SEARXNG_SECRET_KEY", "")),
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            results = []
            try:
                results = resp.json().get("results") or resp.json().get("data") or resp.json().get("hits") or []
            except Exception:
                results = []
            if isinstance(results, dict):
                results = results.get("results") or results.get("data") or []
            if not isinstance(results, list):
                results = []
            if not results and resp.headers.get("Content-Type", "").startswith("text/html"):
                fallback_urls = extract_searxng_html_links(resp.text)
                if fallback_urls:
                    results = [{"url": url} for url in fallback_urls]
            with pg_conn.cursor() as cur:
                for r in results:
                    url = r.get("url", "")
                    if not url:
                        continue
                    cur.execute(
                        """
                        INSERT INTO raw_urls (source_id, url, metadata, status)
                        VALUES (%s, %s, %s, 'PENDING_SCRAPE')
                        ON CONFLICT (url) DO NOTHING
                        """,
                        (
                            searxng_source_id, url,
                            Json({"investigation_id": investigation_id, "osint_query": q}),
                        )
                    )
            pg_conn.commit()
            time.sleep(0.5)
        except Exception as e:
            print(f"[Orchestrator] Initial query failed: '{q}': {e}")


def run_orchestrator_tick(neo4j_driver=None) -> None:
    """
    Single orchestrator tick. Called by worker.py every S11_INTERVAL seconds.
    Safe to call even if there are no active investigations (early return).
    """
    if not DATABASE_URL:
        print("[Orchestrator] No DATABASE_URL — skipping tick")
        return

    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = False
    except Exception as e:
        print(f"[Orchestrator] DB connection failed: {e}")
        return

    global _has_run_startup_recovery

    try:
        with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ── Server Crash Recovery: Reset stuck leads ─────────────────────
            if not _has_run_startup_recovery:
                cur.execute("UPDATE investigation_leads SET status = 'PENDING' WHERE status = 'CLAIMED'")
                recovered = cur.rowcount
                if recovered > 0:
                    print(f"[Orchestrator] Crash Recovery: Reset {recovered} stuck CLAIMED leads back to PENDING.")
                _has_run_startup_recovery = True
            
            cur.execute(
                """
                SELECT id, target, goal_type, findings, concurrent_agents
                FROM investigations
                WHERE status = 'ACTIVE'
                ORDER BY created_at ASC
                """
            )
            active_investigations = cur.fetchall()
        pg_conn.commit()

        if not active_investigations:
            return  # Nothing to do

        print(f"[Orchestrator] Tick: {len(active_investigations)} active investigation(s)")
        searxng_source_id = _get_or_create_searxng_source(pg_conn)

        for inv in active_investigations:
            inv_id   = inv["id"]
            target   = inv["target"]
            findings = inv["findings"] or {}
            max_agents = inv["concurrent_agents"] or 5

            print(f"[Orchestrator] Processing investigation #{inv_id}: '{target}'")

            # ── Step 1: Triage on first tick (no leads yet, no initial queries) ──
            if not findings.get("initial_queries"):
                print(f"[Orchestrator] Investigation #{inv_id}: Running triage...")
                try:
                    triage = triage_target(target, neo4j_driver=neo4j_driver)
                    persist_triage(inv_id, triage, pg_conn)
                    if triage.initial_queries and SEARXNG_URL:
                        _inject_initial_queries(inv_id, triage.initial_queries, pg_conn, searxng_source_id)
                except Exception as e:
                    print(f"[Orchestrator] Triage failed for #{inv_id}: {e}")
                continue  # Give the pipeline time to process before the first harvest

            # ── Step 2: Harvest — read pipeline output, score new leads ─────────
            try:
                harvest = run_harvester(
                    investigation_id     = inv_id,
                    investigation_target = target,
                    goal_type            = inv["goal_type"] or "PROFILING",
                    exhaust_predicate    = findings.get("exhaust_predicate"),
                    pg_conn              = pg_conn,
                    neo4j_driver         = neo4j_driver,
                )
                if harvest.get("goal_achieved"):
                    with pg_conn.cursor() as cur:
                        cur.execute(
                            "UPDATE investigations SET findings = findings || '{\"goal_achieved\": true}'::jsonb WHERE id = %s",
                            (inv_id,)
                        )
                    pg_conn.commit()
            except Exception as e:
                print(f"[Orchestrator] Harvester failed for #{inv_id}: {e}")

            # ── Step 2b: Incrementally update the living investigation report ─────
            try:
                report_conn = psycopg2.connect(DATABASE_URL)
                report_conn.autocommit = False
                run_report_tick(
                    investigation_id     = inv_id,
                    investigation_target = target,
                    pg_conn              = report_conn,
                    inv_meta             = dict(inv),
                )
                report_conn.close()
            except Exception as e:
                print(f"[Orchestrator] Report writer failed for #{inv_id} (non-fatal): {e}")

            # ── Step 3: Check termination before spinning up agents ───────────────
            should_stop, reason = check_termination(inv_id, pg_conn)
            if should_stop:
                complete_investigation(inv_id, reason, pg_conn, neo4j_driver=neo4j_driver)
                continue

            # ── Step 4: Fan-out — spawn parallel sub-agent threads ───────────────
            if not SEARXNG_URL:
                print(f"[Orchestrator] No SEARXNG_URL — cannot run lead agents for #{inv_id}")
                continue
                
            if not validate_license(pg_conn):
                print(f"[Orchestrator] License invalid or credits exhausted. Halting agent sweep for #{inv_id}.")
                continue

            threads = []
            _goal_type = inv["goal_type"] or "PROFILING"
            for _ in range(max_agents):
                # Each thread gets its own independent DB connection to avoid
                # cursor conflicts between concurrent FOR UPDATE SKIP LOCKED calls.
                # IMPORTANT: pass loop variables as default args to freeze their
                # values — Python closures capture by reference, not by value.
                def agent_thread(
                    _inv_id=inv_id,
                    _target=target,
                    _goal_type=_goal_type,
                    _src_id=searxng_source_id
                ):
                    try:
                        agent_conn = psycopg2.connect(DATABASE_URL)
                        agent_conn.autocommit = False
                        try:
                            run_lead_agent(
                                investigation_id     = _inv_id,
                                investigation_target = _target,
                                goal_type            = _goal_type,
                                searxng_url          = SEARXNG_URL,
                                pg_conn              = agent_conn,
                                searxng_source_id    = _src_id,
                            )
                        finally:
                            agent_conn.close()
                    except Exception as e:
                        print(f"[Orchestrator] Agent thread error: {e}")

                t = threading.Thread(target=agent_thread, daemon=True)
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)  # 2-min per-agent cap

            print(f"[Orchestrator] Investigation #{inv_id}: agent sweep complete")

            # Broadcast update to the frontend firehose and status listeners.
            findings = inv.get("findings") or {}
            with pg_conn.cursor() as cur:
                payload = json.dumps({
                    "id": inv_id,
                    "target": target,
                    "status": inv.get("status"),
                    "leads_explored": inv.get("leads_explored", 0),
                    "novel_discoveries": inv.get("novel_discoveries", 0),
                    "goal_achieved": findings.get("goal_achieved", False),
                    "last_summary": findings.get("last_harvest_summary"),
                    "timestamp": int(time.time())
                })
                cur.execute("NOTIFY investigation_update, %s", (payload,))
            pg_conn.commit()

    except Exception as e:
        print(f"[Orchestrator] Fatal tick error: {e}")
    finally:
        try:
            pg_conn.close()
        except Exception:
            pass
