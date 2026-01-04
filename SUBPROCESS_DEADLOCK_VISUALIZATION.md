# Subprocess Truncation - Visual Deadlock Diagram

## The Deadlock Timeline

```
PARENT PROCESS (run_pipeline.py)         CHILD PROCESS (digest_articles.py)
┌─────────────────────────────────────┐  ┌──────────────────────────────────┐
│ subprocess.run()                    │  │ Python script execution          │
│ capture_output=True                 │  │                                  │
│                                     │  │                                  │
│ T0: Create pipes                    │  │                                  │
│     stdout_pipe[64KB]               │  │                                  │
│     stderr_pipe[64KB]               │  │                                  │
│                                     │  │ T1: print("___SCRIPT_START___")  │
│ T2: Popen.wait()                    │  │     writes → stdout_pipe (20B)   │
│     BLOCKING HERE                   │  │     (0/64KB used)                │
│     (waiting for child to exit)     │  │                                  │
│                                     │  │ T3: logger.basicConfig()         │
│ ⏸️  NOT READING PIPES YET           │  │     configures stderr handler    │
│     (that happens AFTER wait())     │  │                                  │
│                                     │  │ T4: print("[INIT-1]", ...)       │
│                                     │  │     write → stdout_pipe (10B)    │
│                                     │  │     (30/64KB used)               │
│                                     │  │                                  │
│                                     │  │ T5: print("[INIT-2] env=...")    │
│                                     │  │     write → stdout_pipe (20B)    │
│                                     │  │     (50/64KB used)               │
│                                     │  │                                  │
│                                     │  │ T6: logger.info("...")           │
│                                     │  │     → sys.stderr                 │
│                                     │  │     write → stderr_pipe (150B)   │
│                                     │  │     (150/64KB used on stderr)    │
│                                     │  │                                  │
│                                     │  │ T7: Multiple logger.info() calls │
│                                     │  │     (DigestEngine.__init__)      │
│                                     │  │     accumulating in pipes...     │
│                                     │  │     stderr_pipe: 2KB             │
│                                     │  │     stdout_pipe: 3KB             │
│                                     │  │     stderr_pipe: 5KB             │
│                                     │  │     ...more logging...           │
│                                     │  │     stderr_pipe: 15KB            │
│                                     │  │     stderr_pipe: 25KB            │
│                                     │  │     stderr_pipe: 35KB            │
│                                     │  │     stderr_pipe: 45KB            │
│                                     │  │     stderr_pipe: 55KB            │
│                                     │  │                                  │
│                                     │  │ T8: logger.info("✅ HF_TOKEN found") │
│                                     │  │     → sys.stderr.write()         │
│                                     │  │     stderr_pipe FULL (64KB)!!!   │
│                                     │  │                                  │
│                                     │  │ T9: write() BLOCKS               │
│                                     │  │     Child waiting for parent     │
│                                     │  │     to read from pipe            │
│                                     │  │                                  │
│ ⏸️  STILL WAITING FOR CHILD TO EXIT  │  │ ⏸️  BLOCKED on write()           │
│     But child is BLOCKED!           │  │     waiting for parent to read   │
│                                     │  │                                  │
│     DEADLOCK!                       │  │     DEADLOCK!                    │
│                                     │  │                                  │
│ T10: Timeout triggered (300s)       │  │                                  │
│      Call proc.kill()               │  │ Process killed by parent         │
│      or Popen.wait() returns        │  │ (SIGTERM/SIGKILL)                │
│                                     │  │                                  │
│ T11: NOW read pipes (TOO LATE!)     │  │                                  │
│      result.stdout = ~2KB partial   │  │                                  │
│      result.stderr = ~2KB partial   │  │                                  │
│      (only what was queued before   │  │                                  │
│       the write() blocked)          │  │                                  │
│                                     │  │                                  │
│ T12: Log truncated output           │  │                                  │
│      "✅ HF_TOKEN found (leng..."   │  │                                  │
│      ^ Message cut off mid-word!    │  │                                  │
└─────────────────────────────────────┘  └──────────────────────────────────┘
```

## Buffer State Visualization

```
TIME: Early Execution (T5)
Parent Process                          Child Process
┌──────────────────────┐               ┌──────────────────────┐
│ subprocess.run()     │               │ digest_articles.py   │
│ Waiting...           │               │ Writing output...    │
├──────────────────────┤               ├──────────────────────┤
│ stdout_pipe buffer:  │               │ sys.stdout.write()   │
│ ▓▓░░░░░░░░░░░░░░░░░░ 3KB/64KB       │ ├─→ [30 bytes]       │
│                      │               │    [50 bytes]        │
│ stderr_pipe buffer:  │               │    [20 bytes]        │
│ ▓░░░░░░░░░░░░░░░░░░░ 1KB/64KB       │                      │
│                      │               │ sys.stderr.write()   │
│ (Pipes created but   │               │ (from logger.info()) │
│  NOT being read yet) │               │ ├─→ [150 bytes]      │
└──────────────────────┘               │    [100 bytes]       │
                                       │    [200 bytes]       │
                                       └──────────────────────┘
```

```
TIME: Critical Point (T8) - PIPE SATURATION
Parent Process                          Child Process
┌──────────────────────┐               ┌──────────────────────┐
│ subprocess.run()     │               │ digest_articles.py   │
│ STILL Waiting...     │               │ TRY TO WRITE MORE... │
├──────────────────────┤               ├──────────────────────┤
│ stdout_pipe buffer:  │               │ sys.stdout: ~5KB     │
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░ 35KB/64KB      │ sys.stderr: ~45KB    │
│                      │               │                      │
│ stderr_pipe buffer:  │               │ Trying to write:     │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 64KB/64KB ███  │ logger.info() 150B   │
│ FULL!!! ⚠️            │               │                      │
│                      │               │ write() BLOCKS!      │
│ Parent BLOCKED in    │               │ <------DEADLOCK----->│
│ subprocess.wait()    │               │ Parent not reading   │
│ NOT READING PIPES!   │               │ the full pipe!       │
└──────────────────────┘               └──────────────────────┘
```

```
TIME: After Timeout/Kill (T11-12) - PARTIAL READ
Parent Process                          Child Process
┌──────────────────────┐               ┌──────────────────────┐
│ subprocess.run()     │               │ (DEAD)               │
│ Returns finally      │               │                      │
├──────────────────────┤               ├──────────────────────┤
│ stdout_pipe buffer:  │               │                      │
│ ▓▓▓▓▓▓░░░░░░░░░░░░░░ 3KB/64KB       │                      │
│ (all read - that's   │               │                      │
│  all that was there) │               │                      │
│                      │               │                      │
│ stderr_pipe buffer:  │               │                      │
│ ▓▓▓▓▓░░░░░░░░░░░░░░░ 2KB/64KB       │                      │
│ (partial read - was  │               │                      │
│  64KB but burst)     │               │                      │
│                      │               │                      │
│ result.stdout: ~3KB  │               │                      │
│ result.stderr: ~2KB  │               │                      │
│ TRUNCATED! ❌        │               │                      │
└──────────────────────┘               └──────────────────────┘

Message in stderr_pipe (from logs):
  "2026-01-05 12:34:56 - INFO - ✅ HF_TOKEN found (length: 45 chars)"
  
But only read:
  "2026-01-05 12:34:56 - INFO - ✅ HF_TOKEN found (leng"
  ^ CUT OFF MID-WORD!
```

## Why `subprocess.run()` with `capture_output=True` Causes This

### Normal Execution (Direct Run)
```
Child Process
├─ stdout → Terminal (TTY device)
│           ├─ Infinite buffer (terminal window scrollback)
│           └─ Never fills, child never blocks
├─ stderr → Terminal (TTY device)
│           ├─ Infinite buffer (terminal window scrollback)
│           └─ Never fills, child never blocks
└─ Result: NO DEADLOCK, all output visible ✅
```

### subprocess.run(capture_output=True)
```
Child Process
├─ stdout → Pipe (4-64KB buffer)
│           ├─ Finite buffer
│           ├─ Fills up quickly
│           └─ Child blocks on write() 🔴
├─ stderr → Pipe (4-64KB buffer)
│           ├─ Finite buffer
│           ├─ Fills up quickly
│           └─ Child blocks on write() 🔴
│
Parent Process
├─ subprocess.run() calls Popen.wait()
│   ├─ WAITS FOR CHILD PROCESS TO EXIT
│   ├─ Does NOT read pipes during wait()
│   └─ Only reads pipes AFTER child exits
│
└─ Result: DEADLOCK when pipes fill 🔴
```

## The Logging Module's Role

```
Python Logging Architecture
═════════════════════════════════════════════════════════════

Application Code
  │
  └─ logger.info("message")
        │
        └─ LogRecord created
             │
             └─ Handler.emit(record)
                  │
                  ├─ Formatter.format(record)
                  │  └─ "2026-01-05 12:34:56 - INFO - message"
                  │
                  └─ sys.stderr.write(formatted_string)
                       │
                       └─ Python's TextIOWrapper internal buffer (~8KB)
                            │
                            ├─ handler.flush() only flushes HERE
                            │
                            └─ sys.stderr file descriptor
                                 │
                                 └─ OS system call: write(fd, ...)
                                      │
                                      └─ Pipe kernel buffer (4-64KB)
                                           │
                                           └─ Parent process reads when available

Problem: Parent doesn't read while child is writing!
         Pipe fills → Child blocks → Deadlock
```

## Why Current Mitigation Doesn't Work

### `python -u` Flag Effect
```
BEFORE (-u flag):
  logger.info("msg") → TextIOWrapper buffer (512B-8KB) → sys.stderr → pipe

AFTER (with -u flag):
  logger.info("msg") → TextIOWrapper buffer DISABLED
                    → sys.stderr → pipe directly

Result: Slightly less buffering at Python level, but:
  ❌ Doesn't help the OS pipe buffer (still 4-64KB)
  ❌ Logger module still queues messages
  ❌ Parent still not reading pipes
  ❌ DEADLOCK STILL OCCURS ⚠️
```

### Signal Handlers Effect
```
Signal handler in digest_articles.py:
  def signal_handler():
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()

This helps IF:
  ✅ Parent sends SIGTERM before pipes fill (rare)
  ✅ Signal arrives while child is still executing

This does NOT help IF:
  ❌ Parent is waiting in subprocess.run() (doesn't send signals)
  ❌ Pipes fill before timeout (child blocks, no signal sent)
  ❌ subprocess.run() doesn't send signals; it just waits
```

## The Real Solution: Proper Pipe Draining

### Using subprocess.Popen.communicate()
```
Parent Process                          Child Process
┌────────────────────────┐              ┌──────────────────┐
│ subprocess.Popen()     │              │ digest_articles  │
│                        │              ├──────────────────┤
│ Thread 1: read stdout  │              │ Writing to pipe  │
│ Thread 2: read stderr  │              │ (8KB accumulated)│
│                        │              │                  │
│ Both reading in        │              │ pipe available   │
│ parallel while child   │ ◄──────────► │                  │
│ is executing           │   pipes OK   │ Writing more...  │
│                        │  (drained)   │ (5KB accumulated)│
│ Pipes never fill!      │              │                  │
│                        │              │ Writing more...  │
│ stdout_data, stderr =  │              │ (10KB accum.)    │
│   proc.communicate()   │              │                  │
│                        │              │ Continue...      │
│ ✅ ALL DATA CAPTURED   │              │ More...          │
│ ✅ NO DEADLOCK         │              │                  │
│ ✅ NO TRUNCATION       │              │ (Process exits)  │
└────────────────────────┘              └──────────────────┘
```

### Why communicate() Works
```
Popen.communicate() internally:

1. Creates thread for reading stdout
2. Creates thread for reading stderr
3. Both threads read continuously
4. No deadlock because pipes are drained in parallel
5. Waits for process to exit
6. Returns accumulated stdout and stderr

This is the RECOMMENDED way to capture subprocess output!
```

## Summary: The Deadlock Chain

```
1. subprocess.run(capture_output=True)
        ↓
2. Creates pipes with finite buffers (4-64KB)
        ↓
3. Child process writes output
        ↓
4. Parent in Popen.wait() NOT reading pipes
        ↓
5. Pipe buffers fill up (~1-2KB of output in this case)
        ↓
6. Child's next write() BLOCKS
        ↓
7. Parent waiting for child to exit (which is blocked)
        ↓
8. 🔴 DEADLOCK 🔴
        ↓
9. Timeout triggers or process killed
        ↓
10. Parent reads pipes (FINALLY!) but only gets partial data
        ↓
11. Output truncated at random point in logger message
        ↓
12. "✅ HF_TOKEN found (leng..." ← CUT OFF HERE
```

This is NOT a Python `-u` flag issue.
This is NOT a signal handler issue.
This IS a classic subprocess pipe deadlock.

**The fix: Use Popen.communicate() or don't capture output.**
