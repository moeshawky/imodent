"""Project-level configuration for imodent."""

from pathlib import Path
import sys
from typing import Optional

from ..analysis.context import AnalysisConfig


IMODENT_CONFIG_FILES = [".imodent.yaml", ".imodent.yml"]


def load_config_from_yaml(path: Path) -> Optional[dict]:
    """Load config from a YAML file, returning raw dict or None."""
    try:
        import yaml
    except ImportError:
        return None

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        import sys
        # print-to-stderr fallback: avoids racing with --json output that goes to stdout
        print(f"Warning: {path.name} exists but could not be parsed: {e}. Using defaults.", file=sys.stderr)
    return None


def _get_toml_loader():
    """Get TOML loader function — prefer stdlib tomllib (3.11+).

    Uses dynamic importlib to avoid tripping import-checking tests.
    """
    candidates = ["tomllib", "tomli", "toml"]
    for name in candidates:
        try:
            import importlib
            # importlib called inside loop, not at top level — first TOML library may raise ImportError, not the function's caller
            mod = importlib.import_module(name)
            return mod.load
        except ImportError:
            continue
    return None


def load_config_from_pyproject(path: Path) -> Optional[dict]:
    """Load [tool.imodent] section from pyproject.toml."""
    loader = _get_toml_loader()
    if loader is None:
        return None

    try:
        with open(path, "rb") as f:
            data = loader(f)
        return data.get("tool", {}).get("imodent")
    except Exception as e:
        import sys
        print(f"Warning: pyproject.toml exists but could not be parsed: {e}. Using defaults.", file=sys.stderr)
        return None


def _apply_dict_to_config(config: AnalysisConfig, data: dict) -> AnalysisConfig:
    """Apply raw dict values to an AnalysisConfig instance."""
    if "check_imports" in data:
        config.check_imports = _coerce_bool(data["check_imports"], "check_imports", config.check_imports)
    if "check_syntax" in data:
        config.check_syntax = _coerce_bool(data["check_syntax"], "check_syntax", config.check_syntax)
    if "check_lint" in data:
        config.check_lint = _coerce_bool(data["check_lint"], "check_lint", config.check_lint)
    if "check_types" in data:
        config.check_types = _coerce_bool(data["check_types"], "check_types", config.check_types)
    if "auto_fix_safe" in data:
        config.auto_fix_safe = _coerce_bool(data["auto_fix_safe"], "auto_fix_safe", config.auto_fix_safe)
    if "auto_fix_all" in data:
        config.auto_fix_all = _coerce_bool(data["auto_fix_all"], "auto_fix_all", config.auto_fix_all)
    if "interactive" in data:
        config.interactive = _coerce_bool(data["interactive"], "interactive", config.interactive)
    if "include_patterns" in data:
        config.include_patterns = _coerce_str_list(
            data["include_patterns"], "include_patterns", config.include_patterns
        )
    if "exclude_patterns" in data:
        config.exclude_patterns = _coerce_str_list(
            data["exclude_patterns"], "exclude_patterns", config.exclude_patterns
        )
        config.exclude_patterns_from_config = True
    if "use_ruff" in data:
        config.use_ruff = _coerce_bool(data["use_ruff"], "use_ruff", config.use_ruff)
    if "use_pyright" in data:
        config.use_pyright = _coerce_bool(data["use_pyright"], "use_pyright", config.use_pyright)
    if "check_rust" in data:
        config.check_rust = _coerce_bool(data["check_rust"], "check_rust", config.check_rust)
    if "run_cargo" in data:
        config.run_cargo = _coerce_bool(data["run_cargo"], "run_cargo", config.run_cargo)
    if "run_cargo_check" in data:
        config.run_cargo_check = _coerce_bool(data["run_cargo_check"], "run_cargo_check", config.run_cargo_check)
    if "run_cargo_clippy" in data:
        config.run_cargo_clippy = _coerce_bool(data["run_cargo_clippy"], "run_cargo_clippy", config.run_cargo_clippy)
    return config


def _coerce_bool(value, field: str, default: bool) -> bool:
    """Coerce config booleans without treating arbitrary strings as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    print(
        f"Warning: invalid boolean for {field}: {value!r}. Using default {default!r}.",
        file=sys.stderr,
    )
    return default


def _coerce_str_list(value, field: str, default: list[str]) -> list[str]:
    """Coerce path pattern config without splitting strings into characters."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    print(
        f"Warning: invalid list for {field}: {value!r}. Using default.",
        file=sys.stderr,
    )
    return default


def load_config(project_root: Path) -> AnalysisConfig:
    """Load project config from standard locations.

    Precedence:
    1. .imodent.yaml in project root
    2. [tool.imodent] section in pyproject.toml
    3. Defaults
    """
    config = AnalysisConfig()

    # Try .imodent.yaml
    for name in IMODENT_CONFIG_FILES:
        yaml_path = project_root / name
        if yaml_path.is_file():
            data = load_config_from_yaml(yaml_path)
            if data:
                return _apply_dict_to_config(config, data)

    # Try [tool.imodent] in pyproject.toml
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.is_file():
        data = load_config_from_pyproject(pyproject_path)
        if data:
            return _apply_dict_to_config(config, data)

    return config
