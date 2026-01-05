# Implementation: Fix digest_articles.py Hanging Queries

This document provides working code you can copy-paste to fix the hanging queries in `digest_articles.py`.

---

## OPTION A: Remove "-pooler" from Connection String (Simplest)

**Implementation Time:** 2 minutes  
**Complexity:** Low  
**Risk:** Medium (uses direct connection, limited availability)

### Change Required

File: `scripts/digest_articles.py`

```python
# Around line 78-80 in __init__():

# BEFORE:
self.database_url = os.getenv("DATABASE_URL")

# AFTER:
database_url = os.getenv("DATABASE_URL")
# Remove -pooler suffix for admin operations
self.database_url = database_url.replace("-pooler", "") if "-pooler" in database_url else database_url
```

### Why This Works

- Removes the pooler layer
- Connects directly to PostgreSQL
- SET statement_timeout now persists
- Timeout exception is properly raised

### Full Example in Context

```python
class DigestEngine:
    def __init__(self):
        print("[INIT-1]", flush=True)
        sys.stdout.flush()
        
        try:
            env_count = len(os.environ)
            print("[INIT-2] env=" + str(env_count), flush=True)
            sys.stdout.flush()
            
            print("[INIT-3-DB-START]", flush=True)
            sys.stdout.flush()
            
            # ← FIX HERE
            database_url = os.getenv("DATABASE_URL")
            self.database_url = database_url.replace("-pooler", "") if "-pooler" in database_url else database_url
            
            print("[INIT-3-DB-DONE]", flush=True)
            sys.stdout.flush()
            
            # ... rest of init ...
```

---

## OPTION B: Use ALTER ROLE (Best for Production)

**Implementation Time:** 5 minutes  
**Complexity:** Low  
**Risk:** Low (persistent setting, works with pooled connections)

### Step 1: One-Time Setup (Run Once)

Use Neon SQL Editor or connect directly:

```sql
-- Find your role
SELECT current_user;
-- Returns something like: neondb_owner

-- Set timeout for the role (PERMANENT)
ALTER ROLE neondb_owner SET statement_timeout = '45s';

-- Verify it worked
SHOW statement_timeout;
-- Should show: 45s
```

### Step 2: No Changes to Your Code

After Step 1, your existing code works:

```python
# This stays the same - timeout is now automatic
self.database_url = os.getenv("DATABASE_URL")  # Can still use -pooler!
```

### How to Find Your Role Name

```python
# Add this to your script to find role name:
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("SELECT current_user;")
role_name = cur.fetchone()[0]
print(f"Your role: {role_name}")

cur.close()
conn.close()
```

---

## OPTION C: Application-Level Timeout Wrapper (Most Reliable)

**Implementation Time:** 15 minutes  
**Complexity:** Medium  
**Risk:** Low (no infrastructure changes needed)

### Full Implementation

Create a new file: `scripts/db_utils_timeout.py`

```python
"""
Database utility functions with timeout support for Neon pooled connections.
"""

import psycopg2
import logging
import time
from typing import List, Tuple, Optional, Any

logger = logging.getLogger(__name__)


class DatabaseTimeoutError(Exception):
    """Raised when database query exceeds timeout"""
    pass


def execute_with_timeout(
    conn,
    query: str,
    params: Optional[Tuple] = None,
    timeout_seconds: int = 50,
    description: str = ""
) -> List[Tuple]:
    """
    Execute a database query with timeout protection.
    
    This is a wrapper around cursor.execute() that:
    1. Sets database-level statement_timeout
    2. Measures execution time
    3. Raises clear exception on timeout
    4. Logs query performance
    
    Args:
        conn: psycopg2 connection object
        query: SQL query string
        params: Query parameters (tuple or list)
        timeout_seconds: Max execution time (default 50s for Render)
        description: Human-readable description for logging
    
    Returns:
        List of tuples (rows from query)
    
    Raises:
        DatabaseTimeoutError: If query exceeds timeout
        psycopg2.Error: If query fails for other reasons
    
    Example:
        rows = execute_with_timeout(
            conn,
            "SELECT * FROM articles WHERE processed_at IS NULL LIMIT %s",
            (10,),
            timeout_seconds=50,
            description="Fetch unprocessed articles"
        )
    """
    cur = None
    
    try:
        cur = conn.cursor()
        start_time = time.time()
        
        # Set database timeout (milliseconds)
        timeout_ms = int(timeout_seconds * 1000)
        try:
            cur.execute(f"SET statement_timeout TO {timeout_ms}")
        except psycopg2.Error:
            # If SET fails, continue anyway - app will timeout instead
            logger.warning("Could not set database statement_timeout")
        
        # Log the operation
        if description:
            logger.info(f"🔄 {description}")
        
        # Execute the query
        if params:
            cur.execute(query, params)
        else:
            cur.execute(query)
        
        # Fetch results
        rows = cur.fetchall()
        
        # Measure time
        elapsed = time.time() - start_time
        
        # Log results
        if elapsed > timeout_seconds * 0.8:
            logger.warning(
                f"⚠️  Query took {elapsed:.2f}s (near timeout of {timeout_seconds}s)"
            )
        elif elapsed > 5:
            logger.info(f"✅ Query completed in {elapsed:.2f}s, fetched {len(rows)} rows")
        else:
            logger.debug(f"✅ Query completed in {elapsed:.2f}s")
        
        return rows
        
    except psycopg2.OperationalError as e:
        error_msg = str(e).lower()
        
        if 'timeout' in error_msg or 'statement timeout' in error_msg:
            logger.error(
                f"❌ Query timeout: Exceeded {timeout_seconds}s limit\n"
                f"   Description: {description}\n"
                f"   Query: {query[:100]}..."
            )
            raise DatabaseTimeoutError(
                f"Query exceeded {timeout_seconds}s timeout"
            ) from e
        else:
            logger.error(f"❌ Operational error: {e}")
            raise
    
    except psycopg2.DatabaseError as e:
        logger.error(f"❌ Database error: {e}")
        raise
    
    except Exception as e:
        logger.error(f"❌ Unexpected error: {type(e).__name__}: {e}")
        raise
    
    finally:
        if cur:
            try:
                cur.close()
            except:
                pass


def fetch_with_retry(
    database_url: str,
    query: str,
    params: Optional[Tuple] = None,
    max_retries: int = 3,
    timeout_seconds: int = 50,
    description: str = ""
) -> List[Tuple]:
    """
    Fetch data with retry logic and timeout.
    
    Handles transient failures from:
    - Network timeouts
    - Query timeouts
    - Render orchestrator killing the process
    
    Args:
        database_url: Database connection string
        query: SQL query string
        params: Query parameters
        max_retries: Number of attempts (default 3)
        timeout_seconds: Per-query timeout
        description: Human-readable description
    
    Returns:
        List of tuples (rows from query)
    
    Raises:
        psycopg2.Error: If all retries fail
        DatabaseTimeoutError: If timeout on final attempt
    
    Example:
        rows = fetch_with_retry(
            os.getenv("DATABASE_URL"),
            "SELECT id, url FROM articles WHERE processed_at IS NULL LIMIT %s",
            (10,),
            max_retries=3,
            timeout_seconds=50,
            description="Fetch articles for processing"
        )
    """
    
    for attempt in range(max_retries):
        conn = None
        try:
            logger.info(f"🔄 Attempt {attempt + 1}/{max_retries}")
            
            # Fresh connection each attempt
            conn = psycopg2.connect(database_url, connect_timeout=5)
            
            # Execute with timeout
            rows = execute_with_timeout(
                conn,
                query,
                params=params,
                timeout_seconds=timeout_seconds,
                description=description
            )
            
            logger.info(f"✅ Success on attempt {attempt + 1}")
            return rows
            
        except DatabaseTimeoutError as e:
            logger.warning(f"⏱️  Timeout on attempt {attempt + 1}: {e}")
            
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s
                wait_seconds = 2 ** attempt
                logger.info(f"   Retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
            else:
                logger.error(f"❌ Query timeout after {max_retries} attempts")
                raise
        
        except psycopg2.OperationalError as e:
            logger.warning(f"🔌 Connection error on attempt {attempt + 1}: {e}")
            
            if attempt < max_retries - 1:
                wait_seconds = 2 ** attempt
                logger.info(f"   Retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
            else:
                logger.error(f"❌ Connection failed after {max_retries} attempts")
                raise
        
        except Exception as e:
            logger.error(f"❌ Unexpected error on attempt {attempt + 1}: {e}")
            raise
        
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
```

### Usage in digest_articles.py

Replace the database fetch code (around line 220-245):

```python
# At top of file, add import:
from scripts.db_utils_timeout import execute_with_timeout, DatabaseTimeoutError

# ... in process_batch() method ...

async def process_batch(self):
    """Process batch of articles, extract facts, deduplicate."""
    conn = None
    
    try:
        logger.info("🔄 Connecting to database...")
        conn = psycopg2.connect(self.database_url, connect_timeout=DB_CONNECT_TIMEOUT)
        logger.info("✅ Database connection established")
        
        # 1. Get Articles that need digestion
        logger.info("📋 Fetching unprocessed articles...")
        print(">>>DB_FETCH_START<<<", flush=True)
        sys.stdout.flush()
        
        query = """
            SELECT id, url, title FROM articles 
            WHERE processed_at IS NULL 
            AND url IS NOT NULL
            LIMIT %s;
        """
        
        try:
            # Use timeout wrapper instead of execute()
            rows = execute_with_timeout(
                conn,
                query,
                (BATCH_SIZE,),
                timeout_seconds=50,
                description="Fetch unprocessed articles"
            )
            
            print(f">>>DB_FETCHALL_DONE_{len(rows)}<<<", flush=True)
            sys.stdout.flush()
            
        except DatabaseTimeoutError:
            logger.error("❌ Article fetch timed out, skipping batch")
            return  # Exit gracefully
        except psycopg2.Error as e:
            logger.error(f"❌ Database fetch failed: {e}")
            raise
        
        if not rows:
            logger.info("✅ All articles processed.")
            return
        
        # ... rest of processing stays the same ...
```

---

## OPTION D: Switch to Psycopg3 with Async (Best Long-term)

**Implementation Time:** 30 minutes  
**Complexity:** High  
**Risk:** Medium (requires Psycopg3 installation and async refactoring)

### Installation

```bash
pip install psycopg[binary]  # Psycopg 3 with binary libpq
```

### Full Async Implementation

Create new file: `scripts/digest_articles_async.py`

```python
"""
Async version of digest_articles.py using psycopg3.
This provides proper timeout handling and better resource management.
"""

import asyncio
import os
import json
import logging
import sys
import signal
import trafilatura
from groq import Groq
from dotenv import load_dotenv
import psycopg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

# Constants
MAX_TOKENS = 8000
BATCH_SIZE = 10
QUERY_TIMEOUT = 50.0  # Leave margin for Render's 55-60s limit

class DigestEngineAsync:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.groq_client = Groq(api_key=self.groq_api_key)
        
        if not self.database_url or not self.groq_api_key:
            raise ValueError("DATABASE_URL or GROQ_API_KEY missing")
    
    async def fetch_articles(self, conn) -> list:
        """
        Fetch unprocessed articles with timeout.
        """
        try:
            async with conn.cursor() as cur:
                # Use asyncio.wait_for for true timeout
                query = """
                    SELECT id, url, title 
                    FROM articles 
                    WHERE processed_at IS NULL 
                    AND url IS NOT NULL
                    LIMIT %s
                """
                
                result = await asyncio.wait_for(
                    cur.execute(query, (BATCH_SIZE,)),
                    timeout=QUERY_TIMEOUT
                )
                
                rows = await cur.fetchall()
                logger.info(f"✅ Fetched {len(rows)} articles")
                return rows
                
        except asyncio.TimeoutError:
            logger.error(f"❌ Query timeout after {QUERY_TIMEOUT}s")
            raise
        except psycopg.Error as e:
            logger.error(f"❌ Database error: {e}")
            raise
    
    async def process_batch(self):
        """
        Main processing loop with proper async/timeout handling.
        """
        try:
            # Connect with timeout
            async with await asyncio.wait_for(
                psycopg.AsyncConnection.connect(self.database_url),
                timeout=10.0
            ) as conn:
                logger.info("✅ Connected to database")
                
                # Fetch articles with timeout
                rows = await self.fetch_articles(conn)
                
                if not rows:
                    logger.info("✅ All articles processed")
                    return
                
                # Process each article
                for aid, url, title in rows:
                    logger.info(f"Processing {aid}: {title[:30]}...")
                    
                    try:
                        # Your processing logic here
                        # ... extract facts, insert into database, etc.
                        pass
                    
                    except asyncio.TimeoutError:
                        logger.error(f"Processing timed out for article {aid}")
                    except Exception as e:
                        logger.error(f"Error processing {aid}: {e}")
        
        except asyncio.TimeoutError:
            logger.error("Failed to connect to database")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            raise

async def main():
    """
    Main entry point with graceful shutdown.
    """
    engine = DigestEngineAsync()
    
    # Set overall timeout (leave 5 seconds margin before Render's SIGKILL)
    try:
        await asyncio.wait_for(
            engine.process_batch(),
            timeout=55.0
        )
        logger.info("✅ Batch processing completed")
        sys.exit(0)
    
    except asyncio.TimeoutError:
        logger.error("❌ Processing exceeded 55 second limit")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Script failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## OPTION E: Minimal Fix - Just Add Timeout Wrapper (Least Invasive)

**Implementation Time:** 5 minutes  
**Complexity:** Low  
**Risk:** Very Low

Add this helper function at the top of `digest_articles.py`:

```python
import time

def safe_execute(cur, query, params=None, timeout_sec=50):
    """
    Execute query with timeout logging.
    Does NOT prevent hanging, but logs how long queries take.
    """
    start = time.time()
    
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    
    elapsed = time.time() - start
    
    if elapsed > 30:
        logger.warning(f"⚠️  Query took {elapsed:.1f}s (slow!)")
    elif elapsed > 10:
        logger.info(f"Query took {elapsed:.1f}s")
    
    return cur.fetchall()
```

Use it:

```python
# Replace:
cur.execute(query, (BATCH_SIZE,))
rows = cur.fetchall()

# With:
rows = safe_execute(cur, query, (BATCH_SIZE,))
```

**Note:** This doesn't actually prevent hanging, just measures it. Use Option A-D for real timeout protection.

---

## WHICH OPTION TO CHOOSE?

### For Quick Hotfix (Production Issue)
→ **Option A** (Remove -pooler, 2 min) or **Option B** (ALTER ROLE, 5 min)

### For Reliable Long-term Solution
→ **Option C** (Timeout wrapper, 15 min)

### For Best Architecture
→ **Option D** (Psycopg3 async, 30 min) + **Option B** (ALTER ROLE)

### For Minimal Risk
→ **Option E** (Logging only, 5 min) - won't solve the problem but will show you where queries are slow

---

## TESTING YOUR FIX

After implementing any option, test with:

```python
# Add this test to digest_articles.py temporarily:

async def test_timeout():
    """Test that timeout works before running actual processing"""
    
    logger.info("🧪 Testing timeout mechanism...")
    
    try:
        conn = psycopg2.connect(self.database_url, connect_timeout=10)
        cur = conn.cursor()
        
        # This should timeout (sleep for 120 seconds with 5 second timeout)
        logger.info("   Attempting query that will timeout...")
        
        try:
            cur.execute("SET statement_timeout TO 5000")  # 5 seconds
            cur.execute("SELECT pg_sleep(120)")  # 120 second sleep
            cur.fetchall()
            logger.error("❌ TEST FAILED: Query should have timed out")
            
        except psycopg2.DatabaseError as e:
            if 'timeout' in str(e).lower():
                logger.info("✅ TEST PASSED: Timeout correctly raised exception")
            else:
                logger.error(f"❌ TEST FAILED: Wrong error - {e}")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ TEST FAILED: Unexpected error - {e}")

# In main():
# await test_timeout()  # Uncomment to test
# await engine.process_batch()
```

---

## MONITORING YOUR QUERIES

Add monitoring to see what's happening:

```python
async def monitor_database(database_url, interval=5):
    """
    Monitor database for slow/hanging queries.
    Run in background while processing.
    """
    while True:
        try:
            conn = psycopg2.connect(database_url)
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    pid, 
                    query, 
                    state,
                    EXTRACT(EPOCH FROM (now() - query_start))::int as seconds
                FROM pg_stat_activity
                WHERE state != 'idle'
                AND query NOT LIKE '%pg_stat_activity%'
                AND EXTRACT(EPOCH FROM (now() - query_start)) > 2
                ORDER BY query_start DESC
            """)
            
            slow_queries = cur.fetchall()
            if slow_queries:
                logger.warning(f"⚠️  {len(slow_queries)} slow queries:")
                for pid, query, state, seconds in slow_queries:
                    logger.warning(f"   PID {pid}: {state} for {seconds}s - {query[:50]}...")
            
            cur.close()
            conn.close()
        
        except Exception as e:
            logger.warning(f"Monitor error: {e}")
        
        await asyncio.sleep(interval)

# In main(), run monitoring in background:
# monitor_task = asyncio.create_task(monitor_database(engine.database_url))
```

---

## SUMMARY

| Option | Speed | Complexity | Reliability | Recommendation |
|--------|-------|-----------|-------------|-----------------|
| A: Remove -pooler | 2 min | Very Low | Medium | **Quick fix** |
| B: ALTER ROLE | 5 min | Very Low | High | **Best quick fix** |
| C: Timeout wrapper | 15 min | Medium | High | **Recommended** |
| D: Psycopg3 async | 30 min | High | Very High | **Best architecture** |
| E: Just logging | 5 min | Very Low | Very Low | **Diagnosis only** |

**My recommendation:** Start with **Option B** (ALTER ROLE) for immediate stability, then implement **Option C** (timeout wrapper) for robustness.

