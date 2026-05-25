"""Shared path discovery helpers."""

from __future__ import annotations

from pathlib import Path


DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
}


def is_generated_artifact(path: Path) -> bool:
    """Return whether a path is under a generated or cache directory."""
    if any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in path.parts):
        return True
    return any(part.endswith(".egg-info") for part in path.parts)
