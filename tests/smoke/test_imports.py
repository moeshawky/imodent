"""G-HALL: Import validation — every import must resolve.

AOP v3 rule: if hallucinated APIs exist, nothing else matters.
These tests run FIRST. If any fail, skip all other tests.
"""

import importlib
import ast
from pathlib import Path


def _extract_imports(source_dir: Path) -> list[str]:
    """Extract all top-level imports from Python files in source_dir."""
    imports = set()
    for py_file in source_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
    return sorted(imports)


class TestNoHallucinatedImports:
    """All imports in imodent source must resolve at runtime."""

    def test_core_imports_resolve(self):
        """Every import in imodent/ resolves without ImportError."""
        source = Path("/srv/imodent/imodent")
        imports = _extract_imports(source)
        # Known third-party deps that may not be installed
        allowed_missing = {"black", "ruamel", "json_repair", "ruff", "pyright"}
        # Internal sub-modules that show as first-segment (not real packages)
        # These come from relative imports like `from .base import X`
        all_parts = set()
        for py_file in source.rglob("*.py"):
            parts = py_file.relative_to(source).parts
            for p in parts:
                all_parts.add(p.replace(".py", ""))
        
        failures = []
        for pkg in imports:
            if pkg in allowed_missing or pkg.startswith("_"):
                continue
            if pkg in all_parts:
                continue
            try:
                importlib.import_module(pkg)
            except ImportError:
                failures.append(pkg)
        
        assert not failures, f"Hallucinated or missing packages: {failures}"

    def test_analysis_module_imports(self):
        """imodent.analysis module imports cleanly."""
        from imodent.analysis import (
            AnalysisContext, AnalysisConfig, FileInfo, DependencyGraph,
            Finding, Severity, Location, FixOption, Advice, Change,
            AnalysisCoordinator, AnalysisResult, FixMode,
        )
        # If we got here, no ImportError
        assert True

    def test_graph_module_imports(self):
        """imodent.graph module imports cleanly."""
        from imodent.graph.imports import extract_imports, ImportInfo, resolve_module_name
        from imodent.graph.dependency import build_dependency_graph
        assert True

    def test_analyzer_module_imports(self):
        """imodent.analyzers module imports cleanly."""
        from imodent.analyzers.base import Analyzer, AnalyzerCapability
        from imodent.analyzers.imports import ImportAnalyzer
        assert True

    def test_fixer_module_imports(self):
        """imodent.fixers module imports cleanly."""
        from imodent.fixers.base import Fixer
        from imodent.fixers.imports import ImportFixer
        assert True

    def test_advisor_module_imports(self):
        """imodent.advisors module imports cleanly."""
        from imodent.advisors.base import Advisor
        from imodent.advisors.architecture import ArchitectureAdvisor
        assert True

    def test_public_api_surface(self):
        """All names in __all__ actually exist in the module."""
        import imodent
        for name in imodent.__all__:
            assert hasattr(imodent, name), f"__all__ claims '{name}' but it doesn't exist"

    def test_fixresult_fields_exist(self):
        """FixResult has the fields the codebase assumes."""
        from imodent.interfaces import FixResult
        result = FixResult(
            success=True, content="x", errors=[], warnings=[],
            original_valid=True, fixed_valid=True
        )
        assert result.success is True
        assert result.content == "x"


class TestNoHallucinatedMethods:
    """Verify methods called on objects actually exist on their type."""

    def test_strategy_registry_methods(self):
        """StrategyRegistry has all methods used by the codebase."""
        from imodent.registry import StrategyRegistry
        required = ['register', 'get', 'get_by_extension', 'detect', 'all', 'clear', '_load_builtins']
        for method in required:
            assert hasattr(StrategyRegistry, method), f"StrategyRegistry missing method: {method}"

    def test_analyzer_interface_methods(self):
        """Analyzer ABC requires all methods used by the codebase."""
        from imodent.analyzers.base import Analyzer
        required = ['name', 'capabilities', 'languages', 'requires_ast', 'analyze', 'can_analyze']
        for method in required:
            assert hasattr(Analyzer, method), f"Analyzer missing: {method}"

    def test_fixer_interface_methods(self):
        """Fixer ABC requires all methods used by the codebase."""
        from imodent.fixers.base import Fixer
        required = ['name', 'handles', 'can_auto_fix', 'get_options', 'apply_fix', 'can_handle']
        for method in required:
            assert hasattr(Fixer, method), f"Fixer missing: {method}"

    def test_coordinator_methods(self):
        """AnalysisCoordinator has all methods used by the codebase."""
        from imodent.analysis.coordinator import AnalysisCoordinator
        required = ['analyze', 'fix']
        for method in required:
            assert hasattr(AnalysisCoordinator, method), f"Coordinator missing: {method}"

    def test_finding_factory_method(self):
        """Finding.create() produces valid Finding objects."""
        from imodent.analysis.findings import Finding, Severity, Location
        from pathlib import Path
        f = Finding.create(
            type="test", severity=Severity.INFO,
            file=Path("/tmp/x.py"), message="test"
        )
        assert f.id is not None
        assert f.severity == Severity.INFO
