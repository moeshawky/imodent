"""Tests for FixPipeline — detect, fix, and validate orchestration."""

import json

from imodent.pipeline import FixPipeline
from imodent.strategies.json import JSONStrategy
from imodent.strategies.python import PythonStrategy


def test_pipeline_detect_python():
    """FixPipeline.detect() returns PythonStrategy for Python content."""
    pipeline = FixPipeline()
    result = pipeline.detect("def f(): pass\n")
    assert result is not None
    assert isinstance(result, PythonStrategy)


def test_pipeline_detect_json():
    """FixPipeline.detect() returns JSONStrategy for JSON content."""
    pipeline = FixPipeline()
    result = pipeline.detect('{"a": 1}')
    assert result is not None
    # The strategy should have the json name
    assert result.name == "json"


def test_pipeline_detect_unknown():
    """FixPipeline.detect() returns None for unidentifiable content."""
    pipeline = FixPipeline()
    result = pipeline.detect("this is not any known format")
    assert result is None


def test_pipeline_fix_with_strategy(sample_json_content):
    """FixPipeline.fix() with explicit strategy returns FixResult."""
    pipeline = FixPipeline()
    result = pipeline.fix(sample_json_content, strategy=JSONStrategy())
    assert result.success is True
    # Verify output is valid JSON
    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)


def test_pipeline_fix_auto_detect(sample_py_content):
    """FixPipeline.fix() auto-detects and fixes without explicit strategy."""
    pipeline = FixPipeline()
    result = pipeline.fix(sample_py_content, strategy=None)
    assert result.success is True


def test_pipeline_fix_none_strategy():
    """FixPipeline.fix() with strategy=None on unknown content returns FixResult(success=False)."""
    pipeline = FixPipeline()
    result = pipeline.fix("unidentifiable content 12345", strategy=None)
    assert result.success is False
    assert any("Unable to detect" in e for e in result.errors)


def test_pipeline_indent_passed_to_strategy():
    """FixPipeline with indent_size=2 produces 2-space indented JSON."""
    pipeline = FixPipeline(indent_size=2)
    result = pipeline.fix('{"a": 1, "b": 2}', strategy=JSONStrategy())
    assert result.success is True
    # With indent 2, the output should contain "  " (2-space) indentation
    assert "  " in result.content
    # 4-space indent should NOT be present
    if "\n" in result.content.strip():
        indent_lines = [
            line for line in result.content.splitlines() if line.startswith("    ")
        ]
        assert len(indent_lines) == 0, (
            "JSON should not have 4-space indent when indent_size=2"
        )


def test_pipeline_validate():
    """FixPipeline.validate() returns FixResult with validation status."""
    pipeline = FixPipeline()
    result = pipeline.validate("x = 1\n")
    assert result.success is True
    assert result.original_valid is True
    assert result.fixed_valid is True
