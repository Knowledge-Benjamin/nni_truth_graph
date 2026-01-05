# Neon Pooled Connection Timeout - Quick Fix Guide

**TL;DR:** `SET statement_timeout` doesn't work on Neon's pooled connections. Use these patterns instead.

---

## THE PROBLEM

```python
# ❌ THIS DOESN'T WORK ON POOLED CONNECTIONS
conn = psycopg2.connect("postgresql://user@....-pooler.us-east-2.aws.neon.tech/...")
cur = conn.cursor()
cur.execute("SET statement_timeout TO 60000")  # ← Lost after transaction ends
cur.execute("SELECT ...")  # ← Query can hang indefinitely
```

**Why:** Neon pooler is in transaction mode. `SET` statements only apply to current transaction, then the connection returns to the pool without the setting.

---

## QUICK FIX #1: Remove "-pooler" from Connection String (Easiest)

```python
import os

# Instead of:
# postgresql://user@ep-cool-darkness-pooler.us-east-2.aws.neon.tech/dbname

database_url = os.getenv("DATABASE_URL")
# Use DIRECT connection for admin/setup tasks
direct_url = database_url.replace("-pooler", "") if "-pooler" in database_url else database_url

conn = psycopg2.connect(direct_url)
cur = conn.cursor()
cur.execute("SET statement_timeout TO 60000")  # ✅ NOW IT WORKS
cur.execute("SELECT ...")
```

**Pros:** Simple, works immediately  
**Cons:** Uses one of your limited direct connections

---

## QUICK FIX #2: Set Timeout at Role Level (Best)

**Step 1: One-time setup (via direct connection or Neon SQL Editor)**

```sql
-- Find your role name
SELECT current_user;  -- e.g., "neon_user"

-- Set timeout for this role
ALTER ROLE neon_user SET statement_timeout = '60s';
```

**Step 2: Your code (pooled connection works now!)**

```python
conn = psycopg2.connect("postgresql://user@...-pooler.us-east-2.aws.neon.tech/...")
cur = conn.cursor()
# statement_timeout is now automatically 60 seconds for all queries!
cur.execute("SELECT ...")
```

**Pros:** Works with pooled connections, persists forever  
**Cons:** Global setting for all queries from that role

---

## QUICK FIX #3: Application-Level Timeout (Most Reliable)

```python
import psycopg2
import logging

logger = logging.getLogger(__name__)

def execute_with_timeout(conn, query, params=None, timeout_seconds=60):
    """
    Execute query with timeout protection.
    
    Args:
        conn: psycopg2 connection
        query: SQL query string
        params: Query parameters (tuple)
        timeout_seconds: Max execution time
    
    Returns:
        List of rows from query result
    
    Raises:
        TimeoutError: If query exceeds timeout
        psycopg2.Error: If query fails
    """
    cur = conn.cursor()
    
    try:
        # Try to set database timeout (might not persist on pooled)
        cur.execute("SET statement_timeout TO %s", (int(timeout_seconds * 1000),))
        
        # Execute the actual query
        if params:
            cur.execute(query, params)
        else:
            cur.execute(query)
        
        rows = cur.fetchall()
        logger.info(f"Query completed in time, fetched {len(rows)} rows")
        return rows
        
    except psycopg2.OperationalError as e:
        error_msg = str(e).lower()
        if 'timeout' in error_msg or 'statement timeout' in error_msg:
            logger.error(f"Query exceeded {timeout_seconds}s timeout")
            raise TimeoutError(f"Query timeout after {timeout_seconds}s") from e
        else:
            logger.error(f"Operational error: {e}")
            raise
            
    except psycopg2.DatabaseError as e:
        logger.error(f"Database error: {e}")
        raise
        
    finally:
        cur.close()

# USAGE:
try:
    rows = execute_with_timeout(
        conn,
        "SELECT id, url FROM articles WHERE processed_at IS NULL LIMIT %s",
        (10,),
        timeout_seconds=60
    )
    for row_id, url in rows:
        print(f"Processing article {row_id}: {url}")
        
except TimeoutError:
    logger.error("Article fetch timed out, skipping batch")
except psycopg2.Error as e:
    logger.error(f"Database error: {e}")
```

---

## QUICK FIX #4: Async with Proper Timeout (Best for Render)

```python
import asyncio
import psycopg
import logging

logger = logging.getLogger(__name__)

async def fetch_articles_async(database_url, batch_size=10, timeout_seconds=55):
    """
    Fetch articles with proper async timeout.
    
    Note: Timeout is 55 seconds to leave 5 seconds margin before Render's SIGKILL
    """
    try:
        async with await psycopg.AsyncConnection.connect(database_url) as conn:
            async with conn.cursor() as cur:
                try:
                    # Use asyncio.wait_for for true timeout
                    query = """
                        SELECT id, url, title 
                        FROM articles 
                        WHERE processed_at IS NULL 
                        LIMIT %s
                    """
                    
                    # Execute with timeout
                    result = await asyncio.wait_for(
                        cur.execute(query, (batch_size,)),
                        timeout=timeout_seconds
                    )
                    
                    rows = await cur.fetchall()
                    logger.info(f"✅ Fetched {len(rows)} articles")
                    return rows
                    
                except asyncio.TimeoutError:
                    logger.error(
                        f"❌ Query exceeded {timeout_seconds}s timeout. "
                        "Process will be killed by Render."
                    )
                    sys.exit(1)
                    
    except psycopg.OperationalError as e:
        logger.error(f"❌ Connection failed: {e}")
        raise

# USAGE:
if __name__ == "__main__":
    import sys
    import os
    
    database_url = os.getenv("DATABASE_URL")
    
    try:
        rows = asyncio.run(
            fetch_articles_async(
                database_url,
                batch_size=10,
                timeout_seconds=55  # Important: leave margin for Render
            )
        )
    except Exception as e:
        logger.error(f"Script failed: {e}")
        sys.exit(1)
```

---

## QUICK FIX #5: Retry Logic for Render Timeouts

```python
import psycopg2
import logging
import time
from typing import List, Tuple

logger = logging.getLogger(__name__)

def fetch_with_retry(
    database_url: str,
    query: str,
    params: Tuple = None,
    max_retries: int = 3,
    timeout_seconds: int = 50
) -> List[Tuple]:
    """
    Execute query with retry logic and timeout.
    
    Handles:
    - Query timeouts
    - Connection failures
    - Render orchestrator timeouts
    """
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_retries}")
            
            # Connect fresh each time (avoid stale connections)
            conn = psycopg2.connect(database_url, connect_timeout=5)
            cur = conn.cursor()
            
            # Set timeout for this query
            cur.execute("SET statement_timeout TO %s", (int(timeout_seconds * 1000),))
            
            # Execute query
            start = time.time()
            if params:
                cur.execute(query, params)
            else:
                cur.execute(query)
            
            rows = cur.fetchall()
            elapsed = time.time() - start
            
            logger.info(f"✅ Query completed in {elapsed:.2f}s, got {len(rows)} rows")
            
            cur.close()
            conn.close()
            return rows
            
        except (psycopg2.OperationalError, psycopg2.DatabaseError) as e:
            error_msg = str(e).lower()
            is_timeout = 'timeout' in error_msg or 'statement timeout' in error_msg
            
            logger.warning(
                f"{'Timeout' if is_timeout else 'Error'} on attempt {attempt + 1}: {e}"
            )
            
            # Clean up this connection
            try:
                cur.close()
                conn.close()
            except:
                pass
            
            # If this was the last attempt, raise the error
            if attempt == max_retries - 1:
                logger.error(f"❌ Query failed after {max_retries} attempts")
                raise
            
            # Wait before retrying (exponential backoff)
            wait_seconds = 2 ** attempt  # 1, 2, 4
            logger.info(f"Waiting {wait_seconds}s before retry...")
            time.sleep(wait_seconds)
        
        except Exception as e:
            logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
            raise

# USAGE:
database_url = os.getenv("DATABASE_URL")

try:
    rows = fetch_with_retry(
        database_url,
        query="SELECT id, url FROM articles WHERE processed_at IS NULL LIMIT %s",
        params=(10,),
        max_retries=3,
        timeout_seconds=50
    )
    
    for row_id, url in rows:
        print(f"Process article {row_id}")
        
except Exception as e:
    logger.error(f"Could not fetch articles: {e}")
    sys.exit(1)
```

---

## QUICK FIX #6: Fix Your Signal Handler (Don't Call logging.shutdown())

❌ **Bad:**

```python
import signal
import logging

def signal_handler(signum, frame):
    logging.shutdown()  # ← Can deadlock with asyncio!
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
```

✅ **Good:**

```python
import signal
import sys
import logging

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    """Handle signals without deadlocking"""
    signal_name = signal.Signals(signum).name
    
    # Write to stderr directly (non-blocking)
    sys.stderr.write(f"\n[SIGNAL] Received {signal_name}\n")
    sys.stderr.flush()
    
    # Log if logger is available
    try:
        logger.info(f"Received {signal_name}, shutting down")
    except:
        pass  # Ignore logging errors during shutdown
    
    # Exit cleanly (don't call logging.shutdown()!)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
```

---

## DIAGNOSTIC: Check if Query is Hanging

```python
import psycopg2
import time
import logging

logger = logging.getLogger(__name__)

def diagnose_slow_queries(database_url):
    """Check what queries are running and taking time"""
    
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                pid,
                query,
                state,
                EXTRACT(EPOCH FROM (now() - query_start))::int AS seconds_running
            FROM pg_stat_activity
            WHERE state != 'idle'
            AND query NOT LIKE '%pg_stat_activity%'
            ORDER BY query_start
        """)
        
        rows = cur.fetchall()
        
        if not rows:
            logger.info("✅ No slow queries found")
        else:
            logger.warning(f"⚠️  Found {len(rows)} potentially slow queries:")
            for pid, query, state, duration in rows:
                logger.warning(
                    f"  PID {pid}: {state} for {duration}s\n"
                    f"    Query: {query[:100]}..."
                )
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Failed to diagnose: {e}")

# Run it:
diagnose_slow_queries(os.getenv("DATABASE_URL"))
```

---

## DECISION TREE: Which Fix to Use?

```
┌─ Is this a one-time setup task (schema migration, etc.)?
│  ├─ YES: Use QUICK FIX #1 (direct connection)
│  └─ NO: Continue
│
├─ Can you afford downtime to set ALTER ROLE once?
│  ├─ YES: Use QUICK FIX #2 (ALTER ROLE) ← BEST LONG-TERM
│  └─ NO: Continue
│
├─ Is this a critical script that must work?
│  ├─ YES: Use QUICK FIX #5 (retry logic + async)
│  └─ NO: Continue
│
└─ Just make it work?
   └─ Use QUICK FIX #3 (app-level timeout wrapper)
```

---

## SUMMARY: Why Your Queries Hang

| Component | Issue | Fix |
|-----------|-------|-----|
| **Neon Pooler** | SET doesn't persist across transactions | Use ALTER ROLE or direct connection |
| **PgBouncer** | Transaction mode resets session state | Expect this, design accordingly |
| **PostgreSQL** | statement_timeout requires setting first | Set at role level, not per-query |
| **Your Code** | No timeout handling | Add asyncio.wait_for() or retry logic |
| **Render** | Kills process after ~1 second | Set timeouts <55 seconds |

---

## KEY NUMBERS FOR RENDER

- **Render grace period:** 1-5 seconds (varies)
- **Safe query timeout:** 50 seconds max
- **Recommended query timeout:** 30-45 seconds
- **Margin for safety:** 5-10 seconds

Always use `timeout=50` instead of `timeout=60` when running on Render.

---

## TESTING YOUR FIX

```python
# Test that timeout actually works:
import time

def test_timeout():
    """Verify timeout works before deploying"""
    
    print("Testing timeout...")
    
    try:
        rows = execute_with_timeout(
            conn,
            "SELECT pg_sleep(120)",  # Sleep for 2 minutes
            timeout_seconds=5  # Should timeout
        )
        print("❌ FAILED: Query should have timed out!")
        
    except TimeoutError:
        print("✅ PASSED: Query correctly timed out")
    
    except Exception as e:
        print(f"❌ FAILED: Unexpected error: {e}")

# Run before deploying:
test_timeout()
```

---

## DON'T FORGET

- Always close database connections in finally blocks
- Log with full exception details for debugging
- Test timeout handling locally before deploying
- Monitor Render logs for "SIGKILL" or "killed" messages
- Use direct connection for schema changes/migrations
- Set role-level defaults for production
