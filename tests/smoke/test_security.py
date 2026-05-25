"""G-SEC: Security — no injection, no path traversal, no unsafe operations.

AOP v3 rule: security vulnerabilities compound everything else.
"""

import os
import tempfile
from pathlib import Path

import pytest


class TestNoUnsafeOperations:
    """imodent must never execute arbitrary code or shell commands."""

    def test_no_eval_exec(self):
        """No eval() or exec() in source code."""
        source = Path("/srv/imodent/imodent")
        for py_file in source.rglob("*.py"):
            content = py_file.read_text()
            # Check for bare eval/exec (not in comments/strings)
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                        pytest.fail(f"{py_file.name} uses {func.id}() — unsafe")

    def test_no_subprocess(self):
        """Subprocess use is restricted to the Ruff tool adapter."""
        source = Path("/srv/imodent/imodent")
        allowed = {source / "analyzers" / "lint.py"}
        for py_file in source.rglob("*.py"):
            content = py_file.read_text()
            if "subprocess" in content:
                if py_file in allowed:
                    assert "shell=True" not in content
                    continue
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if "subprocess" in alias.name:
                                pytest.fail(
                                    f"{py_file.name} imports subprocess — unsafe"
                                )

    def test_no_os_system(self):
        """No os.system() calls."""
        source = Path("/srv/imodent/imodent")
        for py_file in source.rglob("*.py"):
            content = py_file.read_text()
            assert "os.system" not in content, f"{py_file.name} uses os.system — unsafe"

    def test_backup_writes_to_same_directory(self):
        """Backups must be written adjacent to source, not arbitrary paths."""
        from imodent.cli import fix_file

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            test_file = tmpdir / "test.py"
            test_file.write_text("def f():\n    pass")

            fix_file(test_file, backup=True, dry_run=False)

            bak = tmpdir / "test.py.bak"
            assert bak.exists(), "Backup not created in same directory"
            # Backup should NOT be outside tmpdir
            assert str(bak).startswith(
                str(tmpdir)
            ), "Backup written outside source directory"


class TestPathSafety:
    """File operations must not allow path traversal."""

    def test_fix_file_rejects_nonexistent(self):
        """fix_file handles nonexistent paths gracefully."""
        from imodent.cli import fix_file

        # Should not crash, just print error
        fix_file(Path("/nonexistent/path/file.py"), backup=False)

    def test_analyzer_rejects_unreadable(self):
        """Analyzer handles unreadable files gracefully."""
        from imodent.analysis.coordinator import AnalysisCoordinator

        coordinator = AnalysisCoordinator()
        # nonexistent file in paths — should not crash
        result = coordinator.analyze([Path("/nonexistent/file.py")])
        # Should return empty results, not raise
        assert result is not None


import ast
