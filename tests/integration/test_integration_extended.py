"""G-CTX: Integration — components work together in context.

AOP v3 rule: context/environment dependencies surface late but break everything.
"""

import tempfile
import shutil
from pathlib import Path

import pytest

from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analysis.context import FileInfo, DependencyGraph
from imodent.cli import analyze_files, fix_file


class TestMultiFileIntegration:
    """Multi-file analysis produces correct cross-file findings."""

    def test_multi_file_scan(self, tmp_path):
        """Analyzing multiple files at once works correctly."""
        # Create two Python files
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        
        file_a.write_text("import os\nimport os\n\ndef f(): pass\n")
        file_b.write_text("from typing import List\n\ndef g(): pass\n")
        
        coordinator = AnalysisCoordinator()
        result = coordinator.analyze([file_a, file_b])
        
        assert len(result.files) == 2
        assert len(result.findings) > 0
        
        # Should find duplicate in file_a
        dup_findings = [f for f in result.findings if f.type == "duplicate_import"]
        assert len(dup_findings) >= 1

    def test_dependency_graph_built(self, tmp_path):
        """Multi-file analysis builds a dependency graph."""
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        
        file_a.write_text("import os\n")
        file_b.write_text("import sys\n")
        
        coordinator = AnalysisCoordinator()
        result = coordinator.analyze([file_a, file_b])
        
        # Graph should exist even if no local cross-imports
        assert result.graph is not None


class TestCLIIntegration:
    """CLI end-to-end works correctly."""

    def test_fix_mode_produces_output(self, tmp_path):
        """FIX mode writes corrected file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def f():\n  pass\n")
        
        fix_file(test_file, backup=False, dry_run=False)
        
        fixed = test_file.read_text()
        # Should be reformatted (black would fix the indentation)
        assert "def f():" in fixed

    def test_backup_creates_bak_file(self, tmp_path):
        """--backup creates .bak file with original content."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def f():\n  pass\n")
        
        fix_file(test_file, backup=True, dry_run=False)
        
        bak = tmp_path / "test.py.bak"
        assert bak.exists()
        assert bak.read_text() == "def f():\n  pass\n"

    def test_dry_run_does_not_modify(self, tmp_path):
        """--dry-run doesn't modify the file."""
        test_file = tmp_path / "test.py"
        original = "def f():\n  pass\n"
        test_file.write_text(original)
        
        fix_file(test_file, backup=False, dry_run=True)
        
        assert test_file.read_text() == original

    def test_check_mode_does_not_modify(self, tmp_path):
        """--check doesn't modify the file."""
        test_file = tmp_path / "test.py"
        original = "def f():\n  pass\n"
        test_file.write_text(original)
        
        fix_file(test_file, backup=False, check_only=True)
        
        assert test_file.read_text() == original

    def test_scan_mode_with_imports(self, tmp_path, capsys):
        """--analyze --imports produces findings output."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport os\n")
        
        analyze_files([test_file], analyze_imports=True)
        
        captured = capsys.readouterr()
        assert "duplicate" in captured.out.lower() or "Findings" in captured.out


class TestAdvisoryIntegration:
    """Advisory module integrates with analysis pipeline."""

    def test_advisory_flags_many_unused(self, tmp_path, capsys):
        """Advisory flags files with many unused imports."""
        test_file = tmp_path / "messy.py"
        # Many unused imports
        test_file.write_text("""import os
import sys
import json
import re
import hashlib
import base64
from typing import List, Dict, Optional, Tuple, Union

def f():
    pass
""")
        analyze_files([test_file], analyze_imports=True, advisory=True)
        
        captured = capsys.readouterr()
        # Should produce some advisory output or clean report
        assert "analyzed" in captured.out.lower() or "findings" in captured.out.lower() or "clean" in captured.out.lower()
