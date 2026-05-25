"""Tests for --interactive and --report CLI modes."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analysis.findings import Finding, Severity, Location, FixOption
from imodent.cli import analyze_files


class TestFixModeSelection:
    """CLI correctly selects FixMode based on flags."""

    def test_report_mode_sets_fixmode_report(self, tmp_path, capsys):
        """--report uses FixMode.REPORT, no files modified."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        analyze_files(
            paths=[tmp_path],
            analyze_imports=True,
            fix=True,
            report=True,
        )

        captured = capsys.readouterr()
        assert "REVIEW MODE" in captured.out
        # File should NOT be modified
        assert test_file.read_text() == "import os\nimport os\n"

    def test_interactive_mode_sets_fixmode_interactive(self, tmp_path, capsys):
        """--interactive uses FixMode.INTERACTIVE."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        # Mock input() to auto-select option 1
        with patch("builtins.input", return_value="1"):
            analyze_files(
                paths=[tmp_path],
                analyze_imports=True,
                fix=True,
                interactive=True,
            )

        captured = capsys.readouterr()
        assert "INTERACTIVE MODE" in captured.out

    def test_auto_fix_mode_default(self, tmp_path, capsys):
        """--fix without --interactive or --report uses SAFE_AUTO."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        analyze_files(
            paths=[tmp_path],
            analyze_imports=True,
            fix=True,
        )

        captured = capsys.readouterr()
        assert "AUTO-FIX MODE" in captured.out

    def test_no_fix_mode_when_no_fix_flag(self, tmp_path, capsys):
        """Without --fix, --interactive, or --report, no fix mode selected."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        analyze_files(
            paths=[tmp_path],
            analyze_imports=True,
        )

        captured = capsys.readouterr()
        assert "MODE" not in captured.out.replace("ARCHITECTURE REVIEW", "")


class TestInteractivePrompt:
    """_get_user_choice actually prompts user."""

    def test_user_selects_option(self):
        """User input selects the correct option."""
        coordinator = AnalysisCoordinator()
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="os imported but unused",
            location=Location(line=1),
            fixable=True,
        )
        from imodent.fixers.imports import ImportFixer
        from imodent.analysis.context import AnalysisContext

        fixer = ImportFixer()
        context = AnalysisContext()

        # Mock input to select option 2 + TTY for interactive prompt
        with patch("builtins.input", return_value="2"), patch("sys.stdin.isatty", return_value=True):
            result = coordinator._get_user_choice(finding, fixer, context)

        assert result is not None
        options = fixer.get_options(finding, context)
        assert result == options[1]  # 0-indexed, so option 2

    def test_user_skips(self):
        """User typing 's' returns None (skip)."""
        coordinator = AnalysisCoordinator()
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="os imported but unused",
            location=Location(line=1),
            fixable=True,
        )
        from imodent.fixers.imports import ImportFixer
        from imodent.analysis.context import AnalysisContext

        fixer = ImportFixer()
        context = AnalysisContext()

        with patch("builtins.input", return_value="s"), patch("sys.stdin.isatty", return_value=True):
            result = coordinator._get_user_choice(finding, fixer, context)

        assert result is None

    def test_eof_graceful(self):
        """EOFError (piped input) returns None gracefully."""
        coordinator = AnalysisCoordinator()
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="os imported but unused",
            location=Location(line=1),
            fixable=True,
        )
        from imodent.fixers.imports import ImportFixer
        from imodent.analysis.context import AnalysisContext

        fixer = ImportFixer()
        context = AnalysisContext()

        with patch("builtins.input", side_effect=EOFError), patch("sys.stdin.isatty", return_value=True):
            result = coordinator._get_user_choice(finding, fixer, context)

        assert result is None


class TestReportModeIntegrity:
    """Report mode never modifies files."""

    def test_report_does_not_write(self, tmp_path, capsys):
        """--report never writes to files, even with findings."""
        test_file = tmp_path / "test.py"
        original = "import os\nimport os\n\ndef f(): pass\n"
        test_file.write_text(original)

        analyze_files(
            paths=[tmp_path],
            analyze_imports=True,
            fix=True,
            report=True,
        )

        # File must be unchanged
        assert test_file.read_text() == original

    def test_report_with_backup_does_not_create_bak(self, tmp_path, capsys):
        """--report with --backup still doesn't create .bak files."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        analyze_files(
            paths=[tmp_path],
            analyze_imports=True,
            fix=True,
            report=True,
            backup=True,
        )

        bak = tmp_path / "test.py.bak"
        assert not bak.exists()
