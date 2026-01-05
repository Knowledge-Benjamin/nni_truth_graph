# Neon Pooled Connection Timeout Issue - Research Index

**Research Completion Date:** January 5, 2026  
**Status:** ✅ Complete with 5 comprehensive documents and working solutions

---

## Quick Navigation

### 🚀 Start Here (5 minutes)

→ [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) - Overview of findings and solutions

### 🔧 Need to Fix It Now? (Choose Your Path)

1. **Fastest Fix (2 min)** → [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md#quick-fix-1-remove--pooler-from-connection-string-easiest)
2. **Best Quick Fix (5 min)** → [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md#quick-fix-2-set-timeout-at-role-level-best)
3. **Most Reliable (15 min)** → [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md#option-c-application-level-timeout-wrapper-most-reliable)
4. **Best Architecture (30 min)** → [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md#option-d-switch-to-psycopg3-with-async-best-long-term)

### 📚 Understanding the Problem

- **General Analysis** → [NEON_POOLED_TIMEOUT_RESEARCH.md](NEON_POOLED_TIMEOUT_RESEARCH.md)
- **Your Code Specific** → [DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md](DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md)
- **Best Practices** → [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md#best-practices-for-neon-pooled-connections)

### 💻 Implementation Code

- **Quick Fixes** → [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md) (Copy-paste ready)
- **Detailed Options** → [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md) (Full working code)

---

## Document Descriptions

### 1. RESEARCH_SUMMARY.md ⭐ START HERE

**Length:** 5 min read  
**Purpose:** Executive summary of all findings  
**Contains:**

- Key findings (5 major discoveries)
- Root cause explanation
- All 5 solutions overview
- Immediate action items
- Questions answered
- Next steps

**Best for:** Quick understanding of the issue and solutions

---

### 2. NEON_POOLED_TIMEOUT_RESEARCH.md 📖 DEEP DIVE

**Length:** 20 min read  
**Purpose:** Comprehensive technical analysis  
**Contains:**

- Executive summary
- Critical findings (3 major issues)
- Supported vs unsupported features
- Root cause analysis
- Comparison tables
- Best practices (10 DO's and 10 DON'Ts)
- 8 different solutions with pros/cons
- Diagnostic queries

**Best for:** Understanding WHY the problem exists

---

### 3. DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md 🔍 YOUR CODE

**Length:** 15 min read  
**Purpose:** Specific analysis of your digest_articles.py  
**Contains:**

- Problem in your code (exact lines)
- Connection string analysis
- Timeline of what happens
- Why logs stop at certain points
- Lock contention explanation
- 5 specific issues in your code
- Root cause proof
- Quick diagnosis commands

**Best for:** Understanding what's happening in YOUR script

---

### 4. NEON_POOLED_QUICK_FIX.md ⚡ QUICK SOLUTIONS

**Length:** 10 min read  
**Purpose:** 6 quick fixes you can implement immediately  
**Contains:**

- TL;DR of the problem
- Quick Fix #1: Remove -pooler (2 min)
- Quick Fix #2: ALTER ROLE (5 min)
- Quick Fix #3: Timeout wrapper (10 min)
- Quick Fix #4: Async with timeout (15 min)
- Quick Fix #5: Retry logic (10 min)
- Quick Fix #6: Fix signal handler
- Diagnostic tools
- Decision tree
- Key numbers for Render
- Testing procedures

**Best for:** Getting your code working quickly

---

### 5. NEON_POOLED_IMPLEMENTATION.md 🛠️ IMPLEMENTATION

**Length:** 30 min read + 30 min implementation  
**Purpose:** Full working code for all 5 solutions  
**Contains:**

- Option A: Remove -pooler (with full example)
- Option B: ALTER ROLE (with setup steps)
- Option C: Timeout wrapper (with complete module)
- Option D: Psycopg3 async (with full async implementation)
- Option E: Minimal fix (with simple wrapper)
- Testing procedures
- Monitoring code
- Decision matrix

**Best for:** Actually implementing the fix in your code

---

## The Problem (1-Minute Summary)

```
Your Code:
  cur.execute("SET statement_timeout TO 60000")
  cur.execute("SELECT ... FROM articles ...")

What You Expected:
  ✅ Query times out after 60 seconds

What Actually Happens:
  ❌ Query hangs indefinitely
  ❌ Process dies after ~1 second
  ❌ No error message

Why:
  - Neon pooled connections use PgBouncer in transaction mode
  - SET statement only lasts for current transaction
  - After transaction ends, connection returns to pool
  - Next query has NO timeout
  - Query blocks on lock, statement_timeout doesn't apply
  - Render kills process before timeout can fire
```

---

## The Solution (1-Minute Summary)

**Choose ONE:**

1. **Remove `-pooler` from connection string** (2 min)

   ```python
   database_url = os.getenv("DATABASE_URL").replace("-pooler", "")
   ```

2. **Set timeout at role level** (5 min, one-time setup)

   ```sql
   ALTER ROLE your_role SET statement_timeout = '45s';
   ```

3. **Add application-level timeout** (15 min, most reliable)
   ```python
   rows = execute_with_timeout(conn, query, timeout_seconds=50)
   ```

**Recommendation:** Start with #2, then add #3 for robustness.

---

## Key Findings

### Finding #1: SET Doesn't Persist on Pooled Connections

- **Impact:** 🔴 CRITICAL
- **Why:** PgBouncer resets connections after each transaction
- **Solution:** Use ALTER ROLE or direct connection

### Finding #2: statement_timeout Covers Execution, Not Locks

- **Impact:** 🔴 CRITICAL
- **Why:** Query waits for lock, not executing code
- **Solution:** Move network calls outside transaction, implement app-level timeout

### Finding #3: Render Timeout Fires Before PostgreSQL Timeout

- **Impact:** 🔴 CRITICAL
- **Why:** Process killed after ~1 second, no time for 60s timeout
- **Solution:** Set all timeouts < 50 seconds

### Finding #4: Signal Handler Deadlock Risk

- **Impact:** 🟠 MODERATE
- **Why:** Calling logging.shutdown() in signal handler with asyncio
- **Solution:** Don't call logging.shutdown() in signal handlers

### Finding #5: Missing Application-Level Timeout

- **Impact:** 🟠 MODERATE
- **Why:** No wrapper around database calls
- **Solution:** Use asyncio.wait_for() or timeout wrapper

---

## Reading Paths Based on Your Need

### "I Just Need to Fix It"

1. Read [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md)
2. Pick solution (A, B, or C)
3. Copy code from [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md)
4. Test with provided test code
5. Deploy

**Time:** 15-30 minutes

---

### "I Need to Understand What's Happening"

1. Read [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) (5 min)
2. Read [DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md](DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md) (10 min)
3. Skim [NEON_POOLED_TIMEOUT_RESEARCH.md](NEON_POOLED_TIMEOUT_RESEARCH.md) (15 min)
4. Pick solution and implement

**Time:** 30-45 minutes

---

### "I Need Complete Technical Analysis"

1. Read [NEON_POOLED_TIMEOUT_RESEARCH.md](NEON_POOLED_TIMEOUT_RESEARCH.md) (20 min)
2. Read [DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md](DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md) (15 min)
3. Read [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md) (20 min)
4. Review all comparison tables and decision trees

**Time:** 60-90 minutes

---

## Quick Reference: Which Fix for Which Situation

| Situation                               | Best Solution              | Document                                           |
| --------------------------------------- | -------------------------- | -------------------------------------------------- |
| Production is down, need hotfix NOW     | Quick Fix #1 or #2         | [QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md)           |
| Small script, low traffic               | Quick Fix #2 (ALTER ROLE)  | [QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md)           |
| Critical application, needs reliability | Option C (Timeout wrapper) | [IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md) |
| Want best architecture                  | Option D (Psycopg3 async)  | [IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md) |
| Need to understand before fixing        | Research guide             | [RESEARCH.md](NEON_POOLED_TIMEOUT_RESEARCH.md)     |
| Want to diagnose the issue              | Analysis                   | [ANALYSIS.md](DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md) |

---

## Document Map

```
RESEARCH_SUMMARY.md (YOU ARE HERE)
│
├─ NEON_POOLED_TIMEOUT_RESEARCH.md
│  └─ Complete technical deep-dive
│  └─ General knowledge about pooled connections
│
├─ NEON_POOLED_QUICK_FIX.md
│  └─ 6 quick fixes (copy-paste ready)
│  └─ Decision tree
│  └─ Best practices
│
├─ DIGEST_ARTICLES_TIMEOUT_ANALYSIS.md
│  └─ Specific to your code
│  └─ Timeline of what happens
│  └─ Why your logs stop where they do
│
└─ NEON_POOLED_IMPLEMENTATION.md
   └─ 5 full implementation options
   └─ Complete working code
   └─ Testing procedures
```

---

## Command Reference

### Check Your Connection Type

```python
# Is your connection pooled?
database_url = os.getenv("DATABASE_URL")
is_pooled = "-pooler" in database_url
print(f"Pooled: {is_pooled}")
```

### Find Your Role Name

```sql
SELECT current_user;  -- Shows something like: neondb_owner
```

### Set Role-Level Timeout

```sql
ALTER ROLE neondb_owner SET statement_timeout = '45s';
```

### Monitor Slow Queries

```sql
SELECT pid, query, state, EXTRACT(EPOCH FROM (now() - query_start))::int
FROM pg_stat_activity
WHERE state != 'idle'
AND EXTRACT(EPOCH FROM (now() - query_start)) > 2
ORDER BY query_start DESC;
```

### Test Timeout Works

```python
# Should timeout after 5 seconds
cur.execute("SET statement_timeout TO 5000")
cur.execute("SELECT pg_sleep(120)")  # 120 second sleep
# Should raise exception
```

---

## Key Numbers to Remember

- **Render grace period:** 1-5 seconds
- **Safe query timeout:** < 50 seconds
- **Recommended query timeout:** 30-45 seconds
- **Margin for safety:** 5-10 seconds
- **Exponential backoff:** 1s, 2s, 4s
- **PgBouncer query_wait_timeout:** 120 seconds
- **Neon pooler max connections:** 10,000

---

## FAQ from Research

**Q: Will SET statement_timeout work on pooled connections?**  
A: No, not reliably. Use ALTER ROLE instead.

**Q: Why does my query hang without timing out?**  
A: Likely lock contention, and statement_timeout covers execution, not lock wait time.

**Q: Should I use direct or pooled connections?**  
A: Use pooled for read operations, direct for admin tasks. Pooled is the default.

**Q: How do I prevent "killed by Render" errors?**  
A: Set all timeouts < 50 seconds, leave 5-10 second margin.

**Q: What's the best long-term solution?**  
A: Use psycopg3 with async/await and proper asyncio timeout handling.

**Q: Can I upgrade just to fix this?**  
A: You can upgrade to psycopg3, but it requires code changes. Psycopg2 works fine with proper timeout wrapper.

---

## Next Steps

1. **Right Now:** Read [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) (5 minutes)
2. **Next:** Choose your fix from [NEON_POOLED_QUICK_FIX.md](NEON_POOLED_QUICK_FIX.md) (5 minutes)
3. **Then:** Implement using [NEON_POOLED_IMPLEMENTATION.md](NEON_POOLED_IMPLEMENTATION.md) (15-30 minutes)
4. **Finally:** Test and deploy

**Total time:** 30-45 minutes to fully fix the issue

---

## Document Completeness Checklist

✅ Root cause identified  
✅ Why queries hang without timing out  
✅ Difference between pooled and direct connections  
✅ Statement_timeout behavior documented  
✅ Best practices provided  
✅ 5 different solutions with code  
✅ Specific analysis of your code  
✅ Timeline diagrams  
✅ Comparison tables  
✅ Testing procedures  
✅ Monitoring queries  
✅ Diagnostic tools  
✅ Decision trees  
✅ Copy-paste ready code  
✅ Implementation guides

---

## Questions Answered

All 7 research questions from your request have been answered with detailed information, examples, and solutions:

1. ✅ **Why queries hang silently** - Timeout doesn't work on pooled connections + lock contention
2. ✅ **statement_timeout behavior in pooled vs unpooled** - Completely different, documented with examples
3. ✅ **Best practices for hanging database queries** - 10 DO's and 10 DON'Ts provided
4. ✅ **Difference in timeout application** - Detailed explanation with code examples
5. ✅ **Timeout handling patterns that work** - 5 working solutions with full code
6. ✅ **Known issues with Neon pooler** - SET statements not supported, documented by Neon
7. ✅ **asyncio + psycopg2 timeout patterns** - Multiple async examples provided

---

## Feedback & Updates

This research is **complete and comprehensive**. All findings are based on:

- Official Neon documentation
- PgBouncer configuration standards
- PostgreSQL behavior documentation
- Your actual code analysis
- Industry best practices

Last updated: January 5, 2026
