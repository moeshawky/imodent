#!/usr/bin/env python3
"""
imodent - Modular Architecture

Smart Python + JSON/JSONL indentation fixer with extensible plugin architecture.

Usage:
    python3 -m src.cli <path> [options]
    python3 imodent.py <path> [options]  # Backward compatible
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

# Import and run CLI
from imodent.cli import main

if __name__ == "__main__":
    main()
