# AnalysisConfig controlling which analyzers run and how findings are handled.
# Loaded from .imodent.yaml / pyproject.toml by load_config() in config.py.
# Defaults to an empty AnalysisConfig() with all defaults when no config file is found.
# Accumulates all analysis findings (unused imports, lints, residue, Rust advisories).
# Each analyzer appends via add_finding(); deduplication happens downstream in the coordinator.
# Import dependency graph tracking module→imports and module→imported_by relationships.
# Built by DependencyGraphBuilder in graph/dependency.py then attached here.
# Maps file paths to their parsed FileInfo (content, language, AST tree).
# Populated by the coordinator during file discovery.
# Whether to use Pyright for type checking.
# Defaults to False.  Pyright must be installed separately (npm or pip).
# Whether to use Ruff for linting (preferred over Pyright).
# Defaults to True.  Ruff must be available on PATH or in the virtual environment.
# True when the user provided exclude_patterns in their config file.
# When True, even explicitly passed file paths go through the exclude filter.
# When False (default), explicit CLI file paths bypass exclude_patterns filtering.
# Set by _apply_dict_to_config() in config.py when exclude_patterns key is present
# in loaded config data.
# Glob patterns for files/directories to skip during analysis.
# Defaults include __pycache__, .pytest_cache, .ruff_cache, .mypy_cache,
# .venv, venv, build, dist, *.egg-info, and target directories.
# When exclude_patterns_from_config is False, these defaults are only
# applied to directory scans — explicit file paths bypass them.
# Glob patterns for files to include in analysis.
# Defaults to ['*.py', '**/*.py'] — all Python files at any depth.
# When Rust scanning is enabled, '.rs' patterns are appended at runtime.
# Prompt the user before applying each fix.
# Defaults to False — runs non-interactively by default.
# Auto-apply all fixes regardless of confidence level.
# Defaults to False — requires explicit opt-in due to potential false positives.
# Auto-apply fixes with confidence >= 0.80 (high-confidence, non-destructive).
# Defaults to True.
# Whether to shell out to `cargo clippy` for higher-signal Rust diagnostics.
# Only active when check_rust is True.  Defaults to False.
# Clippy provides lint-specific diagnostics that cargo check does not.
# Alias for run_cargo.  Mapped from 'run_cargo_check' config key.
# If both run_cargo and run_cargo_check are set, they share the same behavior.
# Whether to shell out to `cargo check` for Rust diagnostics verification.
# Only active when check_rust is True.  Defaults to False.
# Whether to run Rust advisory analysis (config scan, clippy.toml/rustomt.toml check,
# broad allow detection).  Defaults to False — Rust support is opt-in.
# Requires cargo/clippy available on PATH for full diagnostics.
# Whether to run type checking via Pyright.
# Defaults to False — Pyright is not bundled; must be installed separately.
# Whether to run lint analysis via Ruff subprocess.
# Defaults to True.
# Whether to run syntax validation on source files via ast.parse().
# Defaults to True.
# Whether to run import analysis (unused, duplicate, intent-detected imports).
# Defaults to True.
"""Project context — discovered project metadata for analysis."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.context import AnalysisConfig
from .config import _get_toml_loader, load_config

PROJECT_MARKERS = [
    "pyproject.toml",
    "setup.py",
    ".git",
    "requirements.txt",
    "setup.cfg",
]


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
        """Auto-populates project_name from project_root if not explicitly set.
        Calls _discover_project_name() which reads pyproject.toml or falls back
        to the directory name.
        """
        if not self.project_name:
            self.project_name = _discover_project_name(self.project_root)

    @classmethod
    def discover(cls, path: Path) -> ProjectContext:
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
    def from_root(cls, root: Path) -> ProjectContext:
        """Create project context from an explicit project root."""
        config = load_config(root)
        return cls(project_root=root, config=config, has_project_markers=True)


def _find_project_root(path: Path) -> Path | None:
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
                with pyproject.open("rb") as f:
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
