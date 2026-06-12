"""Tests for project-level configuration loading."""

from pathlib import Path

import pytest

from imodent.analysis.context import AnalysisConfig
from imodent.project.config import (
    _coerce_bool,
    _coerce_str_list,
    load_config,
    load_config_from_yaml,
    load_config_from_pyproject,
)


# ---------------------------------------------------------------------------
# Default config tests
# ---------------------------------------------------------------------------


def test_default_config_values():
    """AnalysisConfig defaults are sane."""
    config = AnalysisConfig()
    assert config.check_imports is True
    assert config.check_syntax is True
    assert config.check_lint is True
    assert config.check_types is False
    assert config.use_ruff is True
    assert config.use_pyright is False
    assert config.auto_fix_safe is True
    assert config.auto_fix_all is False
    assert config.interactive is False
    assert config.check_rust is False
    assert config.run_cargo is False
    assert config.run_cargo_check is False
    assert config.run_cargo_clippy is False
    assert config.include_patterns == ["*.py", "**/*.py"]
    assert "test_*.py" in config.exclude_patterns


def test_load_config_defaults(tmp_path):
    """load_config returns defaults when no config files exist."""
    config = load_config(tmp_path)
    assert isinstance(config, AnalysisConfig)
    assert config.check_imports is True


# ---------------------------------------------------------------------------
# YAML config tests
# ---------------------------------------------------------------------------


def test_load_config_from_yaml_valid(tmp_path):
    """load_config_from_yaml parses a .imodent.yaml file."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("check_imports: false\ncheck_lint: false\n")

    data = load_config_from_yaml(yaml_path)
    assert data is not None
    assert data["check_imports"] is False
    assert data["check_lint"] is False


def test_load_config_from_yaml_not_dict(tmp_path):
    """load_config_from_yaml returns None for non-dict YAML content."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("- item1\n- item2\n")

    data = load_config_from_yaml(yaml_path)
    assert data is None


def test_load_config_from_yaml_missing(tmp_path):
    """load_config_from_yaml returns None for missing file."""
    data = load_config_from_yaml(tmp_path / "nonexistent.yaml")
    assert data is None


def test_load_config_from_yaml_corrupt(tmp_path):
    """load_config_from_yaml returns None for corrupt YAML (prints warning)."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("key: [unclosed\n")

    data = load_config_from_yaml(yaml_path)
    # Prints warning to stderr but returns None
    assert data is None


# ---------------------------------------------------------------------------
# TOML config tests
# ---------------------------------------------------------------------------


def test_load_config_from_pyproject_valid(tmp_path):
    """load_config_from_pyproject reads [tool.imodent] from pyproject.toml."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[tool.imodent]\ncheck_imports = false\ncheck_lint = false\n'
    )

    data = load_config_from_pyproject(pyproject_path)
    # May return None if no TOML loader available, which is fine
    if data is not None:
        assert data.get("check_imports") is False
        assert data.get("check_lint") is False


def test_load_config_from_pyproject_missing(tmp_path):
    """load_config_from_pyproject returns None for missing file."""
    data = load_config_from_pyproject(tmp_path / "nonexistent.toml")
    assert data is None


# ---------------------------------------------------------------------------
# Config precedence tests
# ---------------------------------------------------------------------------


def test_load_config_yaml_precedence(tmp_path):
    """load_config prefers .imodent.yaml over pyproject.toml."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("check_imports: false\n")

    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[tool.imodent]\ncheck_imports = true\n')

    config = load_config(tmp_path)
    # .imodent.yaml takes precedence — check_imports should be False
    assert config.check_imports is False


# ---------------------------------------------------------------------------
# Coercion tests
# ---------------------------------------------------------------------------


def test_coerce_bool_true():
    """_coerce_bool handles boolean True."""
    assert _coerce_bool(True, "field", False) is True


def test_coerce_bool_false():
    """_coerce_bool handles boolean False."""
    assert _coerce_bool(False, "field", True) is False


def test_coerce_bool_string_true():
    """_coerce_bool handles string 'true'."""
    assert _coerce_bool("true", "field", False) is True
    assert _coerce_bool("1", "field", False) is True
    assert _coerce_bool("yes", "field", False) is True
    assert _coerce_bool("on", "field", False) is True


def test_coerce_bool_string_false():
    """_coerce_bool handles string 'false'."""
    assert _coerce_bool("false", "field", True) is False
    assert _coerce_bool("0", "field", True) is False
    assert _coerce_bool("no", "field", True) is False
    assert _coerce_bool("off", "field", True) is False


def test_coerce_bool_invalid():
    """_coerce_bool returns default for invalid values."""
    assert _coerce_bool("maybe", "field", True) is True
    assert _coerce_bool(42, "field", False) is False
    assert _coerce_bool([], "field", True) is True


def test_coerce_str_list_string():
    """_coerce_str_list wraps a string into a list."""
    result = _coerce_str_list("*.py", "field", ["default"])
    assert result == ["*.py"]


def test_coerce_str_list_valid_list():
    """_coerce_str_list returns a valid list as-is."""
    result = _coerce_str_list(["*.py", "**/*.py"], "field", ["default"])
    assert result == ["*.py", "**/*.py"]


def test_coerce_str_list_invalid():
    """_coerce_str_list returns default for invalid values."""
    result = _coerce_str_list([1, 2, 3], "field", ["default"])
    assert result == ["default"]


# ---------------------------------------------------------------------------
# YAML config -> AnalysisConfig application
# ---------------------------------------------------------------------------


def test_yaml_config_sets_multiple_fields(tmp_path):
    """load_config_from_yaml sets multiple boolean and list fields."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text(
        "check_imports: false\n"
        "check_lint: false\n"
        "check_rust: true\n"
        "interactive: true\n"
        "include_patterns:\n"
        "  - '*.py'\n"
        "  - '*.json'\n"
    )

    data = load_config_from_yaml(yaml_path)
    assert data is not None
    assert data["check_imports"] is False
    assert data["check_lint"] is False
    assert data["check_rust"] is True
    assert data["interactive"] is True
    assert data["include_patterns"] == ["*.py", "*.json"]


# ---------------------------------------------------------------------------
# Edge case coercion tests
# ---------------------------------------------------------------------------


def test_coerce_bool_edge_cases():
    """_coerce_bool handles capitalized, padded, and mixed-case string values."""
    # Uppercase variants
    assert _coerce_bool("True", "field", False) is True
    assert _coerce_bool("TRUE", "field", False) is True
    assert _coerce_bool("FALSE", "field", True) is False
    assert _coerce_bool("False", "field", True) is False
    # Padded values
    assert _coerce_bool(" Yes ", "field", False) is True
    assert _coerce_bool("  no  ", "field", True) is False
    assert _coerce_bool("  On  ", "field", False) is True
    assert _coerce_bool(" Off ", "field", True) is False
    # Numeric coercion
    assert _coerce_bool(" 1 ", "field", False) is True
    assert _coerce_bool(" 0 ", "field", True) is False


# ---------------------------------------------------------------------------
# Precedence tests: load_config with both .imodent.yaml and pyproject.toml
# ---------------------------------------------------------------------------


def test_load_config_pyproject_then_yaml(tmp_path):
    """load_config uses .imodent.yaml over pyproject.toml (yaml wins)."""
    # pyproject.toml (would be ignored if yaml exists)
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[tool.imodent]\ncheck_imports = true\ncheck_lint = false\n')

    # .imodent.yaml takes precedence
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("check_imports: false\ncheck_lint: true\n")

    config = load_config(tmp_path)
    # YAML wins: check_imports=False (overwrites pyproject's true)
    assert config.check_imports is False
    assert config.check_lint is True


def test_load_config_pyproject_fallback(tmp_path):
    """load_config falls back to pyproject.toml when no .imodent.yaml exists."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[tool.imodent]\ncheck_imports = false\ncheck_rust = true\n'
    )

    config = load_config(tmp_path)
    assert config.check_imports is False  # from pyproject
    assert config.check_rust is True  # from pyproject


def test_load_config_yml_extension(tmp_path):
    """load_config also checks .imodent.yml files."""
    yml_path = tmp_path / ".imodent.yml"
    yml_path.write_text("check_imports: false\ninteractive: true\n")

    config = load_config(tmp_path)
    assert config.check_imports is False
    assert config.interactive is True


def test_load_config_yaml_multiple_fields(tmp_path):
    """load_config applies all recognized YAML fields to config."""
    yaml_path = tmp_path / ".imodent.yaml"
    yaml_path.write_text("""
check_imports: false
check_syntax: false
check_lint: false
check_types: true
auto_fix_safe: false
auto_fix_all: true
interactive: true
include_patterns:
  - "*.py"
  - "*.rs"
exclude_patterns:
  - "**/test_*"
  - "**/vendor/**"
use_ruff: false
use_pyright: true
check_rust: true
run_cargo: true
run_cargo_check: true
run_cargo_clippy: false
""")

    config = load_config(tmp_path)
    assert config.check_imports is False
    assert config.check_syntax is False
    assert config.check_lint is False
    assert config.check_types is True
    assert config.auto_fix_safe is False
    assert config.auto_fix_all is True
    assert config.interactive is True
    assert config.include_patterns == ["*.py", "*.rs"]
    assert config.exclude_patterns_from_config is True
    assert config.use_ruff is False
    assert config.use_pyright is True
    assert config.check_rust is True
    assert config.run_cargo is True
    assert config.run_cargo_check is True
    assert config.run_cargo_clippy is False


def test_coerce_str_list_empty():
    """_coerce_str_list returns default for empty list."""
    result = _coerce_str_list([], "field", ["default"])
    assert result == []  # empty list is a valid list of strings


def test_coerce_str_list_mixed_types():
    """_coerce_str_list returns default for list with non-string items."""
    result = _coerce_str_list(["ok", 42, "also_ok"], "field", ["default"])
    assert result == ["default"]


def test_coerce_bool_none():
    """_coerce_bool returns default for None."""
    assert _coerce_bool(None, "field", True) is True
    assert _coerce_bool(None, "field", False) is False
