import subprocess
import time
import logging
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [ORCHESTRATOR] - %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Add parent directory to path for db_utils
sys.path.insert(0, SCRIPTS_DIR)

# 10 Executable Stages based on critical dependency path
PIPELINE_STAGES = [
    {
        "name": "1. Ingestion (Parallel)",
        "scripts": ["ingest_rss.py", "ingest_gdelt.py"],
        "frequency": 1800, # Run every 30 mins
        "parallel": True
    },
    {
        "name": "2. Hydration (Scraping)",
        "scripts": ["scrape_content_pro.py"],
        "frequency": 1800, # Run after ingestion
        "parallel": False
    },
    {
        "name": "3. Classification",
        "scripts": ["classify_topics_api.py"],
        "frequency": 1800, # Run after hydration
        "parallel": False
    },
    {
        "name": "4. Metadata & Trust",
        "scripts": ["add_trust_scoring.py"],
        "frequency": 3600, # Hourly updates
        "parallel": False
    },
    {
        "name": "5. Extraction & Deduplication",
        "scripts": ["digest_articles.py"],
        "frequency": 300, # Run very frequently (5 mins) to clear queue
        "parallel": False
    },
    {
        "name": "6. Verification (Provenance)",
        "scripts": ["hunt_provenance.py"],
        "frequency": 600, # Run frequently (10 mins)
        "parallel": False
    },
    {
        "name": "7. Publication (Graph Sync)",
        "scripts": ["sync_truth_graph.py"],
        "frequency": 3600, # Sync hourly
        "parallel": False
    },
    {
        "name": "8. QA (Contradiction Detection - Unified)",
        "scripts": ["detect_contradictions_unified.py"],
        "frequency": 21600, # Every 6 hours (API Cost optimization)
        "parallel": False,
        "description": "Detects contradictions using DeBERTa-MNLI and syncs to Neo4j"
    },
    {
        "name": "8.5. Backfill (Historical Contradictions - ONCE ON FIRST RUN)",
        "scripts": ["detect_contradictions_unified.py"],
        "frequency": 0, # Run only once on initialization (frequency=0 = disabled)
        "parallel": False,
        "description": "Backfill script to process ALL historical facts. Run via: python detect_contradictions_unified.py --backfill"
    },
    # Note: link_facts.py removed - embeddings are synced via sync_truth_graph.py
    # Note: detect_contradictions.py (old Neo4j-based) removed - replaced by detect_contradictions_unified.py
    # Note: detect_evolution.py removed - optional feature for timeline tracking, not critical for core pipeline
    {
        "name": "10. Maintenance (Archival)",
        "scripts": ["archive_old_articles.py"],
        "frequency": 86400, # Daily
        "parallel": False
    }
]

class PipelineOrchestrator:
    def __init__(self):
        self.running = True
        # Initialize last_run with current time, so stages don't all run immediately on startup
        current_time = time.time()
        self.last_run = {stage["name"]: current_time for stage in PIPELINE_STAGES}
        self.failed_scripts = set()  # Track scripts that have failed

    def validate_script(self, script_name):
        """Validate that a script exists and is executable."""
        script_path = os.path.join(SCRIPTS_DIR, script_name)
        
        # 1. Check if script exists
        if not os.path.exists(script_path):
            logger.error(f"❌ Script not found: {script_name}")
            return False
        
        # 2. Check if script is readable/executable
        if not os.access(script_path, os.R_OK):
            logger.error(f"❌ Script not readable: {script_name}")
            return False
        
        return True

    def validate_environment(self):
        """Validate that required environment variables are set."""
        required_vars = ['DATABASE_URL', 'NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD']
        missing = []
        
        for var in required_vars:
            value = os.getenv(var)
            if not value:
                missing.append(var)
                logger.error(f"❌ {var} not found in environment")
            else:
                logger.info(f"✅ {var} is set")
        
        if missing:
            logger.warning(f"⚠️  Missing environment variables: {', '.join(missing)}")
            logger.warning("   Some scripts may fail. Continuing with available variables...")
            return False
        
        return True

    def validate_database_connectivity(self):
        """Validate database connectivity."""
        try:
            from db_utils import get_pg_connection, get_neo4j_driver, release_pg_connection, close_neo4j_driver
            
            # Test PostgreSQL
            try:
                pg_conn = get_pg_connection()
                pg_cur = pg_conn.cursor()
                pg_cur.execute("SELECT 1")
                pg_cur.close()
                release_pg_connection(pg_conn)
                logger.debug("✅ PostgreSQL connection validated")
            except Exception as e:
                logger.warning(f"⚠️  PostgreSQL connection failed: {e}")
                return False
            
            # Test Neo4j
            try:
                neo4j_driver = get_neo4j_driver()
                neo4j_driver.verify_connectivity()
                logger.debug("✅ Neo4j connection validated")
                return True
            except Exception as e:
                logger.warning(f"⚠️  Neo4j connection failed: {e}")
                return False
        except ImportError:
            logger.warning("⚠️  db_utils not available, skipping connectivity check")
            return True  # Don't fail if db_utils unavailable

    def run_script(self, script_name, retry_count=0, max_retries=1):
        """Run a script with validation and error recovery."""
        # Validate script exists
        if not self.validate_script(script_name):
            self.failed_scripts.add(script_name)
            return False
        
        script_path = os.path.join(SCRIPTS_DIR, script_name)
        logger.info(f"▶️  Running: {script_name}...")
        
        try:
            # Run script and capture output
            # CRITICAL: Pass parent process environment variables to subprocess
            # Without this, env vars set on Render won't be available to child scripts
            result = subprocess.run(
                ["python", script_path],
                check=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=3600,  # 1 hour timeout
                env=os.environ.copy()  # Pass environment variables to subprocess
            )
            
            # Log only snippets to avoid spamming main log
            if result.stdout:
                logger.info(f"✅ Finished: {script_name} (Output len: {len(result.stdout)})")
            else:
                logger.info(f"✅ Finished: {script_name}")
            
            # Remove from failed list if it succeeds
            self.failed_scripts.discard(script_name)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error(f"❌ Timeout: {script_name} exceeded 1 hour execution time")
            self.failed_scripts.add(script_name)
            return False
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr[:500] if e.stderr else str(e)
            logger.error(f"❌ Failed: {script_name}\nError: {error_msg}...")
            
            # Retry logic for transient failures
            if retry_count < max_retries:
                logger.info(f"🔄 Retrying {script_name} (attempt {retry_count + 1}/{max_retries + 1})...")
                time.sleep(5)  # Wait 5 seconds before retry
                return self.run_script(script_name, retry_count + 1, max_retries)
            else:
                self.failed_scripts.add(script_name)
                return False
        except Exception as e:
            logger.error(f"❌ Unexpected error running {script_name}: {e}")
            self.failed_scripts.add(script_name)
            return False

    def run_stage(self, stage):
        logger.info(f"🚀 Triggering Stage: {stage['name']}")
        
        if stage.get("parallel", False) and len(stage["scripts"]) > 1:
            # Run parallel using ThreadPool
            with ThreadPoolExecutor(max_workers=len(stage["scripts"])) as executor:
                futures = list(executor.map(self.run_script, stage["scripts"]))
                # Wait for all to complete
        else:
            # Run sequential
            for script in stage["scripts"]:
                self.run_script(script)

    def start(self):
        logger.info("🤖 Truth Engine Orchestrator Online")
        logger.info(f"📅 Scheduled {len(PIPELINE_STAGES)} Stages.")
        
        # Validate environment on startup
        logger.info("🔍 Validating environment...")
        self.validate_environment()
        self.validate_database_connectivity()
        
        # Validate all scripts exist
        logger.info("🔍 Validating scripts...")
        all_scripts = []
        for stage in PIPELINE_STAGES:
            all_scripts.extend(stage.get("scripts", []))
        
        missing_scripts = []
        for script in all_scripts:
            if not self.validate_script(script):
                missing_scripts.append(script)
        
        if missing_scripts:
            logger.warning(f"⚠️  {len(missing_scripts)} script(s) not found. Pipeline may have limited functionality.")
        else:
            logger.info("✅ All scripts validated")
        
        logger.info("🚀 Pipeline Orchestrator ready. Monitoring stages...")
        
        while self.running:
            try:
                now = time.time()
                
                for stage in PIPELINE_STAGES:
                    # Skip stages with frequency=0 (disabled)
                    if stage["frequency"] == 0:
                        continue
                    
                    elapsed = now - self.last_run[stage["name"]]
                    
                    # Check if it's time to run this stage
                    if elapsed >= stage["frequency"]:
                        try:
                            self.run_stage(stage)
                            self.last_run[stage["name"]] = now
                        except Exception as stage_error:
                            logger.error(f"❌ Stage '{stage['name']}' encountered error: {stage_error}")
                            # Don't stop the orchestrator, just log and continue
                
                # Sleep to prevent tight loop (check every 30 seconds for better responsiveness)
                time.sleep(30)
                
            except KeyboardInterrupt:
                logger.info("🛑 Pipeline interrupted by user")
                self.running = False
                break
            except Exception as loop_error:
                logger.error(f"❌ Orchestrator loop error: {loop_error}")
                # Don't crash, just sleep and retry
                time.sleep(30)

    def stop(self, signum, frame):
        logger.info("🛑 Pipeline Stopping...")
        self.running = False
        # DO NOT call sys.exit() - this crashes the entire FastAPI application!
        # Just set running=False and let the main loop exit gracefully

if __name__ == "__main__":
    orchestrator = PipelineOrchestrator()
    signal.signal(signal.SIGINT, orchestrator.stop)
    signal.signal(signal.SIGTERM, orchestrator.stop)
    orchestrator.start()
