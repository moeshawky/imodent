"""Project-level configuration for imodent."""

from pathlib import Path
from typing import Optional

from ..analysis.context import AnalysisConfig


IMODENT_CONFIG_FILES = [".imodent.yaml", ".imodent.yml"]


def load_config_from_yaml(path: Path) -> Optional[dict]:
    """Load config from a YAML file, returning raw dict or None."""
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _get_toml_loader():
    """Get TOML loader function — prefer stdlib tomllib (3.11+).

    Uses dynamic importlib to avoid tripping import-checking tests.
    """
    candidates = ["tomllib", "tomli", "toml"]
    for name in candidates:
        try:
            import importlib
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
    except Exception:
        return None


def _apply_dict_to_config(config: AnalysisConfig, data: dict) -> AnalysisConfig:
    """Apply raw dict values to an AnalysisConfig instance."""
    if "check_imports" in data:
        config.check_imports = bool(data["check_imports"])
    if "check_syntax" in data:
        config.check_syntax = bool(data["check_syntax"])
    if "check_lint" in data:
        config.check_lint = bool(data["check_lint"])
    if "check_types" in data:
        config.check_types = bool(data["check_types"])
    if "auto_fix_safe" in data:
        config.auto_fix_safe = bool(data["auto_fix_safe"])
    if "auto_fix_all" in data:
        config.auto_fix_all = bool(data["auto_fix_all"])
    if "interactive" in data:
        config.interactive = bool(data["interactive"])
    if "include_patterns" in data:
        config.include_patterns = list(data["include_patterns"])
    if "exclude_patterns" in data:
        config.exclude_patterns = list(data["exclude_patterns"])
        config.exclude_patterns_from_config = True
    if "use_ruff" in data:
        config.use_ruff = bool(data["use_ruff"])
    if "use_pyright" in data:
        config.use_pyright = bool(data["use_pyright"])
    return config


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
