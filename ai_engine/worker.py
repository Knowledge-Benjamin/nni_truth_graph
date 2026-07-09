# pyre-ignore-all-errors
"""
AI Engine Pipeline Orchestrator — Smart Adaptive Dispatcher
Replaces the flat 60-second equal-dispatch loop with a pressure-aware
scheduler that queries Postgres every tick and decides per-stage
whether to BURST, DISPATCH, SLOW, or IDLE each pipeline stage.
"""

import os
import sys
import time
import signal
import psycopg2  # type: ignore
from celery.result import AsyncResult
from dotenv import load_dotenv  # type: ignore

# Force UTF-8 stdout so Unicode box-drawing characters don't crash on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

from celery_app import app  # type: ignore
from tasks import launch_pipeline_stage, run_tier3_ingestion  # type: ignore

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

# ─────────────────────────────────────────────────────────────────────────────
# TUNING CONSTANTS  (all times in ticks; 1 tick = TICK_INTERVAL seconds)
# ─────────────────────────────────────────────────────────────────────────────
TICK_INTERVAL      = 15  # seconds between scheduler cycles
IDLE_COOLDOWN      = 8     # ticks to wait after queue goes empty
LOW_THRESHOLD      = 10    # queue depth below which we go SLOW
HIGH_THRESHOLD     = 100   # queue depth above which we BURST
OVERFLOW_THRESHOLD = 300   # downstream pressure that triggers back-pressure on upstream
SLOW_COOLDOWN      = 3     # ticks between dispatches when pressure is LOW
BURST_EXTRA        = 2     # extra Celery tasks dispatched per tick when BURSTing

# Backlog-driven stages: dispatch when the underlying queue has pending work.
# The old .env interval settings are no longer used for the investigation loop.
S1_INTERVAL  = 1
S9_INTERVAL  = 1
S10_INTERVAL = 1
S11_INTERVAL = 1

# ─────────────────────────────────────────────────────────────────────────────
# QUEUE-DEPTH QUERIES — one per stage
# ─────────────────────────────────────────────────────────────────────────────
PRESSURE_QUERIES = {
    "active_investigations": """
        SELECT COUNT(*) FROM investigations WHERE status = 'ACTIVE'
    """,
    "2_scrape.py": """
        SELECT COUNT(*) FROM raw_urls WHERE status = 'PENDING_SCRAPE'
    """,
    "2a_video_scrape.py": """
        SELECT COUNT(*) FROM raw_urls WHERE status = 'PENDING_SCRAPE' AND domain IN ('youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com', 'vimeo.com', 'instagram.com')
    """,
    "3_classification.py": """
        SELECT COUNT(*) FROM raw_articles WHERE status = 'PENDING_CLASSIFICATION'
    """,
    "4_extraction.py": """
        SELECT COUNT(*) FROM raw_articles WHERE status = 'PENDING_EXTRACTION'
    """,
    "5_resolution.py": """
        SELECT COUNT(*) FROM extracted_claims
        WHERE pipeline_stage = 'STAGE_4_RESOLUTION' AND status = 'PROCESSING'
    """,
    "6_deduplication.py": """
        SELECT COUNT(*) FROM extracted_claims
        WHERE pipeline_stage = 'STAGE_6_DEDUP' AND status = 'PROCESSING'
    """,
    "7_cross_reference.py": """
        SELECT COUNT(*) FROM extracted_claims
        WHERE pipeline_stage = 'STAGE_7_CROSS_REF' AND status = 'PROCESSING'
    """,
    "8_graph_mutation.py": """
        SELECT COUNT(*) FROM extracted_claims
        WHERE pipeline_stage = 'STAGE_8_MUTATION_QUEUE'
          AND status IN ('AUTO_APPROVE', 'PROCESSING')
    """,
}

# Ordered pipeline stages for back-pressure traversal
PIPELINE_ORDER = [
    "1_ingest.py",
    "2_scrape.py",
    "2a_video_scrape.py",
    "3_classification.py",
    "4_extraction.py",
    "5_resolution.py",
    "6_deduplication.py",
    "7_cross_reference.py",
    "8_graph_mutation.py",
    "9_truth_evolution.py",
    "10_revalidation.py",
]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_pressure_map() -> dict:
    """Query Postgres for queue depths. Returns {script_name: int}."""
    pressures = {}
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        for script, query in PRESSURE_QUERIES.items():
            try:
                cur.execute(query)
                pressures[script] = cur.fetchone()[0]
            except Exception:
                pressures[script] = 0
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Monitor] DB connection error: {e}")
    return pressures


def pressure_bar(depth: int, width: int = 16) -> str:
    """ASCII pressure bar scaled to HIGH_THRESHOLD."""
    filled = min(width, int(depth / HIGH_THRESHOLD * width))
    return ("█" * filled).ljust(width)


def fmt_timer(ticks_remaining: int) -> str:
    total_secs = ticks_remaining * TICK_INTERVAL
    h, rem = divmod(total_secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    elif m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

class StageScheduler:
    """
    Tracks per-stage cooldown counters and decides dispatch behaviour
    based on live queue-depth pressure and downstream back-pressure.
    """

    STAGE_LABELS = {
        "1_ingest.py":         "S1  Ingest        ",
        "2_scrape.py":         "S2  Scrape        ",
        "2a_video_scrape.py":  "S2A VideoScrape   ",
        "3_classification.py": "S3  Classify      ",
        "4_extraction.py":     "S4  Extract       ",
        "5_resolution.py":     "S5  Resolution    ",
        "6_deduplication.py":  "S6  Dedup         ",
        "7_cross_reference.py":"S7  CrossRef      ",
        "8_graph_mutation.py": "S8  Mutation      ",
        "9_truth_evolution.py":"S9  Evolution     ",
        "10_revalidation.py":  "S10 Revalidation  ",
        "orchestrator":        "S11 Investigator  ",
    }

    def __init__(self):
        # Cooldown remaining (in ticks) per stage
        self._cooldown: dict[str, int] = {s: 0 for s in PIPELINE_ORDER}
        self._tick: int      = 0
        self._orchestrator_running = False
        # Stuck-stage detection: track last seen depth and dispatch count per stage
        self._last_depth: dict[str, int] = {s: -1 for s in PIPELINE_ORDER}  # depth at last dispatch (-1 as None)
        self._dispatch_since: dict[str, int] = {s: 0 for s in PIPELINE_ORDER}  # dispatches since last progress
        self._stuck_stages: set[str] = set()   # stages flagged as stuck
        # Refire guard: min ticks before re-dispatching the same stage
        # (prevents flooding the SQLite queue with redundant tasks)
        self._REFIRE_MIN = {
            "1_ingest.py":         S1_INTERVAL,
            "2_scrape.py":         2,   # Scraping is fast per-item, OK to re-dispatch often
            "2a_video_scrape.py":  4,   # yt-dlp + whisper transcription takes a bit longer
            "3_classification.py": 2,
            "4_extraction.py":     4,   # LLM — give it time
            "5_resolution.py":     4,   # Serper + HF — slow
            "6_deduplication.py":  2,
            "7_cross_reference.py":3,
            "8_graph_mutation.py": 2,   # Fast DB writes
            "9_truth_evolution.py":S9_INTERVAL,
            "10_revalidation.py":  S10_INTERVAL,
        }
        self._last_fired: dict[str, int] = {s: -999 for s in PIPELINE_ORDER}  # tick of last dispatch
        self._actions: dict[str, str] = {}
        self._active_tasks: dict[str, list[str]] = {s: [] for s in PIPELINE_ORDER}

    def _prune_completed_tasks(self) -> None:
        for script, task_ids in list(self._active_tasks.items()):
            if not task_ids:
                continue
            remaining = []
            for task_id in task_ids:
                try:
                    result = AsyncResult(task_id, app=app)
                    if result.state in {"SUCCESS", "FAILURE", "REVOKED", "RETRY"}:
                        continue
                except Exception:
                    continue
                remaining.append(task_id)
            self._active_tasks[script] = remaining

    def tick(self, pressures: dict) -> list[str]:
        """
        Evaluate stage eligibility for this tick.
        Returns list of script names to dispatch.
        """
        self._prune_completed_tasks()
        self._tick += 1
        to_dispatch = []
        self._actions = {}   # label → action string for dashboard

        # ── Downstream pressure lookup ───────────────────────────────────────
        def downstream_pressure(script):
            found_idx = PIPELINE_ORDER.index(script)
            # Use explicit index iteration to bypass Pyre list slicing inference bug
            for i in range(found_idx + 1, len(PIPELINE_ORDER)):
                nxt = PIPELINE_ORDER[i]
                if nxt in pressures:
                    return pressures[nxt]
            return 0

        # ── Stuck-stage detector ─────────────────────────────────────────────
        # A stage is STUCK when it has been dispatched ≥3 times since the last
        # time its queue depth decreased. This means Celery is consuming the
        # task but the script itself is failing silently or hung.
        STUCK_THRESHOLD = 3  # dispatches without progress before alerting

        def update_stuck(script, depth, did_dispatch):
            if did_dispatch:
                prev = self._last_depth[script]
                if prev != -1 and depth >= prev and depth > 0:
                    # Queue didn't shrink despite a dispatch — increment counter
                    self._dispatch_since[script] += 1
                else:
                    # Progress made — reset
                    self._dispatch_since[script] = 0
                    self._stuck_stages.discard(script)
                self._last_depth[script] = depth

                if self._dispatch_since[script] >= STUCK_THRESHOLD:
                    self._stuck_stages.add(script)
                else:
                    self._stuck_stages.discard(script)

        # ── Refire guard ─────────────────────────────────────────────────────
        def refire_ok(script):
            min_gap = self._REFIRE_MIN.get(script, 1)
            return (self._tick - self._last_fired[script]) >= min_gap

        def has_active(script):
            return bool(self._active_tasks.get(script, []))

        def do_dispatch(script):
            to_dispatch.append(script)
            self._last_fired[script] = self._tick

        # Hand-roll index iteration to bypass Pyre list slicing inference bug mapping loop vars to Object
        for i in range(1, 8):
            script = PIPELINE_ORDER[i]
            depth     = pressures.get(script, 0)
            down_pres = downstream_pressure(script)
            stuck_tag = " ⚠STUCK" if script in self._stuck_stages else ""

            # Determine base schedule
            if depth == 0:
                self._cooldown[script] = IDLE_COOLDOWN
                self._actions[script] = f"   0  {'':16s}  [IDLE]     "
                update_stuck(script, depth, False) # No dispatch, so no progress expected
                continue

            if depth < LOW_THRESHOLD:
                base_cd = SLOW_COOLDOWN
            elif depth < HIGH_THRESHOLD:
                base_cd = 1
            else:
                base_cd = 0

            # Back-pressure: throttle upstream if downstream is overflowing
            if down_pres > OVERFLOW_THRESHOLD:
                base_cd = max(base_cd * 2, 2)
                bp_tag = " ⚠ BP"
            else:
                bp_tag = ""

            if self._cooldown[script] > 0:
                self._cooldown[script] -= 1
                self._actions[script] = (
                    f"{depth:4d}  {pressure_bar(depth):16s}  "
                    f"[WAIT {self._cooldown[script]:2d}t]{bp_tag}{stuck_tag}"
                )
                update_stuck(script, depth, False) # No dispatch, so no progress expected
                continue

            if has_active(script):
                self._actions[script] = (
                    f"{depth:4d}  {pressure_bar(depth):16s}  [RUNNING]{bp_tag}{stuck_tag}"
                )
                continue

            # Refire guard: don't re-dispatch if we just fired recently
            if not refire_ok(script):
                ticks_left = self._REFIRE_MIN[script] - (self._tick - self._last_fired[script])
                self._actions[script] = (
                    f"{depth:4d}  {pressure_bar(depth):16s}  "
                    f"[REFIRE {fmt_timer(ticks_left)}]{stuck_tag}"
                )
                update_stuck(script, depth, False) # No dispatch, so no progress expected
                continue

            # Ready to fire
            self._cooldown[script] = base_cd
            if depth >= HIGH_THRESHOLD:
                # BURST: dispatch with extra tasks, but capped to avoid queue flooding
                burst_count = min(1 + BURST_EXTRA, 3)
                for _ in range(burst_count):
                    do_dispatch(script)
                action = f"[BURST x{burst_count}]{bp_tag}{stuck_tag}"
            else:
                do_dispatch(script)
                label  = "SLOW" if depth < LOW_THRESHOLD else "DISPATCH"
                action = f"[{label}]{bp_tag}{stuck_tag}"
            self._actions[script] = f"{depth:4d}  {pressure_bar(depth):16s}  {action}"
            update_stuck(script, depth, True)

        # ── Stage 1 — Ingest (dispatch when scrape backlog exists or there are active investigations) ─
        s2_pres = pressures.get("2_scrape.py", 0)
        active_inv = pressures.get("active_investigations", 0)
        if s2_pres <= OVERFLOW_THRESHOLD or active_inv > 0:
            to_dispatch.append("1_ingest.py")
            if active_inv > 0 and s2_pres > OVERFLOW_THRESHOLD:
                note = " (override for active investigation)"
            else:
                note = ""
            self._actions["1_ingest.py"] = f"  --  {'':16s}  [DISPATCH]{note} "
        else:
            self._actions["1_ingest.py"] = f"  --  {'':16s}  [IDLE]"

        # ── Stage 9 — Evolution (dispatch only when there is pending evolution work) ─
        if pressures.get("9_truth_evolution.py", 0) > 0:
            to_dispatch.append("9_truth_evolution.py")
            self._actions["9_truth_evolution.py"] = f"  --  {'':16s}  [DISPATCH] "
        else:
            self._actions["9_truth_evolution.py"] = f"  --  {'':16s}  [IDLE]"

        # ── Stage 10 — Revalidation (dispatch only when there is pending revalidation work) ─
        if pressures.get("10_revalidation.py", 0) > 0:
            to_dispatch.append("10_revalidation.py")
            self._actions["10_revalidation.py"] = f"  --  {'':16s}  [DISPATCH] "
        else:
            self._actions["10_revalidation.py"] = f"  --  {'':16s}  [IDLE]"

        # ── Stage 11 — OSINT Orchestrator (run whenever active investigations exist and no run is in flight) ─────────
        active_inv = pressures.get("active_investigations", 0)
        if active_inv > 0 and not self._orchestrator_running:
            to_dispatch.append("orchestrator")
            self._actions["orchestrator"] = f"  --  {'':16s}  [DISPATCH] "
        else:
            self._actions["orchestrator"] = (
                f"  --  {'':16s}  [IDLE]"
            )

        return to_dispatch

    def dashboard(self, pressures: dict) -> str:
        """Render a full-width ASCII pipeline dashboard."""
        W   = 64
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        lines = []
        lines.append("╔" + "═" * W + "╗")
        lines.append(f"║  TRUTH PIPELINE MONITOR  [{now}]  ║")
        lines.append("╠══ QUEUE DEPTHS " + "═" * (W - 15) + "╣")

        for script in PIPELINE_ORDER:
            label  = self.STAGE_LABELS.get(script, script)
            action = self._actions.get(script, "  --")
            lines.append(f"║  {label} → {action}  ║")

        # Back-pressure summary row
        lines.append("╠══ BACK-PRESSURE " + "═" * (W - 16) + "╣")
        bp_parts = []
        pairs = [
            ("1_ingest.py",        "2_scrape.py",        "S1→S2"),
            ("3_classification.py","4_extraction.py",    "S3→S4"),
            ("5_resolution.py",    "6_deduplication.py", "S5→S6"),
            ("7_cross_reference.py","8_graph_mutation.py","S7→S8"),
        ]
        for up, down, lbl in pairs:
            down_p = pressures.get(down, 0)
            state  = "OVERFLOW" if down_p > OVERFLOW_THRESHOLD else ("HIGH" if down_p > HIGH_THRESHOLD else "OK")
            bp_parts.append(f"{lbl}:{state}")
        bp_line = "  ".join(bp_parts)
        lines.append(f"║  {bp_line:<{W-2}}║")

        # Graph committed total
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM extracted_claims WHERE status='GRAPH_COMMITTED'")
            committed = cur.fetchone()[0]
            cur.close()
            conn.close()
        except Exception:
            committed = "?"
        lines.append("╠══ TOTALS " + "═" * (W - 9) + "╣")
        lines.append(f"║  Neo4j claims committed: {committed:<{W-25}}║")

        lines.append("╚" + "═" * W + "╝")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

# ── Lazy-import OSINT orchestrator (avoids import overhead when not needed) ──
def _run_osint_orchestrator(neo4j_driver=None):
    try:
        from orchestrator import run_orchestrator_tick  # type: ignore
        run_orchestrator_tick(neo4j_driver=neo4j_driver)
    except Exception as e:
        print(f"[Orchestrator] Error during tick: {e}")


def _has_active_investigations() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM investigations WHERE status = 'ACTIVE'")
        active_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return bool(active_count)
    except Exception as e:
        print(f"[Startup] Failed to check active investigations: {e}")
        return False


def _ensure_schema() -> None:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        print("[Startup] Running auto-migrations...")
        
        # Ensure investigation_leads has context column
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name='investigation_leads' AND column_name='context'
                ) THEN
                    ALTER TABLE investigation_leads ADD COLUMN context TEXT;
                    RAISE NOTICE 'Added context column to investigation_leads';
                END IF;
            END
            $$;
        """)
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Startup] Failed to run schema auto-migrations: {e}")

def main():
    print("=== AI Engine Pipeline Orchestrator (Smart Adaptive Dispatcher) ===")
    
    _ensure_schema()

    def signal_handler(sig, frame):
        print("\n[Orchestrator] Stopping. Note: kill Celery workers separately if needed.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    scheduler = StageScheduler()

    print(f"[+] Smart dispatcher online. Tick interval: {TICK_INTERVAL}s")
    print(f"[+] Thresholds: LOW={LOW_THRESHOLD} | HIGH={HIGH_THRESHOLD} | OVERFLOW={OVERFLOW_THRESHOLD}")
    print(f"[+] Press Ctrl+C to shut down.\n")

    # --- Run OSINT orchestrator once immediately to kick off investigations ---
    try:
        if _has_active_investigations():
            print("[Orchestrator] Active investigations detected on startup. Running immediate sweep...")
            _run_osint_orchestrator()
        else:
            print("[Orchestrator] No active investigations found at startup.")
    except Exception as e:
        print(f"[Orchestrator] Initial run failed: {e}")

    # Tier-3 timer (separate cadence — every 15 min)
    last_tier3: float = 0.0

    try:
        while True:
            tick_start = time.time()

            # 1. Measure queue depths
            pressures = get_pressure_map()

            # 2. Decide what to dispatch
            to_dispatch = scheduler.tick(pressures)

            # 3. Dispatch to Celery (skip orchestrator — runs in-process)
            dispatched = []
            
            # ALWAYS run the orchestrator FIRST if it needs to run, so it can inject investigation leads
            # before we spin up pipeline workers. This prevents workers from picking up background
            # tasks if an investigation just started.
            if "orchestrator" in to_dispatch:
                scheduler._orchestrator_running = True
                try:
                    _run_osint_orchestrator()
                finally:
                    scheduler._orchestrator_running = False
                dispatched.append("orchestrator")
                to_dispatch.remove("orchestrator")
                
            for script in to_dispatch:
                try:
                    task = launch_pipeline_stage.delay(script)
                    scheduler._active_tasks.setdefault(script, []).append(task.id)
                    dispatched.append(script)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"  [Dispatch ERROR] {script}: {e}")

            # 4. Tier-3 ingestion on its own 15-min timer
            now = time.time()
            if now - last_tier3 > 900:
                try:
                    run_tier3_ingestion.delay()
                    last_tier3 = now
                except Exception as e:
                    print(f"  [Tier3 Dispatch ERROR] {e}")

            # 5. Print dashboard
            dash = scheduler.dashboard(pressures)
            print(dash, flush=True)
            if dispatched:
                print(f"  → Dispatched {len(dispatched)} task(s): "
                      f"{', '.join(s.replace('.py','') for s in dispatched)}", flush=True)

            # 6. Sleep for remaining tick time
            elapsed = time.time() - tick_start
            sleep_for = max(0.0, float(TICK_INTERVAL) - elapsed)
            time.sleep(sleep_for)

    except Exception as e:
        print(f"[Orchestrator] Fatal error: {e}")
        signal_handler(None, None)


if __name__ == "__main__":
    main()
