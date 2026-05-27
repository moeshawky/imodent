"""Analysis coordinator - orchestrates all analyzers and fixers."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from typing import Optional
from enum import Enum
import fnmatch
import sys
import time

from .context import AnalysisContext, AnalysisConfig, FileInfo
from .findings import Finding, FixOption, ProofState
from .decisions import (
    DecisionCandidate,
    DecisionEngine,
    _issue_type_from_finding,
    _subject_key_from_finding,
)
from ..graph.dependency import build_dependency_graph
from ..interfaces import FixResult
from ..project.discovery import is_generated_artifact
from ..project.project_context import ProjectContext


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
        config: Optional[AnalysisConfig] = None,
        project_context: Optional["ProjectContext"] = None,
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
        self._analyzers = []
        self._fixers = []
        self._load_plugins()

    def _load_plugins(self):
        """Load analyzer and fixer plugins."""
        from ..analyzers.imports import ImportAnalyzer
        from ..analyzers.lint import LintAnalyzer
        from ..analyzers.residue import ResidueAnalyzer
        from ..fixers.imports import ImportFixer

        self._analyzers = [ImportAnalyzer(), LintAnalyzer(), ResidueAnalyzer()]
        self._fixers = [ImportFixer()]

    def analyze(
        self,
        paths: list[Path],
        analyzers: Optional[list[str]] = None,
    ) -> "AnalysisResult":
        """
        Run analysis on paths.

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
        for path in files:
            try:
                file_infos[path] = FileInfo.from_path(path)
            except Exception as e:
                print(f"Warning: Could not load {path}: {e}")

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
            except Exception as e:
                print(f"Warning: Analyzer {analyzer.name} failed: {e}")

        all_findings = _deduplicate_findings(all_findings)
        self._initialize_proof_states(all_findings)
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
        decisions: Optional[dict[str, str]] = None,
        candidates: Optional[list[DecisionCandidate]] = None,
    ) -> dict[Path, FixResult]:
        """
        Fix findings.

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
        for file_path, file_findings in by_file.items():
            file_info = context.files.get(file_path)
            if not file_info:
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
                    candidate.issue_type if candidate is not None
                    else _issue_type_from_finding(finding)
                )
                routed_finding = _finding_with_issue_type(finding, issue_type)
                fixer = self._get_fixer(routed_finding)
                if not fixer:
                    continue

                # Determine action based on mode
                if mode == FixMode.SAFE_AUTO:
                    if candidate is None or not candidate.destructive_allowed:
                        continue  # Skip
                    option = _destructive_option(fixer, routed_finding, context)
                    if not option:
                        continue

                elif mode == FixMode.ALL_AUTO:
                    if candidate is None or not candidate.destructive_allowed:
                        continue
                    option = _destructive_option(fixer, routed_finding, context)
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
                results[file_path] = FixResult(
                    success=True,
                    content=content,
                    errors=[],
                    warnings=[],
                    original_valid=True,
                    fixed_valid=True,
                )

        return results

    def _discover_files(self, paths: list[Path]) -> list[Path]:
        """Expand paths to list of files."""
        files = []
        project_root = (
            self.project_context.project_root.resolve()
            if self.project_context is not None
            else None
        )
        for path in paths:
            path = path.resolve()
            if path.is_file():
                explicit_excluded = (
                    self.config.exclude_patterns_from_config
                    and self._is_excluded(path, project_root)
                )
                if self._is_included(path, project_root) and not explicit_excluded:
                    files.append(path)
            elif path.is_dir():
                for pattern in self.config.include_patterns:
                    for file_path in path.glob(pattern):
                        if not file_path.is_file():
                            continue
                        file_path = file_path.resolve()
                        if not self._is_excluded(file_path, project_root):
                            files.append(file_path)
        return sorted(set(files))

    def _is_included(self, path: Path, project_root: Optional[Path]) -> bool:
        """Return whether an explicit file matches configured include patterns."""
        if not self.config.include_patterns:
            return True
        candidates = [path]
        if project_root is not None:
            try:
                candidates = [path.relative_to(project_root)]
            except ValueError:
                pass
        return any(
            candidate.match(pattern)
            for candidate in candidates
            for pattern in self.config.include_patterns
        )

    def _is_excluded(self, path: Path, project_root: Optional[Path]) -> bool:
        """Return whether a file matches configured exclude patterns."""
        if is_generated_artifact(path):
            return True
        candidates = [path]
        if project_root is not None:
            try:
                candidates = [path.relative_to(project_root)]
            except ValueError:
                pass
        return any(
            _matches_path_pattern(candidate, pattern)
            for candidate in candidates
            for pattern in self.config.exclude_patterns
        )

    def _find_project_root(self, paths: list[Path]) -> Path:
        """Find project root from paths."""
        if not paths:
            return Path.cwd()

        # Look for common project markers
        markers = ["pyproject.toml", "setup.py", ".git", "requirements.txt"]

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

    def _get_analyzers(self, names: Optional[list[str]] = None) -> list:
        """Get analyzers to run."""
        from ..analyzers.imports import ImportAnalyzer
        from ..analyzers.lint import LintAnalyzer
        from ..analyzers.residue import ResidueAnalyzer

        available = []
        if self.config.check_imports:
            available.append(ImportAnalyzer())
        if self.config.check_lint:
            available.append(LintAnalyzer())
            available.append(ResidueAnalyzer())

        if names:
            return [a for a in available if a.name in names]
        return available

    def _get_fixer(self, finding: Finding):
        """Get fixer for a finding."""
        from ..fixers.imports import ImportFixer

        available = [ImportFixer()]

        for fixer in available:
            if fixer.can_handle(finding):
                return fixer
        return None

    @staticmethod
    def _initialize_proof_states(findings: list[Finding]) -> None:
        """Ensure every finding carries an explicit proof lifecycle state."""
        for finding in findings:
            if finding.proof_state is None:
                finding.proof_state = finding.data.get(
                    "proof_state", ProofState.RAW.value
                )

    def _get_user_choice(
        self,
        finding: Finding,
        fixer,
        context: AnalysisContext,
    ) -> Optional[FixOption]:
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
                else:
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
    if issue_type in {"unused_import", "duplicate_import"}:
        return replace(finding, type=issue_type, fixable=True)
    return finding


def _destructive_option(fixer, finding: Finding, context: AnalysisContext) -> FixOption | None:
    """Select the first destructive option after DecisionEngine safety approval."""
    for option in fixer.get_options(finding, context):
        if option.action == "delete" or not option.is_safe:
            return option
    return None


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
        if finding.type in ("unused_import_file", "unused_import") and finding.lint_source != "ruff":
            sk = _subject_key_from_finding(finding)
            if sk is not None and sk.binding_key in ruff_subject_keys:
                # Ruff already covers this subject; skip the local duplicate
                continue
        deduplicated.append(finding)
    return deduplicated


class AnalysisResult:
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
        """Generate summary of analysis."""
        by_severity = {}
        for f in self.findings:
            sev = f.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1

        lines = [
            f"Analysis completed in {self.elapsed_time:.2f}s",
            f"Files analyzed: {len(self.files)}",
            f"Findings: {len(self.findings)}",
        ]

        for sev, count in sorted(by_severity.items()):
            lines.append(f"  {sev}: {count}")

        return "\n".join(lines)
