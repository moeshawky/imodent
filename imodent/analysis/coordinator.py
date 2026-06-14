"""Analysis coordinator — orchestrates all analyzers, fixers, evidence, and guidance.

The AnalysisCoordinator:
1. Discovers files, builds dependency graph, creates AnalysisContext
2. Runs configured analyzers (ImportAnalyzer, LintAnalyzer, ResidueAnalyzer,
   RustAnalyzer) and collects findings
3. Deduplicates findings (Ruff F401 evidence takes priority over local reports)
4. Initializes proof states and attaches contextual human-readable guidance
   for RAW findings
5. Builds DecisionCandidates from findings + evidence via DecisionEngine
6. Applies fixes via fixers when a fix mode is active

AnalysisResult.summary() produces multi-dimensional aggregation:
severity counts, top files, and top rules (lint codes or finding types).
"""

# NOTE: This file exceeds the 500-line structural review threshold (826 lines).
# Consider splitting into smaller modules when this module next undergoes major changes.

from __future__ import annotations

import contextlib
import fnmatch
import sys
import time
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from ..graph.dependency import build_dependency_graph, trace_symbol_usage
from ..interfaces import FixResult
from ..project.discovery import is_generated_artifact
from .context import AnalysisConfig, AnalysisContext, FileInfo
from .decision_engine import DecisionEngine, _issue_type_from_finding
from .decision_subjects import _subject_key_from_finding
from .evidence import Evidence
from .findings import Finding, FixOption, ProofState, Severity

if TYPE_CHECKING:
    from ..project.project_context import ProjectContext
    from .decision_models import DecisionCandidate


class FixMode(Enum):
    """How to handle fixes."""

    SAFE_AUTO = "safe_auto"  # Only fix auto_fix_safe findings
    ALL_AUTO = "all_auto"  # Fix all fixable findings
    INTERACTIVE = "interactive"  # Present options for each finding
    REPORT = "report"  # Don't fix, just report


class AnalysisCoordinator:
    """Coordinates analysis across multiple files and analyzers."""

    def __init__(
        self,
        config: AnalysisConfig | None = None,
        project_context: ProjectContext | None = None,
    ):
        """
        Initialize coordinator.

        Args:
            config: Analysis configuration (uses defaults if None)
            project_context: Optional project context for project-aware analysis
        """
        self.project_context = project_context
        if project_context is not None:
            self.config = project_context.config
        else:
            self.config = config or AnalysisConfig()
        self._import_fixer = None  # Bug 6: lazy-init to avoid recreating on every call

    def analyze(
        self,
        paths: list[Path],
        analyzers: list[str] | None = None,
    ) -> AnalysisResult:
        """
        Run analysis on paths.

        Tracks and reports file-load failures on stderr (OSError,
        UnicodeDecodeError) so callers can distinguish "file skipped"
        from "file never attempted".  Prints a ``Files loaded: N,
        failed to load: M`` summary when any file fails to load.

        Args:
            paths: Files/directories to analyze
            analyzers: Specific analyzers to run (None = all)

        Returns:
            AnalysisResult with files, graph, and findings
        """
        start_time = time.time()

        # Expand paths to files (use project context if available)
        files = self._discover_files(paths)

        # Load file contents
        file_infos = {}
        load_failures = 0
        for path in files:
            try:
                file_infos[path] = FileInfo.from_path(path)
            except (OSError, UnicodeDecodeError) as e:
                print(f"Warning: Could not load {path}: {e}", file=sys.stderr)
                load_failures += 1

        # Print load-failure summary when any file fails to load; otherwise
        # callers cannot distinguish "file skipped" from "file never attempted."
        if load_failures:
            print(
                f"Files loaded: {len(file_infos)}, failed to load: {load_failures}",
                file=sys.stderr,
            )

        # Build dependency graph (use project context root if available)
        if self.project_context is not None:
            project_root = self.project_context.project_root
        else:
            project_root = self._find_project_root(paths)
        graph = build_dependency_graph(file_infos, project_root)

        # Create context
        context = AnalysisContext(
            files=file_infos,
            graph=graph,
            findings=[],
            evidence=[],
            config=self.config,
            project_root=project_root,
        )

        # Run analyzers
        all_findings = []
        for analyzer in self._get_analyzers(analyzers):
            try:
                findings = analyzer.analyze(context)
                all_findings.extend(findings)
            # NOTE: broad except Exception here is technical debt — warn-and-continue
            # resilience for scan pipeline prevents single-analyzer failures from aborting.
            # This would catch MemoryError/KeyboardInterrupt/SystemExit which should propagate.
            # Narrow to specific exception types when the failure surface is understood.
            except Exception as e:
                print(f"Warning: Analyzer {analyzer.name} failed: {e}", file=sys.stderr)

        all_findings = _deduplicate_findings(all_findings)

        # Syntax check (gated by config.check_syntax)
        if self.config.check_syntax:
            for path, file_info in file_infos.items():
                if file_info.has_syntax_errors:
                    all_findings.append(
                        Finding.create(
                            type="syntax_error",
                            severity=Severity.ERROR,
                            file=path,
                            message=f"{path.name}: contains syntax errors",
                            fixable=False,
                            auto_fix_safe=False,
                            proof_state=ProofState.PROVEN_UNUSED.value,
                        )
                    )

        # Type checking (gated by config.check_types / config.use_pyright)
        if self.config.check_types:
            from ..analyzers.types import check_mypy

            try:
                all_findings.extend(check_mypy(context))
            # NOTE: broad except Exception here is technical debt — mypy subprocess
            # failures should not abort the entire scan pipeline.
            # This would catch MemoryError/KeyboardInterrupt/SystemExit which should propagate.
            # Narrow to specific exception types when the failure surface is understood.
            except Exception as e:
                print(f"Warning: mypy check failed: {e}", file=sys.stderr)

        if self.config.use_pyright:
            from ..analyzers.types import check_pyright

            try:
                all_findings.extend(check_pyright(context))
            # NOTE: broad except Exception here is technical debt — pyright subprocess
            # failures should not abort the entire scan pipeline.
            # This would catch MemoryError/KeyboardInterrupt/SystemExit which should propagate.
            # Narrow to specific exception types when the failure surface is understood.
            except Exception as e:
                print(f"Warning: pyright check failed: {e}", file=sys.stderr)

        # Cross-project symbol usage evidence (gated by config.check_imports)
        if self.config.check_imports:
            # NOTE: broad except Exception here is technical debt — cross-file
            # evidence is internal code whose failure should not be hidden
            try:
                _add_cross_file_evidence(all_findings, context)
            except Exception as e:
                print(f"Warning: cross-file evidence failed: {e}", file=sys.stderr)

        self._initialize_proof_states(all_findings)
        self._attach_guidance(all_findings)
        context.findings = all_findings

        # Build decision candidates from findings and evidence
        engine = DecisionEngine()
        candidates = engine.build_candidates(all_findings, context.evidence)

        elapsed = time.time() - start_time

        return AnalysisResult(
            context=context,
            elapsed_time=elapsed,
            analyzer_names=[a.name for a in self._get_analyzers(analyzers)],
            candidates=candidates,
        )

    def fix(
        self,
        findings: list[Finding],
        context: AnalysisContext,
        mode: FixMode = FixMode.SAFE_AUTO,
        decisions: dict[str, str] | None = None,
        candidates: list[DecisionCandidate] | None = None,
    ) -> dict[Path, FixResult]:
        """
        Fix findings and print an aggregate summary on stderr.

        Tracks four counters across the fix pass: files modified, files
        unchanged, files missing context (no ``FileInfo`` available), and
        findings without a fixer.  Prints ``Fix summary: N files modified,
        M unchanged`` (plus missing-context and without-fixer counts when
        non-zero) so callers can differentiate "no actionable fixes" from
        "file silently skipped."

        Args:
            findings: Findings to fix
            context: Analysis context
            mode: How to handle fixes
            decisions: Pre-made decisions (finding_id -> option_id)
            candidates: DecisionEngine output used as the safety authority

        Returns:
            Dict of path -> FixResult
        """
        if mode == FixMode.INTERACTIVE and not sys.stdin.isatty():
            print(
                "Warning: Interactive mode requested but stdin is not a TTY. "
                "Falling back to safe-auto fix mode.",
                file=sys.stderr,
            )
            mode = FixMode.SAFE_AUTO

        results = {}
        decisions = decisions or {}
        if candidates is None:
            candidates = DecisionEngine.build_candidates(findings, context.evidence)
        candidate_by_finding_id = _index_candidates_by_finding_id(candidates)

        # Group findings by file
        by_file = {}
        for finding in findings:
            if finding.file not in by_file:
                by_file[finding.file] = []
            by_file[finding.file].append(finding)

        # Process each file
        files_with_findings = len(by_file)
        files_missing_context = 0
        files_modified = 0
        findings_without_fixer = 0
        for file_path, file_findings in by_file.items():
            file_info = context.files.get(file_path)
            if not file_info:
                files_missing_context += 1
                continue

            content = file_info.content

            # Fix each finding (in reverse order to preserve line numbers)
            for finding in sorted(
                file_findings,
                key=lambda f: (f.location.line if f.location else 0, f.id),
                reverse=True,
            ):
                candidate = candidate_by_finding_id.get(finding.id)
                issue_type = (
                    candidate.issue_type
                    if candidate is not None
                    else _issue_type_from_finding(finding)
                )
                routed_finding = _finding_with_issue_type(finding, issue_type)
                fixer = self._get_fixer(routed_finding)
                if not fixer:
                    findings_without_fixer += 1
                    continue

                # Determine action based on mode
                if mode == FixMode.SAFE_AUTO:
                    if (
                        candidate is None
                        or candidate.requires_user_decision
                        or not candidate.destructive_allowed
                    ):
                        continue  # Skip
                    option = _destructive_option(fixer, routed_finding, context)
                    if not option:
                        # Fall back to first safe option for
                        # non-destructive fix types (e.g. add_import)
                        option = _first_safe_option(fixer, routed_finding, context)
                    if not option:
                        continue

                elif mode == FixMode.ALL_AUTO:
                    # ALL_AUTO is more permissive than SAFE_AUTO: it will
                    # still try non-destructive (safe) options even when
                    # destructive_allowed is False.  SAFE_AUTO skips
                    # entirely in that case (see branch above).
                    if (
                        candidate is None
                        or candidate.requires_user_decision
                    ):
                        continue
                    option = None
                    if candidate.destructive_allowed:
                        option = _destructive_option(fixer, routed_finding, context)
                    if not option:
                        option = _first_safe_option(fixer, routed_finding, context)
                    if not option:
                        continue

                elif mode == FixMode.INTERACTIVE:
                    # Present options and get user choice
                    option = self._get_user_choice(routed_finding, fixer, context)
                    if not option:
                        continue

                elif mode == FixMode.REPORT:
                    continue  # Don't fix

                else:
                    # UNREACHABLE: all 4 FixMode values handled above. Defensive
                    # fallback for future enum additions.
                    # Check for pre-made decision
                    if routed_finding.id in decisions:
                        option_id = decisions[routed_finding.id]
                        options = fixer.get_options(routed_finding, context)
                        option = next((o for o in options if o.id == option_id), None)
                    else:
                        continue

                if option:
                    result = fixer.apply_fix(routed_finding, option, content)
                    if result.success:
                        finding.proof_state = ProofState.ACCEPTED.value
                        content = result.content

            # Store result for this file
            if content != file_info.content:
                files_modified += 1
                results[file_path] = FixResult(
                    success=True,
                    content=content,
                    errors=[],
                    warnings=[],
                    original_valid=True,
                    fixed_valid=True,
                )

        # Print fix-tracking summary so callers can distinguish "file processed
        # with no actionable fixes" from "file silently skipped." Printed only
        # when counters are non-zero (silent success needs no noise).
        if files_modified > 0 or files_missing_context > 0 or findings_without_fixer > 0:
            files_unchanged = (
                files_with_findings
                - files_missing_context
                - files_modified
            )
            parts = [
                f"Fix summary: {files_modified} files modified",
                f"{files_unchanged} unchanged",
            ]
            if files_missing_context:
                parts.append(f"{files_missing_context} missing context")
            if findings_without_fixer:
                parts.append(f"{findings_without_fixer} findings without fixer")
            print(", ".join(parts), file=sys.stderr)

        return results

    def _discover_files(self, paths: list[Path]) -> list[Path]:
        """Expand paths to list of files."""
        files = []
        project_root = (
            self.project_context.project_root.resolve()
            if self.project_context is not None
            else None
        )
        include_patterns = list(self.config.include_patterns)
        if self.config.check_rust:
            for pat in [
                "*.rs",
                "**/*.rs",
                "Cargo.toml",
                "**/Cargo.toml",
                "clippy.toml",
                "**/clippy.toml",
                "rustfmt.toml",
                "**/rustfmt.toml",
            ]:
                if pat not in include_patterns:
                    include_patterns.append(pat)
        for path in paths:
            if path.is_symlink():
                continue
            path = path.resolve()
            if path.is_file():
                explicit_excluded = (
                    self.config.exclude_patterns_from_config
                    and self._is_excluded(path, project_root)
                )
                if self._is_included(path, project_root) and not explicit_excluded:
                    files.append(path)
            elif path.is_dir():
                for pattern in include_patterns:
                    for file_path in path.glob(pattern):
                        if not file_path.is_file():
                            continue
                        if file_path.is_symlink():
                            continue
                        file_path = file_path.resolve()
                        if not self._is_excluded(file_path, project_root):
                            files.append(file_path)
        return sorted(set(files))

    def _is_included(self, path: Path, project_root: Path | None) -> bool:
        """Return whether an explicit file matches configured include patterns."""
        if not self.config.include_patterns:
            return True
        candidates = [path]
        if project_root is not None:
            with contextlib.suppress(ValueError):
                candidates = [path.relative_to(project_root)]
        return any(
            candidate.match(pattern)
            for candidate in candidates
            for pattern in self.config.include_patterns
        )

    def _is_excluded(self, path: Path, project_root: Path | None) -> bool:
        """Return whether a file matches configured exclude patterns."""
        if is_generated_artifact(path):
            return True
        candidates = [path]
        if project_root is not None:
            with contextlib.suppress(ValueError):
                candidates = [path.relative_to(project_root)]
        return any(
            _matches_path_pattern(candidate, pattern)
            for candidate in candidates
            for pattern in self.config.exclude_patterns
        )

    def _find_project_root(self, paths: list[Path]) -> Path:
        """Find project root from paths.

        NOTE: Duplicated logic exists at
        :func:`imodent.project.project_context._find_project_root`
        (C28 / Pair 4).  The module-level function in project_context
        uses ``PROJECT_MARKERS`` which includes ``setup.cfg`` — this
        method's inline marker list has been synchronised accordingly.
        Consolidate into a single canonical implementation if either
        version changes.
        """
        if not paths:
            # NOTE: Path.cwd() fallback is last-resort; preferred path is
            # _find_project_root in project_context.py.
            return Path.cwd()

        # Look for common project markers
        markers = [
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            ".git",
            "requirements.txt",
        ]

        for path in paths:
            if path.is_file():
                path = path.parent

            # Walk up looking for markers
            current = path
            while current != current.parent:
                if any((current / m).exists() for m in markers):
                    return current
                current = current.parent

        return paths[0].parent if paths[0].is_file() else paths[0]

    def _get_analyzers(self, names: list[str] | None = None) -> list:
        """Get analyzers to run."""
        from ..analyzers.imports import ImportAnalyzer
        from ..analyzers.lint import LintAnalyzer
        from ..analyzers.residue import ResidueAnalyzer
        from ..analyzers.rust import RustAnalyzer

        available = []
        if self.config.check_imports:
            available.append(ImportAnalyzer())
        if self.config.check_lint:
            available.append(LintAnalyzer())
            available.append(ResidueAnalyzer())
        if self.config.check_rust:
            available.append(RustAnalyzer())

        if names:
            return [a for a in available if a.name in names]
        return available

    def _get_fixer(self, finding: Finding):
        """Get fixer for a finding.

        Uses a lazy-initialised :class:`ImportFixer` instance stored on
        ``self._import_fixer`` so the fixer is reused across all findings
        within a single coordinator lifetime.  The fixer is stateless
        between calls, so reuse is safe.
        """
        if self._import_fixer is None:
            from ..fixers.imports import ImportFixer

            self._import_fixer = ImportFixer()

        if self._import_fixer.can_handle(finding):
            return self._import_fixer
        return None

    @staticmethod
    def _initialize_proof_states(findings: list[Finding]) -> None:
        """Ensure every finding carries an explicit proof lifecycle state."""
        for finding in findings:
            if finding.proof_state is None:
                finding.proof_state = finding.data.get(
                    "proof_state", ProofState.RAW.value
                )

    @staticmethod
    def _attach_guidance(findings: list[Finding]) -> None:
        """Generate contextual human-readable guidance for RAW proof-state findings.

        RAW findings have no external verification (no Ruff, no Cargo/Clippy).
        Guidance explains what RAW means for the finding type and what the
        user should do next to elevate the finding's proof state.
        """
        for finding in findings:
            if finding.proof_state != ProofState.RAW.value:
                continue
            guidance = _generate_guidance(finding)
            if guidance:
                finding.guidance = guidance

    def _get_user_choice(
        self,
        finding: Finding,
        fixer,
        context: AnalysisContext,
    ) -> FixOption | None:
        """Get user's choice for interactive mode. Prompts user, doesn't assume."""
        if not sys.stdin.isatty():
            print(
                "Warning: stdin is not a TTY — cannot prompt interactively. "
                "Skipping finding.",
                file=sys.stderr,
            )
            return None

        options = fixer.get_options(finding, context)

        print(f"\n{finding.severity.value.upper()}: {finding.message}")
        print(f"  File: {finding.file}:{finding.location}")
        print("\nOptions:")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt.label}: {opt.description}")

        # Prompt user — this is the actual interactive mode
        while True:
            try:
                choice = input(
                    f"\nChoose option [1-{len(options)}] or 's' to skip: "
                ).strip()
                if choice.lower() == "s":
                    print("  → Skipped")
                    return None
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    print(f"  → Selected: {options[idx].label}")
                    return options[idx]
                print(f"  Invalid: choose 1-{len(options)} or 's'")
            except ValueError:
                print(f"  Invalid: choose 1-{len(options)} or 's'")
            except EOFError:
                if sys.stdin.isatty():
                    print("\n  → EOF, TTY disconnected — skipping rest")
                else:
                    print(
                        "\n  → EOF on non-TTY — skipping rest",
                        file=sys.stderr,
                    )
                return None


def _matches_path_pattern(path: Path, pattern: str) -> bool:
    """Match paths with root-level generated directories handled correctly."""
    path_text = path.as_posix()
    pattern_text = pattern.replace("\\", "/")
    if path.match(pattern_text) or fnmatch.fnmatchcase(path_text, pattern_text):
        return True
    stripped = pattern_text
    while stripped.startswith("**/"):
        stripped = stripped[3:]
    head = stripped.split("/", 1)[0]
    return bool(head) and any(fnmatch.fnmatchcase(part, head) for part in path.parts)


def _index_candidates_by_finding_id(
    candidates: list[DecisionCandidate],
) -> dict[str, DecisionCandidate]:
    """Map every source finding to the candidate that owns its decision."""
    index = {}
    for candidate in candidates:
        for finding_id in candidate.finding_ids:
            index[finding_id] = candidate
    return index


def _finding_with_issue_type(finding: Finding, issue_type: str) -> Finding:
    """Route lint-backed import findings through import fixers without mutating evidence."""
    if finding.type == issue_type:
        return finding
    if issue_type in {"unused_import", "duplicate_import", "undefined_api"}:
        return replace(finding, type=issue_type, fixable=True)
    return finding


def _destructive_option(
    fixer, finding: Finding, context: AnalysisContext
) -> FixOption | None:
    """Select the first destructive option after DecisionEngine safety approval."""
    for option in fixer.get_options(finding, context):
        if option.action == "delete" or not option.is_safe:
            return option
    return None


def _first_safe_option(
    fixer, finding: Finding, context: AnalysisContext
) -> FixOption | None:
    """Select the first safe (``is_safe=True``) option from a fixer.

    Used as a fallback when no destructive option exists but a
    safe automated action (like ``add_import``) is available.

    Returns:
        The first ``FixOption`` with ``is_safe=True``, or ``None``
        if all options are unsafe or no options exist.
    """
    for option in fixer.get_options(finding, context):
        if option.is_safe:
            return option
    return None


def _add_cross_file_evidence(findings: list[Finding], context: AnalysisContext):
    """Attach cross-project symbol usage evidence to unused import findings.

    For each unused-import finding, traces the imported symbol across all
    project files.  If the symbol appears in other files, creates Evidence
    objects (polarity=context, strength=0.30) and attaches them to both
    the finding's data dict and the AnalysisContext evidence list.
    """
    import_type_names = frozenset(["unused_import", "unused_import_file"])

    for finding in findings:
        import_name = None

        if finding.type in import_type_names:
            import_name = getattr(finding, "import_name", None)

        if not import_name:
            import_info = finding.data.get("import_info", {})
            import_name = import_info.get("name")

        if not import_name:
            continue

        usages = trace_symbol_usage(import_name, context.files, context.graph)

        other_file_usages = [u for u in usages if u.file != finding.file]

        if other_file_usages:
            usage_files = sorted({str(u.file.name) for u in other_file_usages})
            evidence = Evidence(
                kind="symbol_usage",
                file=finding.file,
                location=finding.location,
                source="cross_project_trace",
                subject=import_name,
                polarity="context",
                claim=f"Symbol '{import_name}' used in {len(usage_files)} other "
                f"project file(s): {', '.join(usage_files[:3])}",
                strength=0.30,
                data={
                    "symbol": import_name,
                    "total_cross_file_usages": len(other_file_usages),
                    "files": usage_files,
                },
            )
            context.add_evidence(evidence)
            finding.data.setdefault("evidence", []).append({"id": evidence.id})


def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Prefer external Ruff evidence over duplicate local reports by subject key.

    Fuses Ruff F401 and local unused-import findings that share the same
    semantic subject key (module, name, alias), NOT merely (file, line).
    Grouped imports and multiline imports each have their own subject key
    and are preserved as separate subjects.
    """
    # Build a set of subject keys covered by Ruff F401 evidence
    ruff_subject_keys: dict[tuple, Finding] = {}
    for finding in findings:
        if finding.lint_source == "ruff" and finding.lint_code == "F401":
            sk = _subject_key_from_finding(finding)
            if sk is not None:
                key = sk.binding_key
                if key not in ruff_subject_keys:
                    ruff_subject_keys[key] = finding

    if not ruff_subject_keys:
        return findings

    deduplicated: list[Finding] = []
    for finding in findings:
        if (
            finding.type in ("unused_import_file", "unused_import")
            and finding.lint_source != "ruff"
        ):
            sk = _subject_key_from_finding(finding)
            if sk is not None and sk.binding_key in ruff_subject_keys:
                # Ruff already covers this subject; skip the local duplicate
                continue
        deduplicated.append(finding)
    return deduplicated


def _generate_guidance(finding: Finding) -> str | None:
    """Map finding type and proof state to contextual human-readable guidance.

    Produces one-line guidance strings explaining what the finding means and
    what action the user should take next.  Only called for RAW findings —
    externally-verified findings don't need interpretation help.
    """
    ftype = finding.type

    # ── Import intent ────────────────────────────────────────────────────
    if ftype == "import_intent":
        return (
            "This import may carry side-effect or re-export intent. "
            "Verify by: (1) checking __all__, "
            "(2) checking if module uses __getattr__, "
            "(3) adding '# do not remove' if intentional."
        )

    # ── Unused imports ────────────────────────────────────────────────────
    if ftype in ("unused_import", "unused_import_file"):
        return "Run with --lint for Ruff external verification of unused imports."

    # ── Rust advisory ─────────────────────────────────────────────────────
    if ftype == "rust_broad_allow":
        return (
            "Broad allow attribute detected. "
            "Consider scoping to specific lints instead of blanket suppression."
        )
    if ftype in ("rust_config_missing", "rust_config"):
        return "Create clippy.toml or update [lints] in Cargo.toml to define lint policy."
    if ftype in ("rust_oracle", "rust_oracle_unavailable"):
        return (
            "External Rust oracle unavailable. "
            "Run with --cargo or --cargo-clippy for compiler-backed verification."
        )
    if ftype == "rust_diagnostic":
        return (
            "Rust compiler/clippy diagnostic. "
            "Review the diagnostic message and fix in source. "
            "imodent does not edit Rust files."
        )
    if ftype == "rust_residue":
        return (
            "Debug residue marker (todo!, unimplemented!, dbg!) found. "
            "Review whether this marker should remain in production code."
        )
    # Generic RAW fallback for unknown types
    return None


def _rule_key(finding: Finding) -> str:
    """Extract a stable rule identifier from a finding for aggregation.

    Uses lint_code if available (e.g., 'F401', 'S310'), otherwise falls
    back to the finding type. This ensures findings from the same diagnostic
    rule are grouped together in summary displays.
    """
    code = getattr(finding, "lint_code", None)
    if code:
        return code
    return finding.type


class AnalysisResult:
    # The else branch at line 231 catch-all for unknown FixMode checks pre-made
    # decisions dict. This code path is unreachable because the four FixMode enum
    # values (SAFE_AUTO, ALL_AUTO, INTERACTIVE, REPORT) are all handled by
    # preceding elif branches. The code exists as a defensive pattern against
    # future enum additions.
    """Result of an analysis run."""

    def __init__(
        self,
        context: AnalysisContext,
        elapsed_time: float,
        analyzer_names: list[str],
        candidates: list | None = None,
    ):
        self.context = context
        self.elapsed_time = elapsed_time
        self.analyzer_names = analyzer_names
        self.candidates = candidates or []

    @property
    def files(self) -> dict[Path, FileInfo]:
        return self.context.files

    @property
    def findings(self) -> list[Finding]:
        return self.context.findings

    @property
    def graph(self):
        return self.context.graph

    def summary(self) -> str:
        """Generate multi-dimensional analysis summary.

        Returns a string with:
        - Timing and file/finding counts
        - Findings per severity
        - Top files by finding count
        - Top rules (lint codes or message prefixes) by frequency
        """
        by_severity: dict[str, int] = {}
        by_file: dict[str, int] = {}
        by_rule: dict[str, int] = {}

        for f in self.findings:
            sev = f.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1

            fname = f.file.name
            by_file[fname] = by_file.get(fname, 0) + 1

            rule = _rule_key(f)
            by_rule[rule] = by_rule.get(rule, 0) + 1

        lines = [
            f"Analysis completed in {self.elapsed_time:.2f}s",
            f"Files analyzed: {len(self.files)}",
            f"Findings: {len(self.findings)}",
        ]

        for sev, count in sorted(by_severity.items()):
            lines.append(f"  {sev}: {count}")

        # Top files (up to 5)
        if by_file:
            top_files = sorted(by_file.items(), key=lambda x: (-x[1], x[0]))[:5]
            lines.append("Top files:")
            for fname, count in top_files:
                lines.append(f"  {fname}: {count}")

        # Top rules (up to 5)
        if by_rule:
            top_rules = sorted(by_rule.items(), key=lambda x: (-x[1], x[0]))[:5]
            lines.append("Top rules:")
            for rule, count in top_rules:
                lines.append(f"  {rule}: {count}")

        return "\n".join(lines)
