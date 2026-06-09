"""Import fixer - handles unused/duplicate imports with options.

When moedularizer is available, provides a 'refactor' action that
uses cross-file dependency analysis to suggest wiring paths for
imports that carry usage intent but no local references.
"""

import ast

from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, FixOption
from ..analyzers.imports import _is_single_alias_import_statement
from ..interfaces import FixResult
from .base import Fixer


def _moedularizer_available() -> bool:
    """Check whether moedularizer is installed and importable."""
    try:
        import moedularizer  # noqa: F401
        return True
    except ImportError:
        return False


def _parse_content_or_none(content: str):
    """Parse Python source, returning the AST tree or None on syntax error."""
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def _reconstruct_import_line(
    node: ast.Import | ast.ImportFrom,
    remaining: list[ast.alias],
    content: str,
    line: int,
) -> str:
    """Rebuild an import line with only the given aliases.

    Preserves original indentation. For single remaining alias on a from-import,
    emits a one-line form without parentheses. For multiple aliases, wraps in
    parentheses matching the original line's continuation style.
    """
    indent = _extract_indent(content, line)

    if isinstance(node, ast.ImportFrom):
        names_str = ", ".join(_format_alias(n.name, n.asname) for n in remaining)
        if len(remaining) == 1 and not _has_parens(content, line):
            return f"{indent}from {node.module} import {names_str}"
        return f"{indent}from {node.module} import ({names_str})"

    names_str = ", ".join(_format_alias(n.name, n.asname) for n in remaining)
    return f"{indent}import {names_str}"


def _format_alias(name: str, asname: str | None) -> str:
    """Format a single alias for an import statement."""
    if asname:
        return f"{name} as {asname}"
    return name


def _extract_indent(content: str, line: int) -> str:
    """Extract leading whitespace from a line."""
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return ""
    return lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]


def _has_parens(content: str, line: int) -> bool:
    """Check if the import line at *line* uses parenthesized continuation."""
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return False
    stripped = lines[line - 1].strip()
    return "(" in stripped or ")" in stripped


def _drop_import_line(content: str, line_idx: int) -> FixResult:
    """Remove an import line and a following blank line if present."""
    lines = content.splitlines()
    new_lines = lines[:line_idx] + lines[line_idx + 1 :]
    if line_idx < len(new_lines) and not new_lines[line_idx].strip():
        new_lines = new_lines[:line_idx] + new_lines[line_idx + 1 :]

    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    try:
        ast.parse(new_content)
    except SyntaxError as e:
        return FixResult(
            success=False,
            content=content,
            errors=[f"Import line removal would make Python invalid: {e}"],
            warnings=[],
            original_valid=True,
            fixed_valid=False,
        )

    return FixResult(
        success=True,
        content=new_content,
        errors=[],
        warnings=[],
        original_valid=True,
        fixed_valid=True,
    )


class ImportFixer(Fixer):
    """Fixer for import-related issues."""

    @property
    def name(self) -> str:
        return "imports"

    @property
    def handles(self):
        return {"unused_import", "unused_import_file", "duplicate_import"}

    def can_auto_fix(self, finding: Finding) -> bool:
        """Check if finding can be safely auto-fixed."""
        return finding.type == "duplicate_import"

    def get_options(
        self, finding: Finding, context: AnalysisContext
    ) -> list[FixOption]:
        """Get fix options for an import finding."""
        options = []

        if finding.type == "duplicate_import":
            # For duplicates, only one real option
            options.append(
                FixOption(
                    id="remove",
                    label="Remove duplicate",
                    description="Remove this duplicate import (keeping the first occurrence)",
                    action="delete",
                    is_safe=True,
                )
            )

        elif finding.type in ("unused_import", "unused_import_file"):
            import_info = finding.data.get("import_info", {})
            name = import_info.get("name", finding.import_name or "")

            # Option 1: Refactor / wire usage (when moedularizer is available).
            # In agentic development, an unused import is evidence of
            # unfinished intent — moedularizer traces dependencies and
            # generates proper cross-module wiring.
            if _moedularizer_available():
                options.append(
                    FixOption(
                        id="refactor",
                        label="Refactor / wire usage",
                        description="Use moedularizer to trace dependencies and generate wiring",
                        action="refactor",
                        is_safe=True,
                        requires_input=False,
                    )
                )

            # Option 2: Investigate/wire manually.
            options.append(
                FixOption(
                    id="investigate",
                    label="Investigate usage",
                    description="Search codebase and wire the intended usage before deleting",
                    action="investigate",
                    is_safe=True,
                    requires_input=False,
                )
            )

            # Option 2: Keep for typing
            if name:
                options.append(
                    FixOption(
                        id="keep_typing",
                        label="Keep for type hints",
                        description=f"Keep '{name}' - it may be used in type annotations",
                        action="keep",
                        is_safe=True,
                        requires_input=False,
                    )
                )

            # Option 3: Keep with reason
            options.append(
                FixOption(
                    id="keep_reason",
                    label="Keep with reason",
                    description="Keep this import and provide a reason",
                    action="keep",
                    is_safe=True,
                    requires_input=True,  # User needs to provide reason
                )
            )

            # Option 4: False positive
            options.append(
                FixOption(
                    id="false_positive",
                    label="This is used",
                    description="Mark as false positive - the import IS used",
                    action="use",
                    is_safe=True,
                    requires_input=True,  # User provides where it's used
                )
            )

            # Option 5: Delete, terminal action only after intent review
            options.append(
                FixOption(
                    id="delete",
                    label="Remove import",
                    description="Remove only after no wiring, export, registration, or typing intent remains",
                    action="delete",
                    is_safe=False,
                )
            )

        return options

    def apply_fix(self, finding: Finding, option: FixOption, content: str) -> FixResult:
        """Apply the selected fix option."""
        if option.action == "delete":
            return self._remove_import(finding, content)
        if option.action == "keep":
            # Keep the import - no change needed
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[f"Import kept as requested: {option.description}"],
                original_valid=True,
                fixed_valid=True,
            )
        if option.action == "refactor":
            return self._refactor_import(finding, content)
        if option.action == "investigate":
            # Return with investigation request
            return FixResult(
                success=False,
                content=content,
                errors=["Investigation required - please search codebase for usage"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )
        if option.action == "use":
            # Mark as used - no change
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[f"Import marked as used: {option.description}"],
                original_valid=True,
                fixed_valid=True,
            )

        return FixResult(
            success=False,
            content=content,
            errors=[f"Unknown action: {option.action}"],
            warnings=[],
            original_valid=True,
            fixed_valid=True,
        )

    def _remove_import(self, finding: Finding, content: str) -> FixResult:
        """Remove an import from content."""
        lines = content.splitlines()
        location = finding.location

        if not location:
            return FixResult(
                success=False,
                content=content,
                errors=["Cannot find import location"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        line_idx = location.line - 1  # Convert to 0-indexed

        if line_idx < 0 or line_idx >= len(lines):
            return FixResult(
                success=False,
                content=content,
                errors=[f"Invalid line number: {location.line}"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        if not _is_single_alias_import_statement(content, location.line):
            return self._remove_alias_from_multi_import(
                finding, content, lines, line_idx
            )

        # Remove the line
        new_lines = lines[:line_idx] + lines[line_idx + 1 :]

        # Also remove empty line if it follows
        if line_idx < len(new_lines) and not new_lines[line_idx].strip():
            new_lines = new_lines[:line_idx] + new_lines[line_idx + 1 :]

        new_content = "\n".join(new_lines)
        if content.endswith("\n"):
            new_content += "\n"

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            return FixResult(
                success=False,
                content=content,
                errors=[f"Import removal would make Python invalid: {e}"],
                warnings=[],
                original_valid=True,
                fixed_valid=False,
            )

        return FixResult(
            success=True,
            content=new_content,
            errors=[],
            warnings=[],
            original_valid=True,
            fixed_valid=True,
        )

    def _remove_alias_from_multi_import(
        self,
        finding: Finding,
        content: str,
        lines: list[str],
        line_idx: int,
    ) -> FixResult:
        """Remove a single alias from a multi-import statement.

        Rewrites ``from X import A, B, C`` to ``from X import A, C`` when
        an unused alias is targeted for removal. If the target is the last
        remaining alias, removes the entire import line.
        """
        import_info = finding.data.get("import_info", {})
        name = import_info.get("name", "")
        alias = import_info.get("alias")
        target = alias or name

        if not target:
            return FixResult(
                success=False,
                content=content,
                errors=[
                    "No target name or alias in import_info; cannot perform alias-level removal"
                ],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        tree = _parse_content_or_none(content)
        if tree is None:
            return FixResult(
                success=False,
                content=content,
                errors=["Cannot parse source to locate multi-import names"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node.lineno != finding.location.line:
                continue

            if getattr(node, "end_lineno", node.lineno) != node.lineno:
                return FixResult(
                    success=False,
                    content=content,
                    errors=[
                        "Import spans multiple lines; alias-level removal from "
                        "multi-line imports is not yet supported. Remove the "
                        "entire statement or edit manually."
                    ],
                    warnings=[],
                    original_valid=True,
                    fixed_valid=True,
                )

            remaining = [n for n in node.names if (n.asname or n.name) != target]

            if len(remaining) == len(node.names):
                return FixResult(
                    success=False,
                    content=content,
                    errors=[
                        f"Alias '{target}' not found in import statement at line {finding.location.line}"
                    ],
                    warnings=[],
                    original_valid=True,
                    fixed_valid=True,
                )

            if not remaining:
                return _drop_import_line(content, line_idx)

            new_line = _reconstruct_import_line(node, remaining, content, line_idx)
            new_lines = [*lines[:line_idx], new_line, *lines[line_idx + 1 :]]
            new_content = "\n".join(new_lines)
            if content.endswith("\n") and not new_content.endswith("\n"):
                new_content += "\n"

            try:
                ast.parse(new_content)
            except SyntaxError as e:
                return FixResult(
                    success=False,
                    content=content,
                    errors=[f"Alias removal would make Python invalid: {e}"],
                    warnings=[],
                    original_valid=True,
                    fixed_valid=False,
                )

            return FixResult(
                success=True,
                content=new_content,
                errors=[],
                warnings=[
                    f"Removed unused alias '{target}' from import at line {finding.location.line}"
                ],
                original_valid=True,
                fixed_valid=True,
            )

        return FixResult(
            success=False,
            content=content,
            errors=[f"No import node found at line {finding.location.line}"],
            warnings=[],
            original_valid=True,
            fixed_valid=True,
        )

    def _refactor_import(self, finding: Finding, content: str) -> FixResult:
        """Wire an import using moedularizer dependency analysis.

        Uses moedularizer's ImodentBridge to trace cross-file usage
        of the imported symbol and produces a structured report showing
        how the import is used in other files — turning the finding
        from a deletion candidate into a wiring guide.
        """
        try:
            from moedularizer.imodent_bridge import ImodentBridge
        except ImportError:
            return FixResult(
                success=False,
                content=content,
                errors=[
                    "moedularizer is not installed. "
                    "Install it with: pip install moedularizer"
                ],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        import_info = finding.data.get("import_info", {})
        name = import_info.get("name", finding.import_name or "")
        module = import_info.get("module", finding.import_module or "")

        bridge = ImodentBridge()
        project_dir = finding.file.parent
        project_paths = [project_dir] if project_dir.exists() else [finding.file]

        try:
            report = bridge.analyze_project(project_paths, check_lint=False)
        except Exception as e:
            return FixResult(
                success=False,
                content=content,
                errors=[f"moedularizer analysis failed: {e}"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        # Build wiring guidance from the report
        lines: list[str] = []
        lines.append(
            f"Wire guidance for '{module}.{name}' at {finding.file.name}:{finding.location.line}"
        )

        if report.cross_file_deps:
            # Show which modules import this symbol
            importers = [
                m for m, deps in report.cross_file_deps.items()
                if module in deps
            ]
            if importers:
                lines.append(
                    f"  Cross-file importers ({len(importers)}): "
                    + ", ".join(sorted(importers)[:5])
                )

        # Show per-file usage data
        if report.import_usage:
            for path, usages in report.import_usage.items():
                matching = [
                    u for u in usages
                    if u.module == module and (u.name == name or name is None)
                ]
                if matching and path != finding.file:
                    for u in matching[:3]:
                        lines.append(
                            f"  {path.name}:{u.line} — {u.message}"
                        )

        if report.warnings:
            lines.append(f"  Warnings from analysis ({len(report.warnings)}):")
            for w in report.warnings[:5]:
                lines.append(f"    {w}")

        # The import is kept in-place; the guidance shows where to wire it
        return FixResult(
            success=True,
            content=content,
            errors=[],
            warnings=lines,
            original_valid=True,
            fixed_valid=True,
        )
