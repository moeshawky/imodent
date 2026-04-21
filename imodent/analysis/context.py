"""Analysis context - shared state for all analyzers."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING
import mimetypes

if TYPE_CHECKING:
    pass  # ast is already available at runtime


@dataclass
class FileInfo:
    """Information about a single source file."""
    path: Path
    content: str
    language: str
    encoding: str = 'utf-8'
    has_syntax_errors: bool = False
    ast_tree: Optional[ast.AST] = None  # Renamed to avoid conflict with ast module
    
    @classmethod
    def from_path(cls, path: Path) -> "FileInfo":
        """Load file info from path."""
        content = path.read_text(encoding='utf-8')
        language = cls._detect_language(path)
        ast_tree = None
        has_errors = False
        
        if language == 'python':
            try:
                ast_tree = ast.parse(content)
            except SyntaxError:
                has_errors = True
        
        return cls(
            path=path,
            content=content,
            language=language,
            has_syntax_errors=has_errors,
            ast_tree=ast_tree
        )
    
    @staticmethod
    def _detect_language(path: Path) -> str:
        """Detect language from file extension."""
        suffix = path.suffix.lower()
        lang_map = {
            '.py': 'python',
            '.pyw': 'python',
            '.pyi': 'python',
            '.json': 'json',
            '.jsonl': 'jsonl',
            '.ndjson': 'jsonl',
            '.yaml': 'yaml',
            '.yml': 'yaml',
        }
        return lang_map.get(suffix, 'unknown')


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


@dataclass
class SymbolUsage:
    """A usage of a symbol in code."""
    symbol: str
    file: Path
    location: Location
    context: str  # 'import', 'call', 'reference', 'assignment'


# Need to import Location from findings
from .findings import Location


@dataclass 
class AnalysisConfig:
    """Configuration for analysis."""
    # What to analyze
    check_imports: bool = True
    check_syntax: bool = True
    check_lint: bool = True
    check_types: bool = False
    
    # How to handle findings
    auto_fix_safe: bool = True
    auto_fix_all: bool = False
    interactive: bool = False
    
    # Paths
    include_patterns: list[str] = field(default_factory=lambda: ['**/*.py'])
    exclude_patterns: list[str] = field(default_factory=lambda: ['**/test_*.py', '**/__pycache__/**'])
    
    # External tools
    use_ruff: bool = True
    use_pyright: bool = False


@dataclass
class AnalysisContext:
    """Shared context for all analyzers."""
    files: dict[Path, FileInfo] = field(default_factory=dict)
    graph: DependencyGraph = field(default_factory=DependencyGraph)
    findings: list = field(default_factory=list)
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    
    def get_file(self, path: Path) -> Optional[FileInfo]:
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
