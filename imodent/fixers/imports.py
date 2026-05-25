"""Import fixer - handles unused/duplicate imports with options."""

import ast

from .base import Fixer
from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, FixOption
from ..interfaces import FixResult


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
        if finding.type == "duplicate_import":
            return True  # Safe to remove duplicates

        # Unused imports need review - might be for typing, __all__, etc.
        return False

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
                    preview=self._preview_remove_import(finding),
                )
            )

        elif finding.type in ("unused_import", "unused_import_file"):
            import_info = finding.data.get("import_info", {})
            name = import_info.get("name", finding.import_name or "")

            # Option 1: Investigate/wire. In LLM-authored codebases, an unused
            # import is often evidence of unfinished intent, not trash.
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
                    preview=self._preview_remove_import(finding),
                )
            )

        return options

    def apply_fix(self, finding: Finding, option: FixOption, content: str) -> FixResult:
        """Apply the selected fix option."""
        if option.action == "delete":
            return self._remove_import(finding, content)
        elif option.action == "keep":
            # Keep the import - no change needed
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[f"Import kept as requested: {option.description}"],
                original_valid=True,
                fixed_valid=True,
            )
        elif option.action == "investigate":
            # Return with investigation request
            return FixResult(
                success=False,
                content=content,
                errors=["Investigation required - please search codebase for usage"],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )
        elif option.action == "use":
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

    def _preview_remove_import(self, finding: Finding) -> str:
        """Generate preview of import removal."""
        location = finding.location
        if location:
            return f"Line {location.line}: Remove import"
        return "Remove import"

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

        if not _can_remove_whole_import_line(content, location.line):
            return FixResult(
                success=False,
                content=content,
                errors=[
                    "Import shares a statement with other names; alias-level rewrite required"
                ],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
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


def _can_remove_whole_import_line(content: str, line: int) -> bool:
    """Whole-line deletion is safe only for one-line, one-alias imports."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno == line:
            if getattr(node, "end_lineno", node.lineno) != node.lineno:
                return False
            return len(node.names) == 1
    return False
