# Silent Failure Technical Reference - Code Examples & Solutions

## PART 1: THE DEADLOCK MECHANISM EXPLAINED

### How the Deadlock Happens - Detailed Flow

```python
# THREAD 1: Main async event loop
async def process_batch():
    logger.info("📋 Fetching unprocessed articles...")
    # ↓ This acquires logging._lock (reentrant lock)
    # ↓ Calls StreamHandler.emit()
    # ↓ Writes to stderr

    cur.execute(query, (BATCH_SIZE,))  # Blocking call!
    # If this hangs, the thread is blocked
    # Logging._lock is STILL HELD by the logging system

# THREAD 2 (actually same thread): Signal handler (SIGTERM received)
def signal_handler(signum, frame):
    # Process received SIGTERM
    # Now we try to call logging.shutdown()
    logging.shutdown()  # Tries to acquire logging._lock
    # BUT: logging._lock is held by the main thread
    # The main thread is blocked on cur.execute()
    # DEADLOCK: Both threads waiting on each other
    # Actually the same thread - signal handler context
    # Signal handlers run in the current thread's context
```

### Python's Logging Module Lock Architecture

```
logging._lock (RLock - Reentrant Lock)
├── Logger.handle() calls lock.acquire()
├── StreamHandler.emit() calls lock.acquire()
├── formatter.format() might try to acquire lock
└── logging.shutdown() tries to acquire lock
    └── For each handler: handler.close() which might try to acquire lock again

asyncio.run()
├── Creates event loop
├── Runs all async tasks
└── Signal handlers fire INSIDE the event loop context
    └── Signal fires while lock is held by event loop
    └── Signal handler tries to acquire same lock
    └── DEADLOCK
```

### Why Re-entrancy Doesn't Help

Python's `RLock` (reentrant lock) allows the **same thread** to acquire the lock multiple times. However:

```python
# This works:
def function():
    logging._lock.acquire()
    # Later, in same thread
    logging._lock.acquire()  # ✓ Allowed, same thread
    logging._lock.release()
    logging._lock.release()

# This DOESN'T work in signal handler context:
logging_lock_held_by_thread = True

def signal_handler(signum, frame):
    # Code in signal handler runs IN THE SAME THREAD
    # But it's an INTERRUPTION of the current execution
    # The lock might not be re-entrant if held by a different execution context
    logging.shutdown()  # ✗ Attempts to acquire lock
    # Lock is held by outer execution (the cur.execute() call)
    # Even though it's the same thread, the lock is in use
    # DEADLOCK RISK
```

### Specific Python Documentation Warning

From https://docs.python.org/3/library/logging.html:

> "Thread Safety: The logging module is intended to be thread-safe without any special work needing to be done by its clients. It achieves this through using threading locks; there is one lock to serialize access to the module's shared data, and each handler also creates a lock to serialize access to its underlying I/O.
>
> **If you are implementing asynchronous signal handlers using the signal module, you may not be able to use logging from within such handlers. This is because lock implementations in the threading module are not always re-entrant, and so cannot be invoked from such signal handlers.**"

---

## PART 2: RENDER'S ORCHESTRATOR BEHAVIOR

### How Render Terminates Containers

```bash
# Render's container shutdown sequence:

T+0s   Container receives SIGTERM signal
       └─ Grace period starts (default 5-10 seconds for most plans)

T+grace_period   Process still running
       └─ Render issues SIGKILL (-9) to forcefully kill process

T+gracekill+1s   Process is dead
       └─ Output buffer is gone
       └─ No more output can be captured
```

### Why Output Disappears on SIGKILL

```
Process Memory Layout:
┌─────────────────────────────────────┐
│ Running Code                        │ ← Executing cur.execute()
├─────────────────────────────────────┤
│ Stdout Buffer                       │ ← print() writes here
│ "Starting script"                   │   (if not flushed yet)
│ "Database connected"                │
│ ">>> DB_FETCH_START <<<"            │
│ ">>> DB_QUERY_PREP <<<"             │
├─────────────────────────────────────┤
│ Stderr Buffer                       │ ← logger.info() writes here
│ "2024-01-05 10:00:00 - INFO - ✅"  │   (if not flushed yet)
│ "2024-01-05 10:00:01 - INFO - 📋"  │
├─────────────────────────────────────┤
│ Heap/Stack                          │
│ Variables, objects, etc.            │
└─────────────────────────────────────┘

SIGKILL arrives:
  ↓
Kernel immediately terminates process
  ↓
Everything in memory = DELETED
  ↓
What was already written to the pipe
(to docker logs) = PRESERVED
  ↓
What was still in buffer = LOST FOREVER
```

### What Render's Logs Show

```
Captured: 538 chars of output
= Approximately 2-3 full log lines + partial init markers

Example captured output:
___SCRIPT_START___
___IMPORTING_MODULES___
___SYSPATH_UPDATED___
___SEMANTICLINKER_IMPORTED___
[INIT-1]
[INIT-2] env=...
[INIT-3-DB-START]
[INIT-3-DB-DONE]
...
✅ Database connection established
📋 Fetching unprocessed articles...  ← Last complete message
[UNWRITTEN BUFFER] ← Everything after this is in memory, lost on SIGKILL
```

---

## PART 3: SPECIFIC VULNERABILITIES IN YOUR CODE

### Vulnerability #1: Signal Handler + Logging Deadlock

```python
# CURRENT (BROKEN) CODE:
def signal_handler(signum, frame):
    msg = "\n[SIGNAL-HANDLER] Received signal - flushing and exiting gracefully\n"
    sys.stdout.write(msg)
    sys.stderr.write(msg)
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()  # ← DEADLOCK RISK
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Problem scenario:
# 1. Main thread executing: logger.info("...") inside process_batch()
#    → Logging lock is acquired
# 2. SIGTERM arrives
# 3. signal_handler() runs IN THE SAME THREAD
# 4. signal_handler() calls logging.shutdown()
#    → Tries to acquire logging lock
#    → But it's already held by step 1
#    → DEADLOCK

# SOLUTION: Never use logging in signal handlers
# Use only sys.write() which doesn't need locks

def signal_handler(signum, frame):
    msg = "\n[SIGNAL-HANDLER] Received signal - exiting\n"
    # Direct write, no logging module
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.stderr.flush()
    # Don't call logging.shutdown() in signal handler!
    sys.exit(0)
```

### Vulnerability #2: Executor Call Without Timeout

```python
# CURRENT (BROKEN) CODE:
result_json = await loop.run_in_executor(None, self.extract_facts_with_llm, full_text)

# Problem:
# - If self.extract_facts_with_llm() hangs, coroutine waits forever
# - No timeout specified
# - Event loop is blocked waiting for executor thread
# - When orchestrator timeout fires, process hasn't completed

# SOLUTION: Use asyncio.wait_for() for timeout

import asyncio

result_json = await asyncio.wait_for(
    loop.run_in_executor(None, self.extract_facts_with_llm, full_text),
    timeout=30.0  # 30 second timeout for LLM call
)

# Or even better, catch the timeout:
try:
    result_json = await asyncio.wait_for(
        loop.run_in_executor(None, self.extract_facts_with_llm, full_text),
        timeout=30.0
    )
except asyncio.TimeoutError:
    logger.error(f"LLM extraction timeout for {aid}")
    result_json = {"facts": []}  # Fallback
    continue
```

### Vulnerability #3: Database Query Without Timeout

```python
# CURRENT (BROKEN) CODE:
cur.execute(query, (BATCH_SIZE,))

# Problem:
# - PostgreSQL query can hang indefinitely
# - No statement_timeout set
# - If query hangs for > 5 minutes (Render timeout), process killed

# SOLUTION: Add statement_timeout to DATABASE_URL

# In render.yaml or environment:
DATABASE_URL: "postgresql://user:pass@host:5432/db?options=-c%20statement_timeout%3D60000"
# ↑ Note: URL encoding: -c statement_timeout=60000

# Or set it per connection:
conn = psycopg2.connect(
    self.database_url,
    connect_timeout=DB_CONNECT_TIMEOUT,
    options="-c statement_timeout=60000"  # 60 second query timeout
)

# Or set it per transaction:
cur.execute("SET statement_timeout = '60s'")
cur.execute(query, (BATCH_SIZE,))
```

### Vulnerability #4: Blocking I/O in Async Context

```python
# CURRENT (PROBLEMATIC) CODE:
def fetch_fresh_content(self, url):
    """Fetches fresh HTML and extracts text using Trafilatura."""
    try:
        logger.info(f"   📥 Fetching {url[:50]}...")
        # This is a BLOCKING synchronous call
        downloaded = trafilatura.fetch_url(url)
        # ^ Can hang for minutes if network is slow
        if not downloaded:
            logger.warning(f"   ⚠️  No content downloaded from {url}")
            return None
        text = trafilatura.extract(...)
        return text
    except Exception as e:
        logger.warning(f"   ❌ Trafilatura fetch failed for {url}: {e}")
        return None

# Then called from async context:
for aid, url, title in rows:
    full_text = self.fetch_fresh_content(url)  # ← Blocks event loop!

# SOLUTION: Wrap in executor with timeout
async def fetch_fresh_content_async(self, url):
    """Async wrapper for fetch_fresh_content"""
    loop = asyncio.get_event_loop()
    try:
        # Run blocking call in executor with timeout
        downloaded = await asyncio.wait_for(
            loop.run_in_executor(None, trafilatura.fetch_url, url),
            timeout=10.0  # 10 second timeout
        )
        if not downloaded:
            return None

        # Extract is also blocking, wrap it too
        text = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                trafilatura.extract,
                downloaded,
                False,  # include_tables
                False   # include_comments
            ),
            timeout=5.0
        )
        return text
    except asyncio.TimeoutError:
        logger.warning(f"   ⏱️  Timeout fetching {url}")
        return None
    except Exception as e:
        logger.warning(f"   ❌ Trafilatura fetch failed: {e}")
        return None
```

---

## PART 4: WORKING SOLUTIONS

### Solution 1: Safe Signal Handler

```python
import signal
import sys
import logging

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    """Safe signal handler - never uses logging module"""
    sig_name = signal.Signals(signum).name
    msg = f"\n[SIGNAL] Received {sig_name} - shutting down\n"

    # Use only sys.write(), NOT logging
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.stderr.flush()

    # Don't call logging.shutdown() - it causes deadlock
    # Just exit cleanly
    sys.exit(0)

# Register handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
```

### Solution 2: Timeout Wrapper Function

```python
import asyncio
from typing import Callable, Any, TypeVar

T = TypeVar('T')

async def run_with_timeout(
    func: Callable[..., T],
    timeout: float,
    *args,
    **kwargs
) -> T:
    """Run a callable with a timeout"""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func, *args),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error(f"Timeout executing {func.__name__} (timeout={timeout}s)")
        raise
    except Exception as e:
        logger.error(f"Error executing {func.__name__}: {e}")
        raise

# Usage:
try:
    result = await run_with_timeout(
        self.extract_facts_with_llm,
        timeout=30.0,
        full_text
    )
except asyncio.TimeoutError:
    result = {"facts": []}  # Fallback
```

### Solution 3: Database Connection with Timeouts

```python
import psycopg2
from urllib.parse import urlparse, parse_qs

def create_db_connection(database_url: str) -> psycopg2.extensions.connection:
    """Create a database connection with proper timeouts"""

    # Parse the URL
    parsed = urlparse(database_url)

    # Extract components
    user = parsed.username
    password = parsed.password
    host = parsed.hostname
    port = parsed.port or 5432
    database = parsed.path.lstrip('/')

    # Create connection with timeouts
    conn = psycopg2.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
        connect_timeout=10,  # Connection timeout
        options="-c statement_timeout=60000"  # Query timeout: 60 seconds
    )

    return conn
```

### Solution 4: Full Fixed Process Batch Function

```python
async def process_batch_fixed(self):
    """Fixed version with timeouts and proper error handling"""
    conn = None
    cur = None

    try:
        logger.info("🔄 Connecting to database...")
        conn = self.create_db_connection()  # Uses fixed function above
        cur = conn.cursor()
        logger.info("✅ Database connection established")

        # 1. Fetch articles with timeout
        logger.info("📋 Fetching unprocessed articles...")

        try:
            query = """
                SELECT id, url, title FROM articles
                WHERE processed_at IS NULL
                AND url IS NOT NULL
                LIMIT %s;
            """

            cur.execute(query, (BATCH_SIZE,))
            rows = cur.fetchall()
            logger.info(f"  Fetched {len(rows)} articles")

        except psycopg2.errors.QueryCanceled:
            logger.error("Database query timeout - fetch took too long")
            return
        except Exception as e:
            logger.error(f"Database fetch failed: {e}")
            raise

        if not rows:
            logger.info("✅ All articles processed")
            return

        # 2. Process each article with timeouts
        for aid, url, title in rows:
            safe_title = title if title else "Unknown"
            logger.info(f"Processing {aid}: {safe_title[:30]}...")

            # Fetch content with timeout
            try:
                full_text = await asyncio.wait_for(
                    self.fetch_fresh_content_async(url),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Content fetch timeout for {aid}")
                full_text = None
            except Exception as e:
                logger.warning(f"Content fetch error: {e}")
                full_text = None

            if not full_text:
                logger.warning(f"Skipping {aid}: No content")
                try:
                    cur.execute(
                        "UPDATE articles SET processed_at = NOW() WHERE id = %s",
                        (aid,)
                    )
                    conn.commit()
                except Exception as e:
                    logger.warning(f"Failed to mark processed: {e}")
                    conn.rollback()
                continue

            # Extract facts with timeout
            try:
                result_json = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        self.extract_facts_with_llm,
                        full_text
                    ),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                logger.error(f"LLM extraction timeout for {aid}")
                result_json = {"facts": []}
            except Exception as e:
                logger.error(f"LLM extraction error: {e}")
                result_json = {"facts": []}

            # Process and store facts...
            # (rest of processing code)

            # Mark as processed
            try:
                cur.execute(
                    "UPDATE articles SET processed_at = NOW() WHERE id = %s",
                    (aid,)
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"Failed to mark processed: {e}")
                conn.rollback()

    except Exception as e:
        logger.error(f"Batch processing failed: {e}", exc_info=True)
        raise
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass
```

---

## PART 5: VERIFICATION CHECKLIST

### Local Testing

- [ ] Run script locally - verify all logs appear
- [ ] Force SIGTERM locally - verify signal handler works
- [ ] Simulate slow DB - verify timeouts trigger
- [ ] Simulate slow LLM - verify executor timeout works

### Before Deploying to Render

- [ ] Remove `logging.shutdown()` from signal handler
- [ ] Add `asyncio.wait_for()` with timeout to all executor calls
- [ ] Add statement timeout to DATABASE_URL or connection
- [ ] Set connection timeout on psycopg2.connect()
- [ ] Wrap all network calls (trafilatura) with timeout
- [ ] Test graceful shutdown locally

### After Deploying to Render

- [ ] Check Render logs for "📋 Fetching" message
- [ ] Verify script completes or times out gracefully
- [ ] Check for timeout errors in logs
- [ ] Monitor execution time - should be consistent
- [ ] Verify no more silent failures

---

## PART 6: DEBUGGING COMMANDS

### View Render Logs

```bash
# Follow real-time logs
render logs --follow

# View specific time range
render logs --since "5 minutes ago"

# Check exit code
# Look for "Exit Code: X" in logs
```

### Test Locally with Timeouts

```python
# Test if fetch_url hangs
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Operation timed out")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(10)  # 10 second timeout

try:
    result = trafilatura.fetch_url(url)
    signal.alarm(0)  # Cancel alarm
except TimeoutError:
    print(f"Fetch timed out for {url}")
```

### Monitor Async Tasks

```python
import asyncio

async def debug_tasks():
    """Monitor all running tasks"""
    while True:
        tasks = [t for t in asyncio.all_tasks() if not t.done()]
        print(f"Running tasks: {len(tasks)}")
        for task in tasks:
            print(f"  - {task.get_name()}: {task._coro}")
        await asyncio.sleep(5)

# Run alongside main code
# asyncio.create_task(debug_tasks())
```

---

## SUMMARY OF CHANGES NEEDED

**Critical (do immediately):**

1. Remove `logging.shutdown()` from signal handler
2. Add `asyncio.wait_for(..., timeout=30)` to executor calls
3. Add `statement_timeout=60000` to database connection

**Important (do soon):** 4. Wrap trafilatura.fetch_url() with timeout 5. Add connection timeout to psycopg2.connect() 6. Test graceful shutdown handling

**Optional (improvements):** 7. Use asyncpg instead of psycopg2 for async DB access 8. Add explicit timeout handling at event loop level 9. Implement structured logging with proper async support
