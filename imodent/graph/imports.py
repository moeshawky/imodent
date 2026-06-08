"""Import extraction from Python source files."""

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ImportInfo:
    """Information about a single import statement."""

    module: str  # The module being imported
    name: str | None  # Specific name (from X import Y) or None (import X)
    alias: str | None  # as Z
    line: int
    is_from_import: bool
    file: Path

    @property
    def full_name(self) -> str:
        """Get the full imported name."""
        if self.name:
            return f"{self.module}.{self.name}"
        return self.module

    @property
    def import_statement(self) -> str:
        """Reconstruct the import statement."""
        if self.is_from_import:
            stmt = f"from {self.module} import {self.name}"
        else:
            stmt = f"import {self.module}"
        if self.alias:
            stmt += f" as {self.alias}"
        return stmt


def extract_imports(source: str, file: Path) -> list[ImportInfo]:
    """Extract all imports from Python source code.

    Args:
        source: Python source code
        file: Path to the file (for ImportInfo)

    Returns:
        List of ImportInfo objects
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fallback to regex for broken code
        return _extract_imports_regex(source, file)

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    ImportInfo(
                        module=alias.name,
                        name=None,
                        alias=alias.asname,
                        line=node.lineno,
                        is_from_import=False,
                        file=file,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(
                    ImportInfo(
                        module=module,
                        name=alias.name,
                        alias=alias.asname,
                        line=node.lineno,
                        is_from_import=True,
                        file=file,
                    )
                )

    return imports


def _extract_imports_regex(source: str, file: Path) -> list[ImportInfo]:
    """Fallback regex-based import extraction for broken code."""
    imports = []
    lines = source.splitlines()

    # Match: import X, import X as Y, import X, Y, Z
    import_pattern = r"^import\s+([a-zA-Z_][a-zA-Z0-9_.]*(?:\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*)?(?:\s*,\s*[a-zA-Z_][a-zA-Z0-9_.]*(?:\s+as\s+[a-zA-Z_][a-zA-Z0-9_]?)?)*)"

    # Match: from X import Y, from X import Y as Z
    from_pattern = r"^from\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s+import\s+(.+)"

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("#"):
            continue

        # Try from import first
        match = re.match(from_pattern, stripped)
        if match:
            module = match.group(1)
            names_part = match.group(2)

            # Parse names (could be: Y, Y as Z, Y, Z as W)
            for name_spec in names_part.split(","):
                name_spec = name_spec.strip()
                if " as " in name_spec:
                    name, alias = name_spec.split(" as ")
                    name = name.strip()
                    alias = alias.strip()
                else:
                    name = name_spec
                    alias = None

                imports.append(
                    ImportInfo(
                        module=module,
                        name=name,
                        alias=alias,
                        line=i,
                        is_from_import=True,
                        file=file,
                    )
                )
            continue

        # Try regular import
        match = re.match(import_pattern, stripped)
        if match:
            modules_part = match.group(1)

            for mod_spec in modules_part.split(","):
                mod_spec = mod_spec.strip()
                if " as " in mod_spec:
                    mod, alias = mod_spec.split(" as ")
                    mod = mod.strip()
                    alias = alias.strip()
                else:
                    mod = mod_spec
                    alias = None

                imports.append(
                    ImportInfo(
                        module=mod,
                        name=None,
                        alias=alias,
                        line=i,
                        is_from_import=False,
                        file=file,
                    )
                )

    return imports


def resolve_module_name(file: Path, project_root: Path) -> str:
    """Resolve file path to module name.

    Args:
        file: Path to Python file
        project_root: Root of the project

    Returns:
        Module name (e.g., 'imodent.analysis.context')
    """
    try:
        rel_path = file.relative_to(project_root)
        parts = list(rel_path.parts)

        # Remove .py extension from last part
        if parts and parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]

        # Remove __init__ if present
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]

        return ".".join(parts) if parts else ""
    except ValueError:
        return file.stem
