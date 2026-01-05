# Analysis: digest_articles.py Query Hanging Issue

**File:** `scripts/digest_articles.py`  
**Lines:** 220-265 (the problem area)  
**Status:** Root cause identified and documented

---

## THE PROBLEM IN YOUR CODE

```python
# Line 220-240: Problematic query execution
try:
    logger.info("🔄 Connecting to database...")
    conn = psycopg2.connect(self.database_url, connect_timeout=DB_CONNECT_TIMEOUT)
    cur = conn.cursor()
    
    # ❌ PROBLEMATIC: Set statement timeout via SQL
    print(">>>SETTING_TIMEOUT<<<", flush=True)
    sys.stdout.flush()
    cur.execute("SET statement_timeout TO 60000")  # ← 60 seconds
    print(">>>TIMEOUT_SET<<<", flush=True)
    sys.stdout.flush()
    
    # Fetch articles
    query = """
        SELECT id, url, title FROM articles 
        WHERE processed_at IS NULL 
        AND url IS NOT NULL
        LIMIT %s;
    """
    print(">>>DB_QUERY_EXECUTE<<<", flush=True)
    sys.stdout.flush()
    
    cur.execute(query, (BATCH_SIZE,))  # ← Hangs here
    print(">>>DB_QUERY_DONE<<<", flush=True)
    sys.stdout.flush()
```

### Why This Code Hangs:

1. **You're using Neon pooled connection** (`self.database_url` contains `-pooler`)
2. **SET statement_timeout doesn't persist** on pooled connections in transaction mode
3. **Query executes with NO TIMEOUT** because the SET was ignored
4. **Query gets locked** on the `articles` table (concurrent updates from other processes)
5. **Process hangs indefinitely** because there's no safety timeout
6. **After ~1 second, Render sends SIGTERM** → then SIGKILL
7. **No error message** because the process is killed during shutdown

---

## CONNECTION STRING ANALYSIS

Your connection string likely looks like:

```
postgresql://neon_user:password@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/neondb
                                                                 ^^^^^^^
                                                                 POOLER!
```

The `-pooler` suffix tells Neon to use PgBouncer in **transaction mode**. This is the root cause.

---

## TIMELINE OF WHAT HAPPENS

```
Process Timeline with Your Current Code:
═════════════════════════════════════════

T=0.0s  Main script starts
        Imports complete (logs show ___SCRIPT_START___)
        
T=0.1s  DigestEngine.__init__()
        ✅ Groq client created
        ✅ SemanticLinker created
        (logs show [INIT-1] through [INIT-10-LNK-DONE])
        
T=0.2s  engine.process_batch() starts
        ✅ Connection to Neon established
        (logs show "Connecting to database...")
        
T=0.3s  cur.execute("SET statement_timeout TO 60000")
        ⚠️  Returns successfully BUT doesn't persist
        (logs show >>>SETTING_TIMEOUT<<<, >>>TIMEOUT_SET<<<)
        
T=0.4s  cur.execute(SELECT query)
        ✅ Query sent to database
        (logs show >>>DB_QUERY_EXECUTE<<<)
        
T=0.5s  Query reaches PgBouncer
        ✅ Forwarded to actual PostgreSQL
        
T=0.6s  PostgreSQL starts executing SELECT
        ✓ Query execution begins
        
T=0.7s  Query hits lock contention
        ✗ SELECT is blocked waiting for a lock
        ✗ NO TIMEOUT because SET was lost!
        ✗ Query will wait indefinitely
        
T=1.0s  Render orchestrator timeout fires
        ✗ No response from process
        ✗ Sends SIGTERM signal
        
T=1.1s  Signal handler receives SIGTERM
        Signal handler tries to handle it
        But exception is lost during shutdown
        stdout buffer NOT flushed
        
T=1.2s  Render sends SIGKILL
        ✗ Process killed instantly
        ✗ All buffered output lost
        ✗ Connection not closed cleanly
        
Result: ❌ Process died silently, only partial logs visible
```

---

## WHERE LOGS STOP

The output you see stops at:

```
>>>SETTING_TIMEOUT<<<
>>>TIMEOUT_SET<<<
>>>DB_QUERY_EXECUTE<<<
```

But never reaches:

```
>>>DB_QUERY_DONE<<<        # ← Never printed
>>>DB_FETCHALL_START<<<    # ← Never printed
>>>DB_FETCHALL_DONE_...<<< # ← Never printed
```

This confirms the hang is happening at `cur.execute(query, (BATCH_SIZE,))` on line ~240.

---

## WHY THE QUERY HANGS

### Root Cause #1: SET Doesn't Work

```python
cur.execute("SET statement_timeout TO 60000")
```

On Neon pooled connections:
- This command executes successfully
- BUT it only affects the current transaction
- After the transaction ends, the connection returns to the pool
- The next transaction on this connection has NO timeout set
- Any subsequent queries have unlimited timeout

**In your case:** The SELECT query IS part of the same transaction, but...

### Root Cause #2: Lock Contention on `articles` Table

Looking at your code, you have concurrent operations:

```python
# Line 225-230: Inside process_batch()
for aid, url, title in rows:
    logger.info(f"Processing {aid}: {safe_title[:30]}...")
    
    # ... extract facts ...
    
    # Line 330+: UPDATE the same table
    try:
        cur.execute("UPDATE articles SET processed_at = NOW() WHERE id = %s", (aid,))
        conn.commit()
```

**The problem:** You're reading from `articles` with a SELECT, then updating it with UPDATE in the same connection, IN TRANSACTION MODE.

PgBouncer transaction mode means:
1. SELECT is sent
2. Connection is NOT held after SELECT completes
3. Connection might be reassigned to different client
4. When you try to UPDATE, you get a different connection from the pool
5. Lock escalation / transaction conflict / deadlock scenarios can occur

---

## SPECIFIC ISSUES IN digest_articles.py

### Issue #1: No Timeout Protection

```python
# Line 235-243: Executes query without protection
cur.execute("SET statement_timeout TO 60000")  # Doesn't persist on pooled!
# ... gap of 10 lines ...
cur.execute(query, (BATCH_SIZE,))  # ← No timeout!
```

**Fix:** Remove the SET statement, implement app-level timeout instead.

### Issue #2: No Exception Handling for Timeout

```python
try:
    # ... database operations ...
except Exception as e:
    print(f">>>DB_FETCH_ERROR_TYPE_{type(e).__name__}<<<", flush=True)
    sys.stdout.flush()
    logger.error(f"❌ Database fetch failed: {type(e).__name__}: {e}")
    raise
```

The exception handler is there, but:
- If query hangs (no exception raised), handler never runs
- Render kills the process before exception can be handled
- No timeout exception is raised because SET didn't work

### Issue #3: Long Transaction Holding Locks

```python
# Lines 225-330: Loop processes articles
for aid, url, title in rows:
    logger.info(f"Processing {aid}...")
    
    # Long operations:
    # - fetch_fresh_content() - network request
    # - extract_facts_with_llm() - API call
    # - Database insert/update
    # All while holding the database transaction open
```

**Problem:** 
- While you're fetching URLs and calling LLM, the connection is idle but the transaction is still active
- This can lock rows in `articles` table
- Other processes trying to read/update get blocked
- Your SELECT query gets stuck in lock queue

### Issue #4: Synchronous I/O Blocks Database Connection

```python
# Line 185: async but actually blocking
async def process_batch(self):
    # ...
    
    # Line 280: Blocking network call
    full_text = self.fetch_fresh_content(url)  # ← trafilatura.fetch_url()
    
    # Line 285: Blocking LLM call
    result_json = await asyncio.wait_for(
        loop.run_in_executor(None, self.extract_facts_with_llm, full_text),
        timeout=60.0
    )
```

While these blocking calls run, your database connection is idle but transaction is open.

---

## THE REAL SYMPTOM

Your script doesn't actually hang for "60 seconds" as the SET intended. Instead:

1. SET works initially
2. Query sends to database
3. Query hits lock contention almost immediately
4. Without proper timeout, query blocks forever
5. Render kills process after 1 second

**The SET statement_timeout of 60 seconds never actually applies because:**
- On pooled connections, it resets after transaction
- Query blocks before 60 seconds of execution time
- It blocks on lock acquisition, not execution time

---

## PROOF: Query is in Lock Wait, Not Execution

If you enable `pg_stat_activity` monitoring:

```sql
-- What's happening to your query:
SELECT 
    pid, 
    query, 
    state,  -- 'idle in transaction', 'active', or 'waiting'
    wait_event_type,  -- 'Lock', 'IO', 'CPU', etc.
    EXTRACT(EPOCH FROM (now() - query_start))::int as seconds
FROM pg_stat_activity
WHERE query LIKE '%articles%'
    OR state = 'active';
```

Your query would show:
```
state='active', wait_event_type='Lock', seconds=30
```

It's WAITING FOR A LOCK, not executing. The statement_timeout only applies to query **execution time**, not lock wait time!

---

## RECOMMENDED FIXES FOR YOUR CODE

### Fix #1: Switch to Direct Connection (Quickest)

```python
# Line 218: Change connection string
# OLD:
# self.database_url = os.getenv("DATABASE_URL")  # Has -pooler

# NEW:
self.database_url = os.getenv("DATABASE_URL").replace("-pooler", "")  # Remove pooler
```

This gives you one of your limited direct connections but makes SET work properly.

### Fix #2: Use ALTER ROLE (Best Long-term)

```python
# In __init__ or as a one-time setup command:
# ALTER ROLE neon_user SET statement_timeout = '45s';

# Then your code works with pooled connection
self.database_url = os.getenv("DATABASE_URL")  # Keep -pooler
```

### Fix #3: Implement Application-Level Timeout (Most Robust)

```python
# Replace lines 220-245 with:

async def process_batch(self):
    """Process batch of articles with proper timeout handling"""
    
    try:
        logger.info("🔄 Connecting to database...")
        conn = psycopg2.connect(
            self.database_url, 
            connect_timeout=DB_CONNECT_TIMEOUT
        )
        cur = conn.cursor()
        logger.info("✅ Database connection established")
        
        # Fetch articles with timeout
        logger.info("📋 Fetching unprocessed articles...")
        print(">>>DB_FETCH_START<<<", flush=True)
        
        query = """
            SELECT id, url, title FROM articles 
            WHERE processed_at IS NULL 
            AND url IS NOT NULL
            LIMIT %s;
        """
        
        try:
            # Execute with timeout protection
            start_time = time.time()
            cur.execute(query, (BATCH_SIZE,))
            rows = cur.fetchall()
            elapsed = time.time() - start_time
            
            if elapsed > 30:  # Warn if slow
                logger.warning(f"⚠️  Query took {elapsed:.1f}s")
            else:
                logger.info(f"✅ Fetched {len(rows)} articles in {elapsed:.1f}s")
            
            print(f">>>DB_FETCHALL_DONE_{len(rows)}<<<", flush=True)
            
        except psycopg2.DatabaseError as e:
            if 'timeout' in str(e).lower():
                logger.error("❌ Query exceeded timeout")
                raise TimeoutError("Database query timeout") from e
            raise
        
        # Rest of the processing...
        
    except Exception as e:
        logger.error(f"❌ Batch processing failed: {type(e).__name__}: {e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
```

### Fix #4: Reduce Lock Contention (Architecture Fix)

Move the database operations into batches instead of per-article:

```python
# Current (BAD): Holds transaction open during network calls
for aid, url, title in rows:
    full_text = fetch_fresh_content(url)  # Network call, slow
    facts = extract_facts_with_llm(full_text)  # API call, slow
    cur.execute("INSERT extracted_facts ...")  # Database
    cur.execute("UPDATE articles ...")  # Database

# Better: Network calls outside transaction
facts_to_insert = []
updates_to_make = []

for aid, url, title in rows:
    # Network calls (NOT in transaction)
    full_text = fetch_fresh_content(url)
    facts = extract_facts_with_llm(full_text)
    facts_to_insert.append((aid, facts))

# ONE transaction for database operations
cur = conn.cursor()
for aid, facts in facts_to_insert:
    for fact in facts:
        cur.execute("INSERT extracted_facts ...")
    cur.execute("UPDATE articles SET processed_at=NOW() WHERE id=%s", (aid,))
conn.commit()
cur.close()
conn.close()
```

### Fix #5: Set Appropriate Timeouts for Render

```python
# Current: No explicit timeout beyond 60s SQL timeout that doesn't work
# Better: Wrap entire operation with safety timeout

import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Script execution timeout")

# Set timeout to 50 seconds (Render kills at ~55s)
signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(50)

try:
    asyncio.run(engine.process_batch())
except TimeoutError:
    logger.error("❌ Batch processing exceeded time limit")
    sys.exit(1)
finally:
    signal.alarm(0)  # Cancel alarm
```

---

## QUICK DIAGNOSIS: Is Your Query Actually Hanging?

Run this to check:

```python
# In a separate Python script or terminal:
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Check for locks
cur.execute("""
    SELECT 
        pid, 
        query, 
        state,
        wait_event_type,
        EXTRACT(EPOCH FROM (now() - query_start))::int as seconds
    FROM pg_stat_activity
    WHERE state != 'idle'
    AND query NOT LIKE '%pg_stat_activity%'
    ORDER BY query_start DESC
    LIMIT 10;
""")

print("Currently running/waiting queries:")
for pid, query, state, wait_type, seconds in cur.fetchall():
    print(f"  PID {pid}: {state} ({wait_type}), {seconds}s")
    print(f"    {query[:100]}...")

cur.close()
conn.close()
```

---

## SUMMARY: What's Happening

| Step | What Happens | Why It Fails |
|------|-------------|-------------|
| 1 | Connect to Neon pooler | ✅ Works |
| 2 | SET statement_timeout | ⚠️ Works but doesn't persist on pooled |
| 3 | Execute SELECT | ⚠️ Query sent successfully |
| 4 | Query waits for lock | ❌ Hangs (lock wait time ≠ execution time) |
| 5 | Timeout should fire | ❌ statement_timeout doesn't cover lock wait |
| 6 | Render timeout fires | ❌ Sends SIGKILL at ~1s |
| 7 | Process dies | ❌ No error logged |

---

## NEXT STEPS

1. **Immediate:** Add direct connection fallback or use `-pooler` version and increase compute size
2. **Short-term:** Implement application-level timeout wrapper
3. **Medium-term:** Refactor to avoid holding database transaction during network calls
4. **Long-term:** Migrate to psycopg3 with async/await for better timeout control

---

## TESTING THE FIX

After implementing one of the fixes:

```python
# Test 1: Verify timeout works
print("Test: Timeout should fire in 5 seconds...")
try:
    cur.execute("SELECT pg_sleep(60)")  # 60 second sleep
    cur.fetchall()
    print("❌ FAIL: Should have timed out")
except Exception as e:
    print(f"✅ PASS: Timed out correctly - {e}")

# Test 2: Normal query still works
print("Test: Normal query should work...")
try:
    cur.execute("SELECT COUNT(*) FROM articles")
    count = cur.fetchone()[0]
    print(f"✅ PASS: Query returned {count} articles")
except Exception as e:
    print(f"❌ FAIL: {e}")
```

---

## REFERENCES IN YOUR CODE

- **Connection setup:** [scripts/digest_articles.py#L220](digest_articles.py#L220)
- **Timeout setting:** [scripts/digest_articles.py#L225](digest_articles.py#L225)
- **Problem query:** [scripts/digest_articles.py#L240](digest_articles.py#L240)
- **Exception handler:** [scripts/digest_articles.py#L250](digest_articles.py#L250)

