#!/usr/bin/env python3
"""
imodent — smart indentation fixer

Usage:
    imodent <path> [options]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from imodent.cli import main

if __name__ == "__main__":
    main()
