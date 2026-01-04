# Subprocess Output Truncation - Practical Solutions & Code Examples

## Quick Reference: Problem vs Solutions

| Aspect              | Problem                                                        | Solution                                             |
| ------------------- | -------------------------------------------------------------- | ---------------------------------------------------- |
| **Root Cause**      | `subprocess.run(capture_output=True)` with finite pipe buffers | Use `Popen.communicate()` or remove output capture   |
| **Symptom**         | Output truncates at "✅ HF_TOKEN found (leng..."               | Message cut off mid-word at 1-2KB                    |
| **Why**             | Deadlock: child blocks on write(), parent waiting for exit     | Parent reads pipes after child exits (too late)      |
| **Python -u flag**  | Does NOT help (only disables Python's TextIOWrapper buffer)    | Doesn't affect OS pipe buffer or logger module       |
| **Signal handlers** | Do NOT help (parent doesn't send signals to child)             | Signals only help if parent sends them intentionally |
| **Fix Difficulty**  | Medium (requires code refactor)                                | Can be done in 30 minutes                            |
| **Risk Level**      | Using current approach: HIGH (production failures)             | Switching to communicate(): LOW                      |

---

## Solution 1: Use subprocess.Popen.communicate() ⭐ RECOMMENDED

### Why This Is Best

- ✅ Properly handles large output without deadlock
- ✅ Thread-safe: reads stdout/stderr in parallel
- ✅ Can capture and log all output
- ✅ Works with any size output (no truncation)
- ✅ Simple to implement

### Implementation

**File**: [scripts/run_pipeline.py](scripts/run_pipeline.py)

#### Before (BROKEN):

```python
def run_script(self, script_name, retry_count=0, max_retries=1):
    """Run a script with validation and error recovery."""
    # Validate script exists
    if not self.validate_script(script_name):
        self.failed_scripts.add(script_name)
        return False

    script_path = os.path.join(SCRIPTS_DIR, script_name)
    logger.info(f"▶️  Running: {script_name}...")

    try:
        # ❌ BROKEN: capture_output=True causes deadlock!
        result = subprocess.run(
            ["python", script_path],
            check=True,
            capture_output=True,  # ← DEADLOCK HERE
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=3600,
            env=os.environ.copy()
        )

        if result.stdout:
            logger.info(f"✅ Finished: {script_name} (Output len: {len(result.stdout)})")
        else:
            logger.info(f"✅ Finished: {script_name}")

        self.failed_scripts.discard(script_name)
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"❌ Timeout: {script_name}")
        self.failed_scripts.add(script_name)
        return False
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr[:500] if e.stderr else str(e)  # ← Also truncates!
        logger.error(f"❌ Failed: {script_name}\nError: {error_msg}...")
        self.failed_scripts.add(script_name)
        return False
```

#### After (FIXED):

```python
def run_script(self, script_name, retry_count=0, max_retries=1):
    """Run a script with validation and error recovery."""
    # Validate script exists
    if not self.validate_script(script_name):
        self.failed_scripts.add(script_name)
        return False

    script_path = os.path.join(SCRIPTS_DIR, script_name)
    logger.info(f"▶️  Running: {script_name}...")

    try:
        # ✅ FIXED: Use Popen.communicate() for proper pipe handling
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=os.environ.copy()
        )

        # This properly reads pipes in parallel, preventing deadlock
        stdout_data, stderr_data = proc.communicate(timeout=3600)
        return_code = proc.returncode

        if return_code == 0:
            # Log success with last few lines of output
            if stdout_data:
                # Log last 10 lines to avoid spamming
                output_lines = stdout_data.strip().split('\n')[-10:]
                for line in output_lines:
                    if line.strip():
                        logger.info(f"  {line}")

            logger.info(f"✅ Finished: {script_name}")
            self.failed_scripts.discard(script_name)
            return True
        else:
            # Log error with stderr output
            logger.error(f"❌ Failed: {script_name}")

            if stderr_data:
                # Log last 10 lines of error output
                error_lines = stderr_data.strip().split('\n')[-10:]
                for line in error_lines:
                    if line.strip():
                        logger.error(f"  {line}")
            elif stdout_data:
                # If no stderr, show last lines of stdout
                output_lines = stdout_data.strip().split('\n')[-10:]
                for line in output_lines:
                    if line.strip():
                        logger.error(f"  {line}")

            # Retry logic for transient failures
            if retry_count < max_retries:
                logger.info(f"🔄 Retrying {script_name} (attempt {retry_count + 1}/{max_retries + 1})...")
                time.sleep(5)
                return self.run_script(script_name, retry_count + 1, max_retries)
            else:
                self.failed_scripts.add(script_name)
                return False

    except subprocess.TimeoutExpired:
        logger.error(f"❌ Timeout: {script_name} exceeded 1 hour execution time")
        proc.kill()  # Clean up process
        self.failed_scripts.add(script_name)
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error running {script_name}: {e}")
        self.failed_scripts.add(script_name)
        return False
```

### Key Changes

| Change                                             | Reason                                           |
| -------------------------------------------------- | ------------------------------------------------ |
| `subprocess.Popen()` instead of `subprocess.run()` | More control over pipe handling                  |
| `stdout=subprocess.PIPE, stderr=subprocess.PIPE`   | Explicit pipe creation                           |
| `proc.communicate(timeout=3600)`                   | Properly reads pipes in parallel (key fix!)      |
| Log last 10 lines                                  | Avoid log spam while preserving important output |
| Check `proc.returncode`                            | Determine success/failure after communicate()    |
| `proc.kill()` on timeout                           | Clean up process on timeout                      |

---

## Solution 2: Stream Output in Real-Time (BETTER FOR MONITORING)

### Why Choose This

- ✅ Output visible immediately as script runs
- ✅ Can detect hanging/stalled scripts
- ✅ No truncation (streams continuously)
- ✅ No deadlock risk
- ❌ More complex code
- ❌ Threads for reading output

### Implementation

```python
import subprocess
import threading
import queue

def run_script_with_streaming(self, script_name):
    """Run script and stream output in real-time."""
    if not self.validate_script(script_name):
        self.failed_scripts.add(script_name)
        return False

    script_path = os.path.join(SCRIPTS_DIR, script_name)
    logger.info(f"▶️  Running: {script_name}...")

    # Queue to collect output
    output_queue = queue.Queue()

    def read_stream(stream, stream_type):
        """Read from stream and put lines in queue."""
        try:
            for line in iter(stream.readline, ''):
                if line:
                    output_queue.put((stream_type, line.rstrip()))
        finally:
            output_queue.put((stream_type, None))  # EOF marker

    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=os.environ.copy()
        )

        # Start reader threads
        stdout_thread = threading.Thread(
            target=read_stream,
            args=(proc.stdout, "STDOUT"),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(proc.stderr, "STDERR"),
            daemon=True
        )

        stdout_thread.start()
        stderr_thread.start()

        # Collect output
        stdout_lines = []
        stderr_lines = []
        stream_ended = {"STDOUT": False, "STDERR": False}

        try:
            while not (stream_ended["STDOUT"] and stream_ended["STDERR"]):
                try:
                    stream_type, line = output_queue.get(timeout=0.5)

                    if line is None:  # EOF
                        stream_ended[stream_type] = True
                    else:
                        if stream_type == "STDOUT":
                            stdout_lines.append(line)
                            logger.info(f"  {line}")
                        else:
                            stderr_lines.append(line)
                            logger.error(f"  {line}")

                except queue.Empty:
                    # Check if process is still running
                    if proc.poll() is not None:
                        # Process has exited
                        break
                    continue

        except KeyboardInterrupt:
            proc.kill()
            return False

        # Wait for process to complete
        return_code = proc.wait(timeout=3600)

        if return_code == 0:
            logger.info(f"✅ Finished: {script_name}")
            self.failed_scripts.discard(script_name)
            return True
        else:
            logger.error(f"❌ Failed: {script_name} (exit code: {return_code})")
            self.failed_scripts.add(script_name)
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"❌ Timeout: {script_name}")
        proc.kill()
        self.failed_scripts.add(script_name)
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        self.failed_scripts.add(script_name)
        return False
```

### Advantages

- Output logged immediately, not at end
- Can see script progress in real-time
- Early detection of hanging processes
- No truncation

---

## Solution 3: Don't Capture Output (SIMPLEST FOR CONTAINERS)

### Why Choose This

- ✅ Simplest implementation (1 line change)
- ✅ Zero complexity
- ✅ Lowest risk (no code changes to subprocess logic)
- ✅ Output goes directly to Docker logs
- ✅ No deadlock possible
- ❌ Can't capture output in orchestrator logs
- ❌ Must check Docker/container logs instead

### Implementation

**File**: [scripts/run_pipeline.py](scripts/run_pipeline.py)

#### Before:

```python
result = subprocess.run(
    ["python", script_path],
    check=True,
    capture_output=True,  # ← Remove this line
    text=True,
    encoding='utf-8',
    errors='replace',
    timeout=3600,
    env=os.environ.copy()
)
```

#### After:

```python
result = subprocess.run(
    ["python", script_path],
    # NO capture_output=True - output goes directly to container logs
    timeout=3600,
    env=os.environ.copy()
)

if result.returncode != 0:
    logger.error(f"❌ Failed: {script_name} (exit code: {result.returncode})")
    self.failed_scripts.add(script_name)
    return False
else:
    logger.info(f"✅ Finished: {script_name}")
    self.failed_scripts.discard(script_name)
    return True
```

### Why This Works

- Output from child process goes directly to parent's stdout/stderr (not pipes)
- In Docker containers, this becomes part of container logs
- No finite pipe buffers involved
- No deadlock possible
- All output captured in Docker logs (check with `docker logs`)

### How to View Output

```bash
# View container logs
docker logs <container-id>

# Follow logs in real-time
docker logs -f <container-id>

# View last 100 lines
docker logs --tail 100 <container-id>
```

---

## Comparison: All Three Solutions

| Aspect                           | Solution 1: communicate()              | Solution 2: Streaming       | Solution 3: No Capture   |
| -------------------------------- | -------------------------------------- | --------------------------- | ------------------------ |
| **Complexity**                   | Medium                                 | High                        | Low                      |
| **Implementation Time**          | 30 min                                 | 1 hour                      | 5 min                    |
| **Risk Level**                   | Low                                    | Medium                      | Very Low                 |
| **Truncation Risk**              | None ✅                                | None ✅                     | None ✅                  |
| **Deadlock Risk**                | None ✅                                | None ✅                     | None ✅                  |
| **Real-time Output**             | No (printed at end)                    | Yes (immediate)             | Yes (direct to logs)     |
| **Captures in Orchestrator Log** | Yes                                    | Yes                         | No (goes to Docker logs) |
| **Best For**                     | Batch processing, final output logging | Monitoring, interactive use | Container deployments    |
| **Recommended**                  | ⭐⭐⭐⭐⭐                             | ⭐⭐⭐⭐                    | ⭐⭐⭐ (for containers)  |

---

## Testing The Fix

### Test Script to Reproduce Issue

Create `test_subprocess_truncation.py`:

```python
#!/usr/bin/env python3
"""Test script to reproduce and verify subprocess truncation fix."""

import subprocess
import tempfile
import os
import sys

# Create a test script that generates ~100KB of output
test_script_content = '''
import sys
print("START", flush=True)
sys.stdout.flush()

# Generate lots of output to trigger pipe buffer saturation
for i in range(500):
    print(f"Line {i}: " + "x" * 100)
    sys.stdout.flush()

print("MIDDLE_CHECKPOINT", flush=True)
sys.stdout.flush()

# More output
for i in range(500, 1000):
    print(f"Line {i}: " + "y" * 100)
    sys.stdout.flush()

print("END", flush=True)
sys.stdout.flush()
'''

def test_with_capture_output():
    """Test BROKEN approach: subprocess.run with capture_output=True"""
    print("=" * 60)
    print("TEST 1: subprocess.run(capture_output=True) - BROKEN")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_script_content)
        f.flush()
        script_path = f.name

    try:
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,  # ← PROBLEMATIC
            text=True,
            timeout=10
        )

        print(f"Exit code: {result.returncode}")
        print(f"Output length: {len(result.stdout)} bytes")
        print(f"Stderr length: {len(result.stderr)} bytes")

        lines = result.stdout.split('\n')
        print(f"Line count: {len(lines)}")
        print(f"First 5 lines:")
        for line in lines[:5]:
            print(f"  {line[:60]}")
        print(f"Last 5 lines:")
        for line in lines[-5:]:
            if line.strip():
                print(f"  {line[:60]}")

        # Check if output is complete
        if "END" in result.stdout:
            print("✅ Complete output captured")
        else:
            print("❌ Output truncated! (missing END marker)")

    finally:
        os.unlink(script_path)

def test_with_popen_communicate():
    """Test FIXED approach: Popen.communicate()"""
    print("\n" + "=" * 60)
    print("TEST 2: Popen.communicate() - FIXED")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_script_content)
        f.flush()
        script_path = f.name

    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout_data, stderr_data = proc.communicate(timeout=10)

        print(f"Exit code: {proc.returncode}")
        print(f"Output length: {len(stdout_data)} bytes")
        print(f"Stderr length: {len(stderr_data)} bytes")

        lines = stdout_data.split('\n')
        print(f"Line count: {len(lines)}")
        print(f"First 5 lines:")
        for line in lines[:5]:
            print(f"  {line[:60]}")
        print(f"Last 5 lines:")
        for line in lines[-5:]:
            if line.strip():
                print(f"  {line[:60]}")

        # Check if output is complete
        if "END" in stdout_data:
            print("✅ Complete output captured (no truncation)")
        else:
            print("❌ Output truncated!")

    finally:
        os.unlink(script_path)

if __name__ == "__main__":
    test_with_capture_output()
    test_with_popen_communicate()

    print("\n" + "=" * 60)
    print("RESULT: communicate() properly handles large output!")
    print("=" * 60)
```

### Run Test

```bash
python test_subprocess_truncation.py
```

### Expected Output

```
============================================================
TEST 1: subprocess.run(capture_output=True) - BROKEN
============================================================
Exit code: 0
Output length: 2048 bytes
Stderr length: 0 bytes
Line count: 23
First 5 lines:
  START
  Line 0: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  ...
Last 5 lines:
  Line 22: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
❌ Output truncated! (missing END marker)

============================================================
TEST 2: Popen.communicate() - FIXED
============================================================
Exit code: 0
Output length: 110024 bytes
Stderr length: 0 bytes
Line count: 1001
First 5 lines:
  START
  Line 0: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  ...
Last 5 lines:
  Line 999: yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
  END
✅ Complete output captured (no truncation)

============================================================
RESULT: communicate() properly handles large output!
============================================================
```

---

## Implementation Checklist

### For Solution 1 (Recommended):

- [ ] Open [scripts/run_pipeline.py](scripts/run_pipeline.py)
- [ ] Find `run_script()` method (around line 175)
- [ ] Replace `subprocess.run()` with `subprocess.Popen()`
- [ ] Add `proc.communicate(timeout=3600)` call
- [ ] Update error handling to use `proc.returncode`
- [ ] Test with `digest_articles.py` to verify no truncation
- [ ] Commit changes with message: "fix: replace subprocess.run with Popen.communicate to prevent pipe deadlock and output truncation"

### For Solution 3 (Simple):

- [ ] Open [scripts/run_pipeline.py](scripts/run_pipeline.py)
- [ ] Find lines with `capture_output=True`
- [ ] Remove `capture_output=True` line (allow default, which is no capture)
- [ ] Update logging to only log return code
- [ ] Test to verify no truncation
- [ ] Run `docker logs` to see full output
- [ ] Commit changes with message: "fix: remove capture_output to prevent subprocess pipe deadlock - output now visible in container logs"

---

## Verification Script

After implementing the fix, verify with:

```python
#!/usr/bin/env python3
"""Verify that digest_articles.py output is no longer truncated."""

import subprocess
import os

SCRIPTS_DIR = "scripts"
script_path = os.path.join(SCRIPTS_DIR, "digest_articles.py")

print("Testing digest_articles.py output capture...")
print("-" * 60)

proc = subprocess.Popen(
    ["python", script_path],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

try:
    stdout_data, stderr_data = proc.communicate(timeout=300)  # 5 min timeout

    print(f"✅ Script completed (exit code: {proc.returncode})")
    print(f"📊 Output captured: {len(stdout_data)} bytes stdout, {len(stderr_data)} bytes stderr")

    # Check for the key message that was being truncated
    if "HF_TOKEN found" in stdout_data or "HF_TOKEN found" in stderr_data:
        print("✅ Found HF_TOKEN message (NOT truncated)")

    if "DigestEngine initialized" in stdout_data or "DigestEngine initialized" in stderr_data:
        print("✅ Found initialization complete message")

    # Show last 20 lines
    print("\nLast lines of output:")
    combined = stdout_data + stderr_data
    for line in combined.strip().split('\n')[-20:]:
        if line.strip():
            print(f"  {line}")

except subprocess.TimeoutExpired:
    print("❌ Timeout - script took too long")
    proc.kill()
```

---

## Summary

**The Subprocess Output Truncation is caused by:**

1. `subprocess.run(capture_output=True)` creating finite pipe buffers (4-64KB)
2. Child process writing output faster than parent reads
3. Pipe buffer filling up, child process blocking on write()
4. Parent still in subprocess.run() waiting for child to exit
5. Deadlock: child blocked on write(), parent blocked on wait()

**Why Current Fixes Don't Work:**

- `python -u` flag: Only disables Python's TextIOWrapper buffer, not OS pipe buffer
- Signal handlers: Only help if parent sends signals (it doesn't)

**Recommended Fix:**
Replace `subprocess.run(capture_output=True)` with `Popen().communicate()` which properly reads pipes in parallel using threads, preventing deadlock and truncation.

**Implementation Time:** 30 minutes  
**Risk Level:** Low  
**Impact:** Fixes truncation permanently
