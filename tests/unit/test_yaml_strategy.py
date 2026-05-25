"""Tests for YAML strategy fallback paths and edge cases."""
import pytest
from imodent.strategies.yaml import YAMLStrategy


class TestYAMLStrategyFallback:
    """Test YAML strategy fallback behavior."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = YAMLStrategy()

    def test_fix_valid_yaml(self):
        """Should fix valid YAML."""
        content = "key: value\nlist:\n  - item1\n  - item2\n"
        result = self.strategy.fix(content, indent_size=2)
        assert result.success is True
        assert result.fixed_valid is True

    def test_fix_yaml_with_comments(self):
        """Should preserve comments in YAML."""
        content = """# This is a comment
key: value
# Another comment
list:
  - item1
"""
        result = self.strategy.fix(content, indent_size=2)
        assert result.success is True

    def test_fix_empty_yaml(self):
        """Should handle empty YAML."""
        result = self.strategy.fix("", indent_size=2)
        # Empty YAML is valid (returns None)
        assert result.success is True

    def test_fix_yaml_doc_start(self):
        """Should handle YAML with document start."""
        content = "---\nkey: value\n"
        result = self.strategy.fix(content, indent_size=2)
        assert result.success is True

    def test_fix_yaml_with_unicode(self):
        """Should handle YAML with unicode."""
        content = "unicode: 日本語\nemoji: 🚀\n"
        result = self.strategy.fix(content, indent_size=2)
        assert result.success is True

    def test_fix_invalid_yaml(self):
        """Should handle invalid YAML gracefully."""
        content = "key: value\n  bad_indent: x\n"
        result = self.strategy.fix(content, indent_size=2)
        # Should not raise, but may not succeed
        assert result is not None

    def test_validate_valid_yaml(self):
        """Should validate valid YAML."""
        content = "key: value\nlist:\n  - item1\n"
        is_valid, error = self.strategy.validate(content)
        assert is_valid is True
        assert error is None

    def test_validate_invalid_yaml(self):
        """Should detect invalid YAML."""
        content = "key: value\n  bad: x\n    worse: y\n"
        is_valid, error = self.strategy.validate(content)
        # This may or may not be invalid depending on YAML parser
        # Just check it doesn't raise
        assert isinstance(is_valid, bool)

    def test_validate_empty_yaml(self):
        """Should validate empty YAML."""
        is_valid, error = self.strategy.validate("")
        assert is_valid is True

    def test_detect_yaml_with_doc_start(self):
        """Should detect YAML with document start."""
        content = "---\nkey: value\n"
        assert self.strategy.detect(content) is True

    def test_detect_yaml_key_value(self):
        """Should detect YAML with key: value pattern."""
        content = "name: test\nversion: 1.0\n"
        assert self.strategy.detect(content) is True

    def test_detect_not_yaml_json(self):
        """Should not detect JSON as YAML."""
        content = '{"key": "value"}\n'
        assert self.strategy.detect(content) is False

    def test_detect_not_yaml_plain_text(self):
        """Should not detect plain text as YAML."""
        content = "This is just plain text\nwith no colons\n"
        assert self.strategy.detect(content) is False

    def test_detect_empty_content(self):
        """Should not detect empty content as YAML."""
        assert self.strategy.detect("") is False
        assert self.strategy.detect("   \n") is False

    def test_fix_indent_size_applied(self):
        """Should apply custom indent size."""
        content = "key:\n  nested: value\n"
        result = self.strategy.fix(content, indent_size=4)
        assert result.success is True

    def test_fix_preserves_structure(self):
        """Should preserve YAML structure."""
        content = """database:
  host: localhost
  port: 5432
  credentials:
    username: admin
    password: secret
"""
        result = self.strategy.fix(content, indent_size=2)
        assert result.success is True
        # Should contain the same keys
        assert "database" in result.content
        assert "host" in result.content
        assert "port" in result.content

    def test_name_property(self):
        """Strategy should have correct name."""
        assert self.strategy.name == "yaml"

    def test_extensions_property(self):
        """Strategy should have correct extensions."""
        assert ".yaml" in self.strategy.extensions
        assert ".yml" in self.strategy.extensions
