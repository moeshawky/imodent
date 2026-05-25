"""Tests for import intent detection in ImportAnalyzer."""
import pytest
from pathlib import Path
from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analyzers.imports import (
    ImportAnalyzer,
    _detect_import_intent,
    _is_in_try_block,
    _is_in_function_or_class,
    _extract_annotation_names,
    _has_type_annotations,
    _RE_EXPORT_FILES,
    _TYPING_MODULES,
    _REGISTRATION_MODULES,
)
from imodent.analyzers.residue import ResidueAnalyzer
from imodent.graph.dependency import build_dependency_graph
from imodent.graph.imports import ImportInfo


class TestDetectImportIntent:
    """Test the _detect_import_intent function."""

    def test_registration_module_import(self):
        """Imports from registration modules should be detected as side_effect."""
        imp = ImportInfo(
            module="strategies.python",
            name="PythonStrategy",
            alias=None,
            line=5,
            is_from_import=True,
            file=Path("imodent/cli.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, "from strategies.python import PythonStrategy", Path("imodent/cli.py"))
        assert intent == "side_effect"
        assert is_safe is False

    def test_re_export_file(self):
        """Imports in __init__.py should be detected as re_export."""
        imp = ImportInfo(
            module="typing",
            name="List",
            alias=None,
            line=1,
            is_from_import=True,
            file=Path("__init__.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, "from typing import List", Path("__init__.py"))
        assert intent == "re_export"
        assert is_safe is False

    def test_interfaces_file_re_export(self):
        """Imports in interfaces.py should be detected as re_export."""
        imp = ImportInfo(
            module="typing",
            name="Optional",
            alias=None,
            line=1,
            is_from_import=True,
            file=Path("interfaces.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, "from typing import Optional", Path("interfaces.py"))
        assert intent == "re_export"

    def test_typing_module_import(self):
        """Imports from typing modules should be detected as typing."""
        imp = ImportInfo(
            module="typing",
            name="List",
            alias=None,
            line=5,
            is_from_import=True,
            file=Path("some_module.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, "from typing import List", Path("some_module.py"))
        assert intent == "typing"
        assert is_safe is False

    def test_try_block_import(self):
        """Imports in try blocks should be detected as side_effect."""
        content = """try:
    import optional_dep
except ImportError:
    pass
"""
        imp = ImportInfo(
            module="optional_dep",
            name=None,
            alias=None,
            line=2,
            is_from_import=False,
            file=Path("some_module.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, content, Path("some_module.py"))
        assert intent == "side_effect"

    def test_registration_pattern_in_file(self):
        """Files with @register decorators should flag imports as registration."""
        content = """from some_module import SomeClass

@StrategyRegistry.register
class MyStrategy:
    pass
"""
        imp = ImportInfo(
            module="some_module",
            name="SomeClass",
            alias=None,
            line=1,
            is_from_import=True,
            file=Path("some_module.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, content, Path("some_module.py"))
        assert intent == "registration"
        assert is_safe is False

    def test_protection_marker_comment(self):
        """Imports with protection markers should be detected as side_effect."""
        content = """import side_effect_module  # side-effect: triggers registration
"""
        imp = ImportInfo(
            module="side_effect_module",
            name=None,
            alias=None,
            line=1,
            is_from_import=False,
            file=Path("some_module.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, content, Path("some_module.py"))
        assert intent == "side_effect"
        assert is_safe is False

    def test_normal_unused_import(self):
        """Normal imports without special context should be detected as usage."""
        content = """import unused_module

def foo():
    pass
"""
        imp = ImportInfo(
            module="unused_module",
            name=None,
            alias=None,
            line=1,
            is_from_import=False,
            file=Path("some_module.py"),
        )
        intent, reason, is_safe = _detect_import_intent(imp, content, Path("some_module.py"))
        assert intent == "usage"


class TestIsInTryBlock:
    """Test the _is_in_try_block function."""

    def test_line_inside_try(self):
        """Line inside try block should return True."""
        content = """try:
    import optional
except ImportError:
    pass
"""
        assert _is_in_try_block(content, 2) is True

    def test_line_outside_try(self):
        """Line outside try block should return False."""
        content = """import always_here

try:
    import optional
except ImportError:
    pass
"""
        assert _is_in_try_block(content, 1) is False

    def test_line_in_except(self):
        """Line in except block should return False."""
        content = """try:
    import optional
except ImportError:
    pass
"""
        assert _is_in_try_block(content, 4) is False

    def test_invalid_line_number(self):
        """Invalid line numbers should return False."""
        assert _is_in_try_block("content", 0) is False
        assert _is_in_try_block("content", 100) is False


class TestIsInFunctionOrClass:
    """Test the _is_in_function_or_class function."""

    def test_line_inside_function(self):
        """Line inside function should return True."""
        content = """def foo():
    x = 1
    return x
"""
        assert _is_in_function_or_class(content, 2) is True

    def test_line_inside_class(self):
        """Line inside class should return True."""
        content = """class Bar:
    def method(self):
        pass
"""
        assert _is_in_function_or_class(content, 3) is True

    def test_line_at_module_level(self):
        """Line at module level should return False."""
        content = """x = 1

def foo():
    pass
"""
        assert _is_in_function_or_class(content, 1) is False

    def test_invalid_line_number(self):
        """Invalid line numbers should return False."""
        assert _is_in_function_or_class("content", 0) is False
        assert _is_in_function_or_class("content", 100) is False


class TestExtractAnnotationNames:
    """Test the _extract_annotation_names function."""

    def test_simple_name(self):
        """Simple Name annotation should extract the name."""
        import ast
        node = ast.parse("x: int").body[0].annotation
        names = _extract_annotation_names(node)
        assert names == {"int"}

    def test_subscript_name(self):
        """Subscript annotation (List[T]) should extract the base name."""
        import ast
        node = ast.parse("x: List[int]").body[0].annotation
        names = _extract_annotation_names(node)
        assert "List" in names

    def test_attribute_name(self):
        """Attribute annotation (module.Type) should extract the base name."""
        import ast
        node = ast.parse("x: os.PathLike").body[0].annotation
        names = _extract_annotation_names(node)
        assert "os" in names


class TestHasTypeAnnotations:
    """Test the _has_type_annotations function."""

    def test_function_with_return_annotation(self):
        """Function with return type should have annotations."""
        import ast
        tree = ast.parse("def foo() -> int: pass")
        assert _has_type_annotations(tree) is True

    def test_function_with_arg_annotation(self):
        """Function with annotated args should have annotations."""
        import ast
        tree = ast.parse("def foo(x: int) -> None: pass")
        assert _has_type_annotations(tree) is True

    def test_annotated_assignment(self):
        """Annotated assignment should have annotations."""
        import ast
        tree = ast.parse("x: int = 1")
        assert _has_type_annotations(tree) is True

    def test_no_annotations(self):
        """Code without annotations should return False."""
        import ast
        tree = ast.parse("def foo(): pass")
        assert _has_type_annotations(tree) is False


class TestResidueSemantics:
    """Tests for recovery-first residue semantics."""

    def test_side_effect_import_is_intent_not_fixable_unused(self, tmp_path):
        """Side-effect imports become intent findings, not deletion candidates."""
        source = "import plugin  # side-effect: triggers registration\n"
        file_path = tmp_path / "module.py"
        file_path.write_text(source)
        file_info = FileInfo.from_path(file_path)
        context = AnalysisContext(
            files={file_path: file_info},
            graph=build_dependency_graph({file_path: file_info}, tmp_path),
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )

        findings = ImportAnalyzer().analyze(context)

        assert any(f.type == "import_intent" for f in findings)
        assert not any(f.type == "unused_import_file" for f in findings)
        intent = next(f for f in findings if f.type == "import_intent")
        assert intent.fixable is False
        assert intent.data["import_info"]["intent"] == "side_effect"

    def test_lint_flag_without_executor_reports_unwired_behavior(self, tmp_path):
        """A declared --lint path without an executor is LLM residue."""
        cli = tmp_path / "cli.py"
        ctx = tmp_path / "context.py"
        cli.write_text(
            "parser.add_argument(\n"
            '    "--lint",\n'
            '    action="store_true",\n'
            ")\n"
            "analyze_files(analyze_lint=args.lint)\n"
        )
        ctx.write_text("check_lint: bool = True\n")
        files = {
            cli: FileInfo.from_path(cli),
            ctx: FileInfo.from_path(ctx),
        }
        context = AnalysisContext(
            files=files,
            graph=build_dependency_graph(files, tmp_path),
            config=AnalysisConfig(check_imports=False, check_lint=True),
        )

        findings = ResidueAnalyzer().analyze(context)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.type == "declared_behavior_unwired"
        assert finding.fixable is False
        assert finding.data["cluster"] == "lint"
        assert finding.data["destructive_allowed"] is False

    def test_lint_flag_disabled_does_not_report_residue(self, tmp_path):
        """Residue analyzers honor scan selection."""
        cli = tmp_path / "cli.py"
        cli.write_text('parser.add_argument("--lint")\nanalyze_lint=args.lint\n')
        file_info = FileInfo.from_path(cli)
        context = AnalysisContext(
            files={cli: file_info},
            graph=build_dependency_graph({cli: file_info}, tmp_path),
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )

        assert ResidueAnalyzer().analyze(context) == []

    def test_grouped_duplicate_import_is_not_safe_auto_fix(self, tmp_path):
        """Whole-line deletion of grouped imports can delete live aliases."""
        source = "import os, sys\nimport sys, json\nprint(json.dumps({}))\n"
        file_path = tmp_path / "sample.py"
        file_path.write_text(source)

        from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
        from imodent.project.project_context import ProjectContext

        project_context = ProjectContext.from_root(tmp_path)
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([file_path])

        duplicates = [f for f in result.findings if f.type == "duplicate_import"]
        assert duplicates
        assert all(not f.auto_fix_safe for f in duplicates)

        fix_results = coordinator.fix(duplicates, result.context, mode=FixMode.SAFE_AUTO)
        assert fix_results == {}
        assert file_path.read_text() == source
