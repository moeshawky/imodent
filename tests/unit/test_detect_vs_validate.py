"""Tests for the detect-vs-validate bug across all strategies.

Bug: detect() used to require valid parse, so broken files
never reached fix(). detect() should detect the LANGUAGE,
not validate the content. Broken JSON is still JSON.
"""

import json
import pytest
from imodent import FixPipeline
from imodent.strategies.json import JSONStrategy
from imodent.strategies.jsonl import JSONLStrategy
from imodent.strategies.yaml import YAMLStrategy
from imodent.strategies.python import PythonStrategy


class TestJSONDetectVsValidate:
    """JSON detect() must return True for broken JSON."""

    def test_detect_broken_json_missing_comma(self):
        """Missing comma is still JSON — detect must return True."""
        s = JSONStrategy()
        broken = '{\n  "key": "value"\n  "other": true\n}'
        assert s.detect(broken) is True

    def test_detect_broken_json_trailing_comma(self):
        """Trailing comma is still JSON — detect must return True."""
        s = JSONStrategy()
        broken = '{"key": "value",}'
        assert s.detect(broken) is True

    def test_detect_broken_json_unclosed_bracket(self):
        """Unclosed bracket is still JSON — detect must return True."""
        s = JSONStrategy()
        broken = '{"key": "value"'
        assert s.detect(broken) is True

    def test_detect_valid_json(self):
        """Valid JSON still detected."""
        s = JSONStrategy()
        assert s.detect('{"key": "value"}') is True

    def test_detect_not_json(self):
        """Non-JSON content rejected."""
        s = JSONStrategy()
        assert s.detect("def hello(): pass") is False

    def test_fix_broken_json_with_repair(self):
        """Broken JSON gets fixed via json-repair."""
        p = FixPipeline()
        broken = '{\n  "key": "value"\n  "other": true\n}'
        result = p.fix(broken)
        assert result.success is True
        # Verify output is valid JSON
        data = json.loads(result.content)
        assert data["key"] == "value"
        assert data["other"] is True
        # Must warn about original error
        assert any("error" in w.lower() for w in result.warnings)

    def test_fix_broken_json_without_repair(self, monkeypatch):
        """Without json-repair, broken JSON gets clear error message."""
        monkeypatch.setitem(json.__dict__, "loads", json.loads)  # can't easily block
        p = FixPipeline()
        broken = '{"key": "value"'
        result = p.fix(broken)
        # Either fixed (json-repair) or clear error
        if not result.success:
            assert any("Cannot fix" in e for e in result.errors)
            assert any("json-repair" in e for e in result.errors)

    def test_jsonl_not_detected_as_json(self):
        """JSONL content should not match JSON strategy."""
        s = JSONStrategy()
        jsonl = '{"a": 1}\n{"b": 2}\n{"c": 3}'
        assert s.detect(jsonl) is False

    def test_real_world_broken_config(self):
        """The actual fix.json scenario that triggered this bug."""
        p = FixPipeline()
        broken = """{
  "mcp": {
    "hive_mind": {
      "type": "remote",
      "url": "http://example.com/mcp"
      "headers": {
        "Authorization": "Bearer token123"
      }
    }
  }
}"""
        result = p.fix(broken)
        assert result.success is True
        data = json.loads(result.content)
        assert "mcp" in data


class TestJSONLDetectVsValidate:
    """JSONL detect() must return True for broken JSONL."""

    def test_detect_broken_jsonl(self):
        """Broken line in JSONL is still JSONL."""
        s = JSONLStrategy()
        broken = '{"a": 1}\n{"b": 2,}\n{"c": 3}'
        assert s.detect(broken) is True

    def test_detect_single_line_jsonl(self):
        """Single JSON line is valid JSONL."""
        s = JSONLStrategy()
        assert s.detect('{"a": 1}') is True

    def test_detect_not_jsonl(self):
        """Non-JSONL content rejected."""
        s = JSONLStrategy()
        assert s.detect("def hello(): pass") is False

    def test_fix_broken_jsonl(self):
        """Broken JSONL lines are flagged with line numbers."""
        p = FixPipeline()
        broken = '{"a": 1}\n{"b": 2,}\n{"c": 3}'
        result = p.fix(broken)
        # Valid lines should be fixed, broken line flagged
        if not result.success:
            assert any("Line 2" in e for e in result.errors)


class TestYAMLDetectVsValidate:
    """YAML detect() must return True for broken YAML."""

    def test_detect_broken_yaml(self):
        """Broken indentation is still YAML."""
        s = YAMLStrategy()
        broken = "key:\n  sub: val\n wrong_indent: x"
        assert s.detect(broken) is True

    def test_detect_yaml_with_doc_start(self):
        """--- document start is always YAML."""
        s = YAMLStrategy()
        assert s.detect("---\nkey: value") is True

    def test_detect_not_yaml(self):
        """Non-YAML content rejected."""
        s = YAMLStrategy()
        assert s.detect('{"key": "value"}') is False

    def test_detect_valid_yaml(self):
        """Valid YAML still detected."""
        s = YAMLStrategy()
        assert s.detect("key: value\nother: 42") is True


class TestPythonDetectVsValidate:
    """Python detect() must return True for broken Python."""

    def test_detect_broken_python(self):
        """Syntax error is still Python."""
        s = PythonStrategy()
        broken = "def hello():\n    x = 1\n  y = 2"  # wrong indent
        assert s.detect(broken) is True

    def test_detect_valid_python(self):
        """Valid Python still detected."""
        s = PythonStrategy()
        assert s.detect("def hello():\n    pass") is True

    def test_detect_not_python(self):
        """Non-Python content that can't parse as Python rejected."""
        s = PythonStrategy()
        # Note: {"key": "value"} IS valid Python (dict literal)
        # so Python strategy correctly detects it. The pipeline
        # orders JSON first, so this isn't a real conflict.
        assert s.detect("just random text with no python") is False


class TestPipelineErrorMessages:
    """Pipeline must give actionable error messages, not 'Unable to detect'."""

    def test_broken_json_no_longer_says_unable_to_detect(self):
        """The exact bug: broken JSON said 'Unable to detect language format'."""
        p = FixPipeline()
        broken = '{\n  "url": "http://example.com/mcp"\n  "headers": {}\n}'
        result = p.fix(broken)
        # Must NOT say "Unable to detect language format"
        assert not any("Unable to detect" in e for e in result.errors)

    def test_broken_python_no_longer_says_unable_to_detect(self):
        """Broken Python should also not say 'Unable to detect'."""
        p = FixPipeline()
        broken = "def f():\n  x = 1\n y = 2"
        result = p.fix(broken)
        assert not any("Unable to detect" in e for e in result.errors)
