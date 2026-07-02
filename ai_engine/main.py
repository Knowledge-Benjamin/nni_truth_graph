import subprocess
import sys
import time
import os
import threading
import io
import random

# Ensure `ai_engine` imports resolve when running from either repo root or ai_engine folder
try:
    from ai_engine.core.inference_pool import inference_pool  # type: ignore[import]
except ModuleNotFoundError:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from ai_engine.core.inference_pool import inference_pool  # type: ignore[import]

# Clear the terminal every N seconds to prevent stdout buffer buildup crashing the session
CLEAR_INTERVAL_SECONDS = 300  # 5 minutes

# Ping inference service periodically (±jitter) to keep it awake in host environments
HEALTH_PING_INTERVAL_SECONDS = 3600  # 1 hour
HEALTH_PING_JITTER_SECONDS = 300     # +/- 5 minutes

def bootstrap():
    """Install all pip dependencies and Playwright browsers before starting the engine."""
    wdir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(wdir, 'requirements.txt')

    print("=== [Bootstrap] Installing dependencies from requirements.txt ===")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet", "--no-warn-script-location"],
    )
    print("=== [Bootstrap] Installing Playwright browser binaries ===")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
    )
    print("=== [Bootstrap] Done. Starting AI Engine... ===\n")

def terminal_cleaner():
    """Background daemon thread: periodically clears the terminal stdout."""
    while True:
        time.sleep(CLEAR_INTERVAL_SECONDS)
        os.system('cls' if os.name == 'nt' else 'clear')
        print("=" * 60)
        print(f"  [AI Engine] Terminal cleared at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Celery + Pipeline Orchestrator are still running.")
        print(f"  Press Ctrl+C to shut down.")
        print("=" * 60)

def inference_health_pinger():
    """Background daemon thread: periodically ping inference server health endpoint."""
    while True:
        jitter = random.uniform(-HEALTH_PING_JITTER_SECONDS, HEALTH_PING_JITTER_SECONDS)
        sleep_time = max(60.0, HEALTH_PING_INTERVAL_SECONDS + jitter)
        print(f"[HealthPinger] sleeping {sleep_time:.1f}s before next ping")
        time.sleep(sleep_time)

        if inference_pool.health_check():
            print(f"[HealthPinger] inference server is healthy.")
        else:
            print(f"[HealthPinger] inference server health check failed.")


def stream_output(process, label):
    """Background daemon thread: reads a subprocess stream line-by-line and prints it."""
    try:
        for raw_line in iter(process.stdout.readline, b''):
            try:
                line = raw_line.decode('utf-8', errors='replace').rstrip()
            except Exception:
                line = repr(raw_line)
            print(f"[{label}] {line}", flush=True)
    except Exception as e:
        print(f"[{label}] Stream reader error: {e}")

def stream_stderr(process, label):
    """Background daemon thread: reads a subprocess stderr line-by-line and prints it."""
    try:
        for raw_line in iter(process.stderr.readline, b''):
            try:
                line = raw_line.decode('utf-8', errors='replace').rstrip()
            except Exception:
                line = repr(raw_line)
            print(f"[{label}|ERR] {line}", flush=True)
    except Exception as e:
        print(f"[{label}] Stderr reader error: {e}")

def main():
    print("=== Starting Truth Graph AI Engine ===")
    bootstrap()

    # Ensure we run in the ai_engine directory to find celery_app and worker.py
    wdir = os.path.dirname(os.path.abspath(__file__))

    # Start background terminal cleaner to prevent crash from log accumulation
    cleaner = threading.Thread(target=terminal_cleaner, daemon=True)
    cleaner.start()

    # Start the inference server keepalive health ping thread
    health_pinger = threading.Thread(target=inference_health_pinger, daemon=True)
    health_pinger.start()

    print(f"[+] Terminal will auto-clear every {CLEAR_INTERVAL_SECONDS // 60} minute(s).")
    print("[1/2] Starting Celery background worker...")

    # Use sys.executable to ensure we use the same Python environment that is executing main.py
    # stdout=PIPE + stderr=PIPE is CRITICAL — without it all Celery errors are invisible
    # On resource-constrained environments like Hugging Face Spaces, keep
    # Celery concurrency very low to avoid CPU/RAM exhaustion. We explicitly
    # set -c 2 here instead of Celery's default (CPU count).
    celery_process = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "celery_app", "worker", "-l", "info", "--pool=threads", "-c", "2"],
        cwd=wdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wire up live output streaming for the Celery worker
    threading.Thread(target=stream_output, args=(celery_process, "Celery"), daemon=True).start()
    threading.Thread(target=stream_stderr, args=(celery_process, "Celery"), daemon=True).start()

    # Give celery broker a few seconds to initialize
    time.sleep(5)

    print("[2/3] Starting Pipeline Orchestrator (worker.py)...")
    dispatcher_process = subprocess.Popen(
        [sys.executable, "worker.py"],
        cwd=wdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wire up live output streaming for the orchestrator
    threading.Thread(target=stream_output, args=(dispatcher_process, "Orchestrator"), daemon=True).start()
    threading.Thread(target=stream_stderr, args=(dispatcher_process, "Orchestrator"), daemon=True).start()

    # [TEMPORARILY DISABLED] Baseline Knowledge Seeder (seed_baseline_knowledge.py)
    # print("[3/3] Starting Baseline Knowledge Seeder (seed_baseline_knowledge.py)...")
    # print("      [Seeder] Will automatically exit when all datasets are 100% ingested.")
    # seeder_process = subprocess.Popen(
    #     [sys.executable, os.path.join(wdir, "scripts", "seed_baseline_knowledge.py")],
    #     cwd=wdir,
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    # )
    # # Wire up live output streaming for the seeder
    # threading.Thread(target=stream_output, args=(seeder_process, "Seeder"), daemon=True).start()
    # threading.Thread(target=stream_stderr, args=(seeder_process, "Seeder"), daemon=True).start()
    print("[4/5] Starting Universal Ontology Engine (ontology_worker.py)...")
    ontology_process = subprocess.Popen(
        [sys.executable, os.path.join(wdir, "scripts", "ontology_worker.py")],
        cwd=wdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wire up live output streaming for the ontology daemon
    threading.Thread(target=stream_output, args=(ontology_process, "Ontology"), daemon=True).start()
    threading.Thread(target=stream_stderr, args=(ontology_process, "Ontology"), daemon=True).start()

    print("[5/5] Starting Living Entity Article Engine (article_worker.py)...")
    article_process = subprocess.Popen(
        [sys.executable, os.path.join(wdir, "scripts", "article_worker.py")],
        cwd=wdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wire up live output streaming for the article daemon
    threading.Thread(target=stream_output, args=(article_process, "Article"), daemon=True).start()
    threading.Thread(target=stream_stderr, args=(article_process, "Article"), daemon=True).start()

    try:
        # Keep main.py alive and streaming child output
        celery_process.wait()
        dispatcher_process.wait()
        ontology_process.wait()
        article_process.wait()
        # seeder_process is allowed to finish on its own — do not .wait() on it
    except KeyboardInterrupt:
        print("\n[AI Engine] Received shutdown signal. Terminating processes...")
        dispatcher_process.terminate()
        celery_process.terminate()
        ontology_process.terminate()
        article_process.terminate()
        dispatcher_process.wait()
        celery_process.wait()
        ontology_process.wait()
        article_process.wait()
        print("[AI Engine] Successfully shut down.")

if __name__ == "__main__":
    main()
