"""
Command Line Interface for Indentation Fixer.

Backward-compatible CLI that uses the new modular architecture.
"""
import argparse
import shutil
from pathlib import Path
from typing import Optional

# Import strategies to register them (critical!) 
from .strategies import PythonStrategy, JSONStrategy, JSONLStrategy, YAMLStrategy

from .interfaces import FixResult
from .pipeline import FixPipeline
from .registry import StrategyRegistry


def fix_file(
    file_path: Path,
    indent_size: int = 4,
    backup: bool = True,
    dry_run: bool = False,
    check_only: bool = False
):
    """
    Fix indentation in a file.
    
    Args:
        file_path: Path to the file (or directory).
        indent_size: Number of spaces per indentation level.
        backup: Create .bak backup before modifying.
        dry_run: Preview changes without writing.
        check_only: Only validate, don't modify.
    """
    pipeline = FixPipeline(indent_size=indent_size)
    
    if file_path.is_dir():
        # Process directory
        for file in file_path.glob("**/*"):
            if file.is_file() and file.suffix.lower() in ['.py', '.pyw', '.pyi', '.json', '.jsonl', '.ndjson', '.yaml', '.yml']:
                _process_file(pipeline, file, backup, dry_run, check_only)
    else:
        _process_file(pipeline, file_path, backup, dry_run, check_only)


def _process_file(
    pipeline: FixPipeline,
    file_path: Path,
    backup: bool,
    dry_run: bool,
    check_only: bool
):
    """Process a single file."""
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return
    
    # Detect and fix
    result = pipeline.fix(content)
    
    if check_only:
        status = "✓" if result.success else "✗"
        print(f"{status} {file_path}")
        if result.errors:
            for error in result.errors:
                print(f"  Error: {error}")
        return
    
    if dry_run:
        print(f"\n--- {file_path} ---")
        print(result.content, end='')
        if result.warnings:
            print("\nWarnings:")
            for warning in result.warnings:
                print(f"  {warning}")
        return
    
    if backup and file_path.suffix.lower() in ['.py', '.pyw', '.pyi', '.json', '.jsonl', '.ndjson', '.yaml', '.yml']:
        bak = file_path.with_suffix(file_path.suffix + '.bak')
        shutil.copy2(file_path, bak)
        print(f"Backup created: {bak}")
    
    file_path.write_text(result.content, encoding='utf-8')
    print(f"Fixed: {file_path}")
    
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  {error}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  {warning}")


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="imodent — smart indentation fixer"
    )
    parser.add_argument("path", type=Path, help="File or directory to fix")
    parser.add_argument("-i", "--indent", type=int, default=4, help="Indent size (default: 4)")
    parser.add_argument("-b", "--backup", action="store_true", help="Create .bak backup files")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("-c", "--check", action="store_true", help="Only check (don't fix)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Process directories recursively")
    
    args = parser.parse_args()
    
    fix_file(
        args.path,
        indent_size=args.indent,
        backup=args.backup,
        dry_run=args.dry_run,
        check_only=args.check
    )


if __name__ == "__main__":
    main()
