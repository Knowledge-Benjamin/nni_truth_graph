#!/usr/bin/env python3
"""
build_appliance.py
─────────────────────────────────────────────────────────────────────────────
Compiles the NNI Truth Graph `ai_engine` into a native binary executable 
using Nuitka. This protects the intellectual property (IP) of the system 
when deployed to edge OVMs or client-hosted environments.

Requirements:
- pip install nuitka
- A C compiler (gcc/clang or MSVC on Windows)
"""

import os
import sys
import subprocess

def main():
    print("=== NNI Truth Graph Appliance Compiler ===")
    
    # Path to the orchestrator entrypoint (e.g., worker.py or a custom main.py)
    # We will compile the worker loop which imports everything else.
    target_script = os.path.join("ai_engine", "worker.py")
    
    if not os.path.exists(target_script):
        print(f"Error: Target script {target_script} not found.")
        print("Please run this script from the project root (nni_truth_graph).")
        sys.exit(1)

    print(f"Target script found: {target_script}")
    print("Preparing Nuitka build command...")

    # Build the Nuitka command
    # We include necessary packages that often fail with dynamic imports
    command = [
        sys.executable, "-m", "nuitka",
        "--standalone",          # Bundle everything into a folder
        "--onefile",             # Compress the standalone folder into a single executable
        "--include-package=ai_engine",
        "--include-package=instructor",
        "--include-package=pydantic",
        "--include-package=psycopg2",
        "--include-package=jwt",
        "--assume-yes-for-downloads", # Automatically download necessary compilers (e.g., ccache/depends)
        "--output-dir=build",
        target_script
    ]

    print(f"Executing: {' '.join(command)}")
    print("This will take a significant amount of time (often 10-30 minutes). Please wait...\n")

    try:
        subprocess.check_call(command)
        print("\n=== Compilation Successful ===")
        print("The native binary has been placed in the 'build' directory.")
        print("You can now safely distribute this executable to edge OVMs without exposing the Python source.")
    except subprocess.CalledProcessError as e:
        print(f"\n=== Compilation Failed ===")
        print(f"Nuitka exited with code {e.returncode}.")
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("\nCompilation aborted by user.")
        sys.exit(1)

if __name__ == "__main__":
    main()
