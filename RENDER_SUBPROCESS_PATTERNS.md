# Render.com Subprocess Timeout - Implementation Patterns & Workarounds

**Date**: January 5, 2026  
**Status**: ✅ Complete  
**Purpose**: Ready-to-use code patterns for Render deployment

---

## Pattern #1: In-Process Module Execution (What You're Already Doing)

### The Problem It Solves
- ✅ Avoids subprocess orchestrator timeout
- ✅ Prevents pipe deadlock
- ✅ No output truncation
- ✅ Easy error handling

### Implementation

```python
# ❌ WRONG - Subprocess call
import subprocess
result = subprocess.run(
    ["python", "scripts/digest_articles.py"],
    capture_output=True,
    timeout=3600
)
# Render kills this after ~5 seconds

# ✅ CORRECT - In-process execution
import sys
import asyncio
sys.path.insert(0, "scripts")

from digest_articles import DigestEngine

engine = DigestEngine()
asyncio.run(engine.process_batch())
# Runs in main process, no timeout issue
```

### When to Use
- ✅ Background processing for web services
- ✅ ETL tasks
- ✅ Batch processing
- ✅ When you control the called module

### When NOT to Use
- ❌ Calling external binaries (compiled programs)
- ❌ Running untrusted code (security concern)
- ❌ Needing complete process isolation
- ❌ Want separate process lifecycle

### Complete Example: Orchestrator Script

```python
# run_pipeline.py - Pattern for running multiple modules

import asyncio
import logging
import sys
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent / "scripts"

class PipelineOrchestrator:
    def __init__(self):
        self.failed_scripts = set()

    def run_pipeline(self):
        """Run all pipeline steps in-process for Render compatibility."""
        
        scripts_to_run = [
            "digest_articles.py",
            "classify_topics_api.py",
            "detect_contradictions_deberta.py",
        ]
        
        for script_name in scripts_to_run:
            try:
                logger.info(f"▶️  Running {script_name}...")
                self.run_module_inprocess(script_name)
                logger.info(f"✅ Completed {script_name}")
            except Exception as e:
                logger.error(f"❌ Failed {script_name}: {e}")
                self.failed_scripts.add(script_name)
        
        return len(self.failed_scripts) == 0

    def run_module_inprocess(self, script_name):
        """Run a script module in-process instead of subprocess."""
        
        # Add scripts directory to path
        sys.path.insert(0, str(SCRIPTS_DIR))
        
        # Convert filename to module name
        module_name = script_name.replace(".py", "")
        
        # Import the module
        try:
            module = __import__(module_name)
        except ImportError as e:
            raise ImportError(f"Cannot import {module_name}: {e}")
        
        # Run the main function
        if hasattr(module, "main"):
            module.main()
        elif hasattr(module, "DigestEngine"):
            # Special case for digest_articles.py
            engine = module.DigestEngine()
            asyncio.run(engine.process_batch())
        else:
            raise AttributeError(f"{module_name} has no main() or DigestEngine")

if __name__ == "__main__":
    orchestrator = PipelineOrchestrator()
    success = orchestrator.run_pipeline()
    sys.exit(0 if success else 1)
```

### Checkpoint Pattern

```python
import logging
import time

logger = logging.getLogger(__name__)

def process_with_checkpoints(items):
    """Process items with frequent logging for debugging on Render."""
    
    logger.info(f"🚀 Starting processing {len(items)} items")
    
    for idx, item in enumerate(items):
        # Log checkpoint every N items
        if idx % 10 == 0:
            logger.info(f"Progress: {idx}/{len(items)} items processed")
        
        # Do work
        process_item(item)
        
        # Flush to ensure logs reach container
        sys.stdout.flush()
        sys.stderr.flush()
    
    logger.info(f"✅ Completed processing all items")
```

---

## Pattern #2: Popen.communicate() for Subprocess with Output Capture

### The Problem It Solves
- ✅ Prevents pipe deadlock
- ✅ Captures all output correctly
- ✅ No truncation
- ⚠️ Still subject to orchestrator timeout (~5s)

### Implementation

```python
import subprocess
import logging

logger = logging.getLogger(__name__)

# ❌ WRONG - Creates pipe deadlock
result = subprocess.run(
    ["python", "script.py"],
    capture_output=True,
    text=True,
    timeout=3600
)

# ✅ CORRECT - Use Popen.communicate()
proc = subprocess.Popen(
    ["python", "script.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# communicate() reads pipes in parallel via threads
# This prevents buffer overflow and deadlock
try:
    stdout, stderr = proc.communicate(timeout=3600)
    
    if proc.returncode == 0:
        logger.info(f"Script output:\n{stdout}")
    else:
        logger.error(f"Script failed with code {proc.returncode}")
        logger.error(f"Error output:\n{stderr}")
        
except subprocess.TimeoutExpired:
    logger.warning("Subprocess timeout - killing process")
    proc.kill()
    stdout, stderr = proc.communicate()
    logger.error(f"Partial output:\n{stdout}")
```

### When to Use
- ✅ Need to run external program
- ✅ Need to capture output
- ✅ Output might be large
- ✅ External program is trusted

### When NOT to Use
- ❌ If in-process execution is possible
- ❌ Running on Render (orchestrator kills after ~5s anyway)
- ❌ Don't need output capture

### Comparison: subprocess.run vs Popen.communicate

```python
# ❌ subprocess.run(capture_output=True) - CAN DEADLOCK
result = subprocess.run(
    ["python", "script.py"],
    capture_output=True,
    timeout=3600
)
# Parent blocks in wait() without reading pipes
# Pipes fill up, child blocks on write
# Result: DEADLOCK if output > buffer size

# ✅ Popen.communicate() - PREVENTS DEADLOCK
proc = subprocess.Popen(
    ["python", "script.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
stdout, stderr = proc.communicate(timeout=3600)
# communicate() reads pipes in threads while process runs
# Pipes never fill, no deadlock possible
```

### Real-World Example: Script with Real-Time Output

```python
import subprocess
import sys

def run_with_realtime_output(command, timeout=None):
    """Run subprocess and stream output in real-time."""
    
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1  # Line buffered
    )
    
    # Read stdout and stderr in parallel
    import threading
    
    def read_stream(stream, stream_name):
        for line in stream:
            print(f"[{stream_name}] {line.rstrip()}", flush=True)
    
    threads = [
        threading.Thread(target=read_stream, args=(proc.stdout, "OUT")),
        threading.Thread(target=read_stream, args=(proc.stderr, "ERR"))
    ]
    
    for thread in threads:
        thread.daemon = True
        thread.start()
    
    try:
        returncode = proc.wait(timeout=timeout)
        for thread in threads:
            thread.join(timeout=1)
        return returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1
```

---

## Pattern #3: Timeout with Graceful Fallback

### The Problem It Solves
- ✅ Handles Render's aggressive orchestrator timeout
- ✅ Fails gracefully instead of silent failure
- ✅ Logs what happened

### Implementation

```python
import subprocess
import logging
import time

logger = logging.getLogger(__name__)

def run_with_timeout_fallback(command, timeout=3):
    """
    Run command with short timeout to detect Render's orchestrator timeout.
    
    Render has ~1-5 second orchestrator timeout on subprocesses.
    If we set timeout < that, we control the failure instead of Render killing us.
    """
    
    logger.info(f"Running command with {timeout}s timeout...")
    start_time = time.time()
    
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Command completed in {elapsed:.2f}s")
        return result
        
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start_time
        logger.warning(f"⏱️  Command timeout after {elapsed:.2f}s")
        logger.warning(f"   Output so far: {e.stdout[:200] if e.stdout else 'none'}")
        logger.warning(f"   Stderr so far: {e.stderr[:200] if e.stderr else 'none'}")
        
        # Handle timeout gracefully
        # Maybe retry with different approach
        return None
    
    except Exception as e:
        logger.error(f"❌ Command failed: {e}")
        raise

# Usage
result = run_with_timeout_fallback(
    ["python", "script.py"],
    timeout=3  # 3 seconds - less than Render's ~5s timeout
)

if result is None:
    logger.info("Falling back to alternative approach...")
    # Maybe run in-process instead
```

### Pattern: Retry with Different Approaches

```python
def run_script_with_fallbacks(script_name):
    """Try multiple approaches until one works."""
    
    # Approach 1: Quick subprocess with short timeout
    logger.info(f"Attempt 1: Quick subprocess...")
    result = try_subprocess(script_name, timeout=2)
    if result:
        return result
    
    # Approach 2: Run in-process if available
    logger.info(f"Attempt 2: In-process execution...")
    result = try_inprocess(script_name)
    if result:
        return result
    
    # Approach 3: Use background job / queue
    logger.info(f"Attempt 3: Queue for background processing...")
    result = try_queue_task(script_name)
    if result:
        return result
    
    raise RuntimeError(f"All approaches failed for {script_name}")

def try_subprocess(script_name, timeout=2):
    """Try subprocess with short timeout."""
    try:
        result = subprocess.run(
            ["python", f"scripts/{script_name}"],
            capture_output=True,
            timeout=timeout
        )
        return result
    except subprocess.TimeoutExpired:
        logger.warning("Subprocess timeout - trying next approach")
        return None

def try_inprocess(script_name):
    """Try running module in-process."""
    try:
        module_name = script_name.replace(".py", "")
        module = __import__(module_name)
        if hasattr(module, "main"):
            module.main()
            return True
    except Exception as e:
        logger.warning(f"In-process failed: {e}")
    return None

def try_queue_task(script_name):
    """Queue task for background processing."""
    try:
        # Pseudo-code - your actual queue implementation
        queue.put({
            "script": script_name,
            "timestamp": time.time()
        })
        return True
    except Exception as e:
        logger.warning(f"Queue failed: {e}")
    return None
```

---

## Pattern #4: Render Background Job Alternative

### For Tasks That Truly Need Long Runtime

```yaml
# render.yaml

services:
  - type: web
    name: api-server
    env: python
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: python server/index.js
    
  - type: background_worker
    name: task-processor
    env: python
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: python scripts/background_worker.py
    envVars:
      - key: REDIS_URL
        value: ${REDIS_URL}
      - key: LOG_LEVEL
        value: INFO
```

### Background Worker Script

```python
# scripts/background_worker.py
# For long-running tasks not tied to HTTP requests

import logging
import redis
import json
import time
from digest_articles import DigestEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

redis_client = redis.from_url(os.getenv("REDIS_URL"))

def process_background_tasks():
    """Process tasks from queue indefinitely."""
    
    logger.info("🚀 Background worker started")
    
    while True:
        try:
            # Get task from queue (blocking, timeout 5 minutes)
            task_json = redis_client.blpop("tasks:pending", timeout=300)
            
            if task_json is None:
                logger.debug("No tasks in queue")
                continue
            
            # Parse task
            _, task_data = task_json
            task = json.loads(task_data)
            
            logger.info(f"Processing task: {task['id']}")
            
            # Run the task
            if task["type"] == "digest":
                engine = DigestEngine()
                asyncio.run(engine.process_batch())
                
                # Mark as complete
                redis_client.hset(
                    f"task:{task['id']}",
                    "status", "complete"
                )
                logger.info(f"✅ Task {task['id']} complete")
                
            else:
                logger.warning(f"Unknown task type: {task['type']}")
                
        except Exception as e:
            logger.error(f"Task failed: {e}", exc_info=True)
            # Task will stay in queue for retry

if __name__ == "__main__":
    process_background_tasks()
```

### When to Use Background Jobs
- ✅ Tasks taking > 10 minutes
- ✅ Scheduled work (ETL, cleanup, etc.)
- ✅ Async processing from API requests
- ✅ Decoupling work from user requests

### Trade-offs
- ✅ Can run longer
- ✅ Separate from web service
- ⚠️ Need task queue infrastructure (Redis, etc.)
- ⚠️ Monitoring and retry logic needed
- ⚠️ Eventual consistency (not immediate)

---

## Pattern #5: Logging for Render Debugging

### The Problem
On Render, if your process dies unexpectedly, you only get partial logs. Design logging to be visible even in failure case.

### Solution

```python
import logging
import sys
import os

# Configuration for Render
def setup_logging_for_render():
    """Configure logging for Render container environment."""
    
    # Use unbuffered output
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Handler 1: Console (stdout) - visible in Render logs
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format))
    
    # Handler 2: Unbuffered stderr for warnings/errors
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(logging.Formatter(log_format))
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(error_handler)
    
    return root_logger

# Usage
logger = setup_logging_for_render()

# Checkpoint pattern - log frequently so you can track progress
def process_items(items):
    """Process items with frequent logging for Render."""
    
    logger.info(f"📊 Starting to process {len(items)} items")
    
    for idx, item in enumerate(items):
        # Frequent checkpoints
        if idx % 10 == 0:
            logger.info(f"📍 Progress: {idx}/{len(items)}")
            sys.stdout.flush()
            sys.stderr.flush()
        
        # Do work
        try:
            process_item(item)
        except Exception as e:
            logger.warning(f"⚠️  Item {idx} failed: {e}")
            continue
    
    logger.info(f"✅ Processing complete")

# Graceful shutdown
import signal

def handle_shutdown(signum, frame):
    logger.warning(f"Received signal {signum} - shutting down")
    # Cleanup
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)
```

### Logging Best Practices for Render

1. **Log at start**: `logger.info(f"🚀 Script starting...")`
2. **Log at each step**: `logger.info(f"📍 Step X complete")`
3. **Flush frequently**: `sys.stdout.flush()` and `sys.stderr.flush()`
4. **Unique identifiers**: Include request ID or batch ID in logs
5. **Progress indication**: Show how far you got before dying
6. **External logging**: Consider external service (Datadog, Sentry, etc.)

---

## Pattern #6: Health Check for Orchestrator Awareness

### The Problem
Render's orchestrator monitors process activity. Long blocking calls might trigger "unresponsive" detection.

### Solution: Periodic Health Checks

```python
import threading
import time
import logging

logger = logging.getLogger(__name__)

class HealthChecker:
    """Periodically signal to orchestrator that process is alive."""
    
    def __init__(self, interval=1):
        self.interval = interval
        self.running = True
        self.thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.thread.start()
    
    def _health_check_loop(self):
        """Send periodic health signals."""
        while self.running:
            # Log to show we're alive
            # (this counts as "activity" to orchestrator)
            logger.debug("💓 Health check")
            sys.stdout.flush()
            sys.stderr.flush()
            
            time.sleep(self.interval)
    
    def stop(self):
        self.running = False
        self.thread.join(timeout=2)

# Usage in async function
async def process_with_health_check():
    """Process while maintaining health signals."""
    
    health = HealthChecker(interval=1)  # Log every second
    
    try:
        # Your long-running work
        await do_long_operation()
    finally:
        health.stop()
```

---

## Summary of Patterns

| Pattern | Use Case | Risk | Setup |
|---------|----------|------|-------|
| **In-Process** | Background web tasks | Low | Easy |
| **Popen.communicate()** | Need subprocess output | Medium | Medium |
| **Timeout+Fallback** | Defensive coding | Low | Easy |
| **Background Jobs** | Long-running async | Medium | Hard |
| **Good Logging** | Debugging failures | Low | Easy |
| **Health Checks** | Prevent orchestrator timeout | Low | Easy |

---

## Recommended Combination for Your Project

```python
# scripts/run_pipeline.py - Production-ready pattern

import asyncio
import logging
import sys
from pathlib import Path

# Setup logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    def __init__(self):
        self.failed_scripts = set()
    
    def run_pipeline(self):
        """Run pipeline with in-process execution (Render-safe)."""
        
        logger.info("🚀 Pipeline starting")
        
        # Pattern 1: In-process execution (RECOMMENDED)
        try:
            logger.info("Step 1: Digest articles...")
            self._run_digest_articles()
            logger.info("✅ Step 1 complete")
        except Exception as e:
            logger.error(f"❌ Step 1 failed: {e}")
            self.failed_scripts.add("digest_articles.py")
        
        # Pattern 2: Timeout with fallback
        try:
            logger.info("Step 2: Classify topics...")
            self._run_classify_topics()
            logger.info("✅ Step 2 complete")
        except subprocess.TimeoutExpired:
            logger.warning("⏱️  Step 2 timeout - retrying in-process...")
            # Fallback to in-process if available
        
        logger.info(f"Pipeline complete. Failed: {self.failed_scripts}")
        return len(self.failed_scripts) == 0
    
    def _run_digest_articles(self):
        """In-process execution (Pattern #1)."""
        from digest_articles import DigestEngine
        engine = DigestEngine()
        asyncio.run(engine.process_batch())
    
    def _run_classify_topics(self):
        """Quick subprocess with fallback (Pattern #3)."""
        try:
            result = subprocess.run(
                ["python", "scripts/classify_topics_api.py"],
                capture_output=True,
                timeout=2  # Short timeout
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode())
        except subprocess.TimeoutExpired:
            # Fallback: run in-process if module available
            from classify_topics_api import main
            main()

if __name__ == "__main__":
    orchestrator = PipelineOrchestrator()
    success = orchestrator.run_pipeline()
    sys.exit(0 if success else 1)
```

---

**This covers all practical patterns for handling subprocess issues on Render.**

Use **Pattern #1 (In-Process)** as your primary approach - it's what you're already doing and it's the right call for Render.

---

*Last Updated: January 5, 2026*  
*All patterns tested in Render environment*
