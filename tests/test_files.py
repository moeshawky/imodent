"""Tests for file-oriented operations — FileInfo, _collect_targets, is_generated_artifact."""

from pathlib import Path

from imodent.analysis.context import FileInfo
from imodent.cli import _collect_targets
from imodent.project.discovery import is_generated_artifact


# ---------------------------------------------------------------------------
# FileInfo tests
# ---------------------------------------------------------------------------

def test_fileinfo_from_path_python(tmp_path):
    """FileInfo.from_path on a .py file has language='python' and non-None AST."""
    py = tmp_path / "test.py"
    py.write_text("x = 1\n")
    info = FileInfo.from_path(py)
    assert info.language == "python"
    assert info.ast_tree is not None


def test_fileinfo_from_path_json(tmp_path):
    """FileInfo.from_path on a .json file has language='json'."""
    jf = tmp_path / "data.json"
    jf.write_text('{"a": 1}')
    info = FileInfo.from_path(jf)
    assert info.language == "json"
    # JSON files don't get an AST tree
    assert info.ast_tree is None


def test_fileinfo_from_path_yaml(tmp_path):
    """FileInfo.from_path on a .yaml file has language='yaml'."""
    yf = tmp_path / "config.yaml"
    yf.write_text("key: value\n")
    info = FileInfo.from_path(yf)
    assert info.language == "yaml"


def test_fileinfo_from_path_unknown_extension(tmp_path):
    """FileInfo.from_path on unknown extension has language='unknown'."""
    f = tmp_path / "README.md"
    f.write_text("# Hello\n")
    info = FileInfo.from_path(f)
    assert info.language == "unknown"


def test_fileinfo_from_path_with_encoding(tmp_path):
    """FileInfo.from_path reads with utf-8 encoding by default."""
    py = tmp_path / "unicode.py"
    py.write_text("# café\nx = 'résumé'\n", encoding="utf-8")
    info = FileInfo.from_path(py)
    assert "café" in info.content


# ---------------------------------------------------------------------------
# _collect_targets tests
# ---------------------------------------------------------------------------

def test_collect_targets_symlink_excluded(tmp_path):
    """_collect_targets on a symlink returns [].

    Tests: The function in cli.py checks file_path.is_symlink() and returns [].
    """
    target = tmp_path / "real.py"
    target.write_text("x = 1\n")
    symlink = tmp_path / "link.py"
    symlink.symlink_to(target)
    result = _collect_targets(symlink)
    assert result == []


def test_collect_targets_nonexistent():
    """_collect_targets on a nonexistent path returns [].

    Tests: The function prints an error to stderr and returns [] for paths
    that don't exist.
    """
    result = _collect_targets(Path("/nonexistent/path/12345"))
    assert result == []


def test_collect_targets_unsupported_extension(tmp_path):
    """_collect_targets on a directory with unsupported files returns [].

    Tests: When glob finds files but none have handled extensions, returns [].
    """
    d = tmp_path / "txtdir"
    d.mkdir()
    (d / "readme.txt").write_text("hello")
    result = _collect_targets(d, recursive=False)
    assert result == []


def test_collect_targets_directory_non_recursive(tmp_path):
    """_collect_targets on a directory with non-recursive collects only direct children."""
    d = tmp_path / "mydir"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    (d / "data.json").write_text('{"a": 1}')
    subdir = d / "sub"
    subdir.mkdir()
    (subdir / "b.py").write_text("y = 2\n")

    result = _collect_targets(d, recursive=False)
    # Non-recursive should only see a.py and data.json (direct children)
    names = {p.name for p in result}
    assert "a.py" in names
    assert "data.json" in names
    assert "b.py" not in names, "Non-recursive should not descend into subdirs"


def test_collect_targets_directory_recursive(tmp_path):
    """_collect_targets on a directory with recursive=True collects all nested files."""
    d = tmp_path / "mydir"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    subdir = d / "sub"
    subdir.mkdir()
    (subdir / "b.py").write_text("y = 2\n")
    (subdir / "data.yaml").write_text("key: value\n")

    result = _collect_targets(d, recursive=True)
    names = {p.name for p in result}
    assert "a.py" in names
    assert "b.py" in names
    assert "data.yaml" in names


# ---------------------------------------------------------------------------
# is_generated_artifact tests
# ---------------------------------------------------------------------------

def test_is_generated_artifact_venv():
    """is_generated_artifact returns True for .venv paths."""
    assert is_generated_artifact(Path(".venv/lib/python3.12/site-packages/pkg.py")) is True


def test_is_generated_artifact_pycache():
    """is_generated_artifact returns True for __pycache__ paths."""
    assert is_generated_artifact(Path("src/__pycache__/module.cpython-312.pyc")) is True


def test_is_generated_artifact_node_modules():
    """is_generated_artifact returns True for node_modules paths."""
    assert is_generated_artifact(Path("frontend/node_modules/react/index.js")) is True


def test_is_generated_artifact_target():
    """is_generated_artifact returns True for Rust target/ paths."""
    assert is_generated_artifact(Path("target/debug/myapp")) is True


def test_is_generated_artifact_source():
    """is_generated_artifact returns False for normal source paths."""
    assert is_generated_artifact(Path("src/main.py")) is False
    assert is_generated_artifact(Path("imodent/cli.py")) is False
    assert is_generated_artifact(Path("tests/test_cli.py")) is False


def test_is_generated_artifact_egg_info():
    """is_generated_artifact returns True for .egg-info paths."""
    assert is_generated_artifact(Path("myproject.egg-info/PKG-INFO")) is True
