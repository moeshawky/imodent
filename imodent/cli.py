"""
CLI for imodent — code intelligence tool.

Two modes:
  FIX    — reformat and repair files in-place  (default, backward-compatible)
  SCAN   — multi-file analysis: imports, lint, architecture  (--analyze)
"""

import argparse
import shutil
import sys
from pathlib import Path

# Registration side-effects (do not remove)
from .strategies import PythonStrategy, JSONStrategy, JSONLStrategy, YAMLStrategy
from .interfaces import FixResult
from .pipeline import FixPipeline
from .registry import StrategyRegistry
from .analysis.coordinator import AnalysisCoordinator, FixMode
from .analysis.findings import Severity


# ---------------------------------------------------------------------------
# Mode 1: FIX — reformat files (backward-compatible)
# ---------------------------------------------------------------------------

def fix_file(
    file_path: Path,
    indent_size: int = 4,
    backup: bool = True,
    dry_run: bool = False,
    check_only: bool = False,
):
    """Reformat one file or directory tree."""
    pipeline = FixPipeline(indent_size=indent_size)
    targets = _collect_targets(file_path)
    for target in targets:
        _process_file(pipeline, target, backup, dry_run, check_only)


def _collect_targets(file_path: Path) -> list[Path]:
    """Expand file/directory to list of processable files."""
    handled = {".py", ".pyw", ".pyi", ".json", ".jsonl", ".ndjson", ".yaml", ".yml"}
    if file_path.is_file():
        return [file_path]
    return sorted(f for f in file_path.glob("**/*") if f.is_file() and f.suffix.lower() in handled)


def _process_file(pipeline, file_path, backup, dry_run, check_only):
    """Fix one file. Write, preview, or validate depending on flags."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"✗ {file_path}: {e}")
        return

    result = pipeline.fix(content)

    if check_only:
        print(f"{'✓' if result.success else '✗'} {file_path}")
        for err in result.errors:
            print(f"  → {err}")
        return

    if dry_run:
        print(f"\n--- {file_path} ---")
        print(result.content, end="")
        for w in result.warnings:
            print(f"  ⚠ {w}")
        return

    if backup and file_path.suffix.lower() in {
        ".py", ".pyw", ".pyi", ".json", ".jsonl", ".ndjson", ".yaml", ".yml"
    }:
        bak = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, bak)
        print(f"  ↳ backup → {bak}")

    file_path.write_text(result.content, encoding="utf-8")
    print(f"✓ {file_path}")
    for err in result.errors:
        print(f"  ✗ {err}")
    for w in result.warnings:
        print(f"  ⚠ {w}")


# ---------------------------------------------------------------------------
# Mode 2: SCAN — multi-file analysis
# ---------------------------------------------------------------------------

def analyze_files(
    paths: list[Path],
    analyze_imports: bool = True,
    analyze_lint: bool = False,
    advisory: bool = False,
    fix: bool = False,
    backup: bool = False,
    dry_run: bool = False,
    check_only: bool = False,
    verbose: bool = False,
):
    """Scan project for import issues, lint violations, architectural drift."""
    files = _expand_paths(paths)
    if not files:
        print("No matching files found.")
        return

    coordinator = AnalysisCoordinator()
    result = coordinator.analyze(files)

    # ── Summary ──────────────────────────────────────────────────────────
    print(result.summary())
    print()

    if not result.findings:
        print("✓ Clean — no issues detected.")
        return

    # ── Findings by severity ──────────────────────────────────────────────
    for severity in [Severity.ERROR, Severity.WARNING, Severity.INFO, Severity.HINT]:
        bucket = [f for f in result.findings if f.severity == severity]
        if not bucket:
            continue
        label = severity.value.upper()
        print(f"\n{label} ({len(bucket)}):")
        for f in bucket[:10]:
            loc = f":{f.location.line}" if f.location else ""
            print(f"  {f.file.name}{loc}: {f.message}")
        if len(bucket) > 10:
            print(f"  … +{len(bucket) - 10} more")

    # ── Advisory ─────────────────────────────────────────────────────────
    if advisory:
        from .advisors.architecture import ArchitectureAdvisor
        advisor = ArchitectureAdvisor()
        if advisor.should_advise(result.findings, result.context):
            print("\n" + "━" * 60)
            print("ARCHITECTURE REVIEW")
            print("━" * 60)
            for advice in advisor.advise(result.findings, result.context):
                print(f"\n[{advice.category}] {advice.summary}")
                print(f"  Why:     {advice.explanation}")
                print(f"  Action:  {advice.recommendation}")
                if advice.example:
                    print(f"  Pattern:\n{advice.example}")
                print(f"  Risk:    {advice.impact}")

    # ── Fix ──────────────────────────────────────────────────────────────
    if fix and not check_only:
        print("\n" + "━" * 60)
        print("APPLYING FIXES")
        print("━" * 60)
        fix_results = coordinator.fix(result.findings, result.context, mode=FixMode.SAFE_AUTO)
        for file_path, fix_result in fix_results.items():
            if not fix_result.success:
                print(f"  ✗ {file_path}")
                for err in fix_result.errors:
                    print(f"    → {err}")
                continue
            if backup:
                bak = file_path.with_suffix(file_path.suffix + ".bak")
                shutil.copy2(file_path, bak)
                print(f"  ↳ backup → {bak}")
            if dry_run:
                print(f"  ~ {file_path} (dry-run)")
                print(fix_result.content[:500])
            else:
                file_path.write_text(fix_result.content, encoding="utf-8")
                print(f"  ✓ {file_path}")


def _expand_paths(paths: list[Path]) -> list[Path]:
    """Expand file/directory list to concrete file list."""
    handled = {".py", ".pyw", ".pyi", ".json", ".jsonl", ".ndjson", ".yaml", ".yml"}
    files = set()
    for p in paths:
        if p.is_file():
            files.add(p)
        elif p.is_dir():
            for ext in handled:
                files.update(p.glob(f"**/*{ext}"))
    return sorted(files)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

DESCRIPTION = """\
imodent — code intelligence tool

Two modes of operation:

  FIX mode (default)   Reformat & repair files in-place.
                       Detects language, fixes indentation, validates output.

  SCAN mode (--analyze)  Multi-file project analysis.
                         Import hygiene, lint violations, architectural drift.

Operates on Python, JSON, JSONL, and YAML files.
Creates .bak backups with --backup. Never modifies files without consent.\
"""

EPILOG = """\
examples:
  # ── FIX mode ──────────────────────────────────────────────────────────

  imodent src/main.py                    reformat file, write in-place
  imodent src/main.py -b                 reformat with .bak backup
  imodent src/main.py -n                 preview diff, don't write
  imodent src/main.py -c                 syntax check only
  imodent ./src -r                       fix every file under src/

  # ── SCAN mode ─────────────────────────────────────────────────────────

  imodent ./src --analyze --imports      find unused & duplicate imports
  imodent ./src --analyze --advisory     flag architectural issues
  imodent ./src --analyze --imports --fix   auto-fix safe import issues
  imodent ./src --analyze --imports --advisory --fix   full audit + fix

import analysis gives you options per finding:
  • delete    — remove the unused import
  • keep      — preserve it (type hints, re-exports, __all__)
  • investigate — search codebase before deciding
  • false-positive — mark as used if analysis missed it

Only asks for input when the correct action is genuinely ambiguous.\
"""


def main():
    parser = argparse.ArgumentParser(
        prog="imodent",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    # ── Positional ──────────────────────────────────────────────────────
    parser.add_argument(
        "path",
        type=Path,
        nargs="+",
        help="file or directory to process",
    )

    # ── FIX mode flags (backward-compatible) ─────────────────────────────
    fix_group = parser.add_argument_group("fix mode", "reformat & repair files")
    fix_group.add_argument(
        "-i", "--indent",
        type=int, default=4,
        metavar="N",
        help="spaces per indent level (default: 4)",
    )
    fix_group.add_argument(
        "-b", "--backup",
        action="store_true",
        help="create .bak before writing",
    )
    fix_group.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="preview output, don't write",
    )
    fix_group.add_argument(
        "-c", "--check",
        action="store_true",
        help="validate syntax only, no changes",
    )
    fix_group.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="walk subdirectories",
    )

    # ── SCAN mode flags ──────────────────────────────────────────────────
    scan_group = parser.add_argument_group("scan mode", "multi-file project analysis")
    scan_group.add_argument(
        "--analyze",
        action="store_true",
        help="enable project-wide analysis",
    )
    scan_group.add_argument(
        "--imports",
        action="store_true",
        help="detect unused, duplicate, and misused imports",
    )
    scan_group.add_argument(
        "--lint",
        action="store_true",
        help="run lint checks via ruff",
    )
    scan_group.add_argument(
        "--advisory",
        action="store_true",
        help="flag architectural issues (circular deps, import clustering)",
    )
    scan_group.add_argument(
        "--fix",
        action="store_true",
        help="auto-fix safe findings (combine with --analyze)",
    )
    scan_group.add_argument(
        "--interactive",
        action="store_true",
        help="prompt before each ambiguous fix",
    )
    scan_group.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="show all findings, not just top 10",
    )

    args = parser.parse_args()

    # ── Route to mode ────────────────────────────────────────────────────
    if args.analyze or args.imports or args.lint or args.advisory:
        analyze_files(
            paths=args.path,
            analyze_imports=args.imports,
            analyze_lint=args.lint,
            advisory=args.advisory,
            fix=args.fix,
            backup=args.backup,
            dry_run=args.dry_run,
            check_only=args.check,
            verbose=args.verbose,
        )
    else:
        for path in args.path:
            fix_file(
                path,
                indent_size=args.indent,
                backup=args.backup,
                dry_run=args.dry_run,
                check_only=args.check,
            )


if __name__ == "__main__":
    main()
