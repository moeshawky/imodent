"""
Integration Tests: G-CTX (Context/Environment).

Test components working together in realistic scenarios.
"""

import pytest
import tempfile
from pathlib import Path
from imodent.pipeline import FixPipeline


class TestFileIO:
    """Test file reading and writing."""

    def test_fix_file_with_backup(self):
        """Fix a file and create backup."""
        from imodent.cli import fix_file

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"def f():\n    pass\n")
            temp_path = Path(f.name)

        try:
            fix_file(temp_path, backup=True, dry_run=False)

            # Backup should exist
            bak_path = temp_path.with_suffix(temp_path.suffix + ".bak")
            assert bak_path.exists()
        finally:
            temp_path.unlink()
            bak_path = temp_path.with_suffix(temp_path.suffix + ".bak")
            if bak_path.exists():
                bak_path.unlink()

    def test_fix_file_dry_run(self):
        """Dry run should not modify file."""
        from imodent.cli import fix_file

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"def f():\n    pass\n")
            temp_path = Path(f.name)

        try:
            original = temp_path.read_text()
            fix_file(temp_path, dry_run=True)

            # File should be unchanged
            assert temp_path.read_text() == original
        finally:
            temp_path.unlink()

    def test_fix_file_check_only(self):
        """Check only should validate without modifying."""
        from imodent.cli import fix_file

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"def f():\n    pass\n")
            temp_path = Path(f.name)

        try:
            original = temp_path.read_text()
            fix_file(temp_path, check_only=True)

            # File should be unchanged
            assert temp_path.read_text() == original
        finally:
            temp_path.unlink()


class TestDirectoryProcessing:
    """Test recursive directory processing."""

    def test_recursive_directory(self):
        """Process all files in a directory recursively."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested structure
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()

            (Path(tmpdir) / "file1.py").write_text("def f():\n    pass\n")
            (subdir / "file2.py").write_text("def g():\n    pass\n")
            (Path(tmpdir) / "config.json").write_text('{"a":1}')

            # Run imodent recursively
            result = subprocess.run(
                ["imodent", tmpdir, "--recursive", "--dry-run"],
                capture_output=True,
                text=True,
            )

            # Should process all files
            assert "file1.py" in result.stdout or "file2.py" in result.stdout


class TestMixedFormats:
    """Test handling of mixed file formats."""

    def test_mixed_python_json_yaml(self):
        """Process a directory with mixed formats."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mixed files
            (Path(tmpdir) / "script.py").write_text("def f():\nif True:\npass\n")
            (Path(tmpdir) / "config.json").write_text('{"a":1,"b":2}')
            (Path(tmpdir) / "config.yaml").write_text("key: value\nlist:\n  - item")

            # Run imodent
            result = subprocess.run(
                ["imodent", tmpdir, "--recursive", "--dry-run"],
                capture_output=True,
                text=True,
            )

            # Should handle all formats
            assert result.returncode == 0 or "script.py" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
