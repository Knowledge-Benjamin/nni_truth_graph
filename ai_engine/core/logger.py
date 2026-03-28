"""
Pipeline Color Logger
=====================
ANSI color-coded logging utility for the Truth AI pipeline stages.
Compatible with PowerShell (Windows 10+ ANSI support) and Git Bash.

Usage in each pipeline script:
    from ai_engine.core.logger import pipeline_print as pprint
    pprint("Stage 1: Ingest complete")
    
Or with a custom stage override:
    from ai_engine.core.logger import get_printer
    log = get_printer(3)
    log("Classification starting...")
"""
import os
import sys

# Force ANSI on Windows via enabling VT processing
if sys.platform == "win32":
    import ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        # Enable ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004) on stdout
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass  # Gracefully degrade if not supported

# --- ANSI Reset ---
RESET = "\033[0m"
BOLD  = "\033[1m"

# --- Stage color map (stage number -> ANSI color code) ---
STAGE_COLORS = {
    1:  "\033[96m",    # Bright Cyan    — Ingest
    2:  "\033[93m",    # Bright Yellow  — Scrape
    3:  "\033[94m",    # Bright Blue    — Classification
    4:  "\033[95m",    # Bright Magenta — Extraction
    5:  "\033[92m",    # Bright Green   — Resolution
    6:  "\033[91m",    # Bright Red     — Deduplication
    7:  "\033[36m",    # Cyan           — Cross-Reference
    8:  "\033[33m",    # Yellow         — Graph Mutation
    9:  "\033[35m",    # Magenta        — Truth Evolution
    10: "\033[32m",    # Green          — Revalidation
}

STAGE_NAMES = {
    1:  "INGEST",
    2:  "SCRAPE",
    3:  "CLASSIFY",
    4:  "EXTRACT",
    5:  "RESOLVE",
    6:  "DEDUP",
    7:  "CROSS-REF",
    8:  "MUTATE",
    9:  "EVOLVE",
    10: "REVALIDATE",
}

def colored(stage: int, message: str) -> str:
    """Return a color-wrapped string for the given stage number."""
    color = STAGE_COLORS.get(stage, "")
    name  = STAGE_NAMES.get(stage, f"STAGE {stage}")
    return f"{BOLD}{color}[{name}]{RESET} {color}{message}{RESET}"

def get_printer(stage: int):
    """
    Returns a print function pre-bound to a specific pipeline stage color.
    
    Example:
        log = get_printer(4)
        log("Extraction started")
    """
    def _print(message: str, **kwargs):
        print(colored(stage, message), **kwargs)
    return _print
