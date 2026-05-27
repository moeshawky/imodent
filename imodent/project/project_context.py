"""Project context — discovered project metadata for analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Optional

from ..analysis.context import AnalysisConfig
from .config import load_config, _get_toml_loader


PROJECT_MARKERS = ["pyproject.toml", "setup.py", ".git", "requirements.txt", "setup.cfg"]


@dataclass
class ProjectContext:
    """Discovered project context for a single analysis run.

    Wraps project root discovery, config loading, and metadata into one object.
    This is the bridge between the CLI (path-based) and the coordinator (project-aware).
    """

    project_root: Path
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    project_name: str = ""
    has_project_markers: bool = True

    @property
    def cache_dir(self) -> Path:
        """Resolve the cache directory for this project."""
        return self.project_root / ".imodent" / "cache"

    def __post_init__(self):
        if not self.project_name:
            self.project_name = _discover_project_name(self.project_root)

    @classmethod
    def discover(cls, path: Path) -> "ProjectContext":
        """Discover project context from a file or directory path.

        Walks up from the given path looking for project markers.
        If none found, uses the first existing parent or cwd.
        """
        root = _find_project_root(path)
        has_markers = root is not None

        if root is None:
            root = path if path.is_dir() else path.parent

        config = load_config(root)

        return cls(
            project_root=root,
            config=config,
            has_project_markers=has_markers,
        )

    @classmethod
    def from_root(cls, root: Path) -> "ProjectContext":
        """Create project context from an explicit project root."""
        config = load_config(root)
        return cls(project_root=root, config=config, has_project_markers=True)


def _find_project_root(path: Path) -> Optional[Path]:
    """Walk up from path to find project root using markers."""
    current = path.resolve()
    if not current.is_dir():
        current = current.parent

    while current != current.parent:
        for marker in PROJECT_MARKERS:
            if (current / marker).exists():
                return current
        current = current.parent

    return None


def _discover_project_name(project_root: Path) -> str:
    """Extract project name from pyproject.toml or fall back to directory name."""
    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        loader = _get_toml_loader()
        if loader is not None:
            try:
                with open(pyproject, "rb") as f:
                    data = loader(f)
                name = data.get("project", {}).get("name", "")
                if name:
                    return str(name)
            except Exception as e:
                print(f"Warning: Could not parse {pyproject}: {e}", file=sys.stderr)

    return project_root.name


def _find_project_root_or_cwd(path: Path) -> Path:
    """Find project root or fall back to cwd."""
    root = _find_project_root(path)
    return root or Path.cwd()
