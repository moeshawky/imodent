"""CLI for imodent — code intelligence tool.

Two modes:
FIX — reformat and repair files in-place (default, backward-compatible)
SCAN — multi-file analysis: imports, lint, architecture (--analyze)
"""

import argparse
import shutil
import sys
from pathlib import Path

# Registration imports: these modules register side-effect strategies/analyzers into global registries. Removing them breaks the registry. Also: StrategyRegistry, FixPipeline, ProjectContext, AnalysisCoordinator are actual runtime dependencies.

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
        if file_path.is_symlink():
            return []
        return [file_path.resolve()]
    pattern = "**/*" if recursive else "*"
    return sorted(
        f.resolve()
        for f in file_path.glob(pattern)
        if f.is_file()
        and not f.is_symlink()
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

    if check_only:
        result = pipeline.validate(content, strategy=_strategy_for_path(file_path))
        print(f"{'✓' if result.success else '✗'} {file_path}")
        for err in result.errors:
            print(f" → {err}")
        return

    result = pipeline.fix(content, strategy=_strategy_for_path(file_path), force=force)

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
        if bak.is_symlink():
            print(f"✗ {file_path}: refusing to write backup through symlink: {bak}")
            return
        try:
            shutil.copy2(file_path, bak)
        except Exception as e:
            print(f"✗ {file_path}: could not create backup: {e}")
            return
        print(f" ↳ backup → {bak}")

    try:
        file_path.write_text(result.content, encoding="utf-8")
    except Exception as e:
        print(f"✗ {file_path}: could not write file: {e}")
        return
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
    analyze_imports: bool | None = None,
    analyze_lint: bool | None = None,
    advisory: bool = False,
    fix: bool = False,
    interactive: bool = False,
    report: bool = False,
    backup: bool = False,
    dry_run: bool = False,
    check_only: bool = False,
    verbose: bool = False,
    confidence: bool = False,
):
    """Scan project for import issues, lint violations, architectural drift."""
    if not paths:
        print("No matching files found.")
        return

    resolved_paths = [path.resolve() for path in paths]
    project_context = ProjectContext.discover(resolved_paths[0])
    if analyze_imports is None:
        analyze_imports = project_context.config.check_imports
    if analyze_lint is None:
        analyze_lint = project_context.config.check_lint
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

    # ── Confidence display ─────────────────────────────────────────────
    if confidence:
        _display_confidence_output(result, verbose)

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
        if fix and check_only:
            print(
                "Note: --check prevents --fix from applying changes. "
                "Remove --check to apply fixes.",
                file=sys.stderr,
            )

    # ── Apply fixes ───────────────────────────────────────────────────────
    if fix_mode is not None:
        fix_results = coordinator.fix(
            result.findings,
            result.context,
            mode=fix_mode,
            candidates=result.candidates,
        )
        for file_path, fix_result in fix_results.items():
            if not fix_result.success:
                print(f" ✗ {file_path}")
                for err in fix_result.errors:
                    print(f"  → {err}")
                continue
            if dry_run or fix_mode == FixMode.REPORT:
                tag = "dry-run" if dry_run else "review"
                print(f" ~ {file_path} ({tag})")
                print(fix_result.content[:500])
            else:
                if backup:
                    bak = file_path.with_suffix(file_path.suffix + ".bak")
                    if bak.is_symlink():
                        print(f" ✗ {file_path}")
                        print(f"  → refusing to write backup through symlink: {bak}")
                        continue
                    try:
                        shutil.copy2(file_path, bak)
                    except Exception as e:
                        print(f" ✗ {file_path}")
                        print(f"  → could not create backup: {e}")
                        continue
                    print(f" ↳ backup → {bak}")
                try:
                    file_path.write_text(fix_result.content, encoding="utf-8")
                except Exception as e:
                    print(f" ✗ {file_path}")
                    print(f"  → could not write file: {e}")
                    continue
                print(f" ✓ {file_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _display_confidence_output(result, verbose: bool = False) -> None:
    """Print decision-grade output with evidence and confidence."""
    candidates = getattr(result, "candidates", []) or []
    if not candidates:
        print("\nNo decision candidates available.")
        return

    print()
    print("=" * 60)
    print("DECISION CANDIDATES (confidence-weighted)")
    print("=" * 60)

    for i, c in enumerate(candidates, 1):
        sk = c.subject_key
        rel = str(sk.file)
        location_str = ""
        if c.location:
            location_str = f":{c.location.line}"

        print(f"\n[{i}] {c.issue_type.upper()} — {c.confidence_label} confidence ({c.confidence:.2f})")
        print(f"    path:      {rel}{location_str}")
        print(f"    subject:   {sk.kind}  module={sk.module}  name={sk.name}  alias={sk.alias}")
        print(f"    proof:     {c.proof_state}")
        print(f"    findings:  {', '.join(c.finding_ids) if c.finding_ids else '(none)'}")

        if c.evidence_for:
            kinds = sorted({e.kind for e in c.evidence_for})
            print(f"    evidence for:  {', '.join(kinds)}")
        if c.evidence_against:
            kinds = sorted({e.kind for e in c.evidence_against})
            print(f"    evidence against: {', '.join(kinds)}")

        if c.suggested_actions:
            print(f"    actions:   {' | '.join(a.label for a in c.suggested_actions)}")

        print(f"    destructive: {'ALLOWED' if c.destructive_allowed else 'BLOCKED'}")
        print(f"    user decision: {'REQUIRED' if c.requires_user_decision else 'not required'}")

        if verbose:
            for ev in c.evidence_for[:5]:
                print(f"       + {ev.claim or ev.kind} ({ev.polarity}, {ev.strength:.2f})")
            for ev in c.evidence_against[:5]:
                print(f"       - {ev.claim or ev.kind} ({ev.polarity}, {ev.strength:.2f})")


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
  imodent ./src --fix                         auto-fix safe import issues (analysis implied)
  imodent ./src --report                      review report without modifying files

  Scan mode always recurses into subdirectories. -b, -n, and -c also work
  with scan fixes; --force and -r are fix-mode-only controls.

  import analysis gives you options per finding:
  • delete — remove the unused import
  • keep — preserve it (type hints, re-exports, __all__)
  • investigate — search codebase before deciding
  • false-positive — mark as used if analysis missed it

  Only asks for input when the correct action is genuinely ambiguous.
"""


def main():
    """Entry point. Parses CLI args into argparse namespace, routes to either:
    - fix_file() for FIX mode (default, backward-compatible path-based reformatting)
    - analyze_files() for SCAN mode (any --analyze/--imports/--lint/--advisory/--fix/--report/--interactive/--confidence flag)
    Routing decision: any scan flag → analyze; missing path → print usage; otherwise → fix.
    fix_file() never returns a status code — exits 0 on all paths.
    """
    parser = argparse.ArgumentParser(
        prog="imodent",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="imodent 1.0.0a1",
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
    scan_group.add_argument(
        "--confidence",
        action="store_true",
        help="show decision candidates with evidence and confidence scores",
    )

    args = parser.parse_args()

    # ── Route to mode ────────────────────────────────────────────────────
    # Any scan-mode flag triggers the analysis path. --fix without --analyze
    # implies scan mode (analysis is the prerequisite for fixing).
    # FIX-only flags (-r, --force) are silently accepted but warn when
    # combined with scan mode, since scan is always recursive and skips
    # unparseable files instead of forcing.
    scan_requested = (
        args.analyze
        or args.imports
        or args.lint
        or args.advisory
        or args.report
        or args.interactive
        or args.fix
        or args.confidence
    )
    if scan_requested:
        if args.recursive:
            print(
                "Note: scan mode is always recursive; -r has no additional effect.",
                file=sys.stderr,
            )
        if args.force:
            print(
                "Note: --force is a fix-mode flag only. Scan mode skips files "
                "with syntax errors and reports them as unanalyzable.",
                file=sys.stderr,
            )
        analyze_files(
            paths=args.path or [],
            analyze_imports=True if args.imports else None,
            analyze_lint=True if args.lint else None,
            advisory=args.advisory,
            fix=args.fix,
            interactive=args.interactive,
            report=args.report,
            backup=args.backup,
            dry_run=args.dry_run,
            check_only=args.check,
            verbose=args.verbose,
            confidence=args.confidence,
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
