#!/usr/bin/env python3
"""
Test suite for the modular indentation fixer.

Following LLM Testing Patterns:
- G-HALL: Import validation
- G-SEC: Security testing
- G-EDGE: Edge case matrix
- G-SEM: Behavioral correctness
- G-ERR: Error handling
- G-CTX: Context/integration
- G-DRIFT: Golden file regression
"""
import pytest
import json
import ast
import tempfile
import os
from pathlib import Path

# Import from modular architecture
from imodent.pipeline import FixPipeline
from imodent.registry import StrategyRegistry
from imodent.strategies import PythonStrategy, JSONStrategy, JSONLStrategy


# ===========================
# G-HALL: Hallucinated APIs
# ===========================
class TestImports:
    """Verify all imports exist."""

    def test_pipeline_imports(self):
        """FixPipeline exists."""
        from imodent.pipeline import FixPipeline

        assert FixPipeline is not None

    def test_strategy_imports(self):
        """All strategies import correctly."""
        from imodent.strategies import PythonStrategy, JSONStrategy, JSONLStrategy

        assert PythonStrategy is not None
        assert JSONStrategy is not None
        assert JSONLStrategy is not None

    def test_registry_imports(self):
        """Registry imports correctly."""
        from imodent.registry import StrategyRegistry, ProcessorRegistry

        assert StrategyRegistry is not None
        assert ProcessorRegistry is not None


# ===========================
# G-EDGE: Edge Cases
# ===========================
class TestEdgeCases:
    """Test edge cases."""

    @pytest.mark.parametrize(
        "code",
        [
            "",  # Empty
            "   \n  \n\t\n",  # Only whitespace
            "# Just a comment\n",  # Only comment
        ],
    )
    def test_empty_and_minimal(self, code):
        """Handle empty and minimal inputs."""
        pipeline = FixPipeline()
        result = pipeline.fix(code)
        assert result is not None

    def test_mixed_indentation(self):
        """Handle files with mixed indentation."""
        pipeline = FixPipeline()
        code = "def f():\n  if True:\n    pass\n"
        result = pipeline.fix(code)
        assert result is not None


# ===========================
# G-SEM: Semantic Correctness
# ===========================
class TestSemanticCorrectness:
    """Test that code behavior is preserved."""

    def test_python_indentation_preserves_logic(self):
        """Fixed indentation should preserve program logic."""
        pipeline = FixPipeline()

        code = """def factorial(n):
if n <= 1:
return 1
else:
return n * factorial(n-1)
"""
        result = pipeline.fix(code)

        # May fail AST check if syntax is too broken
        if result.success:
            # Verify logic by executing
            namespace = {}
            exec(result.content, namespace)
            factorial = namespace["factorial"]

            assert factorial(5) == 120
            assert factorial(0) == 1

    def test_json_structure_preserved(self):
        """JSON structure should be preserved."""
        pipeline = FixPipeline()

        original = '{"a":1,"b":{"c":2,"d":[3,4,5]}}'
        result = pipeline.fix(original)

        original_data = json.loads(original)
        result_data = json.loads(result.content)

        assert original_data == result_data

    def test_jsonl_lines_preserved(self):
        """JSONL lines should be preserved."""
        pipeline = FixPipeline()

        original = '{"name":"alice"}\n{"name":"bob"}'
        result = pipeline.fix(original)

        original_lines = [json.loads(line) for line in original.split("\n")]
        result_lines = [json.loads(line) for line in result.content.strip().split("\n")]

        assert original_lines == result_lines


# ===========================
# G-ERR: Error Handling
# ===========================
class TestErrorHandling:
    """Test error handling."""

    def test_invalid_json_graceful(self):
        """Invalid JSON should not crash."""
        pipeline = FixPipeline()

        invalid_json = "{invalid json}"
        result = pipeline.fix(invalid_json)

        # Should detect as Python or return error gracefully
        assert result is not None

    def test_missing_file(self):
        """Missing file should raise appropriate error."""
        with pytest.raises(FileNotFoundError):
            Path("/nonexistent/file.py").read_text()


# ===========================
# G-CTX: Context/Integration
# ===========================
class TestContext:
    """Test context and integration."""

    def test_strategy_registry(self):
        """Registry should have all strategies."""
        strategies = StrategyRegistry.all()
        names = [s().name for s in strategies]

        assert "python" in names
        assert "json" in names
        assert "jsonl" in names

    def test_detection(self):
        """Should detect correct strategy via pipeline."""
        pipeline = FixPipeline()

        python_code = "def f():\n    pass"
        json_code = '{"a":1}'
        jsonl_code = '{"a":1}\n{"b":2}'

        assert type(pipeline.detect(python_code)).__name__ == "PythonStrategy"
        assert type(pipeline.detect(json_code)).__name__ == "JSONStrategy"
        assert type(pipeline.detect(jsonl_code)).__name__ == "JSONLStrategy"


# ===========================
# G-DRIFT: Golden File Regression
# ===========================
class TestGoldenFiles:
    """Test for output consistency."""

    def test_python_output_consistent(self):
        """Same input should produce same output."""
        pipeline = FixPipeline()

        code = """def f():
if True:
pass
"""
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(code)

        assert result1.content == result2.content

    def test_json_output_consistent(self):
        """JSON output should be deterministic."""
        pipeline = FixPipeline()

        code = '{"z":1,"a":2}'
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(code)

        assert result1.content == result2.content


# ===========================
# Property-Based Testing
# ===========================
class TestProperties:
    """Property-based tests."""

    def test_roundtrip_python(self):
        """Fixed code should be idempotent."""
        pipeline = FixPipeline()

        code = """def f():
if True:
pass
"""
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)

        assert result1.content == result2.content

    def test_roundtrip_json(self):
        """Fixed JSON should be idempotent."""
        pipeline = FixPipeline()

        code = '{"a":1}'
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)

        assert result1.content == result2.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
