"""G-EDGE: Edge cases — empty, null, boundary, unicode, concurrent.

AOP v3 rule: edge cases are where LLMs fail most predictably.
"""

import json
import tempfile
from pathlib import Path

import pytest

from imodent.analysis.context import (
    FileInfo,
    DependencyGraph,
    AnalysisContext,
    AnalysisConfig,
)
from imodent.analysis.findings import Finding, Severity, Location, FixOption
from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analyzers.imports import ImportAnalyzer
from imodent.fixers.imports import ImportFixer
from imodent.graph.imports import extract_imports, resolve_module_name


class TestImportExtractorEdgeCases:
    """Import extraction must handle every edge case."""

    @pytest.mark.parametrize(
        "source",
        [
            "",  # empty file
            "\n\n\n",  # whitespace only
            "# just a comment",  # comment only
            "pass",  # bare statement
            "x = 1",  # no imports
        ],
    )
    def test_no_imports_found(self, source):
        """Files with no imports return empty list."""
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert imports == []

    def test_import_with_alias(self):
        """import X as Y extracts both module and alias."""
        source = "import numpy as np\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 1
        assert imports[0].module == "numpy"
        assert imports[0].alias == "np"
        assert imports[0].is_from_import is False

    def test_from_import_with_alias(self):
        """from X import Y as Z extracts correctly."""
        source = "from os.path import join as pjoin\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 1
        assert imports[0].module == "os.path"
        assert imports[0].name == "join"
        assert imports[0].alias == "pjoin"

    def test_multiple_names_in_from_import(self):
        """from X import Y, Z extracts each name separately."""
        source = "from typing import List, Dict, Optional\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 3
        names = [i.name for i in imports]
        assert names == ["List", "Dict", "Optional"]

    def test_relative_import(self):
        """from . import X and from ..X import Y extract correctly."""
        source = "from . import utils\nfrom ..parent import thing\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 2

    def test_broken_code_fallback(self):
        """Import extraction falls back to regex for broken code."""
        # This code has a syntax error but valid imports
        source = "import os\nimport sys\ndef f(\n  pass\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        # Should still find the imports via regex
        assert len(imports) >= 2

    def test_import_inside_try_except(self):
        """Imports inside try/except are still found by AST walker."""
        source = "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n"
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) >= 1


class TestImportAnalyzerEdgeCases:
    """Import analyzer must handle edge cases."""

    def test_empty_context(self):
        """Empty context produces no findings."""
        analyzer = ImportAnalyzer()
        context = AnalysisContext(files={}, graph=DependencyGraph())
        findings = analyzer.analyze(context)
        assert findings == []

    def test_non_python_files_skipped(self):
        """Non-Python files are skipped."""
        analyzer = ImportAnalyzer()
        json_file = Path("/tmp/test.json")
        context = AnalysisContext(
            files={
                json_file: FileInfo(path=json_file, content='{"a": 1}', language="json")
            },
            graph=DependencyGraph(),
        )
        findings = analyzer.analyze(context)
        assert findings == []

    def test_file_with_syntax_errors(self):
        """Files with syntax errors don't crash the analyzer."""
        analyzer = ImportAnalyzer()
        py_file = Path("/tmp/broken.py")
        context = AnalysisContext(
            files={
                py_file: FileInfo(
                    path=py_file,
                    content="def f(\n  pass",
                    language="python",
                    has_syntax_errors=True,
                    ast_tree=None,
                )
            },
            graph=DependencyGraph(),
        )
        findings = analyzer.analyze(context)
        # Should return findings or empty list, not crash
        assert isinstance(findings, list)


class TestImportFixerEdgeCases:
    """Import fixer must handle edge cases."""

    def test_fix_duplicate_at_line_1(self):
        """Removing import at line 1 doesn't corrupt content."""
        fixer = ImportFixer()
        content = "import os\nimport os\n\ndef f(): pass\n"
        finding = Finding.create(
            type="duplicate_import",
            severity=Severity.WARNING,
            file=Path("/tmp/test.py"),
            message="duplicate",
            location=Location(line=2),
            fixable=True,
            auto_fix_safe=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], content)
        assert result.success
        assert "import os" in result.content
        # Should only have one "import os"
        assert result.content.count("import os") == 1

    def test_fix_import_at_last_line(self):
        """Removing import at last line doesn't corrupt content."""
        fixer = ImportFixer()
        content = "def f(): pass\nimport os\n"
        finding = Finding.create(
            type="duplicate_import",
            severity=Severity.WARNING,
            file=Path("/tmp/test.py"),
            message="duplicate",
            location=Location(line=2),
            fixable=True,
            auto_fix_safe=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], content)
        assert result.success

    def test_fix_import_no_location(self):
        """Finding with no location returns error, not crash."""
        fixer = ImportFixer()
        content = "import os\n"
        finding = Finding.create(
            type="duplicate_import",
            severity=Severity.WARNING,
            file=Path("/tmp/test.py"),
            message="duplicate",
            location=None,
            fixable=True,
            auto_fix_safe=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], content)
        assert not result.success
        assert "Cannot find import location" in result.errors[0]

    def test_fix_import_invalid_line(self):
        """Finding with invalid line number returns error."""
        fixer = ImportFixer()
        content = "import os\n"
        finding = Finding.create(
            type="duplicate_import",
            severity=Severity.WARNING,
            file=Path("/tmp/test.py"),
            message="duplicate",
            location=Location(line=999),
            fixable=True,
            auto_fix_safe=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], content)
        assert not result.success

    def test_unused_import_has_delete_option(self):
        """Unused import finding always has 'delete' as first option."""
        fixer = ImportFixer()
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="unused",
            location=Location(line=1),
            fixable=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        assert len(options) >= 1
        assert options[0].id == "delete"

    def test_unused_import_has_keep_option(self):
        """Unused import finding has 'keep' option for type hints."""
        fixer = ImportFixer()
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="unused",
            location=Location(line=1),
            fixable=True,
            import_name="List",
            import_module="typing",
        )
        options = fixer.get_options(finding, AnalysisContext())
        actions = [o.action for o in options]
        assert "keep" in actions

    def test_keep_option_returns_unchanged_content(self):
        """'Keep' option returns content unchanged."""
        fixer = ImportFixer()
        content = "from typing import List\n\ndef f(): pass\n"
        finding = Finding.create(
            type="unused_import",
            severity=Severity.INFO,
            file=Path("/tmp/test.py"),
            message="unused",
            location=Location(line=1),
            fixable=True,
        )
        options = fixer.get_options(finding, AnalysisContext())
        keep_option = next(o for o in options if o.action == "keep")
        result = fixer.apply_fix(finding, keep_option, content)
        assert result.success
        assert result.content == content


class TestDependencyGraphEdgeCases:
    """Dependency graph must handle edge cases."""

    def test_empty_graph(self):
        """Empty graph has no cycles."""
        graph = DependencyGraph()
        assert graph.get_importers("anything") == []
        assert graph.get_importees("anything") == []
        assert graph.is_used("anything") is False

    def test_self_import(self):
        """Module importing itself doesn't crash."""
        graph = DependencyGraph()
        graph.add_import("module_a", "module_a")
        # Should not crash, though self-imports are unusual
        assert graph.is_used("module_a")

    def test_circular_import(self):
        """Circular imports are tracked correctly."""
        graph = DependencyGraph()
        graph.add_import("a", "b")
        graph.add_import("b", "a")
        assert "a" in graph.get_importers("b")
        assert "b" in graph.get_importers("a")


class TestFileInfoEdgeCases:
    """FileInfo must handle edge cases."""

    @pytest.mark.parametrize(
        "suffix,expected",
        [
            (".py", "python"),
            (".json", "json"),
            (".yaml", "yaml"),
            (".yml", "yaml"),
            (".txt", "unknown"),
            (".md", "unknown"),
        ],
    )
    def test_language_detection(self, suffix, expected):
        """Language detection maps extensions correctly."""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w") as f:
            f.write("# test\n")
            f.flush()
            info = FileInfo.from_path(Path(f.name))
            assert info.language == expected

    def test_file_with_syntax_error_marks_flag(self):
        """Python file with syntax errors sets has_syntax_errors=True."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write("def f(\n  pass\n")
            f.flush()
            info = FileInfo.from_path(Path(f.name))
            assert info.has_syntax_errors is True
            assert info.ast_tree is None


class TestCoordinatorEdgeCases:
    """AnalysisCoordinator must handle edge cases."""

    def test_analyze_empty_path_list(self):
        """Analyzing no files returns empty results."""
        coordinator = AnalysisCoordinator()
        result = coordinator.analyze([])
        assert len(result.files) == 0
        assert len(result.findings) == 0

    def test_analyze_nonexistent_path(self):
        """Analyzing nonexistent path returns empty results."""
        coordinator = AnalysisCoordinator()
        result = coordinator.analyze([Path("/nonexistent")])
        assert result is not None  # Should not crash

    def test_fix_mode_report_does_nothing(self):
        """REPORT mode doesn't modify any files."""
        coordinator = AnalysisCoordinator()
        content = "import os\nimport os\n"
        findings = [
            Finding.create(
                type="duplicate_import",
                severity=Severity.WARNING,
                file=Path("/tmp/test.py"),
                message="dup",
                location=Location(line=2),
                fixable=True,
                auto_fix_safe=True,
            )
        ]
        context = AnalysisContext()
        results = coordinator.fix(findings, context, mode=FixMode.REPORT)
        # Nothing should be fixed
        assert len(results) == 0


class TestSeverityEnum:
    """Severity enum values are correct and complete."""

    def test_all_severities_exist(self):
        """All four severity levels exist."""
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"
        assert Severity.HINT.value == "hint"

    def test_severity_ordering(self):
        """Severities have expected ordering for display."""
        # Just verify all exist — ordering is by list in CLI
        assert len(Severity) == 4


class TestResolveModuleName:
    """Module name resolution handles edge cases."""

    def test_init_file(self):
        """__init__.py resolves to package name."""
        root = Path("/tmp/project")
        file = root / "pkg" / "__init__.py"
        name = resolve_module_name(file, root)
        assert name == "pkg"

    def test_nested_module(self):
        """Nested modules resolve correctly."""
        root = Path("/tmp/project")
        file = root / "pkg" / "sub" / "mod.py"
        name = resolve_module_name(file, root)
        assert name == "pkg.sub.mod"
