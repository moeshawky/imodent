"""Import analyzer with cognitive intent detection.

Understands import intent to avoid false positives:
- REGISTRATION: Triggers decorators (plugin systems)
- RE_EXPORT: Public API re-exports in __init__.py
- TYPING: Type hints and annotations
- CONDITIONAL: Function-level or try-block imports
- SIDE_EFFECT: Imports that trigger module initialization
"""
import ast
import re
from pathlib import Path
from typing import Optional

from .base import Analyzer, AnalyzerCapability
from ..analysis.context import AnalysisContext, FileInfo
from ..analysis.findings import Finding, Severity, Location
from ..graph.imports import extract_imports, ImportInfo


# Files indicating public API re-export
_RE_EXPORT_FILES = {"__init__.py", "interfaces.py"}

# Modules often used only for typing
_TYPING_MODULES = {"typing", "abc", "dataclasses", "collections.abc"}

# Known registration modules - imports from these are side-effect imports
_REGISTRATION_MODULES = {
    "strategies",
    "analyzers", 
    "fixers",
    "registry",
}

# Pattern for decorator-based registration
_REGISTRATION_PATTERN = re.compile(r"@\w+\.register|@register|_load_plugins")

# Comment markers that indicate intentional imports
_PROTECTION_MARKERS = re.compile(r"#\s*(do not remove|side.effect|registration|trigger)", re.IGNORECASE)


def _is_in_try_block(content: str, line: int) -> bool:
    """Check if line is inside a try block."""
    lines = content.split("\n")
    if line < 1 or line > len(lines):
        return False

    target_indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())

    for i in range(line - 2, -1, -1):
        lstripped = lines[i].lstrip()
        if not lstripped:
            continue
        current_indent = len(lines[i]) - len(lstripped)
        if current_indent <= target_indent:
            if lstripped.startswith("try:"):
                return True
            if lstripped.startswith(("except", "else:", "finally:")):
                return False
    return False


def _is_in_function_or_class(content: str, line: int) -> bool:
    """Check if line is inside a function or class definition."""
    lines = content.split("\n")
    if line < 1 or line > len(lines):
        return False

    target_indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())
    if target_indent == 0:
        return False

    for i in range(line - 2, -1, -1):
        lstripped = lines[i].lstrip()
        if not lstripped:
            continue
        current_indent = len(lines[i]) - len(lstripped)
        if current_indent < target_indent:
            if lstripped.startswith(("def ", "class ", "async def ")):
                return True
            return False
    return False


def _extract_annotation_names(annotation) -> set[str]:
    """Extract all type names from an annotation node."""
    names = set()
    if isinstance(annotation, ast.Name):
        names.add(annotation.id)
    elif isinstance(annotation, ast.Subscript):
        # Optional[Location], List[Location]
        if isinstance(annotation.value, ast.Name):
            names.add(annotation.value.id)
        # Extract from subscript slice
        if isinstance(annotation.slice, ast.Name):
            names.add(annotation.slice.id)
        elif isinstance(annotation.slice, ast.Tuple):
            for elt in annotation.slice.elts:
                if isinstance(elt, ast.Name):
                    names.add(elt.id)
    elif isinstance(annotation, ast.Attribute):
        # module.Type
        if isinstance(annotation.value, ast.Name):
            names.add(annotation.value.id)
    return names


def _detect_import_intent(
    imp: ImportInfo, content: str, file_path: Path
) -> tuple[str, str, bool]:
    """
    Detect import intent and return (intent, reason, is_safe_to_remove).
    
    Returns:
        intent: Category of import (registration, re_export, typing, side_effect, usage)
        reason: Human-readable explanation
        is_safe_to_remove: Whether auto-fix can safely remove this import
    """
    module = imp.module.split(".")[0] if imp.module else ""
    filename = file_path.name
    lines = content.split("\n")
    
    # Check for protection markers in comments
    for i in range(max(0, imp.line - 3), min(len(lines), imp.line + 1)):
        if i < len(lines) and _PROTECTION_MARKERS.search(lines[i]):
            return "side_effect", "Protected by comment marker", False
    
    # Check if in re-export file
    if filename in _RE_EXPORT_FILES:
        return "re_export", "Public API re-export - do not remove", False
    
    # Check if typing module
    if module in _TYPING_MODULES:
        return "typing", "Type hints import - used in annotations", False
    
    # Check if from registration module (side-effect import)
    if module in _REGISTRATION_MODULES:
        return "side_effect", f"Registration module import from '{module}' - triggers decorators", False
    
    # Check if in try/except block
    if _is_in_try_block(content, imp.line):
        return "side_effect", "Conditional import in try block - may be for optional dependencies", False
    
    # Check for registration patterns in file
    if _REGISTRATION_PATTERN.search(content):
        return "registration", "File has registration decorators - imports may trigger them", False
    
    return "usage", "Normal import - appears unused", False


def _has_type_annotations(ast_tree) -> bool:
    """Check if file has type annotations."""
    for node in ast.walk(ast_tree):
        if isinstance(node, ast.AnnAssign):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                return True
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation:
                    return True
    return False


class ImportAnalyzer(Analyzer):
    @property
    def name(self) -> str:
        return "imports"

    @property
    def capabilities(self):
        return {AnalyzerCapability.IMPORTS}

    @property
    def languages(self):
        return {"python"}

    @property
    def requires_ast(self) -> bool:
        return True

    def analyze(self, context: AnalysisContext) -> list[Finding]:
        """Analyze imports for issues."""
        findings = []

        for path, file_info in context.files.items():
            if file_info.language != "python":
                continue

            imports = extract_imports(file_info.content, path)

            # Find duplicates (only module-level)
            duplicates = self._find_duplicates(imports, file_info.content)
            findings.extend(duplicates)

            # Find unused
            unused = self._find_unused_in_file(file_info, imports)
            findings.extend(unused)

            # Check project-wide usage
            for imp in imports:
                if self._is_local_import(imp, context):
                    continue

                used_elsewhere = self._check_project_usage(imp, context)
                if not used_elsewhere and imp.name:
                    intent, reason, _ = _detect_import_intent(
                        imp, file_info.content, path
                    )
                    
                    finding = Finding.create(
                        type="unused_import",
                        severity=Severity.INFO,
                        file=path,
                        message=f"Import '{imp.import_statement}' may be unused ({reason})",
                        location=Location(line=imp.line),
                        fixable=True,
                        auto_fix_safe=False,  # Never safe by default
                        import_name=imp.name,
                        import_module=imp.module,
                        usage_count=0,
                        data={
                            "import_info": {
                                "module": imp.module,
                                "name": imp.name,
                                "alias": imp.alias,
                                "is_from": imp.is_from_import,
                                "intent": intent,
                                "intent_reason": reason,
                            }
                        },
                    )
                    findings.append(finding)

        return findings

    def _find_duplicates(
        self, imports: list[ImportInfo], source_content: str = ""
    ) -> list[Finding]:
        """Find duplicate imports. Only flags if BOTH are at module scope."""
        findings = []
        seen = {}

        for imp in imports:
            key = (imp.module, imp.name, imp.alias)

            # Skip if inside function/class
            if source_content and _is_in_function_or_class(source_content, imp.line):
                continue

            if key in seen:
                first_imp = seen[key]
                # Skip if first was inside function
                if source_content and _is_in_function_or_class(
                    source_content, first_imp.line
                ):
                    continue

                findings.append(
                    Finding.create(
                        type="duplicate_import",
                        severity=Severity.WARNING,
                        file=imp.file,
                        message=f"Duplicate import '{imp.import_statement}' (first at line {first_imp.line})",
                        location=Location(line=imp.line),
                        fixable=True,
                        auto_fix_safe=True,
                        import_name=imp.name,
                        import_module=imp.module,
                        data={"first_occurrence": first_imp.line},
                    )
                )
            else:
                seen[key] = imp

        return findings

    def _find_unused_in_file(
        self, file_info: FileInfo, imports: list[ImportInfo]
    ) -> list[Finding]:
        """Find imports not used in the file."""
        findings = []

        if not file_info.ast_tree:
            return findings

        # Check if file has type annotations
        has_annotations = _has_type_annotations(file_info.ast_tree)

        # Collect ALL name references including type annotations
        used_names = set()
        for node in ast.walk(file_info.ast_tree):
            # Direct usage
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            # Attribute access (foo.bar -> foo)
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name):
                    used_names.add(node.value.id)
            # Type annotations (AnnAssign)
            elif isinstance(node, ast.AnnAssign):
                if node.annotation:
                    used_names.update(_extract_annotation_names(node.annotation))
            # Function definitions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Return type
                if node.returns:
                    used_names.update(_extract_annotation_names(node.returns))
                # Argument annotations
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation:
                        used_names.update(_extract_annotation_names(arg.annotation))
                # Decorators
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        used_names.add(dec.id)
            # Class definitions
            elif isinstance(node, ast.ClassDef):
                # Base classes
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        used_names.add(base.id)
                    elif isinstance(base, ast.Subscript):
                        used_names.update(_extract_annotation_names(base))
                # Decorators
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        used_names.add(dec.id)

        # Check __all__ exports
        dunder_all_names = set()
        for node in ast.walk(file_info.ast_tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(
                                    elt.value, str
                                ):
                                    dunder_all_names.add(elt.value)

        for imp in imports:
            name_to_check = imp.alias or imp.name or imp.module.split(".")[0]

            # Skip typing modules - they're always potentially used
            if name_to_check in _TYPING_MODULES:
                continue

            # Check if used
            is_used = name_to_check in used_names or name_to_check in dunder_all_names

            if not is_used:
                # Cognitive detection
                intent, intent_reason, auto_fix_safe = _detect_import_intent(
                    imp, file_info.content, file_info.path
                )

                # Better message based on intent
                if intent in ("registration", "re_export", "typing", "side_effect"):
                    message = f"Import '{imp.import_statement}' not directly used: {intent_reason}"
                else:
                    message = f"Import '{imp.import_statement}' is not used in this file"

                findings.append(
                    Finding.create(
                        type="unused_import_file",
                        severity=Severity.INFO,
                        file=file_info.path,
                        message=message,
                        location=Location(line=imp.line),
                        fixable=True,
                        auto_fix_safe=auto_fix_safe,
                        import_name=imp.name,
                        import_module=imp.module,
                        data={
                            "import_info": {
                                "module": imp.module,
                                "name": imp.name,
                                "checked_name": name_to_check,
                                "intent": intent,
                                "intent_reason": intent_reason,
                            }
                        },
                    )
                )

        return findings

    def _is_local_import(
        self, imp: ImportInfo, context: AnalysisContext
    ) -> bool:
        """Check if import is from the same project."""
        module = imp.module.split(".")[0] if imp.module else ""
        return module in context.graph.module_to_file

    def _check_project_usage(
        self, imp: ImportInfo, context: AnalysisContext
    ) -> bool:
        """Check if imported symbol is used elsewhere."""
        name = imp.alias or imp.name
        if not name:
            return False
        module = imp.module
        return len(context.graph.get_importers(module)) > 1
