"""Analysis coordinator - orchestrates all analyzers and fixers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from enum import Enum
import time

from .context import AnalysisContext, AnalysisConfig, FileInfo
from .findings import Finding, FixOption
from ..graph.dependency import build_dependency_graph
from ..interfaces import FixResult
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
        from ..analyzers.residue import ResidueAnalyzer
        from ..fixers.imports import ImportFixer

        self._analyzers = [ImportAnalyzer(), ResidueAnalyzer()]
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
            files=file_infos, graph=graph, findings=[], config=self.config
        )

        # Run analyzers
        all_findings = []
        for analyzer in self._get_analyzers(analyzers):
            try:
                findings = analyzer.analyze(context)
                all_findings.extend(findings)
            except Exception as e:
                print(f"Warning: Analyzer {analyzer.name} failed: {e}")

        context.findings = all_findings

        elapsed = time.time() - start_time

        return AnalysisResult(
            context=context,
            elapsed_time=elapsed,
            analyzer_names=[a.name for a in self._get_analyzers(analyzers)],
        )

    def fix(
        self,
        findings: list[Finding],
        context: AnalysisContext,
        mode: FixMode = FixMode.SAFE_AUTO,
        decisions: Optional[dict[str, str]] = None,
    ) -> dict[Path, FixResult]:
        """
        Fix findings.

        Args:
            findings: Findings to fix
            context: Analysis context
            mode: How to handle fixes
            decisions: Pre-made decisions (finding_id -> option_id)

        Returns:
            Dict of path -> FixResult
        """
        results = {}
        decisions = decisions or {}

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
                fixer = self._get_fixer(finding)
                if not fixer:
                    continue

                # Determine action based on mode
                if mode == FixMode.SAFE_AUTO:
                    if not finding.auto_fix_safe:
                        continue  # Skip
                    option = fixer.get_options(finding, context)[
                        0
                    ]  # Use first (safest)

                elif mode == FixMode.ALL_AUTO:
                    if not fixer.can_auto_fix(finding):
                        continue
                    option = fixer.get_options(finding, context)[0]

                elif mode == FixMode.INTERACTIVE:
                    # Present options and get user choice
                    option = self._get_user_choice(finding, fixer, context)
                    if not option:
                        continue

                elif mode == FixMode.REPORT:
                    continue  # Don't fix

                else:
                    # Check for pre-made decision
                    if finding.id in decisions:
                        option_id = decisions[finding.id]
                        options = fixer.get_options(finding, context)
                        option = next((o for o in options if o.id == option_id), None)
                    else:
                        continue

                if option:
                    result = fixer.apply_fix(finding, option, content)
                    if result.success:
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
                if self._is_included(path, project_root) and not self._is_excluded(
                    path, project_root
                ):
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
                candidates.append(path.relative_to(project_root))
            except ValueError:
                pass
        return any(
            candidate.match(pattern)
            for candidate in candidates
            for pattern in self.config.include_patterns
        )

    def _is_excluded(self, path: Path, project_root: Optional[Path]) -> bool:
        """Return whether a file matches configured exclude patterns."""
        candidates = [path]
        if project_root is not None:
            try:
                candidates.append(path.relative_to(project_root))
            except ValueError:
                pass
        return any(
            candidate.match(pattern)
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
        from ..analyzers.residue import ResidueAnalyzer

        available = []
        if self.config.check_imports:
            available.append(ImportAnalyzer())
        if self.config.check_lint:
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

    def _get_user_choice(
        self,
        finding: Finding,
        fixer,
        context: AnalysisContext,
    ) -> Optional[FixOption]:
        """Get user's choice for interactive mode. Prompts user, doesn't assume."""
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
                print("\n  → EOF, skipping rest")
                return None


class AnalysisResult:
    """Result of an analysis run."""

    def __init__(
        self, context: AnalysisContext, elapsed_time: float, analyzer_names: list[str]
    ):
        self.context = context
        self.elapsed_time = elapsed_time
        self.analyzer_names = analyzer_names

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
