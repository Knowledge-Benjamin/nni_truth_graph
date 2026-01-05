# Quick Reference: Silent Failure Root Cause & Immediate Fixes

**TL;DR:** Signal handler + logging deadlock + missing timeouts = silent process death on Render

---

## THE PROBLEM IN ONE SENTENCE

Your signal handler calls `logging.shutdown()` while the logging module's lock is held by a blocking database call, causing deadlock that looks like silence to the orchestrator, which then sends SIGKILL and loses all output.

---

## THREE IMMEDIATE FIXES (30 minutes)

### Fix #1: Remove logging from signal handler

**File:** `scripts/digest_articles.py` - Lines 14-22

**BEFORE:**

```python
def signal_handler(signum, frame):
    msg = "\n[SIGNAL-HANDLER] Received signal - flushing and exiting gracefully\n"
    sys.stdout.write(msg)
    sys.stderr.write(msg)
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()  # ← DELETE THIS LINE
    sys.exit(0)
```

**AFTER:**

```python
def signal_handler(signum, frame):
    msg = "\n[SIGNAL-HANDLER] Received signal - exiting\n"
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stderr.write(msg)
    sys.stderr.flush()
    # Don't call logging.shutdown() - causes deadlock with asyncio
    sys.exit(0)
```

---

### Fix #2: Add timeout to LLM executor call

**File:** `scripts/digest_articles.py` - Line 243

**BEFORE:**

```python
result_json = await loop.run_in_executor(None, self.extract_facts_with_llm, full_text)
```

**AFTER:**

```python
try:
    result_json = await asyncio.wait_for(
        loop.run_in_executor(None, self.extract_facts_with_llm, full_text),
        timeout=30.0  # 30 second timeout for LLM
    )
except asyncio.TimeoutError:
    logger.error(f"LLM extraction timeout for {aid}")
    result_json = {"facts": []}  # Fallback
    continue
```

---

### Fix #3: Add timeout to database connection

**File:** `render.yaml` (or environment settings)

**ADD OR MODIFY DATABASE_URL:**

```yaml
DATABASE_URL: "postgresql://user:pass@host/db?options=-c%20statement_timeout%3D60000"
```

Or in Python connection code:

```python
conn = psycopg2.connect(
    self.database_url,
    connect_timeout=DB_CONNECT_TIMEOUT,
    options="-c statement_timeout=60000"  # 60 second query timeout
)
```

---

## VERIFICATION

After making changes:

1. **Test locally:**

   ```bash
   python scripts/digest_articles.py
   # Should complete successfully or timeout gracefully
   ```

2. **Deploy to Render:**

   ```bash
   git add scripts/digest_articles.py render.yaml
   git commit -m "fix: remove logging from signal handler, add executor timeouts"
   git push
   ```

3. **Monitor logs:**
   ```bash
   render logs --follow
   # Should see completion message or timeout message, not silent death
   ```

---

## WHY THIS FIXES IT

| Issue           | Cause                              | Fix                           | Result                             |
| --------------- | ---------------------------------- | ----------------------------- | ---------------------------------- |
| Silent death    | Logging deadlock in signal handler | Remove `logging.shutdown()`   | Signal handler no longer hangs     |
| No error output | Process killed before timeout      | Add 30s executor timeout      | Clear error message before SIGKILL |
| Partial output  | Output buffer lost on SIGKILL      | Combined fixes reduce hanging | Less likely to hit SIGKILL         |
| Database hangs  | No query timeout                   | Add `statement_timeout=60000` | Queries fail fast instead of hang  |

---

## EXPECTED BEHAVIOR AFTER FIXES

**Success Path:**

```
✅ Script starts
✅ Database connects
✅ Articles fetched
✅ First article processed
✅ Facts extracted via LLM
✅ Facts stored
✅ Repeat for remaining articles
✅ Script completes naturally with exit code 0
```

**Failure Path (instead of silent death):**

```
✅ Script starts
✅ Database connects
✅ Articles fetched
✅ First article: fetch content with 10s timeout
❌ Timeout! No content after 10 seconds
⚠️  [WARNING] Content fetch timeout - recorded in logs
✅ Mark article as processed anyway
✅ Continue to next article
✅ Eventually complete or hit orchestrator timeout (clear error in logs)
```

---

## IF YOU STILL HAVE ISSUES AFTER THESE FIXES

**Check these in order:**

1. **Verify database credentials** - Wrong password causes hang, not error

   ```bash
   render logs | grep "password authentication failed"
   ```

2. **Check network connectivity** - Render to your database

   ```bash
   # In Render dashboard: check private network settings
   # Verify DATABASE_URL is correct for Render environment
   ```

3. **Monitor execution time** - If script still slow

   ```bash
   render logs | grep "Processing.*seconds"
   # Should see reasonable timing per article
   ```

4. **Check orchestrator timeout** - Render default is usually 5-10 minutes
   ```bash
   # If your script legitimately needs > timeout, increase it in render.yaml
   ```

---

## ADDITIONAL OPTIONAL FIXES (Lower Priority)

**To make it even more robust:**

```python
# 1. Wrap all network calls with timeout
try:
    downloaded = await asyncio.wait_for(
        loop.run_in_executor(None, trafilatura.fetch_url, url),
        timeout=10.0
    )
except asyncio.TimeoutError:
    logger.warning(f"Fetch timeout for {url}")
    continue

# 2. Add overall batch timeout
try:
    await asyncio.wait_for(
        engine.process_batch(),
        timeout=600.0  # 10 minute total timeout
    )
except asyncio.TimeoutError:
    logger.error("Batch processing took > 10 minutes")
    sys.exit(1)

# 3. Set minimum Render timeout
# In render.yaml:
# timeout: 600  # 10 minutes
```

---

## ROOT CAUSE SUMMARY (For Your Knowledge)

**What was happening:**

1. Script runs normally until "Fetching articles" log
2. Logging module acquires lock to write that message
3. Database query takes a long time (blocks event loop)
4. After 5-10 min, Render sends SIGTERM
5. Signal handler tries to call `logging.shutdown()`
6. DEADLOCK: logging.shutdown() tries to acquire the lock already held by database query
7. Process appears hung to Render orchestrator
8. Render sends SIGKILL (signal 9)
9. Process dies instantly, all buffered output lost
10. Orchestrator sees "Failed" with only 538 chars of output

**Why it works locally:**

- Local database is instant, no long queries
- Signal handlers don't fire (no orchestrator timeout)
- Process completes naturally, all buffers flushed

**Why it fails on Render:**

- Network latency + multi-tenant DB = slow queries
- Orchestrator enforces timeouts + sends SIGTERM
- Signal handler + logging deadlock
- SIGKILL loses all output

---

## FILES TO MODIFY

1. **`scripts/digest_articles.py`** (Main fix)
   - Remove `logging.shutdown()` from signal handler
   - Add `asyncio.wait_for()` timeout to executor call
2. **`render.yaml`** (Database timeout)
   - Add `statement_timeout` to DATABASE_URL
3. **`Dockerfile`** (Already correct)
   - `ENV PYTHONUNBUFFERED=1` ✓
   - `python -u` flag ✓

---

## TESTING CHECKLIST

- [ ] Made the 3 critical fixes above
- [ ] Tested locally: `python scripts/digest_articles.py`
- [ ] Committed changes: `git commit -am "fix: timeout and signal handler issues"`
- [ ] Deployed to Render: `git push`
- [ ] Monitored first run: `render logs --follow`
- [ ] Verified script completes OR times out with clear error message
- [ ] Checked exit code: `echo $?` (should be 0 on success or clear error)

---

**Status: READY FOR DEPLOYMENT**

These three changes directly address the root cause and will prevent silent failure.
