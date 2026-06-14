# # Registration imports: these modules register side-effect strategies/analyzers into global registries. Removing them breaks the registry. Also: StrategyRegistry, FixPipeline, ProjectContext, AnalysisCoordinator are actual runtime dependencies.
# # Any scan-mode flag triggers the analysis path. --fix without --analyze
# # implies scan mode (analysis is the prerequisite for fixing).
# # FIX-only flags (-r, --force) are silently accepted but warn when
# # combined with scan mode, since scan is always recursive and skips
# # unparseable files instead of forcing.
"""CLI for imodent — code intelligence tool.

Two modes:
FIX — reformat and repair files in-place (default, backward-compatible)
SCAN — multi-file analysis: imports, lint, architecture (--analyze)

Scan-mode display features:
- Severity-bucketed findings with color-coded output (ERROR=red, WARNING=yellow,
  HINT=dim). Use --no-color to suppress ANSI codes.
- In-bucket sorting: security (S-band) → correctness (F-band) → rest.
- Compact confidence tags (e.g., [HIGH 0.90]) shown for every finding.
- Contextual guidance for RAW proof-state findings explaining next steps.
- --summary flag: aggregated file-level and rule-level view with per-file
  rule breakdowns, replacing the per-finding listing.
- --confidence flag: full decision candidate table with evidence counts,
  destructive safety, and action suggestions.
- --verbose shows proof_state, evidence kinds, and full confidence details.
"""

# NOTE: This file exceeds the 500-line structural review threshold (977 lines).
# Consider splitting into smaller modules when this module next undergoes major changes.

import argparse
import logging
import shutil
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

try:
    __version__ = _pkg_version("imodent")
except PackageNotFoundError:
    __version__ = "0.0.0dev"

# Registration imports: these modules register side-effect strategies/analyzers into global registries. Removing them breaks the registry. Also: StrategyRegistry, FixPipeline, ProjectContext, AnalysisCoordinator are actual runtime dependencies.

# Registration side-effects (do not remove)
from .analysis.coordinator import AnalysisCoordinator, FixMode
from .analysis.findings import Severity
from .pipeline import FixPipeline
from .project.discovery import is_generated_artifact
from .project.project_context import ProjectContext
from .registry import StrategyRegistry

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
    """Reformat one file or directory tree.

    Prints per-file status lines and a final aggregate count when multiple
    files are processed, so callers can distinguish "no files found" from
    "all files failed."
    """
    pipeline = FixPipeline(indent_size=indent_size)
    targets = _collect_targets(file_path, recursive=recursive)
    success_count = 0
    error_count = 0
    for target in targets:
        if _process_file(pipeline, target, backup, dry_run, check_only, force):
            success_count += 1
        else:
            error_count += 1
    if success_count + error_count > 1:
        print(
            f"Files processed: {success_count} succeeded, {error_count} failed",
            file=sys.stderr,
        )


def _collect_targets(file_path: Path, recursive: bool = False) -> list[Path]:
    """Expand file/directory to list of processable files."""
    handled = {".py", ".pyw", ".pyi", ".json", ".jsonl", ".ndjson", ".yaml", ".yml"}
    if file_path.is_file():
        if file_path.is_symlink():
            return []
        return [file_path.resolve()]
    if not file_path.exists():
        print(f"✗ {file_path}: no such file or directory", file=sys.stderr)
        return []
    if not file_path.is_dir():
        print(f"✗ {file_path}: not a regular file or directory", file=sys.stderr)
        return []
    pattern = "**/*" if recursive else "*"
    try:
        return sorted(
            f.resolve()
            for f in file_path.glob(pattern)
            if f.is_file()
            and not f.is_symlink()
            and f.suffix.lower() in handled
            and not is_generated_artifact(f.resolve())
        )
    except PermissionError:
        print(f"✗ {file_path}: permission denied", file=sys.stderr)
        return []


def _process_file(pipeline, file_path, backup, dry_run, check_only, force=False):
    """Fix one file. Write, preview, or validate depending on flags.

    Returns:
        True if the file was processed successfully (including check-only
        and dry-run modes), False if an error prevented processing.
    """
    try:
        if file_path.stat().st_size > 10_000_000:
            print(f"✗ {file_path}: file too large (>10MB)", file=sys.stderr)
            return False
    except OSError:
        pass
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"✗ {file_path}: {e}")
        return False

    if check_only:
        result = pipeline.validate(content, strategy=_strategy_for_path(file_path))
        print(f"{'✓' if result.success else '✗'} {file_path}")
        for err in result.errors:
            print(f" → {err}")
        return True

    result = pipeline.fix(content, strategy=_strategy_for_path(file_path), force=force)

    if dry_run:
        print(f"\n--- {file_path} ---")
        print(result.content, end="")
        for w in result.warnings:
            print(f" ⚠ {w}")
        return True

    if not result.success:
        print(f"✗ {file_path}")
        for err in result.errors:
            print(f" → {err}")
        for w in result.warnings:
            print(f" ⚠ {w}")
        return False

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
            return False
        try:
            shutil.copy2(file_path, bak)
        except OSError as e:
            print(f"✗ {file_path}: could not create backup: {e}")
            return False
        print(f" ↳ backup → {bak}")

    if file_path.is_symlink():
        print(f"✗ {file_path}: refusing to write through symlink")
        return False
    try:
        file_path.write_text(result.content, encoding="utf-8")
    except (OSError, UnicodeError) as e:
        print(f"✗ {file_path}: could not write file: {e}")
        return False
    print(f"✓ {file_path}")
    for err in result.errors:
        print(f" ✗ {err}")
    for w in result.warnings:
        print(f" ⚠ {w}")
    return True


def _strategy_for_path(file_path: Path):
    strategy_class = StrategyRegistry.get_by_extension(file_path.suffix.lower())
    return strategy_class() if strategy_class else None


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
    summary: bool = False,
    color: bool | None = None,
    check_rust: bool = False,
    run_cargo: bool = False,
    run_cargo_check: bool = False,
    run_cargo_clippy: bool = False,
    show_graph: bool = False,
):
    """Scan project for import issues, lint violations, architectural drift, Rust advisory.

    If *show_graph* is True, prints the Mermaid dependency graph after
    the findings summary.
    """
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

    if fix and not analyze_imports:
        print(
            "Note: import analysis is disabled in config (check_imports: false). "
            "Use --imports to override, or --fix may produce zero fixes.",
            file=sys.stderr,
        )

    # Apply Rust/Cargo flags
    if check_rust or run_cargo or run_cargo_check or run_cargo_clippy:
        project_context.config.check_rust = True
    if run_cargo:
        project_context.config.run_cargo = True
        project_context.config.run_cargo_check = True
        project_context.config.run_cargo_clippy = True
    if run_cargo_check:
        project_context.config.run_cargo_check = True
    if run_cargo_clippy:
        project_context.config.run_cargo_clippy = True

    coordinator = AnalysisCoordinator(project_context=project_context)
    result = coordinator.analyze(resolved_paths)
    if not result.context.files:
        print("No matching files found.")
        return

    # Resolve color mode: explicit flag overrides TTY auto-detection
    use_color = color if color is not None else sys.stdout.isatty()

    # Build finding_id → confidence lookup from decision candidates
    confidence_by_finding_id: dict[str, tuple[float, str]] = {}
    for c in result.candidates:
        for fid in c.finding_ids:
            confidence_by_finding_id[fid] = (c.confidence, c.confidence_label)

    # ── Summary ──────────────────────────────────────────────────────────
    print(result.summary())
    print()
    if not result.findings:
        print("✓ Clean — no issues detected.")
        return

    # ── Aggregated summary view (--summary) ─────────────────────────────
    if summary:
        _display_summary_view(result, use_color, verbose,
                              confidence_by_finding_id)
        if not confidence:
            return  # Don't show per-finding listing when --summary only

    # ── Top Issues: 3 most critical findings ─────────────────────────────
    _display_top_issues(result.findings, use_color)

    # ── Findings by severity ──────────────────────────────────────────────
    for severity in [Severity.ERROR, Severity.WARNING, Severity.INFO, Severity.HINT]:
        bucket = [f for f in result.findings if f.severity == severity]
        if not bucket:
            continue
        # Sort within bucket: security (S-band) → correctness (F-band) → rest
        bucket = _sort_findings_by_priority(bucket)
        label = severity.value.upper()
        color_fn = _severity_color(severity) if use_color else lambda x: x
        header = f"\n{color_fn(label)} ({len(bucket)}):"
        print(header)
        shown = bucket if verbose else bucket[:10]
        for f in shown:
            loc = f":{f.location.line}" if f.location else ""
            # Confidence tag
            conf_tuple = confidence_by_finding_id.get(f.id)
            conf_tag = _format_confidence_tag(conf_tuple)
            # Security marker
            sec_marker = _security_marker(f)
            # Color the file:loc
            file_loc = f"{f.file.name}{loc}"
            if use_color:
                file_loc = color_fn(file_loc)
            msg = f" {file_loc}: {conf_tag}{sec_marker}{f.message}"
            print(msg)
            if f.guidance:
                print(f"   → {f.guidance}")
            if verbose and f.proof_state:
                print(f"   proof_state: {f.proof_state}")
                evidence = f.data.get("evidence") or []
                if evidence:
                    kinds = sorted({item.get("kind", "evidence") for item in evidence})
                    print(f"   evidence: {', '.join(kinds)}")
                if conf_tuple:
                    confidence, conf_label = conf_tuple
                    print(f"   confidence: {confidence:.2f} ({conf_label})")
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

    # ── Dependency graph ─────────────────────────────────────────────────
    if show_graph:
        _display_graph_output(result)

    # ── Determine fix mode ────────────────────────────────────────────────
    config = project_context.config
    if interactive or config.interactive:
        fix_mode = FixMode.INTERACTIVE
        print("\n" + "━" * 60)
        print("INTERACTIVE MODE — prompt before each fix")
        print("━" * 60)
    elif report:
        fix_mode = FixMode.REPORT
        print("\n" + "━" * 60)
        print("REVIEW MODE — no files modified")
        print("━" * 60)
    elif fix and not check_only:
        fix_mode = FixMode.ALL_AUTO if config.auto_fix_all else FixMode.SAFE_AUTO
        print("\n" + "━" * 60)
        print(
            "AUTO-FIX MODE — safe fixes only"
            if fix_mode == FixMode.SAFE_AUTO
            else "AUTO-FIX MODE — all fixes"
        )
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
                    except OSError as e:
                        print(f" ✗ {file_path}")
                        print(f"  → could not create backup: {e}")
                        continue
                    print(f" ↳ backup → {bak}")
                if file_path.is_symlink():
                    print(f" ✗ {file_path}")
                    print("  → refusing to write through symlink")
                    continue
                try:
                    file_path.write_text(fix_result.content, encoding="utf-8")
                except (OSError, UnicodeError) as e:
                    print(f" ✗ {file_path}")
                    print(f"  → could not write file: {e}")
                    continue
                print(f" ✓ {file_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ANSI color codes for severity-based display
# ---------------------------------------------------------------------------

# ANSI escape sequences (only emitted when use_color is True)
_ANSI_RED = "\033[91m"
_ANSI_YELLOW = "\033[93m"
_ANSI_DIM = "\033[2m"
_ANSI_RESET = "\033[0m"


def _severity_color(severity):
    """Return a function that wraps text in the ANSI color for the severity.

    Args:
        severity: A Severity enum value.

    Returns:
        Callable ``str -> str`` that applies the color code (or identity if no match).
    """
    if severity == Severity.ERROR:
        return lambda text: f"{_ANSI_RED}{text}{_ANSI_RESET}"
    if severity == Severity.WARNING:
        return lambda text: f"{_ANSI_YELLOW}{text}{_ANSI_RESET}"
    if severity == Severity.HINT:
        return lambda text: f"{_ANSI_DIM}{text}{_ANSI_RESET}"
    return lambda text: text


def _sort_findings_by_priority(findings: list) -> list:
    """Sort findings within the same severity bucket by diagnostic priority.

    Priority order:
    1. Security-related: lint codes matching S3xx, S6xx patterns
    2. Correctness issues: F-band lint codes (F401, F821, F841, F811)
    3. Everything else: style, SIM, TRY, B, etc.

    Args:
        findings: List of Finding objects at the same severity level.

    Returns:
        New list sorted by priority (most critical first).
    """
    def _priority_key(f):
        code = getattr(f, "lint_code", None) or ""
        if code[:1] == "S" and code[1:2].isdigit():
            # S-band: security (e.g., S310, S608)
            return (0, code)
        if code[:1] == "F" and code[1:2].isdigit():
            # F-band: correctness (e.g., F401, F821)
            return (1, code)
        return (2, code)

    return sorted(findings, key=_priority_key)


def _security_marker(finding) -> str:
    """Return '[SEC] ' if the finding has an S-band security lint code.

    Args:
        finding: A Finding object.

    Returns:
        ``'[SEC] '`` for S3xx/S6xx codes, empty string otherwise.
    """
    code = getattr(finding, "lint_code", None) or ""
    if code and code[:1] == "S" and code[1:2].isdigit():
        return "[SEC] "
    return ""


def _format_confidence_tag(conf_tuple: tuple | None) -> str:
    """Format a compact confidence tag from a (score, label) tuple.

    Args:
        conf_tuple: ``(confidence: float, label: str)`` or None.

    Returns:
        String like ``'[HIGH 0.90] '``, ``'[LOW 0.30] '``, or empty.
    """
    if conf_tuple is None:
        return ""
    score, label = conf_tuple
    return f"[{label.upper()} {score:.2f}] "


def _display_summary_view(
    result, use_color: bool, verbose: bool,
    confidence_lookup: dict[str, tuple[float, str]],
) -> None:
    """Print file-level and rule-level aggregated view (--summary flag).

    Shows:
    - Top files by finding count with rule breakdown
    - Top rules by finding count
    - Per-file: rule breakdown with counts
    """
    from collections import defaultdict

    by_file: dict[str, list] = defaultdict(list)
    by_rule: dict[str, int] = defaultdict(int)

    for f in result.findings:
        fname = f.file.name
        code = getattr(f, "lint_code", None) or f.type
        by_file[fname].append((code, f))
        by_rule[code] += 1

    # ── Top files ───────────────────────────────────────────────────────
    top_files = sorted(by_file.items(), key=lambda x: -len(x[1]))[:10]
    print()
    print("═══ Top files by finding count ═══")
    for fname, items in top_files:
        code_counts: dict[str, int] = defaultdict(int)
        for code, _ in items:
            code_counts[code] += 1
        breakdown = ", ".join(
            f"{code}({cnt})"
            for code, cnt in sorted(code_counts.items(), key=lambda x: -x[1])
        )
        print(f"  {fname}: {len(items)} findings → {breakdown}")
        if verbose:
            for _code, finding in items:
                loc = f":{finding.location.line}" if finding.location else ""
                conf_tag = _format_confidence_tag(confidence_lookup.get(finding.id))
                print(f"    {finding.file.name}{loc}: {conf_tag}{finding.message}")

    # ── Top rules ────────────────────────────────────────────────────────
    top_rules = sorted(by_rule.items(), key=lambda x: -x[1])[:10]
    print()
    print("═══ Top rules by finding count ═══")
    for code, count in top_rules:
        print(f"  {code}: {count}")

    # ── Per-file rule breakdown ──────────────────────────────────────────
    print()
    print("═══ Per-file rule breakdown ═══")
    for fname in sorted(by_file.keys()):
        items = by_file[fname]
        code_counts: dict[str, int] = defaultdict(int)
        for code, _ in items:
            code_counts[code] += 1
        breakdown = ", ".join(
            f"{code}({cnt})"
            for code, cnt in sorted(code_counts.items(), key=lambda x: -x[1])
        )
        print(f"  {fname}: {len(items)} total — {breakdown}")


def _display_top_issues(findings: list, use_color: bool) -> None:
    """Print the 3 most critical findings as a 'Fix these first' summary.

    Critical = ERROR severity OR WARNING with S-band security lint code.
    Sorted by severity (ERROR first) then by priority.

    Args:
        findings: All findings from the analysis run.
        use_color: Whether to emit ANSI color codes.
    """
    critical = [
        f for f in findings
        if f.severity in (Severity.ERROR, Severity.WARNING)
        and (
            f.severity == Severity.ERROR
            or (getattr(f, "lint_code", None) or "").startswith("S")
        )
    ]
    if not critical:
        return

    critical = sorted(critical, key=lambda f: (0 if f.severity == Severity.ERROR else 1, f.message))
    top3 = critical[:3]

    color_fn = _severity_color(Severity.ERROR) if use_color else lambda x: x
    print(color_fn("┌─ Fix these first:"))
    for f in top3:
        loc = f":{f.location.line}" if f.location else ""
        sec = _security_marker(f)
        marker = "🔴" if f.severity == Severity.ERROR else "🟡"
        print(color_fn(f"│ {marker} {f.file.name}{loc}: {sec}{f.message}"))
    print(color_fn("└─"))


def _display_confidence_output(result, verbose: bool = False) -> None:
    """Print the full decision candidate table with evidence, confidence scores, and actions.

    This is the verbose confidence view triggered by --confidence.
    Lightweight confidence tags are shown in the default findings display;
    this function shows the complete decision-grade output.
    """
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

        print(
            f"\n[{i}] {c.issue_type.upper()} — {c.confidence_label} confidence ({c.confidence:.2f})"
        )
        print(f"    path:      {rel}{location_str}")
        print(
            f"    subject:   {sk.kind}  module={sk.module}  name={sk.name}  alias={sk.alias}"
        )
        print(f"    proof:     {c.proof_state}")
        print(
            f"    findings:  {', '.join(c.finding_ids) if c.finding_ids else '(none)'}"
        )

        if c.evidence_for:
            kinds = sorted({e.kind for e in c.evidence_for})
            print(f"    evidence for:  {', '.join(kinds)}")
        if c.evidence_against:
            kinds = sorted({e.kind for e in c.evidence_against})
            print(f"    evidence against: {', '.join(kinds)}")

        if c.suggested_actions:
            print(f"    actions:   {' | '.join(a.label for a in c.suggested_actions)}")

        print(f"    destructive: {'ALLOWED' if c.destructive_allowed else 'BLOCKED'}")
        print(
            f"    user decision: {'REQUIRED' if c.requires_user_decision else 'not required'}"
        )

        if verbose:
            for ev in c.evidence_for[:5]:
                print(
                    f"       + {ev.claim or ev.kind} ({ev.polarity}, {ev.strength:.2f})"
                )
            for ev in c.evidence_against[:5]:
                print(
                    f"       - {ev.claim or ev.kind} ({ev.polarity}, {ev.strength:.2f})"
                )


def _display_graph_output(result) -> None:
    """Print the dependency graph as a Mermaid flowchart.

    Args:
        result: An ``AnalysisResult`` whose ``context.graph`` provides the
            dependency data.

    The output is a ``flowchart LR`` Mermaid diagram ready for embedding
    in Markdown or rendering with a Mermaid-compatible viewer.

    If the graph has no import edges, prints a notice instead of an
    empty diagram.
    """
    graph = result.context.graph
    if not graph.imports:
        print("\n" + "━" * 60)
        print("DEPENDENCY GRAPH")
        print("━" * 60)
        print("  (no import relationships detected)")
        return

    print("\n" + "━" * 60)
    print("DEPENDENCY GRAPH (Mermaid)")
    print("━" * 60)
    try:
        mermaid = graph.to_mermaid()
        print(mermaid)
    # NOTE: broad except Exception here is technical debt — Mermaid diagram
    # rendering is display-only; failures should not abort the entire tool.
    # This would catch MemoryError/KeyboardInterrupt/SystemExit which should propagate.
    # Narrow to specific exception types when the failure surface is understood.
    except Exception as exc:
        print(f"  Error rendering graph: {exc}")


DESCRIPTION = """\
imodent — code intelligence tool

Two modes of operation:

FIX mode (default)
  Reformat & repair files in-place.
  Detects language, fixes code, validates output.

SCAN mode (--analyze)
  Multi-file project analysis.
  Import hygiene, lint violations, architectural drift, Rust advisory.

Operates on Python, JSON, JSONL, YAML, and Rust files.
Rust support is advisory-only (no formatting, no auto-fix).
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

  # ── SCAN mode with Rust advisory ──────────────────────────────────────
  imodent . --analyze --rust --report --confidence
  imodent . --analyze --rust --cargo --report --verbose

  Scan mode always recurses into subdirectories. -b, -n, and -c also work
  with scan fixes; --force and -r are fix-mode-only controls.

  Rust support is advisory-only. --rust scans Cargo.toml, clippy.toml,
  rustfmt.toml, and .rs source for configuration and residue patterns.
  --cargo runs external Cargo/Clippy oracles (may be slower).
  imodent does not edit Rust files.

  import analysis gives you options per finding:
  • investigate — search codebase before deciding
  • keep — preserve it (type hints, re-exports, __all__)
  • delete — remove the unused import
  • false-positive — mark as used if analysis missed it

  Only asks for input when the correct action is genuinely ambiguous.
"""


def _warn_fix_mode_flags(args) -> None:
    """Warn when SCAN-mode flags are passed but no scan was requested.

    Prints a single stderr line listing each inapplicable flag so the
    operator knows they are being silently ignored.  Only called from the
    FIX-mode branch of ``main()``.
    """
    warn_flags: list[str] = []
    if args.verbose:
        warn_flags.append("--verbose")
    if args.color is not None:
        warn_flags.append("--color" if args.color else "--no-color")
    if args.graph:
        warn_flags.append("--graph")
    if warn_flags:
        print(
            "Note: the following SCAN-mode flags have no effect in FIX mode: "
            + ", ".join(warn_flags),
            file=sys.stderr,
        )


def main():
    """Entry point. Parses CLI args into argparse namespace, routes to either:
    - fix_file() for FIX mode (default, backward-compatible path-based reformatting)
    - analyze_files() for SCAN mode (any --analyze/--imports/--lint/--advisory/--fix/--report/--interactive/--confidence flag)
    Routing decision: any scan flag → analyze; missing path → print usage; otherwise → fix.
    fix_file() never returns a status code — exits 0 on all paths.
    """
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s: %(message)s"
    )

    parser = argparse.ArgumentParser(
        prog="imodent",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"imodent {__version__}",
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
        help="spaces per indent level (default: 4, FIX mode only)",
    )
    fix_group.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="create .bak before writing (both FIX and SCAN mode)",
    )
    fix_group.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="preview output, don't write (both FIX and SCAN mode)",
    )
    fix_group.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="validate syntax only in FIX mode; in SCAN mode prevents --fix from applying",
    )
    fix_group.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="walk subdirectories (FIX mode only; SCAN is always recursive)",
    )
    fix_group.add_argument(
        "--force",
        action="store_true",
        help="attempt fix on structurally broken code (FIX mode only)",
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
        help="auto-fix safe findings; implies --analyze (SCAN mode only)",
    )
    scan_group.add_argument(
        "--interactive",
        action="store_true",
        help="prompt before each fix, not for batch mode (SCAN mode only)",
    )
    scan_group.add_argument(
        "--report",
        action="store_true",
        help="generate review report without modifying files (SCAN mode only)",
    )
    scan_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show all findings, not just top 10 (SCAN mode only)",
    )
    scan_group.add_argument(
        "--confidence",
        action="store_true",
        help="show full decision candidate table with evidence and scores (SCAN mode only)",
    )
    scan_group.add_argument(
        "--summary",
        action="store_true",
        help="show file-level and rule-level aggregation instead of per-finding listing (SCAN mode only)",
    )
    scan_group.add_argument(
        "--color",
        action="store_true",
        default=None,
        help="force color output (SCAN mode only)",
    )
    scan_group.add_argument(
        "--no-color",
        action="store_false",
        dest="color",
        help="disable color output even when stdout is a TTY (SCAN mode only)",
    )
    scan_group.add_argument(
        "--rust",
        action="store_true",
        help="include Rust advisory analysis (config/source scan only, no formatting)",
    )
    scan_group.add_argument(
        "--cargo",
        action="store_true",
        help="run Cargo/Clippy external oracles; implies --rust (may be slower)",
    )
    scan_group.add_argument(
        "--cargo-check",
        action="store_true",
        help="run cargo check oracle only; implies --rust",
    )
    scan_group.add_argument(
        "--cargo-clippy",
        action="store_true",
        help="run cargo clippy oracle only; implies --rust",
    )
    scan_group.add_argument(
        "--graph",
        action="store_true",
        help="render dependency graph as Mermaid flowchart (SCAN mode only)",
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
        or args.summary
        or args.rust
        or args.cargo
        or args.cargo_check
        or args.cargo_clippy
        or args.graph
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
            summary=args.summary,
            color=args.color,
            check_rust=args.rust,
            run_cargo=args.cargo,
            run_cargo_check=args.cargo_check,
            run_cargo_clippy=args.cargo_clippy,
            show_graph=args.graph,
        )
    elif not args.path:
        parser.print_usage()
    else:
        # Warn about SCAN-mode flags that have no effect in FIX mode.
        _warn_fix_mode_flags(args)
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
