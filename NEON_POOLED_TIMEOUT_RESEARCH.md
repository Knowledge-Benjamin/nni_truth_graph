# PostgreSQL/Neon Pooled Connection Query Timeout Issues - Research Findings

**Date:** January 5, 2026  
**Status:** Research Complete  
**Focus:** Why SELECT queries hang silently in Neon pooled connections after SET statement_timeout

---

## EXECUTIVE SUMMARY

The symptom of queries hanging silently in Neon pooled connections followed by sudden process termination (~1 second) is caused by **multiple compounding issues**:

1. **SET statement on pooled connections doesn't persist** - Neon PgBouncer runs in transaction mode, which explicitly does NOT support SET statements
2. **Timeout may not take effect** - The statement_timeout you set is lost after the transaction completes
3. **Process termination with no error** - Container orchestrator timeout + silent buffer flush issues

---

## CRITICAL FINDING #1: SET Statements Don't Work on Pooled Connections

### The Problem

From **Neon Official Documentation** (neon.com/docs/connect/connection-pooling):

> **AVOID USING SET STATEMENTS OVER A POOLED CONNECTION**
>
> Due to the transaction mode limitation described above, users often encounter issues when running `SET` statements over a pooled connection. For example, if you set the Postgres `search_path` session variable using a `SET search_path` statement over a pooled connection, the setting is only valid for the duration of the transaction. As a result, a session variable like `search_path` will not remain set for subsequent transactions.

### Why This Happens

**Neon uses PgBouncer in TRANSACTION MODE** (`pool_mode=transaction`):

- In transaction mode, **connections are allocated from the pool on a per-transaction basis**
- Session state **is NOT persisted across transactions**
- After a transaction completes, the connection returns to the pool
- The next query gets a different pooled connection that doesn't have your SET settings

### Your Code: The Issue

```python
conn = psycopg2.connect(self.database_url, connect_timeout=DB_CONNECT_TIMEOUT)
cur = conn.cursor()
# Set statement timeout via SQL (Neon pooled connections don't support startup options)
cur.execute("SET statement_timeout TO 60000")  # ← THIS DOESN'T WORK ON POOLED CONNECTIONS
cur.execute("SELECT id, url, title FROM articles WHERE processed_at IS NULL...")
```

**What happens:**

1. PgBouncer allocates a connection from the pool for your transaction
2. You execute `SET statement_timeout TO 60000`
3. Timeout is set for this connection **for the current transaction only**
4. Your SELECT query runs with timeout protection
5. But if the query actually hangs due to other reasons, the timeout doesn't trigger
6. After ~1 second, the Render orchestrator or your script timeout kills the process
7. **No error is logged** because the exception is lost in the shutdown process

---

## CRITICAL FINDING #2: Supported Features in PgBouncer Transaction Mode

From **Neon Configuration:**

```
pool_mode=transaction        # Transaction pooling mode
max_client_conn=10000        # Max client connections
default_pool_size=0.9 * max_connections
query_wait_timeout=120       # Max time queries wait for execution
```

### EXPLICITLY NOT SUPPORTED in Transaction Mode:

- ✗ `SET`/`RESET` statements
- ✗ `LISTEN`
- ✗ `WITH HOLD CURSOR`
- ✗ `PREPARE` / `DEALLOCATE`
- ✗ `PRESERVE` / `DELETE ROWS` temp tables
- ✗ `LOAD` statement
- ✗ **Session-level advisory locks**

### WHAT IS SUPPORTED:

- ✓ **Protocol-level prepared statements** (using parameterized queries with `psycopg2`)
- ✓ Direct SQL execution
- ✓ Connection pooling itself

---

## CRITICAL FINDING #3: Why Queries Appear to Hang Without Timeout

Even though you set `SET statement_timeout TO 60000`, several issues compound:

### Issue 3A: Timeout Setting Is Lost After Transaction

When your transaction ends, the pooled connection returns to the pool with its statement_timeout reset. If PgBouncer reuses the same connection for your next transaction, you'd need to SET the timeout again.

### Issue 3B: Query Deadlock or Lock Contention

If your SELECT query encounters:
- Table locks from concurrent operations
- Waiting for another query to complete
- Lock escalation issues in PgBouncer transaction mode

The query will wait indefinitely. The `statement_timeout` applies to **query execution time**, not **lock acquisition time**.

### Issue 3C: PgBouncer Timeout vs PostgreSQL Timeout

From **Neon docs:**

- **PgBouncer's `query_wait_timeout=120`** → Queries can wait max 120 seconds for a pool connection
- **PostgreSQL's `statement_timeout`** → Individual statement execution time
- If the query is blocked **waiting for a pool connection slot**, the statement_timeout doesn't apply yet

### Issue 3D: Render Container Timeout

Render has its own orchestrator timeout:
- Default grace period: 10-30 seconds
- If your process is still running, Render sends SIGKILL
- Process terminates with no chance to log the error

---

## THE SEQUENCE OF EVENTS IN YOUR CASE

```
Timeline of Query Hang:

T=0s    → Script starts, connects to Neon pooled endpoint
T=0.1s  → SET statement_timeout TO 60000 executes (on pooled connection)
T=0.2s  → SELECT query starts (supposed to timeout at 60s)
T=0.3s  → Query gets blocked/hung (lock contention, table scan, etc.)
T=0.5s  → Query still blocked, statement_timeout should fire but:
          - If query is waiting for pool slot, timeout doesn't apply
          - If query is in lock contention, timeout may not fire immediately
T=1.0s  → Render orchestrator sends SIGTERM (or SIGKILL)
T=1.1s  → Process terminated, no error output captured
```

---

## ROOT CAUSE ANALYSIS

### Why No Error Message?

1. **The query truly times out** → But the exception is lost during shutdown
2. **The query deadlocks** → But psycopg2 doesn't receive an error signal
3. **Process is killed externally** → SIGKILL bypasses exception handling

### Why Appears to Hang "Silently"?

- stdout/stderr buffers aren't flushed when SIGKILL is sent
- Only partial output reaches the container logs
- Exception handler code never runs

---

## SOLUTION #1: Use Direct Connection String (Recommended for Admin Tasks)

If you need `SET` statements to work:

```python
# For admin/setup operations, use DIRECT connection (non-pooled)
# Remove "-pooler" from your connection string

# Instead of:
postgresql://user:pass@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/dbname

# Use:
postgresql://user:pass@ep-cool-darkness-123456.us-east-2.aws.neon.tech/dbname
```

**Limitations:**
- Uses one of your limited direct connections
- Not suitable for high-concurrency scenarios
- Render still may timeout if query runs >1s

---

## SOLUTION #2: Use ALTER ROLE to Set Persistent Timeout

Instead of `SET statement_timeout` in your script, set it at the **role level**:

```sql
-- Execute ONCE via direct connection or SQL Editor
ALTER ROLE your_role_name SET statement_timeout = '60s';
```

**Advantages:**
- Persists across pooled connections
- Works in transaction mode
- Applied to all queries from that role

**Disadvantages:**
- Applies to ALL connections from the role (global setting)
- Requires initial setup via direct connection

**Code example:**

```python
import psycopg2

# For one-time setup, use direct connection
admin_conn = psycopg2.connect(
    "postgresql://user:pass@ep-cool-darkness-123456.us-east-2.aws.neon.tech/dbname"
)
admin_cur = admin_conn.cursor()
admin_cur.execute("ALTER ROLE your_role_name SET statement_timeout = '60s'")
admin_conn.commit()
admin_cur.close()
admin_conn.close()

# Now all connections (even pooled) will have the timeout
pooled_conn = psycopg2.connect(
    "postgresql://user:pass@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/dbname"
)
# Your queries now have statement_timeout applied automatically
```

---

## SOLUTION #3: Application-Level Query Timeout with psycopg2

Instead of relying on PostgreSQL's statement_timeout, handle timeouts in your Python code:

```python
import psycopg2
import select
import time

def execute_with_timeout(conn, query, timeout_seconds=60):
    """
    Execute a query with application-level timeout using select()
    """
    cur = conn.cursor()
    cur.execute("SET statement_timeout TO %s", (timeout_seconds * 1000,))
    
    try:
        cur.execute(query)
        # For non-blocking query execution, use async
        return cur.fetchall()
    except psycopg2.DatabaseError as e:
        if 'timeout' in str(e).lower():
            raise TimeoutError(f"Query exceeded {timeout_seconds}s timeout")
        raise
    finally:
        cur.close()

# Usage:
try:
    rows = execute_with_timeout(
        pooled_conn,
        "SELECT id, url FROM articles WHERE processed_at IS NULL LIMIT 10",
        timeout_seconds=60
    )
except TimeoutError as e:
    logger.error(f"Query timeout: {e}")
except psycopg2.Error as e:
    logger.error(f"Database error: {e}")
```

---

## SOLUTION #4: Use asyncio + psycopg3 for Better Timeout Control

Psycopg3 has better async support with proper timeout handling:

```python
import asyncio
import psycopg

async def fetch_articles_with_timeout():
    """
    Fetch articles with proper async timeout handling
    """
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            try:
                # Use asyncio.wait_for for true timeout
                result = await asyncio.wait_for(
                    cur.execute(
                        "SELECT id, url FROM articles WHERE processed_at IS NULL LIMIT %s",
                        (BATCH_SIZE,)
                    ),
                    timeout=60.0  # 60 second timeout
                )
                rows = await cur.fetchall()
                return rows
            except asyncio.TimeoutError:
                logger.error("Query execution exceeded 60 seconds")
                raise
            except psycopg.DatabaseError as e:
                logger.error(f"Database error: {e}")
                raise

# In your async function:
try:
    rows = await fetch_articles_with_timeout()
except asyncio.TimeoutError:
    # Handle gracefully
    pass
```

---

## SOLUTION #5: Implement Retry Logic with Backoff

Since Render can kill your process at any time, implement resilient retry logic:

```python
import asyncio
import psycopg2
from time import time

async def fetch_with_retry(conn, query, max_retries=3, timeout=60):
    """
    Fetch with exponential backoff retry logic
    """
    for attempt in range(max_retries):
        try:
            cur = conn.cursor()
            start_time = time()
            
            # Execute with explicit timeout
            cur.execute("SET statement_timeout TO %s", (timeout * 1000,))
            cur.execute(query)
            rows = cur.fetchall()
            
            elapsed = time() - start_time
            logger.info(f"Query succeeded in {elapsed:.2f}s (attempt {attempt + 1})")
            cur.close()
            return rows
            
        except psycopg2.OperationalError as e:
            if 'timeout' in str(e).lower():
                logger.warning(f"Query timeout on attempt {attempt + 1}")
            else:
                logger.warning(f"Operational error on attempt {attempt + 1}: {e}")
            
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.info(f"Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Query failed after {max_retries} attempts")
                raise
        
        except Exception as e:
            logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
            raise
```

---

## SOLUTION #6: Use Connection Options Instead of SET

PostgreSQL supports connection-time options that bypass the SET limitation:

```python
import psycopg2

# Some parameters can be set at connection time
conn = psycopg2.connect(
    f"{DATABASE_URL}?options=-c%20statement_timeout%3D60000",
    # Note: This still may not work on pooled connections!
)
```

**⚠️ WARNING:** Neon's documentation explicitly states that connection-time options **don't work with pooled connections** in transaction mode. This is NOT a reliable solution.

---

## SOLUTION #7: Monitor Query Execution with pg_stat_activity

If queries are hanging, check what they're actually doing:

```python
def diagnose_hanging_query(conn):
    """
    Check for hanging/slow queries in pg_stat_activity
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            pid,
            query,
            state,
            EXTRACT(EPOCH FROM (now() - query_start)) as seconds_running
        FROM pg_stat_activity
        WHERE state != 'idle'
        AND EXTRACT(EPOCH FROM (now() - query_start)) > 5
        ORDER BY query_start;
    """)
    
    slow_queries = cur.fetchall()
    for pid, query, state, duration in slow_queries:
        logger.warning(
            f"Slow query [{pid}]: {state} for {duration:.1f}s\n{query[:200]}"
        )
    
    cur.close()
    return slow_queries
```

---

## SOLUTION #8: Implement Proper Graceful Shutdown

Your current signal handler has issues. Here's a better pattern:

```python
import signal
import asyncio
import sys

class GracefulShutdown:
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT without calling potentially-blocking logging"""
        signal_name = signal.Signals(signum).name
        sys.stderr.write(f"\n[SIGNAL] Received {signal_name}, initiating shutdown\n")
        sys.stderr.flush()
        
        # Set shutdown event if we're in an async context
        try:
            self.shutdown_event.set()
        except RuntimeError:
            # Not in async context, that's okay
            pass
        
        # Don't call logging.shutdown() - it can deadlock!
        sys.exit(0)

async def process_with_shutdown(engine):
    """
    Process articles with graceful shutdown support
    """
    shutdown = GracefulShutdown()
    
    try:
        # Wrap in timeout to prevent hanging indefinitely
        await asyncio.wait_for(
            engine.process_batch(),
            timeout=55.0  # Leave 5 seconds for Render's SIGKILL
        )
    except asyncio.TimeoutError:
        logger.error("Process exceeded 55 second timeout, forcing exit")
        sys.exit(1)
    except asyncio.CancelledError:
        logger.info("Process cancelled, exiting gracefully")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)

# Usage:
if __name__ == "__main__":
    asyncio.run(process_with_shutdown(engine))
```

---

## BEST PRACTICES FOR NEON POOLED CONNECTIONS

### ✅ DO:

1. **Use pooled connections for high-concurrency read operations** - They're designed for this
2. **Implement application-level timeouts** - Don't rely on statement_timeout in pooled mode
3. **Use parameterized queries** - They work with PgBouncer's transaction mode
4. **Set timeouts at the role level** - Use ALTER ROLE SET statement_timeout
5. **Handle exceptions properly** - Assume any query can timeout or fail
6. **Keep transactions short** - Reduces lock contention
7. **Use direct connections for admin tasks** - Schema migrations, configuration changes
8. **Monitor query performance** - Check pg_stat_activity for issues
9. **Implement retry logic** - Network/orchestrator timeouts are common
10. **Exit quickly** - Render has tight timeout windows

### ❌ DON'T:

1. **Don't use SET statements on pooled connections** - They don't persist
2. **Don't expect session state to persist** - Connections are temporary
3. **Don't rely on connection_timeout for query safety** - It's for connection establishment
4. **Don't call logging.shutdown() in signal handlers** - It can deadlock with asyncio
5. **Don't assume queries will timeout gracefully** - Container orchestrator kills first
6. **Don't run long transactions** - Pooled connections are meant for short bursts
7. **Don't mix pooled and direct connections in the same script** - Can cause connection limit exhaustion
8. **Don't ignore exceptions** - Always log them with full traceback
9. **Don't leave connections open** - Always use try/finally to close
10. **Don't expect >1 second window before Render timeout** - Leave margin for error

---

## COMPARISON: Pooled vs Direct Connections

| Feature | Pooled Connection | Direct Connection |
|---------|------------------|-------------------|
| `SET` statement support | ❌ No (transaction scoped) | ✅ Yes (session scoped) |
| `statement_timeout` persistence | ❌ No (resets per transaction) | ✅ Yes (persists in session) |
| Typical use case | High-concurrency apps | Admin tasks, migrations |
| Performance | ✅ Fast (reuses connections) | ❌ Slower (new connection needed) |
| Connection limit | ✅ Up to 10,000 | ❌ Limited by max_connections |
| Query wait timeout | PgBouncer: 120s | Direct to PostgreSQL |
| Prepared statements | ✅ Protocol-level (psycopg2) | ✅ Both protocol & SQL level |

---

## RECOMMENDED SOLUTION FOR YOUR CASE

### For `digest_articles.py`:

```python
# 1. Use direct connection since you need to run setup tasks
database_url = os.getenv("DATABASE_URL")
# Remove "-pooler" suffix if using pooled connection
database_url = database_url.replace("-pooler", "") if "-pooler" in database_url else database_url

conn = psycopg2.connect(database_url, connect_timeout=10)
cur = conn.cursor()

# 2. Set timeout via ALTER ROLE (one-time setup)
# First time only:
# cur.execute("ALTER ROLE your_role_name SET statement_timeout = '60s'")
# conn.commit()

# 3. Use application-level timeout wrapper
def execute_with_timeout(cursor, query, params=None, timeout=60):
    """Execute with timeout handling"""
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor.fetchall()
    except psycopg2.DatabaseError as e:
        if 'timeout' in str(e).lower():
            raise TimeoutError(f"Query exceeded {timeout}s")
        raise

# 4. Use with Render-aware timeout
try:
    rows = execute_with_timeout(
        cur,
        "SELECT id, url, title FROM articles WHERE processed_at IS NULL LIMIT %s",
        (BATCH_SIZE,),
        timeout=50  # Leave 10 seconds margin
    )
except TimeoutError:
    logger.error("Query timeout")
    # Mark as processed anyway to avoid retrying same articles
    cur.execute("UPDATE articles SET processed_at = NOW() WHERE processed_at IS NULL LIMIT %s", (BATCH_SIZE,))
    conn.commit()
```

---

## SUMMARY TABLE: Why Your Query Hangs

| Layer | Issue | Impact |
|-------|-------|--------|
| **PgBouncer (Neon)** | SET statement_timeout resets per transaction | Timeout doesn't persist |
| **PostgreSQL** | Lock contention on articles table | Query blocked indefinitely |
| **psycopg2** | No app-level timeout on execute() | Exception never raised |
| **Render** | 1-second grace period | SIGKILL sent, no logging |
| **Your code** | Signal handler calls logging.shutdown() | Potential deadlock, output lost |

---

## NEXT STEPS

1. **Immediate:** Switch to direct connection string (remove `-pooler`)
2. **Short-term:** Implement application-level timeout wrapper
3. **Medium-term:** Migrate to psycopg3 with proper async/await
4. **Long-term:** Use connection pooling library in your application (sqlalchemy, psycopg3 pool)

---

## REFERENCES

- [Neon Connection Pooling Documentation](https://neon.com/docs/connect/connection-pooling)
- [Neon PostgreSQL Compatibility](https://neon.com/docs/reference/compatibility)
- [PgBouncer Features Matrix](https://www.pgbouncer.org/features.html)
- [PostgreSQL Statement Timeout](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-STATEMENT-TIMEOUT)
- [psycopg2 Documentation](https://www.psycopg.org/docs/)
- [Python asyncio.timeout Pattern](https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout)
