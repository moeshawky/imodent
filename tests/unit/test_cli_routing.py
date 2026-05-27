"""Tests for CLI main function and argument routing."""
import pytest
import sys
from io import StringIO
from unittest.mock import patch
from imodent.cli import main, fix_file, analyze_files, _collect_targets
from imodent.analysis.coordinator import AnalysisCoordinator
from imodent.project.project_context import ProjectContext


class TestCLIRouting:
    """Test that CLI arguments route to the correct mode."""

    def test_fix_mode_default(self, tmp_path):
        """Without --analyze, should route to fix mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x=1\n")

        with patch("imodent.cli.fix_file") as mock_fix:
            with patch.object(sys, "argv", ["imodent", str(test_file)]):
                main()
            mock_fix.assert_called_once()

    def test_analyze_mode_with_imports(self, tmp_path):
        """With --analyze --imports, should route to analyze mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        with patch("imodent.cli.analyze_files") as mock_analyze:
            with patch.object(sys, "argv", ["imodent", str(test_file), "--analyze", "--imports"]):
                main()
            mock_analyze.assert_called_once()

    def test_analyze_mode_with_advisory(self, tmp_path):
        """With --analyze --advisory, should route to analyze mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport sys\n")

        with patch("imodent.cli.analyze_files") as mock_analyze:
            with patch.object(sys, "argv", ["imodent", str(test_file), "--analyze", "--advisory"]):
                main()
            mock_analyze.assert_called_once()

    def test_analyze_mode_with_lint(self, tmp_path):
        """With --analyze --lint, should route to analyze mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x=1\n")

        with patch("imodent.cli.analyze_files") as mock_analyze:
            with patch.object(sys, "argv", ["imodent", str(test_file), "--analyze", "--lint"]):
                main()
            mock_analyze.assert_called_once()

    def test_scan_recursive_flag_prints_note(self, tmp_path, capsys):
        """-r is accepted in scan mode but documented as a no-op."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        with patch("imodent.cli.analyze_files") as mock_analyze:
            with patch.object(sys, "argv", ["imodent", str(test_file), "--analyze", "-r"]):
                main()

        mock_analyze.assert_called_once()
        captured = capsys.readouterr()
        assert "scan mode is always recursive" in captured.err

    def test_scan_force_flag_prints_note(self, tmp_path, capsys):
        """--force is a fix-mode-only flag and should not disappear silently."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        with patch("imodent.cli.analyze_files") as mock_analyze:
            with patch.object(sys, "argv", ["imodent", str(test_file), "--analyze", "--force"]):
                main()

        mock_analyze.assert_called_once()
        captured = capsys.readouterr()
        assert "--force is a fix-mode flag only" in captured.err

    def test_report_without_analyze_routes_to_scan_mode(self, tmp_path):
        """--report alone should never fall through to writing fix mode."""
        test_file = tmp_path / "test.py"
        original = "def foo():\n  pass\n"
        test_file.write_text(original)

        with patch.object(sys, "argv", ["imodent", str(test_file), "--report"]):
            main()

        assert test_file.read_text() == original

    def test_multiple_paths(self, tmp_path):
        """Multiple paths should be passed to the handler."""
        file1 = tmp_path / "a.py"
        file2 = tmp_path / "b.py"
        file1.write_text("x=1\n")
        file2.write_text("y=2\n")

        with patch("imodent.cli.fix_file") as mock_fix:
            with patch.object(sys, "argv", ["imodent", str(file1), str(file2)]):
                main()
            assert mock_fix.call_count == 2


class TestFixFile:
    """Test the fix_file function."""

    def test_fix_single_file(self, tmp_path):
        """Should fix a single file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n  pass\n")

        fix_file(test_file, indent_size=4, backup=False, dry_run=True)
        # Should not raise

    def test_fix_directory(self, tmp_path):
        """Should fix all files in a directory."""
        file1 = tmp_path / "a.py"
        file2 = tmp_path / "b.py"
        file1.write_text("def foo():\n  pass\n")
        file2.write_text("def bar():\n  pass\n")

        fix_file(tmp_path, indent_size=4, backup=False, dry_run=True)
        # Should not raise

    def test_fix_nonexistent_file(self, tmp_path):
        """Should handle nonexistent file gracefully."""
        nonexistent = tmp_path / "nonexistent.py"
        fix_file(nonexistent, indent_size=4, backup=False)
        # Should not raise

    def test_fix_with_backup(self, tmp_path):
        """Should create backup when requested."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n  pass\n")

        fix_file(test_file, indent_size=4, backup=True, dry_run=False)

        bak_file = tmp_path / "test.py.bak"
        assert bak_file.exists()

    def test_failed_fix_does_not_write_partial_content(self, tmp_path):
        """Failed JSONL repair must leave the original file untouched."""
        test_file = tmp_path / "events.jsonl"
        original = ' { "a" : 1 }\nnot-json\n'
        test_file.write_text(original)

        fix_file(test_file, backup=False)

        assert test_file.read_text() == original

    def test_jsonl_extension_uses_jsonl_strategy_for_single_line(self, tmp_path):
        """A one-record .jsonl file should not be formatted as multi-line JSON."""
        test_file = tmp_path / "events.jsonl"
        test_file.write_text('{"a":1}\n')

        fix_file(test_file, backup=False)

        assert test_file.read_text() == '{"a": 1}\n'

    def test_directory_fix_only_recurses_when_requested(self, tmp_path):
        """The --recursive flag controls directory descent in fix mode."""
        root_file = tmp_path / "root.py"
        nested = tmp_path / "nested"
        nested.mkdir()
        nested_file = nested / "child.py"
        root_file.write_text("x=1\n")
        nested_file.write_text("y=1\n")

        assert _collect_targets(tmp_path, recursive=False) == [root_file.resolve()]
        assert _collect_targets(tmp_path, recursive=True) == [
            nested_file.resolve(),
            root_file.resolve(),
        ]


class TestAnalyzeFiles:
    """Test the analyze_files function."""

    def test_analyze_empty_directory(self, tmp_path):
        """Should handle empty directory."""
        analyze_files([tmp_path], analyze_imports=False)
        # Should not raise

    def test_analyze_no_matching_files(self, tmp_path):
        """Should report when no matching files found."""
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("Hello\n")

        captured = StringIO()
        with patch("sys.stdout", captured):
            analyze_files([tmp_path], analyze_imports=False)
        assert "No matching files found" in captured.getvalue()

    def test_analyze_python_file(self, tmp_path):
        """Should analyze Python files."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")

        captured = StringIO()
        with patch("sys.stdout", captured):
            analyze_files([test_file], analyze_imports=True)
        # Should produce output
        assert "Analysis completed" in captured.getvalue() or "Clean" in captured.getvalue()

    def test_fix_check_conflict_prints_note(self, tmp_path, capsys):
        """--fix --check intentionally analyzes only, but must explain why."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        analyze_files([test_file], analyze_imports=True, fix=True, check_only=True)

        captured = capsys.readouterr()
        assert "--check prevents --fix" in captured.err

    def test_analyze_discovery_applies_excludes_and_resolves_paths(self, tmp_path):
        """Coordinator discovery is the scan authority for excludes and path identity."""
        keep = tmp_path / "keep.py"
        excluded = tmp_path / "test_excluded.py"
        build_dir = tmp_path / "build" / "lib"
        build_dir.mkdir(parents=True)
        generated = build_dir / "keep.py"
        keep.write_text("x = 1\n")
        excluded.write_text("y = 1\n")
        generated.write_text("z = 1\n")

        project_context = ProjectContext.from_root(tmp_path)
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([tmp_path])

        assert keep.resolve() in result.context.files
        assert excluded.resolve() not in result.context.files
        assert generated.resolve() not in result.context.files
        assert all(path.is_absolute() for path in result.context.files)

    def test_explicit_generated_file_is_still_analyzed(self, tmp_path):
        """Explicit files remain visible even under default generated-artifact excludes."""
        build_dir = tmp_path / "build" / "lib"
        build_dir.mkdir(parents=True)
        generated = build_dir / "keep.py"
        generated.write_text("z = 1\n")

        project_context = ProjectContext.from_root(tmp_path)
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([generated])

        assert generated.resolve() in result.context.files

    def test_lint_flag_runs_ruff_oracle(self, tmp_path):
        """--lint should produce Ruff diagnostics instead of residue-only lint findings."""
        test_file = tmp_path / "sample.py"
        test_file.write_text("def f():\n    unused = 1\n")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_imports = False
        project_context.config.check_lint = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([test_file])

        assert any(
            f.type == "lint" and f.lint_source == "ruff" and f.lint_code == "F841"
            for f in result.findings
        )
        assert not any(f.type == "declared_behavior_unwired" for f in result.findings)

    def test_ruff_unused_import_deduplicates_local_unused_import(self, tmp_path):
        """When --imports and --lint run together, F401 owns duplicate unused-import output."""
        test_file = tmp_path / "sample.py"
        test_file.write_text("import os\n\nx = 1\n")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_imports = True
        project_context.config.check_lint = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([test_file])

        assert any(
            f.type == "lint" and f.lint_source == "ruff" and f.lint_code == "F401"
            for f in result.findings
        )
        assert not any(f.type == "unused_import_file" for f in result.findings)

    def test_analyze_respects_config_when_flags_are_not_explicit(self, tmp_path, capsys):
        """--analyze alone should not silently replace project scan config."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n")
        (tmp_path / ".imodent.yaml").write_text(
            "check_imports: false\ncheck_lint: false\n"
        )
        test_file = tmp_path / "sample.py"
        test_file.write_text("import os\n\nx = 1\n")

        analyze_files([test_file])

        captured = capsys.readouterr()
        assert "Findings: 0" in captured.out

    def test_lint_flag_does_not_disable_import_analysis(self, tmp_path, monkeypatch):
        """Passing --lint alone should not force analyze_imports=False.

        The None sentinel must survive so analyze_files reads the config default.
        """
        import imodent.cli as cli_module

        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr(sys, "argv", ["imodent", str(test_file), "--analyze", "--lint"])
        cli_module.main()

        assert capture_call.kwargs is not None
        assert capture_call.kwargs["analyze_imports"] is None, (
            "--lint must not suppress import analysis (None sentinel expected)"
        )
        assert capture_call.kwargs["analyze_lint"] is True

    def test_imports_flag_does_not_disable_lint_analysis(self, tmp_path, monkeypatch):
        """Passing --imports alone should not force analyze_lint=False.

        The None sentinel must survive so analyze_files reads the config default.
        """
        import imodent.cli as cli_module

        test_file = tmp_path / "test.py"
        test_file.write_text("import os\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr(sys, "argv", ["imodent", str(test_file), "--analyze", "--imports"])
        cli_module.main()

        assert capture_call.kwargs is not None
        assert capture_call.kwargs["analyze_imports"] is True
        assert capture_call.kwargs["analyze_lint"] is None, (
            "--imports must not suppress lint analysis (None sentinel expected)"
        )


class TestCLIHelp:
    """Test CLI help output."""

    def test_help_contains_fix_mode(self):
        """Help should mention fix mode."""
        captured = StringIO()
        with patch("sys.stdout", captured):
            with patch.object(sys, "argv", ["imodent", "--help"]):
                with pytest.raises(SystemExit):
                    main()
        help_text = captured.getvalue()
        assert "FIX mode" in help_text or "fix mode" in help_text

    def test_help_contains_scan_mode(self):
        """Help should mention scan mode."""
        captured = StringIO()
        with patch("sys.stdout", captured):
            with patch.object(sys, "argv", ["imodent", "--help"]):
                with pytest.raises(SystemExit):
                    main()
        help_text = captured.getvalue()
        assert "SCAN mode" in help_text or "scan mode" in help_text
        assert "Scan mode always recurses" in help_text
        assert "--fix                         auto-fix safe import issues" in help_text
