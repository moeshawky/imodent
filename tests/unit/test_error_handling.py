"""G-ERR: Error handling — no swallowed errors, no silent failures.

AOP v3 rule: error handling gaps mask deeper integration failures.
"""

import tempfile
from pathlib import Path

import pytest

from imodent.analysis.coordinator import AnalysisCoordinator
from imodent.analysis.context import AnalysisContext, FileInfo, DependencyGraph
from imodent.analysis.findings import Finding, Severity, Location
from imodent.fixers.imports import ImportFixer
from imodent.cli import fix_file


class TestAnalyzerErrorHandling:
    """Analyzers never crash — they return findings or empty lists."""

    def test_analyzer_with_empty_content(self):
        """ImportAnalyzer handles empty file content."""
        from imodent.analyzers.imports import ImportAnalyzer
        analyzer = ImportAnalyzer()
        py_file = Path("/tmp/empty.py")
        context = AnalysisContext(
            files={py_file: FileInfo(
                path=py_file, content="", language='python', ast_tree=None
            )},
            graph=DependencyGraph()
        )
        findings = analyzer.analyze(context)
        assert isinstance(findings, list)  # Never crashes

    def test_analyzer_with_binary_content(self):
        """ImportAnalyzer handles binary-like content."""
        from imodent.analyzers.imports import ImportAnalyzer
        analyzer = ImportAnalyzer()
        py_file = Path("/tmp/binary.py")
        context = AnalysisContext(
            files={py_file: FileInfo(
                path=py_file, content="\x00\x01\x02", language='python',
                has_syntax_errors=True, ast_tree=None
            )},
            graph=DependencyGraph()
        )
        findings = analyzer.analyze(context)
        assert isinstance(findings, list)  # Never crashes

    def test_coordinator_handles_missing_file_gracefully(self):
        """Coordinator doesn't crash on missing files."""
        coordinator = AnalysisCoordinator()
        result = coordinator.analyze([Path("/tmp/nonexistent_xyz_123.py")])
        assert result is not None


class TestFixerErrorHandling:
    """Fixers return FixResult with errors, never raise exceptions."""

    def test_fixer_with_invalid_line_number(self):
        """ImportFixer handles line number beyond file length."""
        fixer = ImportFixer()
        finding = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="dup",
            location=Location(line=9999), fixable=True, auto_fix_safe=True
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], "import os\n")
        assert not result.success
        assert len(result.errors) > 0

    def test_fixer_with_zero_line_number(self):
        """ImportFixer handles line number 0."""
        fixer = ImportFixer()
        finding = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="dup",
            location=Location(line=0), fixable=True, auto_fix_safe=True
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], "import os\n")
        assert not result.success

    def test_fixer_with_unknown_action(self):
        """ImportFixer handles unknown fix action."""
        fixer = ImportFixer()
        from imodent.analysis.findings import FixOption
        finding = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="dup",
            location=Location(line=1), fixable=True, auto_fix_safe=True
        )
        bad_option = FixOption(
            id="bad", label="Bad", description="Bad action",
            action="explode", is_safe=False
        )
        result = fixer.apply_fix(finding, bad_option, "import os\n")
        assert not result.success
        assert "Unknown action" in result.errors[0]


class TestCLIErrorHandling:
    """CLI handles errors gracefully — no uncaught exceptions."""

    def test_fix_nonexistent_file(self):
        """fix_file with nonexistent path doesn't crash."""
        # Should print error, not raise
        fix_file(Path("/nonexistent/file.py"), backup=False)

    def test_fix_empty_file(self):
        """fix_file with empty file doesn't crash."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode='w') as f:
            f.write("")
            f.flush()
            # Should not crash
            fix_file(Path(f.name), backup=False, dry_run=True)

    def test_fix_file_with_syntax_errors(self):
        """fix_file handles files with syntax errors."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode='w') as f:
            f.write("def f(\n  pass\n")
            f.flush()
            # Should not crash — may fall back to heuristic
            fix_file(Path(f.name), backup=False, dry_run=True)


class TestPipelineErrorHandling:
    """FixPipeline handles undetectable content gracefully."""

    def test_unrecognizable_content(self):
        """Pipeline returns error for unrecognizable content, doesn't crash."""
        from imodent import FixPipeline
        pipeline = FixPipeline()
        result = pipeline.fix("this is not any known format {{{")
        assert not result.success
        assert "Unable to detect" in result.errors[0]

    def test_empty_content(self):
        """Pipeline handles empty content."""
        from imodent import FixPipeline
        pipeline = FixPipeline()
        result = pipeline.fix("")
        # Should not crash — may return success or failure
        assert result is not None
