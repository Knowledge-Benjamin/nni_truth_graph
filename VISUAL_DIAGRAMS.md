# Silent Failure - Visual Diagrams & Process Flows

---

## DIAGRAM 1: The Deadlock Mechanism

```
TIME →

LOCAL EXECUTION (WORKS):
═════════════════════════════════════════════════════════════════

T+0s    logger.info("📋 Fetching articles...")
        │
        ├─ Logging module acquires lock ─────────┐
        │                                         │
        └─ Writes to stderr                       ├─ Lock held briefly
                                                  │
        └─ Logging module releases lock ◄────────┘
        
        cur.execute(query)  ← Executes quickly on local DB
        
        Script continues normally...
        └─ Completes with exit(0)
        └─ All buffers flushed
        └─ All output appears ✓


RENDER EXECUTION (DEADLOCK):
═════════════════════════════════════════════════════════════════

T+0s    logger.info("📋 Fetching articles...")
        │
        ├─ Logging module acquires lock ─────────┐
        │                                         │
        └─ Queues message to stderr               ├─ Lock held
                                                  │
        cur.execute(query)  ← Slow network DB    │
        │                                         │
        └─ Thread blocks waiting for DB           ├─ Lock STILL held!
          └─ cur.execute() is synchronous         │
            └─ Event loop blocked                 │


T+5min  SIGTERM arrives (Render orchestrator timeout)
        │
        └─ signal_handler() fires
          │
          └─ In SAME THREAD context ─┐
            │                         │
            └─ Tries to acquire lock  │ DEADLOCK!
              │                       │
              └─ Already held by ◄────┘
                  cur.execute()
              
              → No progress
              → Process appears hung
              → Logging doesn't complete


T+5m+30s  SIGKILL arrives (Render patience exhausted)
          │
          └─ Process terminated immediately
            │
            ├─ All memory deleted
            ├─ Unflushed buffers lost
            ├─ Output buffer = gone
            └─ Exit code = failure

```

---

## DIAGRAM 2: Output Buffer Loss on SIGKILL

```
CONTAINER PROCESS MEMORY LAYOUT:
┌─────────────────────────────────────────────────────────┐
│                    PYTHON PROCESS                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Execution Stack                                        │
│  ├─ Running: asyncio event loop                        │
│  ├─ Waiting on: cur.execute() (blocked on network)    │
│  └─ Signal: SIGTERM received → signal_handler()       │
│     └─ Deadlock waiting for logging lock              │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Stdout Buffer (Python's internal)                     │
│  ├─ "___SCRIPT_START___"                              │
│  ├─ "___IMPORTING_MODULES___"                         │
│  ├─ "___SYSPATH_UPDATED___"                           │
│  ├─ "[INIT-1]"                                        │
│  ├─ "[INIT-2] env=246"                                │
│  └─ (MORE BUFFERED but not yet written to pipe)      │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Stderr Buffer (Logging module's buffer)              │
│  ├─ "2024-01-05 10:00:00 - INFO - ✅ Connected"       │
│  ├─ "2024-01-05 10:00:01 - INFO - 📋 Fetching..."    │
│  └─ (Partially formatted message, not yet written)   │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Heap / Stack / Globals                                │
│  ├─ Logger objects                                     │
│  ├─ Connection objects                                │
│  ├─ Thread locks                                       │
│  └─ Program variables                                 │
│                                                         │
└─────────────────────────────────────────────────────────┘
                          │
                          │ SIGKILL (-9) arrives
                          ↓
┌─────────────────────────────────────────────────────────┐
│  [PROCESS TERMINATED]                                  │
│  - All memory deleted                                   │
│  - All buffers cleared                                 │
│  - All state lost                                       │
└─────────────────────────────────────────────────────────┘

DOCKER LOG STREAM (from pipe):
═══════════════════════════════════════════════════════════

What got written to pipe BEFORE SIGKILL:
├─ "___SCRIPT_START___"
├─ "___IMPORTING_MODULES___"
├─ "___SYSPATH_UPDATED___"
├─ ... (more init lines)
└─ "✅ Database connection established"
   └─ "📋 Fetching unprocessed articles..."

Total: ~538 characters

What was in BUFFER but never written to pipe:
├─ ">>>DB_FETCH_START<<<"
├─ ">>>DB_QUERY_PREP<<<"
├─ ">>>DB_TRY_START<<<"
├─ ">>>DB_QUERY_EXECUTE<<<"
├─ "[DB-4] Fetched X articles"
└─ (Any print() statements after logger.info())

Status: LOST FOREVER ✗
```

---

## DIAGRAM 3: Timeline Comparison - Local vs Render

```
LOCAL EXECUTION
═════════════════════════════════════════════════════════════════

T+0.1s   Script starts
         └─ Init logs: "___SCRIPT_START___", etc.

T+1s     Database connects  
         └─ "✅ Database connection established"

T+2s     "📋 Fetching unprocessed articles..."
         └─ cur.execute() - FAST on local network

T+2.1s   "Fetched 5 articles"
         └─ Start processing

T+5s     "Processing article 1..."
         └─ Fetch content with trafilatura

T+7s     "Extracted 10 facts"
         └─ Store in database

T+8s     "Processing article 2..."
         └─ ... repeat ...

T+15s    "✅ Batch completed successfully"
         └─ exit(0)

T+15.1s  All buffers flushed
         Output: COMPLETE ✓


RENDER EXECUTION  
═════════════════════════════════════════════════════════════════

T+0.1s   Script starts
         └─ Init logs appear

T+1s     Database connects
         └─ "✅ Database connection established"

T+2s     "📋 Fetching unprocessed articles..."
         └─ cur.execute() - STARTS

T+2.1s   Database query in progress
         └─ (Network latency)

T+15s    Still executing query
         └─ (Slow network, maybe slow DB)

T+30s    Still executing query
         └─ (Patience wearing thin)

T+2m     Still executing query
         └─ (Definitely taking too long)

T+5m     Orchestrator timeout fires!
         └─ Sends SIGTERM to container

T+5m:00.1s   signal_handler() executes
             └─ Tries to acquire logging lock
             └─ Lock held by cur.execute() thread
             └─ DEADLOCK ✗

T+5m:30s    Still deadlocked
             └─ Process appears hung

T+5m:35s    Orchestrator patience exhausted
            └─ Sends SIGKILL (-9)

T+5m:35.1s  Process terminated
            └─ All buffers deleted
            └─ Output stream frozen at last write

T+5m:35.2s  Orchestrator logs:
            └─ Captured 538 chars
            └─ Exit code: failure (SIGKILL)
            └─ Last line: "📋 Fetching..."
            └─ NO ERROR MESSAGE ✗
            └─ NO TIMEOUT ERROR ✗
            └─ Just... silence...
```

---

## DIAGRAM 4: Code Flow With Deadlock Point

```
EXECUTION FLOW:

Main Thread:
┌─ asyncio.run(engine.process_batch())
│  │
│  └─ Event loop starts
│     │
│     ├─ logger.info("📋 Fetching articles...")
│     │  └─ Acquires logging._lock
│     │     ├─ Calls Logger.handle()
│     │     ├─ Calls StreamHandler.emit()
│     │     └─ Queues message to stderr
│     │
│     ├─ cur.execute(query, (BATCH_SIZE,))    ◄── BLOCKS HERE
│     │  └─ Waits for PostgreSQL response
│     │     └─ Network latency
│     │        └─ Still holding logging._lock from above!
│     │           └─ Lock never released!
│     │
│     ├─ [MEANWHILE: Render timeout reaches 5 minutes]
│     │  └─ SIGTERM signal sent to process
│     │
│     └─ signal_handler() fires     ◄── INTERRUPTS HERE
│        │
│        └─ logging.shutdown()
│           └─ Tries to acquire logging._lock
│              └─ Already held by cur.execute()
│              └─ DEADLOCK DETECTED ✗
│                 └─ No progress possible
│                 └─ Process hangs
│
└─ [Render timeout extended by grace period]
   └─ Still hung
   └─ SIGKILL sent
   └─ Process dies
   └─ Output lost


The Lock Chain:
═══════════════════════════════════════════════════════════════

logging._lock
  ├─ Acquired by: logger.info() in process_batch()
  │  Status: HELD by cur.execute() (blocking)
  │
  └─ Requested by: logging.shutdown() in signal_handler()
     Status: WAITING - can never acquire
     
     Reason: Same thread, different execution context
             Can't reacquire the same lock
             Even though RLock is reentrant, signal handler
             context is different from original lock holder context
```

---

## DIAGRAM 5: Fix Impact

```
BEFORE FIXES (Broken):
═══════════════════════════════════════════════════════════════

Query takes 2s
├─ ... (all fine) ✓

Query takes 30s
├─ ... (all fine) ✓
└─ But orchestrator timeout = 5 min

Query takes 5min+
├─ ... (all fine) ✓
├─ Orchestrator timeout fires (5 min)
│  └─ SIGTERM
│     └─ signal_handler() calls logging.shutdown()
│        └─ DEADLOCK ✗

Query takes 10min
├─ Same DEADLOCK ✗

Result:
└─ Always deadlock if query takes > orchestrator timeout
└─ Always lose output on SIGKILL
└─ Always marked as "Failed"


AFTER FIX #1 (Remove logging.shutdown()):
═══════════════════════════════════════════════════════════════

Query takes 5min+
├─ ... (all fine) ✓
├─ Orchestrator timeout fires (5 min)
│  └─ SIGTERM
│     └─ signal_handler() uses only sys.write()
│        └─ No lock needed ✓
│           └─ Quick exit ✓

Result:
└─ No deadlock ✓
└─ Process exits cleanly if possible
└─ But might still SIGKILL if query still running


AFTER FIX #2 (Add executor timeout):
═══════════════════════════════════════════════════════════════

Query takes 30s
├─ ... (all fine) ✓

LLM extraction called
├─ Executor with 30s timeout
│  ├─ Takes 20s
│  │  └─ Returns successfully ✓
│  │
│  └─ Takes 35s
│     └─ asyncio.TimeoutError after 30s ✓
│        └─ Logged error message ✓
│        └─ Continue to next article ✓

Result:
└─ No indefinite wait on executor
└─ Clear error in logs if timeout
└─ Script continues or fails fast


AFTER FIX #3 (Add database timeout):
═══════════════════════════════════════════════════════════════

Query executes:
├─ Fast query: completes in 2s ✓
├─ Slow query: completes in 30s ✓
├─ Slow query: exceeds 60s timeout
│  └─ PostgreSQL cancels query
│  └─ cur.execute() raises exception ✓
│  └─ Caught and logged ✓
│  └─ Script continues ✓

Result:
└─ No hanging queries
└─ Clear error messages
└─ Predictable timeout behavior


COMBINED RESULT (All 3 fixes):
═══════════════════════════════════════════════════════════════

Scenario 1: Everything works fast
├─ Script completes normally ✓
├─ All output appears ✓
├─ Exit code 0 ✓

Scenario 2: LLM extraction slow
├─ Times out after 30s ✓
├─ Error logged ✓
├─ Script continues ✓
└─ Clear messages in logs ✓

Scenario 3: Database query slow
├─ Times out after 60s ✓
├─ Exception caught ✓
├─ Script continues ✓
└─ Error visible in logs ✓

Scenario 4: Everything slow
├─ Hits timeouts at various points ✓
├─ Each timeout logged ✓
├─ Script fails gracefully ✓
├─ No deadlock ✓
├─ Exit code non-zero ✓
└─ Clear error trail in logs ✓

NO SILENT FAILURES ✓
```

---

## DIAGRAM 6: Signal Handler Comparison

```
UNSAFE (Current Code):
═══════════════════════════════════════════════════════════════

def signal_handler(signum, frame):
    msg = "Signal received\n"
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.stderr.flush()
    logging.shutdown()  ✗ UNSAFE!
    │
    └─ Tries to acquire logging module lock
       └─ If lock is held by main thread
          └─ DEADLOCK ✗

Risk level: 🔴🔴🔴 CRITICAL


SAFE (Fixed Code):
═══════════════════════════════════════════════════════════════

def signal_handler(signum, frame):
    msg = "Signal received\n"
    sys.stdout.write(msg)    ✓ Safe - direct I/O
    sys.stdout.flush()       ✓ Safe - direct flush
    sys.stderr.write(msg)    ✓ Safe - direct I/O
    sys.stderr.flush()       ✓ Safe - direct flush
    # No logging.shutdown()  ✓ Safe - no lock needed
    sys.exit(0)

Risk level: 🟢 SAFE

Why it's safe:
├─ sys.write() doesn't use locks
├─ sys.flush() doesn't use locks
├─ No interaction with logging module
├─ No deadlock possible
└─ Process exits cleanly
```

---

## DIAGRAM 7: Timeout Protection Pattern

```
WITHOUT TIMEOUT (Vulnerable):
═══════════════════════════════════════════════════════════════

async def process():
    result = await loop.run_in_executor(None, slow_function)
    # ↑ Can wait FOREVER if slow_function hangs
    return result

Process timeline:
├─ T+0s: Start executor
├─ T+5min: Still waiting (unaware of timeout)
├─ T+10min: Still waiting
├─ ...
├─ T+5h: Still waiting ✗
└─ Eventually:
   ├─ Orchestrator times out
   └─ SIGKILL


WITH TIMEOUT (Protected):
═══════════════════════════════════════════════════════════════

async def process():
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, slow_function),
            timeout=30.0  ← SET EXPLICIT TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("Timeout!")
        result = {}  # Fallback
    return result

Process timeline:
├─ T+0s: Start executor with 30s timeout
├─ T+10s: Function completes ✓
│  └─ Result returned
│
├─ Or:
│  ├─ T+0s: Start executor with 30s timeout
│  ├─ T+30s: Timeout! ✓
│  │  └─ asyncio.TimeoutError raised
│  ├─ T+30.1s: Exception caught ✓
│  │  └─ Error logged
│  └─ T+30.2s: Continue with fallback ✓


TIMEOUT HIERARCHY (Recommended):
═══════════════════════════════════════════════════════════════

Overall batch timeout: 600s (10 min)
├─ Individual LLM executor: 30s
├─ Individual fetch executor: 10s
├─ Database query: 60s
├─ Database connection: 10s
└─ File I/O: 5s

This way:
├─ No individual operation can hang forever
├─ Each timeout is logged
├─ Script completes or fails within known time
└─ Orchestrator never needs to SIGKILL for timeout
```

---

## KEY VISUALIZATION: The Moment of Deadlock

```
EXECUTION SNAPSHOT at T+5m (When SIGTERM arrives):

┌──────────────────────────────────────────────────────┐
│  Python Thread Stack                                 │
├──────────────────────────────────────────────────────┤
│                                                      │
│  Frame 0 (Innermost): cur.execute()                │
│  ├─ Location: psycopg2 library                      │
│  ├─ State: BLOCKED waiting for PostgreSQL response  │
│  ├─ Holds: Nothing (but logging._lock held above)  │
│  └─ Waiting for: Network response from database    │
│                                                      │
│  Frame 1: process_batch() async                    │
│  ├─ Location: Line 210 in digest_articles.py       │
│  ├─ Holds: logging._lock (acquired at line 203)    │
│  │         (from logger.info() call)                │
│  └─ Blocking on: cur.execute()                     │
│                                                      │
│  Frame 2: asyncio event loop                       │
│  ├─ Location: Inside Python's asyncio module       │
│  ├─ State: Running coroutine                        │
│  └─ Waiting for: process_batch() to return         │
│                                                      │
│  Frame 3: <signal handler context>     ◄─ SIGTERM   │
│  ├─ Location: signal_handler() function             │
│  ├─ Triggered by: SIGTERM signal                    │
│  ├─ Executing: logging.shutdown()                   │
│  └─ Attempting to acquire: logging._lock            │
│     ✗ LOCKED by Frame 1!                            │
│     ✗ Can't proceed!                                │
│     ✗ Deadlock condition detected!                  │
│                                                      │
└──────────────────────────────────────────────────────┘

Result:
├─ Thread cannot make progress
├─ logging.shutdown() waits for lock
├─ process_batch() doesn't release lock (blocked on network)
├─ Network response may never come
├─ Process appears hung to orchestrator
└─ SIGKILL eventually sent
   └─ All output lost
```

---

**These diagrams show exactly why the fixes work and why the current code fails.**

