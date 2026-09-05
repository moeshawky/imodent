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
"""Analysis context - shared state for all analyzers."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import Evidence
from .findings import Finding, Location


@dataclass
class FileInfo:
    """Information about a single source file."""

    path: Path
    content: str
    language: str
    encoding: str = "utf-8"
    has_syntax_errors: bool = False
    ast_tree: ast.AST | None = None  # Renamed to avoid conflict with ast module

    @classmethod
    def from_path(cls, path: Path) -> FileInfo:
        """Load file info from path."""
        content = path.read_text(encoding="utf-8")
        language = cls._detect_language(path)
        ast_tree = None
        has_errors = False

        if language == "python":
            try:
                ast_tree = ast.parse(content, type_comments=True)
            except (SyntaxError, ValueError):
                try:
                    ast_tree = ast.parse(content)
                except (SyntaxError, ValueError):
                    has_errors = True
                    ast_tree = None

        return cls(
            path=path,
            content=content,
            language=language,
            has_syntax_errors=has_errors,
            ast_tree=ast_tree,
        )

    @staticmethod
    def _detect_language(path: Path) -> str:
        """Detect language from file extension."""
        suffix = path.suffix.lower()
        lang_map = {
            ".py": "python",
            ".pyw": "python",
            ".pyi": "python",
            ".json": "json",
            ".jsonl": "jsonl",
            ".ndjson": "jsonl",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".rs": "rust",
            ".toml": "toml",
        }
        return lang_map.get(suffix, "unknown")


@dataclass
class DependencyGraph:
    """Import dependency graph for a project."""

    # module -> list of modules it imports
    imports: dict[str, list[str]] = field(default_factory=dict)
    # module -> list of modules that import it
    imported_by: dict[str, list[str]] = field(default_factory=dict)
    # file path -> module name
    file_to_module: dict[Path, str] = field(default_factory=dict)
    # module name -> file path
    module_to_file: dict[str, Path] = field(default_factory=dict)

    def add_import(self, importer: str, importee: str):
        """Add an import relationship."""
        if importer not in self.imports:
            self.imports[importer] = []
        if importee not in self.imports[importer]:
            self.imports[importer].append(importee)

        if importee not in self.imported_by:
            self.imported_by[importee] = []
        if importer not in self.imported_by[importee]:
            self.imported_by[importee].append(importer)

    def get_importers(self, module: str) -> list[str]:
        """Get all modules that import this module."""
        return self.imported_by.get(module, [])

    def get_importees(self, module: str) -> list[str]:
        """Get all modules this module imports."""
        return self.imports.get(module, [])

    def is_used(self, module: str) -> bool:
        """Check if a module is imported by anything."""
        return len(self.imported_by.get(module, [])) > 0

    def to_dict(self) -> dict:
        """Serialize the dependency graph to a structured dictionary.

        Returns:
            dict with keys:
                - ``modules``: `list[str]` — all known module names
                - ``edges``: `list[dict]` — each with ``from``, ``to``, and
                  ``files`` (importer path, importee path)
                - ``node_count``: `int` — total modules
                - ``edge_count``: `int` — total import relationships

        Each edge appears only once even if the same pair has multiple
        import statements.
        """
        modules = sorted(self.imports.keys())
        edges: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()
        for importer, importees in self.imports.items():
            for importee in importees:
                pair = (importer, importee)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                importer_file = self.module_to_file.get(importer)
                importee_file = self.module_to_file.get(importee)
                edges.append(
                    {
                        "from": importer,
                        "to": importee,
                        "from_file": str(importer_file) if importer_file else None,
                        "to_file": str(importee_file) if importee_file else None,
                    }
                )
        return {
            "modules": modules,
            "edges": edges,
            "node_count": len(modules),
            "edge_count": len(edges),
        }

    def to_mermaid(self) -> str:
        """Render the dependency graph as Mermaid flowchart syntax.

        Returns:
            A string with Mermaid ``flowchart LR`` syntax.  Each module
            becomes a ``[module]`` node; each import edge becomes an
            arrow ``A --> B``.  The output is suitable for embedding in
            Markdown fenced code blocks (`` ```mermaid ``).

        Example output::

            flowchart LR
                imodent[imodent]
                imodent.cli[imodent.cli]
                imodent.analysis.context[imodent.analysis.context]
                imodent.cli --> imodent.analysis.coordinator
                imodent.analysis.coordinator --> imodent.analysis.context
        """
        lines: list[str] = ["flowchart LR"]
        modules = sorted(set(self.imports.keys()) | set(self.imported_by.keys()))
        # Emit node declarations with short labels
        for module in modules:
            label = module.split(".")[-1] if "." in module else module
            # Sanitize: Mermaid node IDs cannot contain dots
            node_id = module.replace(".", "_")
            lines.append(f"    {node_id}[{label}]")
        # Emit edges (deduplicated)
        seen_pairs: set[tuple[str, str]] = set()
        for importer, importees in self.imports.items():
            for importee in importees:
                pair = (importer, importee)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                src_id = importer.replace(".", "_")
                tgt_id = importee.replace(".", "_")
                lines.append(f"    {src_id} --> {tgt_id}")
        return "\n".join(lines)

    def to_dot(self) -> str:
        """Render the dependency graph as Graphviz DOT format.

        Returns:
            A string with ``digraph`` DOT syntax.  Each module is a node
            with its short name as label.  Each import edge is a directed
            edge ``A -> B``.  Suitable for rendering with ``dot -Tpng``.
        """
        lines: list[str] = [
            "digraph imodent_deps {",
            '    rankdir="LR";',
            "    node [shape=box, style=rounded];",
        ]
        modules = sorted(set(self.imports.keys()) | set(self.imported_by.keys()))
        for module in modules:
            node_id = module.replace(".", "_")
            label = module.split(".")[-1] if "." in module else module
            lines.append(f'    {node_id} [label="{label}"];')
        seen_pairs: set[tuple[str, str]] = set()
        for importer, importees in self.imports.items():
            for importee in importees:
                pair = (importer, importee)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                src_id = importer.replace(".", "_")
                tgt_id = importee.replace(".", "_")
                lines.append(f"    {src_id} -> {tgt_id};")
        lines.append("}")
        return "\n".join(lines)


@dataclass
class SymbolUsage:
    """A usage of a symbol in code."""

    symbol: str
    file: Path
    location: Location
    context: str  # 'import', 'call', 'reference', 'assignment'


@dataclass
class AnalysisConfig:
    """Configuration for analysis."""

    # What to analyze
    check_imports: bool = True
    check_syntax: bool = True
    check_lint: bool = True
    check_types: bool = False

    # Rust advisory support (alpha)
    check_rust: bool = False
    run_cargo: bool = False
    run_cargo_check: bool = False
    run_cargo_clippy: bool = False

    # How to handle findings
    auto_fix_safe: bool = True
    auto_fix_all: bool = False
    interactive: bool = False

    # Paths
    include_patterns: list[str] = field(default_factory=lambda: ["*.py", "**/*.py"])
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "**/test_*.py",
            "test_*.py",
            "**/__pycache__/**",
            "__pycache__/**",
            "**/.pytest_cache/**",
            ".pytest_cache/**",
            "**/.ruff_cache/**",
            ".ruff_cache/**",
            "**/.mypy_cache/**",
            ".mypy_cache/**",
            "**/.venv/**",
            ".venv/**",
            "**/venv/**",
            "venv/**",
            "**/build/**",
            "build/**",
            "**/dist/**",
            "dist/**",
            "**/*.egg-info/**",
            "*.egg-info/**",
            "**/target/**",
            "target/**",
            "target",
        ]
    )
    exclude_patterns_from_config: bool = False

    # External tools
    use_ruff: bool = True
    use_pyright: bool = False


@dataclass
class AnalysisContext:
    """Shared context for all analyzers."""

    files: dict[Path, FileInfo] = field(default_factory=dict)
    graph: DependencyGraph = field(default_factory=DependencyGraph)
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    project_root: Path | None = None

    def get_file(self, path: Path) -> FileInfo | None:
        """Get file info by path."""
        return self.files.get(path)

    def get_importers(self, module: str) -> list[str]:
        """Get modules that import this module."""
        return self.graph.get_importers(module)

    def get_importees(self, module: str) -> list[str]:
        """Get modules this module imports."""
        return self.graph.get_importees(module)

    def is_module_used(self, module: str) -> bool:
        """Check if module is used anywhere."""
        return self.graph.is_used(module)

    def add_finding(self, finding):
        """Add a finding to the context."""
        self.findings.append(finding)

    def add_evidence(self, evidence: Evidence):
        """Add a replayable evidence record to the context."""
        self.evidence.append(evidence)
