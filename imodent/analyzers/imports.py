# # __all__ collection: iterates list/tuple elements in __all__ = [...] assignments.
# # Names appearing in __all__ are excluded from unused-import detection.
# # This catches explicit package re-export patterns but misses dynamic __all__.
"""Import analyzer with cognitive intent detection.

Understands import intent to avoid false positives:
- REGISTRATION: Triggers decorators (plugin systems)
- RE_EXPORT: Public API re-exports in __init__.py
- TYPING: Type hints and annotations
- CONDITIONAL: Function-level or try-block imports
- SIDE_EFFECT: Imports that trigger module initialization
"""

# NOTE: This file exceeds the 500-line structural review threshold (836 lines).
# Consider splitting into smaller modules when this module next undergoes major changes.

import ast
import re
import sys
from pathlib import Path
from typing import Any

from ..analysis.context import AnalysisContext, FileInfo
from ..analysis.evidence import Evidence
from ..analysis.findings import Finding, Location, Severity
from ..graph.imports import ImportInfo, extract_imports, resolve_module_name
from .base import Analyzer, AnalyzerCapability

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
_PROTECTION_MARKERS = re.compile(
    r"#\s*(do not remove|side.effect|registration|trigger)", re.IGNORECASE
)

_NOQA_PATTERN = re.compile(r"#\s*noqa\b", re.IGNORECASE)


def _has_noqa_comment(content_lines: list[str], lineno: int) -> bool:
    """Check if physical 1-based line carries a # noqa suppression comment."""
    if lineno < 1 or lineno > len(content_lines):
        return False
    return bool(_NOQA_PATTERN.search(content_lines[lineno - 1]))


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


def _classify_try_context(content: str, line: int) -> str:
    """Classify the except handler strength for a try-block import.

    Returns one of:
      - "optional dependency (ImportError)" — strong evidence of optional dep
      - "optional dependency (ModuleNotFoundError)" — strong evidence
      - "broad except Exception" — weak context, import remains reviewable
      - "try block, unknown handler" — default when handler can't be parsed
    """
    lines = content.split("\n")
    if line < 1 or line > len(lines):
        return "try block, unknown handler"

    target_indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())

    # Walk forward from the try line to find the except clause
    for i in range(line - 2, -1, -1):
        lstripped = lines[i].lstrip()
        if not lstripped:
            continue
        current_indent = len(lines[i]) - len(lstripped)
        if current_indent <= target_indent:
            if lstripped.startswith("try:"):
                # Now walk forward from the try to find the except
                try_end = _find_try_end(lines, i)
                return _classify_except_handler(lines, i, try_end, current_indent)
            if lstripped.startswith(("except", "else:", "finally:")):
                return "try block, unknown handler"
    return "try block, unknown handler"


def _find_try_end(lines: list[str], try_line: int) -> int:
    """Find the last line of the try block (before except/else/finally)."""
    try_indent = len(lines[try_line]) - len(lines[try_line].lstrip())
    for i in range(try_line + 1, len(lines)):
        stripped = lines[i].lstrip()
        if not stripped:
            continue
        current_indent = len(lines[i]) - len(stripped)
        if current_indent <= try_indent:
            if stripped.startswith(("except", "else:", "finally:")):
                return i
            break
    return len(lines) - 1


def _classify_except_handler(
    lines: list[str], try_line: int, try_end: int, indent: int
) -> str:
    """Classify the first except handler in the try block."""
    for i in range(try_end, len(lines)):
        stripped = lines[i].lstrip()
        if not stripped:
            continue
        current_indent = len(lines[i]) - len(stripped)
        if current_indent < indent:
            break
        if current_indent == indent:
            if stripped.startswith("except ImportError"):
                return "optional dependency (ImportError)"
            if stripped.startswith("except ModuleNotFoundError"):
                return "optional dependency (ModuleNotFoundError)"
            if stripped.startswith("except Exception"):
                return "broad except Exception"
            if stripped.startswith("except"):
                return "try block, specific handler"
            if stripped.startswith(("else:", "finally:")):
                break
    return "try block, no exception handler"


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
        if current_indent <= target_indent:
            if lstripped.startswith(("def ", "class ", "async def ")):
                return True
            if current_indent == 0:
                # Check if this is a continuation of a multi-line def/class
                if lstripped.startswith(
                    (")", ",", "]", "}", "as ", "except", "finally:")
                ):
                    continue
                return False
            target_indent = current_indent
    return False


def _extract_annotation_names(annotation: Any) -> set[str]:
    """Extract all type names from an annotation node."""
    names = set()
    if isinstance(annotation, ast.Name):
        names.add(annotation.id)
    elif isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        names.update(_extract_annotation_string_names(annotation.value))
    elif isinstance(annotation, ast.Subscript):
        names.update(_extract_annotation_names(annotation.value))
        names.update(_extract_annotation_names(annotation.slice))
    elif isinstance(annotation, ast.Tuple):
        for elt in annotation.elts:
            names.update(_extract_annotation_names(elt))
    elif isinstance(annotation, ast.Attribute):
        # module.Type
        if isinstance(annotation.value, ast.Name):
            names.add(annotation.value.id)
    elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        names.update(_extract_annotation_names(annotation.left))
        names.update(_extract_annotation_names(annotation.right))
    return names


def _extract_annotation_string_names(annotation: str) -> set[str]:
    """Extract type names from string annotations such as 'Dict[str, int]'."""
    try:
        expression = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return set()
    return _extract_annotation_names(expression)


def _collect_type_use_names(ast_tree: ast.AST) -> set[str]:
    """Collect names that are used in annotation/type positions."""
    type_names: set[str] = set()

    for node in ast.walk(ast_tree):
        if (isinstance(node, ast.AnnAssign) and node.annotation) or (
            isinstance(node, ast.arg) and node.annotation
        ):
            type_names.update(_extract_annotation_names(node.annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                type_names.update(_extract_annotation_names(node.returns))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    continue
            comment = getattr(node, "type_comment", None)
            if comment:
                type_names.update(_extract_annotation_string_names(comment))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            comment = getattr(node, "type_comment", None)
            if comment:
                type_names.update(_extract_annotation_string_names(comment))

    return type_names


def _import_binding_evidence(
    imp: ImportInfo, file_path: Path, checked_name: str, intent: str
) -> Evidence:
    """Build normalized evidence for an import binding decision."""
    return Evidence(
        kind="ImportBinding",
        file=file_path,
        location=Location(line=imp.line),
        source="ast",
        subject=checked_name,
        data={
            "module": imp.module,
            "name": imp.name,
            "alias": imp.alias,
            "is_from": imp.is_from_import,
            "intent": intent,
        },
    )


def _is_reexport_candidate(
    imp: ImportInfo, file_path: Path, context: AnalysisContext | None = None
) -> bool:
    """Return whether an import is plausibly a package public API re-export."""
    if file_path.name == "interfaces.py":
        return True
    if file_path.name != "__init__.py":
        return False

    module_root = imp.module.split(".", 1)[0] if imp.module else ""
    if module_root in _TYPING_MODULES:
        return False
    return module_root not in getattr(sys, "stdlib_module_names", set())


def _detect_import_intent(
    imp: ImportInfo,
    content: str,
    file_path: Path,
    type_use_names: set[str] | None = None,
    dunder_all_names: set[str] | None = None,
    context: AnalysisContext | None = None,
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

    # __future__ imports are compiler directives, not runtime names
    if imp.module == "__future__":
        return (
            "side_effect",
            "__future__ compiler directive - not a runtime name",
            False,
        )

    # Check for protection markers in comments
    for i in range(max(0, imp.line - 3), min(len(lines), imp.line + 1)):
        if i < len(lines) and _PROTECTION_MARKERS.search(lines[i]):
            return "side_effect", "Protected by comment marker", False

    if _has_noqa_comment(lines, imp.line):
        return ("side_effect", "Suppressed by # noqa comment", False)

    # __all__ is a stronger re-export signal regardless of filename
    if dunder_all_names:
        name_to_check = (
            imp.alias or imp.name or imp.module.split(".")[0] if imp.module else ""
        )
        if name_to_check in dunder_all_names:
            return (
                "re_export",
                f"Name '{name_to_check}' appears in __all__ export list",
                False,
            )

    # Check if in public API re-export file.
    if filename in _RE_EXPORT_FILES and _is_reexport_candidate(imp, file_path, context):
        return "re_export", "Public API re-export - do not remove", False

    # Check if typing module. This is intent only when the imported binding has
    # a real annotation/type-use edge, not merely because it came from typing.
    if module in _TYPING_MODULES:
        name_to_check = imp.alias or imp.name or module
        if type_use_names and name_to_check in type_use_names:
            return "typing", "Type hints import - used in annotations", False

    # Check if from registration module (side-effect import)
    if module in _REGISTRATION_MODULES:
        return (
            "side_effect",
            f"Registration module import from '{module}' - triggers decorators",
            False,
        )

    # Check if in try/except block — context, not exoneration
    if _is_in_try_block(content, imp.line):
        try_context = _classify_try_context(content, imp.line)
        return "try_block", f"Import in try block: {try_context}", False

    # Check for registration patterns in file
    if _REGISTRATION_PATTERN.search(content):
        return (
            "registration",
            "File has registration decorators - imports may trigger them",
            False,
        )

    return "usage", "Normal import - appears unused", False


def _is_single_alias_import_statement(content: str, line: int) -> bool:
    """Return True only when a line contains a one-alias import statement.

    NOTE: Sibling implementation ``_is_single_alias_source_line`` exists in
    ``imodent.analyzers.lint`` (C28 / Pair 3).  That variant operates on a
    single source line parsed in isolation rather than full-file content.
    Different inputs, different reliability requirements — no code merge.
    """
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
        """
        Returns (intent:str, reason:str, is_safe_to_remove:bool). Intent categories:
        side_effect (__future__, protection markers, registration modules), re_export
        (__init__.py + project-local module), typing (annotation-use edge present),
        try_block (inside try/except), registration (decorator pattern), usage (default).
        NOTE: The is_safe_to_remove return value is DISCARDED by the caller in
        analyze() at line 429 (_ = third element) but USED at line 582 in
        _find_unused_in_file. Inconsistent pattern — two call sites handle the
        third return value differently.
        """
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

            if file_info.ast_tree is None:
                findings.append(
                    Finding.create(
                        type="unanalyzable_file",
                        severity=Severity.INFO,
                        file=path,
                        message="Skipped: file has syntax errors — import analysis unavailable",
                        fixable=False,
                        auto_fix_safe=False,
                    )
                )
                continue

            imports = extract_imports(file_info.content, path)
            has_annotations = _has_type_annotations(file_info.ast_tree)
            type_use_names = (
                _collect_type_use_names(file_info.ast_tree)
                if has_annotations
                else set()
            )
            for imp in imports:
                bound_name = imp.alias or imp.name or imp.module.split(".")[0]
                context.add_evidence(
                    _import_binding_evidence(imp, path, bound_name, "binding")
                )
            for type_name in type_use_names:
                context.add_evidence(
                    Evidence(
                        kind="AnnotationUse",
                        file=path,
                        location=None,
                        source=self.name,
                        subject=type_name,
                    )
                )

            # Find redundant aliases
            redundant_aliases = self._find_redundant_aliases(imports, path)
            findings.extend(redundant_aliases)

            # Find duplicates (only module-level)
            duplicates = self._find_duplicates(imports, file_info.content)
            findings.extend(duplicates)

            # Find unused
            unused = self._find_unused_in_file(file_info, imports, context)
            findings.extend(unused)

            # Check project-wide usage
            for imp in imports:
                if self._is_local_import(imp, context):
                    continue

                # Skip __future__ imports - compiler directives, not runtime names
                if imp.module == "__future__":
                    continue

                used_elsewhere = self._check_project_usage(imp, context)
                if not used_elsewhere and imp.name:
                    # Skip if used in-file (class bases, annotations, etc.)
                    name_to_check = imp.alias or imp.name
                    if name_to_check in type_use_names:
                        continue
                    if file_info.ast_tree and self._is_name_used_in_file(
                        file_info.ast_tree, name_to_check
                    ):
                        continue

                    intent, reason, _ = _detect_import_intent(
                        imp,
                        file_info.content,
                        path,
                        type_use_names,
                        context=context,
                    )

                    # Skip if intent indicates the import is intentionally used
                    if intent != "usage":
                        continue

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
                        data={
                            "import_info": {
                                "module": imp.module,
                                "name": imp.name,
                                "alias": imp.alias,
                                "intent": intent,
                                "intent_reason": reason,
                                "single_alias": _is_single_alias_import_statement(
                                    file_info.content, imp.line
                                ),
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
                        auto_fix_safe=_is_single_alias_import_statement(
                            source_content, imp.line
                        ),
                        import_name=imp.name,
                        import_module=imp.module,
                        data={},
                    )
                )
            else:
                seen[key] = imp

        return findings

    def _find_redundant_aliases(
        self, imports: list[ImportInfo], file_path: Path
    ) -> list[Finding]:
        """Find redundant aliases where alias matches the bound name."""
        findings = []
        for imp in imports:
            if imp.alias is None:
                continue
            bound_name = imp.name or imp.module.split(".")[0]
            if imp.alias == bound_name:
                findings.append(
                    Finding.create(
                        type="redundant_alias",
                        severity=Severity.INFO,
                        file=file_path,
                        message=f"Redundant alias: '{bound_name} as {imp.alias}' in '{imp.import_statement}'",
                        location=Location(line=imp.line),
                        fixable=True,
                        auto_fix_safe=True,
                        import_name=imp.name,
                        import_module=imp.module,
                        data={
                            "import_info": {
                                "module": imp.module,
                                "name": imp.name,
                                "alias": imp.alias,
                            }
                        },
                    )
                )
        return findings

    def _find_unused_in_file(
        self,
        file_info: FileInfo,
        imports: list[ImportInfo],
        context: AnalysisContext | None = None,
    ) -> list[Finding]:
        """Find imports not used in the file."""
        findings = []

        if not file_info.ast_tree:
            return findings

        # Collect ALL name references including type annotations
        used_names = set()
        type_use_names = _collect_type_use_names(file_info.ast_tree)
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
        # __all__ collection: iterates list/tuple elements in __all__ = [...] assignments.
        # Names appearing in __all__ are excluded from unused-import detection.
        # This catches explicit package re-export patterns but misses dynamic __all__.
        dunder_all_names = set()
        for node in ast.walk(file_info.ast_tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "__all__"
                        and isinstance(node.value, (ast.List, ast.Tuple))
                    ):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str
                            ):
                                dunder_all_names.add(elt.value)

        for imp in imports:
            name_to_check = imp.alias or imp.name or imp.module.split(".")[0]

            # Skip __future__ imports - they are compiler directives, not runtime names
            if imp.module == "__future__":
                continue

            # Check if used
            is_used = name_to_check in used_names or name_to_check in dunder_all_names

            if not is_used:
                # Cognitive detection
                intent, intent_reason, auto_fix_safe = _detect_import_intent(
                    imp,
                    file_info.content,
                    file_info.path,
                    type_use_names,
                    dunder_all_names,
                    context,
                )

                # Project-graph verification for re-export intent
                reexport_importers: list[str] = []
                if (
                    intent == "re_export"
                    and context is not None
                    and context.project_root is not None
                ):
                    module_name = resolve_module_name(
                        file_info.path, context.project_root
                    )
                    graph_importers = context.graph.get_importers(module_name)
                    if not graph_importers:
                        intent = "usage"
                        intent_reason = "Re-export file but no cross-file importers found in project graph"
                        auto_fix_safe = False
                    else:
                        reexport_importers = graph_importers

                if intent in (
                    "registration",
                    "re_export",
                    "typing",
                    "side_effect",
                    "try_block",
                ):
                    finding_type = "import_intent"
                    message = (
                        f"Import '{imp.import_statement}' is not directly used, "
                        f"but carries intent: {intent_reason}"
                    )
                    fixable = False
                else:
                    finding_type = "unused_import_file"
                    message = (
                        f"Import '{imp.import_statement}' is not used in this file"
                    )
                    fixable = True

                evidence_entries = [
                    _import_binding_evidence(
                        imp, file_info.path, name_to_check, intent
                    ).to_dict()
                ]
                if intent == "re_export":
                    evidence_entries.append(
                        Evidence(
                            kind="ReExport",
                            file=file_info.path,
                            location=Location(line=imp.line),
                            source="ImportAnalyzer",
                            subject=f"{imp.module}.{imp.name}",
                            polarity="context",
                            strength=0.60,
                        ).to_dict()
                    )
                    evidence_entries.append(
                        Evidence(
                            kind="AllExport",
                            file=file_info.path,
                            location=Location(line=imp.line),
                            source="ImportAnalyzer",
                            subject=name_to_check,
                            claim=(
                                f"{name_to_check} appears in __all__ de facto package export"
                                if name_to_check in dunder_all_names
                                else f"{name_to_check} not in __all__"
                            ),
                            polarity=(
                                "context"
                                if name_to_check in dunder_all_names
                                else "against"
                            ),
                            strength=(
                                0.30 if name_to_check in dunder_all_names else 0.40
                            ),
                        ).to_dict()
                    )
                    if reexport_importers:
                        evidence_entries.append(
                            Evidence(
                                kind="ProjectGraphImporters",
                                file=file_info.path,
                                location=Location(line=imp.line),
                                source="ImportAnalyzer",
                                subject=name_to_check,
                                claim=(
                                    f"Confirmed re-export: imported by "
                                    f"{len(reexport_importers)} module(s) "
                                    f"({', '.join(sorted(reexport_importers)[:5])}"
                                    f"{'...' if len(reexport_importers) > 5 else ''})"
                                ),
                                polarity="context",
                                strength=0.25,
                            ).to_dict()
                        )

                findings.append(
                    Finding.create(
                        type=finding_type,
                        severity=Severity.INFO,
                        file=file_info.path,
                        message=message,
                        location=Location(line=imp.line),
                        fixable=fixable,
                        auto_fix_safe=auto_fix_safe,
                        import_name=imp.name,
                        import_module=imp.module,
                        data={
                            "evidence": evidence_entries,
                            "import_info": {
                                "module": imp.module,
                                "name": imp.name,
                                "intent": intent,
                                "intent_reason": intent_reason,
                                "single_alias": _is_single_alias_import_statement(
                                    file_info.content, imp.line
                                ),
                            },
                        },
                    )
                )

        return findings

    def _is_local_import(self, imp: ImportInfo, context: AnalysisContext) -> bool:
        """Check if import is from the same project."""
        module = imp.module.split(".")[0] if imp.module else ""
        return module in context.graph.module_to_file

    def _check_project_usage(self, imp: ImportInfo, context: AnalysisContext) -> bool:
        """Check if imported symbol is used elsewhere."""
        name = imp.alias or imp.name
        if not name:
            return False
        module = imp.module
        return len(context.graph.get_importers(module)) > 1

    @staticmethod
    def _is_name_used_in_file(ast_tree, name: str) -> bool:
        """Check if a name is referenced in the AST (excluding import nodes)."""
        for node in ast.walk(ast_tree):
            if isinstance(node, ast.Name) and node.id == name:
                return True
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == name
            ):
                return True
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == name:
                        return True
        return False
