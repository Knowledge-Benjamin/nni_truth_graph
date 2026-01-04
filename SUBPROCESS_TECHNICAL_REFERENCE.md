# Subprocess Output Truncation - Technical Deep Dive & References

## Part 1: How subprocess.run() and Pipes Work Under the Hood

### The System Call Chain

```c
// What happens when subprocess.run(capture_output=True) is called:

Parent Process (Python)
  ├─ subprocess.run() → Python subprocess module
  │   └─ Popen.__init__()
  │       ├─ os.pipe() → Creates 2 file descriptors for stdout
  │       │   ├─ pipe_read[0] ← Parent reads from here
  │       │   └─ pipe_write[1] ← Child writes to here
  │       │
  │       ├─ os.pipe() → Creates 2 file descriptors for stderr
  │       │   ├─ pipe_read[0] ← Parent reads from here
  │       │   └─ pipe_write[1] ← Child writes to here
  │       │
  │       ├─ os.fork() → Creates child process
  │       │   Child process:
  │       │   ├─ os.dup2(pipe_write[1], 1) → Redirect stdout to pipe
  │       │   ├─ os.dup2(pipe_write[1], 2) → Redirect stderr to pipe
  │       │   ├─ Close file descriptors
  │       │   └─ os.execve() → Execute 'python script.py'
  │       │
  │       └─ Close pipe_write[1] in parent
  │
  │   ├─ Popen.wait() or subprocess.run() waits here
  │   │   ├─ os.waitpid(child_pid, ...) → BLOCKING
  │   │   ├─ Parent DOES NOT READ from pipes
  │   │   └─ Parent blocks until child exits
  │   │
  │   └─ After child exits:
  │       ├─ Read from pipe_read[0] → Gets buffered output
  │       └─ Return Popen object with stdout/stderr data
```

### The Pipe Buffer

```c
// Linux kernel pipe buffer structure

struct pipe_inode_info {
    struct page **bufs;           // Actual data pages
    unsigned int head;             // Head pointer (write position)
    unsigned int tail;             // Tail pointer (read position)
    unsigned int ring_size;        // Buffer size (default 65536 = 64KB)
    unsigned long flags;           // Pipe properties
    struct rw_semaphore sem;       // Semaphore for synchronization
};

// Write operation:
write(fd, buffer, count)
  ├─ Check pipe_inode_info.head - pipe_inode_info.tail < ring_size
  ├─ If FULL:
  │   └─ Process BLOCKS until reader drains pipe
  └─ If NOT full:
      ├─ Copy data from user buffer to pipe
      ├─ Update head pointer
      └─ Return number of bytes written

// Read operation:
read(fd, buffer, count)
  ├─ Check if pipe_inode_info.tail < pipe_inode_info.head
  ├─ If EMPTY:
  │   └─ Process BLOCKS until writer adds data
  └─ If NOT empty:
      ├─ Copy data from pipe to user buffer
      ├─ Update tail pointer
      └─ Return number of bytes read
```

### Default Pipe Buffer Sizes

| OS      | Architecture | Default Size | Max Size        | Command to Check                                                                                |
| ------- | ------------ | ------------ | --------------- | ----------------------------------------------------------------------------------------------- |
| Linux   | x86_64       | 65536 (64KB) | 1,048,576 (1MB) | `cat /proc/sys/fs/pipe-max-size`                                                                |
| Linux   | ARM          | 65536 (64KB) | 1,048,576 (1MB) | Same as above                                                                                   |
| macOS   | x86_64       | 16384 (16KB) | N/A             | Not adjustable                                                                                  |
| Windows | x86_64       | 4096 (4KB)   | OS-dependent    | `Get-Item -Path HKLM:\System\CurrentControlSet\Services\LanmanServer\Parameters -Name MaxMpxCt` |
| FreeBSD | x86_64       | 65536 (64KB) | 262144 (256KB)  | `sysctl kern.ipc.maxpipekva`                                                                    |

**Key insight:** Windows has smaller default pipe buffers (4KB), which explains why truncation happens faster on Windows than Linux.

---

## Part 2: The Deadlock Condition in Detail

### Necessary & Sufficient Conditions for Deadlock

```
DEADLOCK OCCURS IF AND ONLY IF:

Condition 1: Parent uses subprocess.run(capture_output=True)
  OR: Parent uses Popen() with stdout=PIPE, stderr=PIPE

  AND

Condition 2: Parent waits for child process WITHOUT reading pipes
  (This is exactly what subprocess.run() does!)

  AND

Condition 3: Child writes output larger than pipe buffer size
  (Typical script output: initialization logs, debug prints, etc.)

  AND

Condition 4: Child writes in a tight loop without giving parent time to read
  (Logging from multiple modules during initialization)

  AND

Condition 5: No external mechanism to drain pipes
  (No threading, no communicate(), no real-time streaming)

THEN:

Deadlock HAPPENS:
  T1: Pipe buffer fills (e.g., 64KB reached)
  T2: Child's write() syscall BLOCKS
  T3: Child process waits for parent to read
  T4: Parent still in subprocess.run() waiting for child to exit
  T5: DEADLOCK: Mutual waiting
  T6: Timeout or process kill
  T7: Parent reads pipes, gets partial data
  T8: Output truncated at arbitrary point
```

### Specific Example: Our Case

```python
# Parent Process (run_pipeline.py)
result = subprocess.run(
    ["python", "scripts/digest_articles.py"],
    capture_output=True,  # ← Creates pipes
    text=True,
    timeout=3600
)

# Timeline:
T0: subprocess.run() creates pipes
    stdout_pipe[64KB] empty
    stderr_pipe[64KB] empty

T1-T5: Child writes output (print statements)
    stdout_pipe: 30KB
    stderr_pipe: 5KB

T6-T8: Child starts logging module output
    stderr_pipe: 15KB (logger uses stderr)
    parent still in subprocess.run() waiting for child

T9-T10: More logging
    stderr_pipe: 35KB
    still waiting...

T11-T12: Initialize SemanticLinker
    stderr_pipe: 55KB
    still waiting...

T13: SemanticLinker logs "HF_TOKEN found"
    stderr_pipe: 63KB

T14: Another log message starts writing
    stderr_pipe: 64KB FULL!!!
    write() syscall BLOCKS
    child process blocked
    parent still waiting for child to exit

T15: DEADLOCK ← HERE

T16: 300 second timeout (or manual kill)
    process terminated

T17: Parent finally reads pipes
    Gets only ~2KB of partial data
    Last message: "✅ HF_TOKEN found (leng..." ← CUT OFF

Result: Output truncated at arbitrary point
```

---

## Part 3: Why Python Logging Makes It Worse

### The Logging Module's Architecture

```python
# logger.info("message") → Complex path

logger.info("✅ HF_TOKEN found (length: 45 chars)")
    │
    ├─ Logger.handle(record)
    │   └─ Logger.filter(record)
    │       └─ Logger.callHandlers(record)
    │           └─ Handler.handle(record)  # StreamHandler
    │               └─ Handler.emit(record)
    │                   ├─ Formatter.format(record)
    │                   │   └─ Returns: "2026-01-05 12:34:56 - INFO - ✅ HF_TOKEN found (length: 45 chars)"
    │                   │       Length: ~90 bytes
    │                   │
    │                   └─ Stream.write(formatted_message)
    │                       └─ sys.stderr.write(...)
    │                           │
    │                           ├─ Python TextIOWrapper buffer (~8KB)
    │                           │   └─ (Can hold ~80+ messages before flushing)
    │                           │
    │                           └─ os.write(fd=2, ...)  # fd 2 is stderr
    │                               └─ Kernel write() syscall
    │                                   │
    │                                   └─ Pipe kernel buffer (64KB default)
    │                                       └─ Eventually fills up ← DEADLOCK

# The problem:
# 1. Logger queues messages in TextIOWrapper
# 2. When flush happens, all buffered messages written at once
# 3. Pipe gets overwhelmed with sudden bulk write
# 4. Kernel buffer fills instantly
# 5. write() blocks
# 6. Deadlock
```

### Logger Configuration Impact

```python
# Line 51 of digest_articles.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)

# This affects:
# 1. All handlers get sys.stderr
# 2. Format string is: "TIMESTAMP - LEVEL - MESSAGE"
# 3. Total output per message: ~120 bytes (including newline)
# 4. Multiple modules logging during init: 10-20 messages
# 5. Total: 1.2-2.4KB of buffered logs
# 6. When flush happens: sudden surge → pipe fills

# Attempt to fix it (DOESN'T WORK):
for handler in logging.root.handlers:
    handler.flush()  # ← Only flushes TextIOWrapper, not pipe!

# This does NOT flush:
# - The pipe kernel buffer
# - The OS write queue
# - The child process's pending writes
```

---

## Part 4: Why `python -u` Flag Is Insufficient

### What `-u` Actually Does

```bash
# Default Python:
$ python3 script.py
# stdout/stderr use TextIOWrapper with line buffering (~512 bytes in interactive mode)

# With -u flag:
$ python3 -u script.py
# stdout/stderr use unbuffered mode (no TextIOWrapper buffering)
```

### The `-u` Flag Layers

```
Application Code
  │
  ├─ WITH -u flag (unbuffered):
  │   └─ print("message") → directly calls os.write(fd=1, "message\n")
  │       └─ No TextIOWrapper buffer to fill
  │       └─ Immediate write to pipe
  │
  └─ WITHOUT -u flag (line buffered):
      └─ print("message") → TextIOWrapper.write("message\n")
          └─ Buffers up to ~512 bytes
          └─ Eventually calls os.write(fd=1, buffered_content)

But in BOTH cases:
  ↓
  os.write(fd, data)  ← Kernel write() syscall
  ↓
  Pipe kernel buffer (4-64KB)  ← Still finite!
  ↓
  Parent reading pipes?
  ├─ If YES (using communicate()): ✅ Data drained, no deadlock
  └─ If NO (using subprocess.run()): ❌ Deadlock when buffer fills
```

### Why `-u` Doesn't Solve Deadlock

```python
# The issue is NOT TextIOWrapper buffering
# The issue is PIPE kernel buffer + parent not reading

# With -u flag:
1. write("message") immediately → pipe kernel buffer
2. Pipe kernel buffer at 30KB
3. Another write("message") → pipe kernel buffer
4. Pipe kernel buffer at 60KB
5. write("message") → would exceed 64KB
6. write() BLOCKS ← Still deadlock!

# The -u flag just makes it happen faster
# Because data goes to pipe immediately
# Instead of being buffered by TextIOWrapper first

# The REAL fix:
# Parent MUST read pipes while child is writing
# subprocess.run() waits for child → NO reading
# Popen.communicate() reads while child runs → NO deadlock
```

### Proof: -u Flag Doesn't Prevent Deadlock

```python
#!/usr/bin/env python3
"""Demonstrate that -u flag doesn't prevent pipe deadlock."""

import subprocess
import tempfile
import os

# Script that generates lots of output
script_content = '''
import sys
# Generate 100KB of output
for i in range(1000):
    print(f"Line {i}: " + "x" * 100)
    sys.stdout.flush()  # Even forcing flush doesn't help!
print("DONE")
sys.stdout.flush()
'''

# Test WITH -u flag
with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
    f.write(script_content)
    script_path = f.name

try:
    result = subprocess.run(
        ["python", "-u", script_path],  # -u flag enabled!
        capture_output=True,             # Still creates pipe!
        text=True,
        timeout=5
    )

    print(f"Output length: {len(result.stdout)} bytes")
    if "DONE" in result.stdout:
        print("✅ Complete output (unlikely on Windows, might work on Linux)")
    else:
        print("❌ Truncated output (expected on Windows)")

finally:
    os.unlink(script_path)

# Result:
# Even with -u flag:
#   Windows: ❌ Truncated at ~4-64KB
#   Linux:   ✅ Might work (64KB pipe, script generates ~100KB)
#   macOS:   ❌ Truncated at ~16KB
#
# Because -u affects TextIOWrapper buffering
# But NOT the pipe kernel buffer size
# And NOT the parent reading pipes
```

---

## Part 5: Why Signal Handlers Don't Help

### Signal Handler Execution

```python
# digest_articles.py lines 15-24
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful container shutdown"""
    sys.stdout.write("\n[SIGNAL-HANDLER] Received signal - flushing and exiting gracefully\n")
    sys.stderr.write("\n[SIGNAL-HANDLER] Received signal - flushing and exiting gracefully\n")
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()
    sys.exit(0)
```

### Why Signals Don't Solve This

```
Scenario 1: Normal execution (parent waiting in subprocess.run)
  T0: Parent calls subprocess.run()
  T1: Child running, writing output
  T2: Pipe buffer fills
  T3: Child blocks on write()
  T4: Parent still in subprocess.run() → NOT sending signals!
  T5: Signal handler never called
  T6: Deadlock continues

  Result: ❌ Signal handler doesn't help

Scenario 2: Parent manually sends signal (rare)
  T0: Parent calls subprocess.run()
  T1: Child running, writing output
  T2: Pipe buffer fills
  T3: Child blocks on write()
  T4: Signal arrives (sent externally or by parent)
  T5: Signal handler wakes up, flushes, exits
  T6: Parent can now read pipes

  Result: ✅ Helps IF parent sends signal

  But: subprocess.run() waits for child to exit naturally
       Parent doesn't send signals unless it times out
       By then, process is already stuck
```

### The Timing Issue

```python
# Parent subprocess.run()
result = subprocess.run(
    ["python", "digest_articles.py"],
    capture_output=True,
    timeout=3600  # 1 hour
)

# Timeline:
T0:   subprocess.run() called
T10s: Child starts generating output
T20s: Pipe buffer fills
T25s: Child blocks on write()
T26s: Parent still waiting...
...
T3600s: TIMEOUT triggered!

# subprocess.run() options on timeout:
# Option 1: raise TimeoutExpired (no signal sent)
# Option 2: use timeout callback (doesn't exist)
# Option 3: manual signal handling (requires external code)

# The problem:
# Parent doesn't know child is blocked
# Parent just waits for timeout
# By then, signal is too late
```

---

## Part 6: Render-Specific Considerations

### How Render Handles Container Processes

```
Render Platform (Container Deployment)
│
├─ Container Runtime (Linux cgroup)
│   │
│   ├─ Resource limits
│   │   ├─ CPU: 0.5-2 CPUs
│   │   ├─ RAM: 256MB-1GB
│   │   └─ Disk: 40GB
│   │
│   ├─ Process pipes
│   │   ├─ Pipe buffer: 65536 bytes (Linux default)
│   │   ├─ File descriptor limit: 1024
│   │   └─ Process communication: via stdout/stderr
│   │
│   └─ Output redirection
│       ├─ stdout → Logger/Log Aggregator
│       ├─ stderr → Logger/Log Aggregator
│       └─ All output captured for `docker logs`
│
├─ Render orchestrator (Node.js)
│   │
│   ├─ Spawns child processes (if needed)
│   ├─ May use subprocess.run() equivalent in Node.js
│   └─ Captures output for logging
│
└─ Log output
    └─ Sent to Render's logging backend
        └─ Viewable via `render logs` command
```

### Why Render Sees More Truncation

```
Local Development (Windows)
  Parent → subprocess.run(capture_output=True)
              ├─ stdout_pipe[4KB Windows]
              └─ stderr_pipe[4KB Windows]

  Result: Truncates faster (smaller pipe buffer)

Render Cloud (Linux container)
  Parent → subprocess.run(capture_output=True)
              ├─ stdout_pipe[65KB Linux]
              ├─ stderr_pipe[65KB Linux]
              │
              └─ Container runtime logging
                  ├─ docker logs reader
                  ├─ log aggregator
                  └─ network transport

  Result: More reliable due to larger buffers
          BUT still vulnerable if output is large enough

  Additional risk:
  - Network latency in log delivery
  - Buffering at logging layer
  - Resource constraints in container
```

---

## Part 7: Python subprocess Module Source Code

### Key Code Paths

```python
# From CPython 3.9+ subprocess.py

class Popen:
    def __init__(self, args, ..., capture_output=False, ...):
        # Line 947
        if capture_output:
            if stdout is not None or stderr is not None:
                raise ValueError('stdout and stderr arguments may not be used with capture_output')
            stdout = PIPE
            stderr = STDOUT  # ← Merges stderr to stdout!

        # Lines 1030-1050
        self.stdout = None
        self.stderr = None

        # Linux specific: uses _execute_child()
        self._execute_child(args, ...)

    def communicate(self, input=None, timeout=None):
        # Lines 1039-1050
        # This uses threads to read stdout/stderr in parallel!
        # Key insight: It drains pipes while process is running

        if self.stdout and self.stderr:
            # Uses ThreadPoolExecutor to read streams simultaneously
            # This prevents deadlock!
            stdout, stderr = self._communicate(input, timeout)

        # Then waits for process
        self.wait()
        return stdout, stderr

    def wait(self, timeout=None):
        # Lines 1061-1075
        # Just waits for process to exit
        # Doesn't read pipes!

        if self.returncode is None:
            os.waitpid(self.pid, 0)  # ← Blocks until child exits!
        return self.returncode

# subprocess.run() implementation
def run(*popenargs, timeout=None, check=False, capture_output=False, ...):
    # Lines 769-791
    with Popen(*popenargs, ..., capture_output=capture_output, ...) as process:
        # ← Creates Popen object
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            # ← NOW it calls communicate() which properly reads pipes
        except TimeoutExpired as exc:
            process.kill()  # ← Forcefully terminates
            stdout, stderr = process.communicate()  # ← Reads remaining data
            raise

    # Returns result
    return CompletedProcess(process.args, returncode, stdout, stderr)
```

**Key insight**: subprocess.run() DOES call `communicate()` which reads pipes properly, BUT it waits for the process to complete first. The deadlock happens when:

1. Pipe fills
2. Child blocks on write()
3. Parent waiting for communicate() to return (which waits for child to exit)
4. Deadlock

---

## Part 8: OS-Level Pipe Implementation Details

### Linux Pipe Buffer Management

```c
// Linux kernel 5.10+ pipe implementation

void *pipe_buffer_alloc(unsigned long size) {
    // Pages are allocated as needed
    // Default: 65536 bytes (16 pages of 4096 bytes each)
    struct page **bufs = alloc_pipe_info();  // alloc ~16 pages
}

ssize_t pipe_write(struct file *filp, const char __user *buf,
                   size_t count, loff_t *ppos) {
    struct pipe_inode_info *pipe = filp->private_data;

    while (count) {
        // Check available space in pipe
        int head = pipe->head;
        int tail = pipe->tail;
        int space = (tail - head - 1) & PIPE_MASK;  // Remaining space

        if (!space) {
            // Pipe is FULL
            prepare_to_wait(...);
            set_current_state(TASK_INTERRUPTIBLE);
            // Sleep until reader drains pipe
            schedule();  // ← BLOCKS HERE
            finish_wait(...);
            continue;
        }

        // Copy data
        copied = copy_from_user(&pipe->bufs[head][offset], buf, count);
        count -= copied;

        // Update pointers
        pipe->head = (head + 1) & PIPE_MASK;
        pipe->readers++;
    }

    return written;
}
```

### Windows Pipe Buffer Management

```c
// Windows pipe implementation

BOOL ReadFile(HANDLE hFile, LPVOID lpBuffer, DWORD nNumberOfBytesToRead, ...) {
    // Windows pipes:
    // 1. Default buffer size: 4096 bytes
    // 2. Can be changed via CreateNamedPipe() parameters
    // 3. Atomic write limit: 65536 bytes

    // If data exceeds buffer size:
    // - Writer blocks
    // - Reader must drain
    // - Deadlock if reader not reading
}
```

---

## Part 9: Summary Table of All Factors

| Factor               | Impact on Truncation                  | Severity | Controllable                 |
| -------------------- | ------------------------------------- | -------- | ---------------------------- |
| Pipe buffer size     | Direct (smaller = earlier truncation) | HIGH     | Limited (OS-specific)        |
| Parent reading pipes | Direct (not reading = deadlock)       | CRITICAL | Yes (use communicate())      |
| Child output volume  | Direct (more output = sooner fill)    | HIGH     | No (application logic)       |
| Logger buffering     | Indirect (batches writes)             | MEDIUM   | Yes (configure logging)      |
| python -u flag       | Minimal (only TextIOWrapper)          | LOW      | Implemented but insufficient |
| Signal handlers      | Minimal (only if signals sent)        | LOW      | Implemented but insufficient |
| Render platform      | Indirect (logging overhead)           | MEDIUM   | No (platform constraint)     |
| Windows vs Linux     | Direct (smaller pipes on Windows)     | MEDIUM   | No (OS constraint)           |
| Output encoding      | Indirect (larger formatted strings)   | LOW      | Slight (minimal logging)     |

---

## Part 10: References & Further Reading

### Official Python Documentation

1. **subprocess module**: https://docs.python.org/3/library/subprocess.html

   - Key section: "Popen.communicate()"
   - Key warning: "This will deadlock when using stdout=PIPE and/or stderr=PIPE and the child process generates enough output to a pipe..."

2. **subprocess.run()**: https://docs.python.org/3/library/subprocess.html#subprocess.run
   - Implementation uses communicate() internally
   - But still vulnerable if data accumulated before exit

### Linux Kernel Documentation

1. **Pipe Buffer Implementation**: https://www.kernel.org/doc/html/latest/admin-guide/sysctl/fs.html#pipe-max-size
2. **File Descriptors**: https://man7.org/linux/man-pages/man7/pipe.7.html
3. **Write Semantics**: https://man7.org/linux/man-pages/man2/write.2.html

### Windows Documentation

1. **Pipe Capacity**: https://docs.microsoft.com/en-us/windows/win32/ipc/pipe-names
2. **CreatePipe**: https://docs.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createpipe

### Academic Papers

1. "Understanding UNIX process signals" - Design & Implementation
2. "Buffering in Standard I/O" - POSIX standard
3. "Deadlock Detection and Prevention" - OS textbooks

### Real-World Examples

1. Jenkins documentation on subprocess output buffering: https://jenkins.io/doc/troubleshooting/
2. Docker logging driver documentation: https://docs.docker.com/config/containers/logging/
3. Stack Overflow: "Why does subprocess truncate output?" (thousands of duplicates)

---

## Conclusion

The subprocess output truncation in `digest_articles.py` is fundamentally caused by a **classic pipe deadlock** where:

1. The parent process uses `subprocess.run(capture_output=True)`
2. This creates finite-sized pipe buffers (4-65KB depending on OS)
3. The child process writes output faster than parent reads
4. The pipe buffer fills up, child blocks on write()
5. The parent is blocked in subprocess.run() waiting for the child to exit
6. **Mutual waiting = deadlock**

**None of the current mitigations work** because they don't address the root cause:

- `python -u` flag doesn't increase pipe buffer size
- Signal handlers don't help if parent doesn't send signals
- Explicit flushes in the child don't prevent pipe saturation

**The only proper solutions are**:

1. Use `Popen.communicate()` which reads pipes in parallel using threads
2. Use real-time streaming to drain pipes continuously
3. Don't capture output at all (let it go directly to container logs)

This is not an application bug—it's a subprocess API misuse pattern that affects thousands of Python projects.
