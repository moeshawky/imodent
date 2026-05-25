"""CLI for imodent — code intelligence tool.

Two modes:
FIX — reformat and repair files in-place (default, backward-compatible)
SCAN — multi-file analysis: imports, lint, architecture (--analyze)
"""

import argparse
import shutil
from pathlib import Path

# Registration side-effects (do not remove)
from .pipeline import FixPipeline
from .registry import StrategyRegistry
from .analysis.coordinator import AnalysisCoordinator, FixMode
from .analysis.findings import Severity
from .project.discovery import is_generated_artifact
from .project.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Mode 1: FIX — reformat files (backward-compatible)
# ---------------------------------------------------------------------------


def fix_file(
    file_path: Path,
    indent_size: int = 4,
    backup: bool = True,
    dry_run: bool = False,
    check_only: bool = False,
    force: bool = False,
    recursive: bool = False,
):
    """Reformat one file or directory tree."""
    pipeline = FixPipeline(indent_size=indent_size)
    targets = _collect_targets(file_path, recursive=recursive)
    for target in targets:
        _process_file(pipeline, target, backup, dry_run, check_only, force)


def _collect_targets(file_path: Path, recursive: bool = False) -> list[Path]:
    """Expand file/directory to list of processable files."""
    handled = {".py", ".pyw", ".pyi", ".json", ".jsonl", ".ndjson", ".yaml", ".yml"}
    if file_path.is_file():
        return [file_path.resolve()]
    pattern = "**/*" if recursive else "*"
    return sorted(
        f.resolve()
        for f in file_path.glob(pattern)
        if f.is_file()
        and f.suffix.lower() in handled
        and not is_generated_artifact(f.resolve())
    )


def _process_file(pipeline, file_path, backup, dry_run, check_only, force=False):
    """Fix one file. Write, preview, or validate depending on flags."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"✗ {file_path}: {e}")
        return

    result = pipeline.fix(content, strategy=_strategy_for_path(file_path), force=force)

    if check_only:
        print(f"{'✓' if result.success else '✗'} {file_path}")
        for err in result.errors:
            print(f" → {err}")
        return

    if dry_run:
        print(f"\n--- {file_path} ---")
        print(result.content, end="")
        for w in result.warnings:
            print(f" ⚠ {w}")
        return

    if not result.success:
        print(f"✗ {file_path}")
        for err in result.errors:
            print(f" → {err}")
        for w in result.warnings:
            print(f" ⚠ {w}")
        return

    if backup and file_path.suffix.lower() in {
        ".py",
        ".pyw",
        ".pyi",
        ".json",
        ".jsonl",
        ".ndjson",
        ".yaml",
        ".yml",
    }:
        bak = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, bak)
        print(f" ↳ backup → {bak}")

    file_path.write_text(result.content, encoding="utf-8")
    print(f"✓ {file_path}")
    for err in result.errors:
        print(f" ✗ {err}")
    for w in result.warnings:
        print(f" ⚠ {w}")


def _strategy_for_path(file_path: Path):
    """Prefer extension-specific strategy when a known file suffix is present."""
    by_suffix = {
        ".py": "python",
        ".pyw": "python",
        ".pyi": "python",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "jsonl",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    name = by_suffix.get(file_path.suffix.lower())
    strategy = StrategyRegistry.get(name) if name else None
    return strategy() if strategy else None


# ---------------------------------------------------------------------------
# Mode 2: SCAN — multi-file analysis
# ---------------------------------------------------------------------------


def analyze_files(
    paths: list[Path],
    analyze_imports: bool = True,
    analyze_lint: bool = False,
    advisory: bool = False,
    fix: bool = False,
    interactive: bool = False,
    report: bool = False,
    backup: bool = False,
    dry_run: bool = False,
    check_only: bool = False,
    verbose: bool = False,
):
    """Scan project for import issues, lint violations, architectural drift."""
    if not (analyze_imports or analyze_lint or advisory):
        analyze_imports = True

    if not paths:
        print("No matching files found.")
        return

    resolved_paths = [path.resolve() for path in paths]
    project_context = ProjectContext.discover(resolved_paths[0])
    project_context.config.check_imports = analyze_imports
    project_context.config.check_lint = analyze_lint
    coordinator = AnalysisCoordinator(project_context=project_context)
    result = coordinator.analyze(resolved_paths)
    if not result.context.files:
        print("No matching files found.")
        return

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
        shown = bucket if verbose else bucket[:10]
        for f in shown:
            loc = f":{f.location.line}" if f.location else ""
            print(f" {f.file.name}{loc}: {f.message}")
            if verbose and f.proof_state:
                print(f"   proof_state: {f.proof_state}")
                evidence = f.data.get("evidence") or []
                if evidence:
                    kinds = sorted({item.get("kind", "evidence") for item in evidence})
                    print(f"   evidence: {', '.join(kinds)}")
        if not verbose and len(bucket) > 10:
            print(f" … +{len(bucket) - 10} more")

    # ── Advisory ──────────────────────────────────────────────────────────
    if advisory:
        from .advisors.architecture import ArchitectureAdvisor

        advisor = ArchitectureAdvisor()
        if advisor.should_advise(result.findings, result.context):
            print("\n" + "━" * 60)
            print("ARCHITECTURE REVIEW")
            print("━" * 60)
            for advice in advisor.advise(result.findings, result.context):
                print(f"\n[{advice.category}] {advice.summary}")
                print(f"  Why: {advice.explanation}")
                print(f"  Action: {advice.recommendation}")
                if advice.example:
                    print(f"  Pattern:\n{advice.example}")
                print(f"  Risk: {advice.impact}")

    # ── Determine fix mode ────────────────────────────────────────────────
    if report:
        fix_mode = FixMode.REPORT
        print("\n" + "━" * 60)
        print("REVIEW MODE — no files modified")
        print("━" * 60)
    elif interactive:
        fix_mode = FixMode.INTERACTIVE
        print("\n" + "━" * 60)
        print("INTERACTIVE MODE — prompt before each fix")
        print("━" * 60)
    elif fix and not check_only:
        fix_mode = FixMode.SAFE_AUTO
        print("\n" + "━" * 60)
        print("AUTO-FIX MODE — safe fixes only")
        print("━" * 60)
    else:
        fix_mode = None

    # ── Apply fixes ───────────────────────────────────────────────────────
    if fix_mode is not None:
        fix_results = coordinator.fix(result.findings, result.context, mode=fix_mode)
        for file_path, fix_result in fix_results.items():
            if not fix_result.success:
                print(f" ✗ {file_path}")
                for err in fix_result.errors:
                    print(f"  → {err}")
                continue
            if backup and fix_mode != FixMode.REPORT:
                bak = file_path.with_suffix(file_path.suffix + ".bak")
                shutil.copy2(file_path, bak)
                print(f" ↳ backup → {bak}")
            if dry_run or fix_mode == FixMode.REPORT:
                tag = "dry-run" if dry_run else "review"
                print(f" ~ {file_path} ({tag})")
                print(fix_result.content[:500])
            else:
                file_path.write_text(fix_result.content, encoding="utf-8")
                print(f" ✓ {file_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

DESCRIPTION = """\
imodent — code intelligence tool

Two modes of operation:

FIX mode (default)
  Reformat & repair files in-place.
  Detects language, fixes code, validates output.

SCAN mode (--analyze)
  Multi-file project analysis.
  Import hygiene, lint violations, architectural drift.

Operates on Python, JSON, JSONL, and YAML files.
Creates .bak backups with --backup. Never modifies files without consent.
"""

EPILOG = """\
examples:

  # ── FIX mode ──────────────────────────────────────────────────────────
  imodent src/main.py                 reformat file, write in-place
  imodent src/main.py -b              reformat with .bak backup
  imodent src/main.py -n              preview diff, don't write
  imodent src/main.py -c              syntax check only
  imodent ./src -r                    fix every file under src/
  imodent broken.py --force           attempt fix on structurally broken code

  # ── SCAN mode ─────────────────────────────────────────────────────────
  imodent ./src --analyze --imports           find unused & duplicate imports
  imodent ./src --analyze --advisory          flag architectural issues
  imodent ./src --analyze --imports --fix     auto-fix safe import issues
  imodent ./src --analyze --imports --interactive  prompt before each fix
  imodent ./src --analyze --imports --report  review report, no changes
  imodent ./src --analyze --imports --advisory --fix  full audit + fix

  import analysis gives you options per finding:
  • delete — remove the unused import
  • keep — preserve it (type hints, re-exports, __all__)
  • investigate — search codebase before deciding
  • false-positive — mark as used if analysis missed it

  Only asks for input when the correct action is genuinely ambiguous.
"""


def main():
    parser = argparse.ArgumentParser(
        prog="imodent",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="imodent 1.0.0",
        help="show version and exit",
    )

    # ── Positional ──────────────────────────────────────────────────────
    parser.add_argument(
        "path",
        type=Path,
        nargs="*",
        help="file or directory to process",
    )

    # ── FIX mode flags (backward-compatible) ─────────────────────────────
    fix_group = parser.add_argument_group("fix mode", "reformat & repair files")
    fix_group.add_argument(
        "-i",
        "--indent",
        type=int,
        default=4,
        metavar="N",
        help="spaces per indent level (default: 4)",
    )
    fix_group.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="create .bak before writing",
    )
    fix_group.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="preview output, don't write",
    )
    fix_group.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="validate syntax only, no changes",
    )
    fix_group.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="walk subdirectories",
    )
    fix_group.add_argument(
        "--force",
        action="store_true",
        help="attempt fix on structurally broken code (skips pre-flight check)",
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
        help="run Ruff-backed lint checks and attach oracle evidence",
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
        help="prompt before each fix (not for batch mode)",
    )
    scan_group.add_argument(
        "--report",
        action="store_true",
        help="generate review report without modifying files",
    )
    scan_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show all findings, not just top 10",
    )

    args = parser.parse_args()

    # ── Route to mode ────────────────────────────────────────────────────
    if (
        args.analyze
        or args.imports
        or args.lint
        or args.advisory
        or args.report
        or args.interactive
        or args.fix
    ):
        analyze_files(
            paths=args.path or [],
            analyze_imports=args.imports,
            analyze_lint=args.lint,
            advisory=args.advisory,
            fix=args.fix,
            interactive=args.interactive,
            report=args.report,
            backup=args.backup,
            dry_run=args.dry_run,
            check_only=args.check,
            verbose=args.verbose,
        )
    elif not args.path:
        parser.print_usage()
    else:
        for path in args.path:
            fix_file(
                path,
                indent_size=args.indent,
                backup=args.backup,
                dry_run=args.dry_run,
                check_only=args.check,
                force=args.force,
                recursive=args.recursive,
            )


if __name__ == "__main__":
    main()
