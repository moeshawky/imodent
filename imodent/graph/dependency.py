"""Dependency graph builder for multi-file analysis."""

import ast
from pathlib import Path
from typing import Optional
from ..analysis.context import DependencyGraph, FileInfo, SymbolUsage
from ..analysis.findings import Location
from .imports import extract_imports, resolve_module_name, ImportInfo


def build_dependency_graph(
    files: dict[Path, FileInfo], project_root: Optional[Path] = None
) -> DependencyGraph:
    """Build dependency graph from analyzed files.

    Args:
        files: Dict of path -> FileInfo
        project_root: Root of project (for module resolution)

    Returns:
        Populated DependencyGraph
    """
    if project_root is None:
        # Guess project root from file paths
        if files:
            project_root = _find_common_root(files.keys())
        else:
            project_root = Path.cwd()

    graph = DependencyGraph()

    # First pass: register all modules
    for path, file_info in files.items():
        if file_info.language == "python":
            module_name = resolve_module_name(path, project_root)
            graph.file_to_module[path] = module_name
            graph.module_to_file[module_name] = path

    # Second pass: extract and resolve imports
    for path, file_info in files.items():
        if file_info.language != "python":
            continue

        importer_module = graph.file_to_module.get(path, "")
        imports = extract_imports(file_info.content, path)

        for imp in imports:
            # Resolve the import to a module name
            importee_module = _resolve_import(imp, project_root, graph)
            if importee_module and importee_module != importer_module:
                graph.add_import(importer_module, importee_module)

    return graph


def _find_common_root(paths: list[Path]) -> Path:
    """Find common ancestor of all paths."""
    if not paths:
        return Path.cwd()

    # Convert to absolute paths
    abs_paths = [p.absolute() for p in paths]

    # Start with first path's parents
    common = abs_paths[0]

    for path in abs_paths[1:]:
        # Find common prefix
        while common not in path.parents and common != path:
            if common.parent == common:
                # Reached root
                break
            common = common.parent

    return common


def _resolve_import(
    imp: ImportInfo, project_root: Path, graph: DependencyGraph
) -> Optional[str]:
    """Resolve an import to a module name.

    Args:
        imp: ImportInfo object
        project_root: Project root path
        graph: Current dependency graph

    Returns:
        Resolved module name or None if not found
    """
    module = imp.module

    # Check if it's a local module
    if module in graph.module_to_file:
        return module

    # Try as a file path
    possible_paths = [
        project_root / f"{module.replace('.', '/')}.py",
        project_root / module.replace(".", "/") / "__init__.py",
    ]

    for path in possible_paths:
        if path.exists():
            return resolve_module_name(path, project_root)

    # It's likely a third-party module
    # Still track it in the graph for usage analysis
    return module


def find_unused_imports(
    graph: DependencyGraph, files: dict[Path, FileInfo], project_root: Path
) -> list[dict]:
    """Find imports that are never used in the project.

    Args:
        graph: Dependency graph
        files: All analyzed files
        project_root: Project root

    Returns:
        List of dicts with 'module', 'imported_by', 'is_third_party'
    """
    unused = []

    for module, importers in graph.imported_by.items():
        # Check if the module itself exists in project
        is_local = module in graph.module_to_file

        # Check if module is used anywhere
        # For third-party modules, we check if they're imported anywhere
        # For local modules, we check if they're actually used

        if not importers:
            # No one imports this - could be an entry point or unused
            if is_local:
                unused.append(
                    {
                        "module": module,
                        "imported_by": [],
                        "is_third_party": False,
                        "file": graph.module_to_file.get(module),
                    }
                )

    return unused


def trace_symbol_usage(
    symbol: str, files: dict[Path, FileInfo], graph: DependencyGraph
) -> list[SymbolUsage]:
    """Trace where a symbol is used across the codebase.

    Args:
        symbol: Symbol name to trace
        files: All analyzed files
        graph: Dependency graph

    Returns:
        List of usage locations as SymbolUsage objects
    """
    usages = []

    for path, file_info in files.items():
        if file_info.language != "python" or not file_info.ast_tree:
            continue

        for node in ast.walk(file_info.ast_tree):
            if isinstance(node, ast.Name) and node.id == symbol:
                usages.append(
                    SymbolUsage(
                        symbol=symbol,
                        file=path,
                        location=Location(line=node.lineno),
                        context="reference",
                    )
                )
            elif isinstance(node, ast.Attribute) and node.attr == symbol:
                usages.append(
                    SymbolUsage(
                        symbol=symbol,
                        file=path,
                        location=Location(line=node.lineno),
                        context="reference",
                    )
                )

    return usages


def _get_context(content: str, line: int, context_lines: int = 2) -> str:
    """Get context around a line."""
    lines = content.splitlines()
    start = max(0, line - context_lines - 1)
    end = min(len(lines), line + context_lines)
    return "\n".join(lines[start:end])
