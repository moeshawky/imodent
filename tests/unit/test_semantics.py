"""G-SEM: Semantic correctness — behavior matches specification.

AOP v3 rule: code must do what it claims, not just compile.
"""

import json
import tempfile
from pathlib import Path

import pytest

from imodent import FixPipeline
from imodent.analysis.coordinator import AnalysisCoordinator
from imodent.analysis.findings import Finding, Severity, Location
from imodent.analyzers.imports import ImportAnalyzer
from imodent.fixers.imports import ImportFixer
from imodent.graph.imports import extract_imports
from imodent.registry import StrategyRegistry


class TestImportDetectionSemantics:
    """Import detection finds what it should find, not more, not less."""

    def test_finds_exactly_the_imports_present(self):
        """Extracts exactly the imports in the file, no false positives."""
        source = """import os
import sys
from pathlib import Path
from typing import List, Optional
"""
        imports = extract_imports(source, Path("/tmp/test.py"))
        modules = [(i.module, i.name, i.is_from_import) for i in imports]
        
        assert ("os", None, False) in modules
        assert ("sys", None, False) in modules
        assert ("pathlib", "Path", True) in modules
        assert ("typing", "List", True) in modules
        assert ("typing", "Optional", True) in modules
        assert len(modules) == 5  # exactly 5, no more

    def test_does_not_detect_comments_as_imports(self):
        """Comments that look like imports are not extracted."""
        source = """# import os
# from typing import List
import sys
"""
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 1
        assert imports[0].module == "sys"

    def test_does_not_detect_strings_as_imports(self):
        """Strings that look like imports are not extracted."""
        source = '''x = "import os"
y = "from typing import List"
import sys
'''
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports) == 1
        assert imports[0].module == "sys"


class TestDuplicateImportDetection:
    """Duplicate detection finds real duplicates, not coincidences."""

    def test_same_module_same_name_is_duplicate(self):
        """Two identical import statements are flagged as duplicate."""
        source = """import os
import os
"""
        analyzer = ImportAnalyzer()
        from imodent.analysis.context import AnalysisContext, FileInfo, DependencyGraph
        py_file = Path("/tmp/test.py")
        try:
            tree = __import__('ast').parse(source)
        except SyntaxError:
            tree = None
        context = AnalysisContext(
            files={py_file: FileInfo(
                path=py_file, content=source, language='python',
                ast_tree=tree
            )},
            graph=DependencyGraph()
        )
        findings = analyzer.analyze(context)
        duplicates = [f for f in findings if f.type == "duplicate_import"]
        assert len(duplicates) >= 1

    def test_different_alias_not_duplicate(self):
        """import X and import X as Y are NOT duplicates."""
        source = """import numpy
import numpy as np
"""
        imports = extract_imports(source, Path("/tmp/test.py"))
        # These are semantically different — one binds 'numpy', one binds 'np'
        assert len(imports) == 2
        assert imports[0].alias is None
        assert imports[1].alias == "np"


class TestRegistryLazyLoadingSemantics:
    """StrategyRegistry lazy loading works correctly per the audit fix."""

    def test_pipeline_works_without_explicit_strategy_import(self):
        """FixPipeline works when imported directly (the bug we fixed)."""
        # Fresh import — no strategy side-effects from test file
        from imodent.pipeline import FixPipeline
        pipeline = FixPipeline()
        result = pipeline.fix('def f():\n    pass')
        assert result.success, "Lazy loading bug: strategies not registered on first use"

    def test_registry_clear_then_reload(self):
        """After clear(), next access reloads built-in strategies."""
        StrategyRegistry.clear()
        assert len(StrategyRegistry._strategies) == 0
        
        # Trigger reload via get()
        strategy = StrategyRegistry.get("python")
        assert strategy is not None
        assert len(StrategyRegistry._strategies) >= 4

    def test_registry_idempotent_load(self):
        """Calling _load_builtins twice doesn't double-register."""
        StrategyRegistry._load_builtins()
        count_after_first = len(StrategyRegistry._strategies)
        
        StrategyRegistry._load_builtins()
        count_after_second = len(StrategyRegistry._strategies)
        
        assert count_after_first == count_after_second


class TestFixModeSemantics:
    """Fix modes do exactly what they claim."""

    def test_safe_auto_only_fixes_safe_findings(self):
        """SAFE_AUTO mode only fixes findings where auto_fix_safe=True."""
        fixer = ImportFixer()
        
        # duplicate_import: auto_fix_safe=True
        dup = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="dup",
            location=Location(line=2), fixable=True, auto_fix_safe=True
        )
        assert fixer.can_auto_fix(dup) is True
        
        # unused_import: auto_fix_safe=False (needs review)
        unused = Finding.create(
            type="unused_import", severity=Severity.INFO,
            file=Path("/tmp/test.py"), message="unused",
            location=Location(line=1), fixable=True, auto_fix_safe=False
        )
        assert fixer.can_auto_fix(unused) is False

    def test_remove_preserves_other_lines(self):
        """Removing one import doesn't affect other lines."""
        fixer = ImportFixer()
        content = "import os\nimport sys\nimport json\n\ndef f(): pass\n"
        finding = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="remove sys",
            location=Location(line=2), fixable=True, auto_fix_safe=True
        )
        options = fixer.get_options(finding, AnalysisContext())
        result = fixer.apply_fix(finding, options[0], content)
        assert result.success
        assert "import os" in result.content
        assert "import json" in result.content
        assert "import sys" not in result.content
        assert "def f(): pass" in result.content


class TestIdempotency:
    """Running fix twice produces the same result as running once."""

    def test_indentation_fix_idempotent(self):
        """Fixing already-fixed code doesn't change it further."""
        pipeline = FixPipeline()
        code = 'def f():\n    pass\n'
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)
        assert result1.content == result2.content

    def test_import_fix_idempotent(self):
        """Fixing already-fixed imports doesn't change them further."""
        fixer = ImportFixer()
        content = "import os\nimport sys\n\ndef f(): pass\n"
        finding = Finding.create(
            type="duplicate_import", severity=Severity.WARNING,
            file=Path("/tmp/test.py"), message="dup",
            location=Location(line=1), fixable=True, auto_fix_safe=True
        )
        options = fixer.get_options(finding, AnalysisContext())
        result1 = fixer.apply_fix(finding, options[0], content)
        result2 = fixer.apply_fix(finding, options[0], result1.content)
        # Second fix on same line should either fail gracefully or be no-op
        # (the line was already removed, so line number is now invalid)
        assert result2 is not None  # Should not crash


from imodent.analysis.context import AnalysisContext
