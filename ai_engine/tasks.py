import os
import sys
import time
import importlib
from celery import shared_task
from dotenv import load_dotenv

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Explicit map of each pipeline script to its main entrypoint function name.
# This is authoritative — avoids fragile runtime discovery that silently skips stages.
PIPELINE_ENTRYPOINTS = {
    "1_ingest.py":         "ingest_urls",
    "2_scrape.py":         "process_scraping_queue",
    "3_classification.py": "process_classification_queue",
    "4_extraction.py":     "process_extraction_queue",
    "5_resolution.py":     "process_resolution_queue",
    "6_deduplication.py":  "process_dedup_queue",
    "7_cross_reference.py":"process_cross_ref_queue",
    "8_graph_mutation.py": "process_mutation_queue",
    "9_truth_evolution.py":"run_evolution_engine",
    "10_revalidation.py":  "run_revalidation_daemon",
}

@shared_task(bind=True)
def launch_pipeline_stage(self, script_name: str):
    print(f"[Celery Worker] Executing pipeline stage: {script_name}")
    
    func_name = PIPELINE_ENTRYPOINTS.get(script_name)
    if not func_name:
        msg = f"[Celery Worker] Unknown pipeline stage: {script_name}"
        print(msg)
        return msg

    module_name = script_name.replace('.py', '')
    module_path = f"ai_engine.pipeline.{module_name}"

    try:
        module = importlib.import_module(module_path)
        process_func = getattr(module, func_name, None)
        if not process_func:
            return f"Failed to execute {script_name}: function '{func_name}' not found in module"
        process_func()
        return f"Successfully executed {script_name} via {func_name}"
    except Exception as e:
        print(f"[Celery Worker] Error executing {script_name}: {e}")
        return f"Error executing {script_name}: {str(e)}"

@shared_task(bind=True)
def run_tier3_ingestion(self):
    print("[Celery Worker] Executing Tier 3 Ingestion (OpenAlex & GDELT)...")
    try:
        import sys
        import os
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
            
        from scripts.seed_tier3_authorities import fetch_openalex_abstracts, fetch_gdelt_events
        fetch_openalex_abstracts(limit=50)
        time.sleep(2)
        fetch_gdelt_events(limit=50)
        return "Tier 3 Ingestion completed."
    except Exception as e:
        print(f"[Celery Worker] Error executing Tier 3: {e}")
        return f"Error executing Tier 3: {str(e)}"
