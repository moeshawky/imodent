"""G-DRIFT: Regression — golden files and output stability.

AOP v3 rule: same input must produce same output across runs.
"""

import json
from pathlib import Path

import pytest

from imodent import FixPipeline
from imodent.analysis.coordinator import AnalysisCoordinator
from imodent.graph.imports import extract_imports


class TestGoldenOutputStability:
    """Same input produces same output — no drift between runs."""

    def test_python_fix_deterministic(self):
        """Python fix produces identical output on repeated runs."""
        pipeline = FixPipeline()
        code = "def f():\n    if True:\n        pass\n"
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(code)
        assert result1.content == result2.content

    def test_json_fix_deterministic(self):
        """JSON fix produces identical output on repeated runs."""
        pipeline = FixPipeline()
        code = '{"z":1,"a":2}'
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(code)
        assert result1.content == result2.content

    def test_import_extraction_deterministic(self):
        """Import extraction produces identical results on repeated runs."""
        source = "import os\nfrom typing import List, Optional\n"
        imports1 = extract_imports(source, Path("/tmp/test.py"))
        imports2 = extract_imports(source, Path("/tmp/test.py"))
        assert len(imports1) == len(imports2)
        for a, b in zip(imports1, imports2):
            assert a.module == b.module
            assert a.name == b.name
            assert a.alias == b.alias

    def test_analysis_deterministic(self):
        """Multi-file analysis produces same findings on repeated runs."""
        test_file = Path("/srv/imodent/imodent/__init__.py")
        coordinator = AnalysisCoordinator()
        result1 = coordinator.analyze([test_file])
        result2 = coordinator.analyze([test_file])
        assert len(result1.findings) == len(result2.findings)

    def test_roundtrip_python_stable(self):
        """Fix → Fix produces stable output (idempotent)."""
        pipeline = FixPipeline()
        code = "def f():\n    if True:\n        pass\n"
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)
        assert result1.content == result2.content

    def test_roundtrip_json_stable(self):
        """JSON roundtrip is stable."""
        pipeline = FixPipeline()
        code = '{"a": 1, "b": {"c": 2}}'
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)
        assert result1.content == result2.content


class TestImportCountConsistency:
    """Import extraction count is consistent."""

    @pytest.mark.parametrize(
        "source,expected_count",
        [
            ("import os", 1),
            ("import os\nimport sys", 2),
            ("from typing import List", 1),
            ("from typing import List, Dict", 2),
            ("import os\nfrom pathlib import Path", 2),
            ("", 0),
            ("# import os", 0),
        ],
    )
    def test_import_count(self, source, expected_count):
        """Import count matches expected for known inputs."""
        imports = extract_imports(source, Path("/tmp/test.py"))
        assert (
            len(imports) == expected_count
        ), f"Expected {expected_count} imports, got {len(imports)}: {[i.import_statement for i in imports]}"
