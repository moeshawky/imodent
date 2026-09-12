"""Tests for path discovery helpers."""

from pathlib import Path

import pytest

from imodent.project.discovery import is_generated_artifact


@pytest.mark.parametrize(
    "path_str",
    [
        ".git/config",
        "src/.mypy_cache/foo.json",
        "build/lib/foo.py",
        "dist/foo.whl",
        "node_modules/pkg/index.js",
        "htmlcov/index.html",
        "__pycache__/foo.cpython-39.pyc",
        ".tox/py39/bin/python",
        ".venv/bin/activate",
        "venv/bin/activate",
        "target/debug/foo",
    ],
)
def test_is_generated_artifact_excluded_dirs(path_str: str) -> None:
    """Test that paths containing excluded directories are identified as generated artifacts."""
    assert is_generated_artifact(Path(path_str)) is True


@pytest.mark.parametrize(
    "path_str",
    [
        "foo.egg-info/PKG-INFO",
        "src/foo.egg-info/SOURCES.txt",
    ],
)
def test_is_generated_artifact_egg_info(path_str: str) -> None:
    """Test that paths containing .egg-info directories are identified as generated artifacts."""
    assert is_generated_artifact(Path(path_str)) is True


@pytest.mark.parametrize(
    "path_str",
    [
        "src/main.py",
        "README.md",
        "tests/test_discovery.py",
        "docs/index.md",
        "a/b/c/d.txt",
        "builder/file.py",  # 'builder' is not 'build'
        "distributed/file.py",  # 'distributed' is not 'dist'
    ],
)
def test_is_generated_artifact_normal_paths(path_str: str) -> None:
    """Test that normal paths are not identified as generated artifacts."""
    assert is_generated_artifact(Path(path_str)) is False
