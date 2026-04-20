"""
Unit Tests: G-EDGE (Edge Cases) and G-ERR (Error Handling).

Test individual components with edge cases and error conditions.
"""
import pytest
from imodent.pipeline import FixPipeline
from imodent.strategies.python import PythonStrategy
from imodent.strategies.json import JSONStrategy
from imodent.strategies.jsonl import JSONLStrategy
from imodent.strategies.yaml import YAMLStrategy


class TestPythonEdgeCases:
    """Test Python strategy with edge cases."""
    
    @pytest.mark.parametrize("code", [
        "",  # Empty
        "   \n  \n\t\n",  # Only whitespace
        "# Just a comment\n",  # Only comment
        "def f():\n    pass",  # Single line function
        "class C:\n    pass",  # Single line class
        "import os\nimport sys",  # Multiple imports
        "x = 1\ny = 2\nz = 3",  # Multiple assignments
        "def f():\n    if True:\n        if True:\n            if True:\n                pass",  # Deep nesting
    ])
    def test_edge_cases(self, code):
        """Handle various edge cases without crashing."""
        strategy = PythonStrategy()
        result = strategy.fix(code)
        
        assert result is not None
        # Should not crash even if it can't fix
        assert result.content is not None


class TestJSONEdgeCases:
    """Test JSON strategy with edge cases."""
    
    @pytest.mark.parametrize("code", [
        "{}",  # Empty object
        "[]",  # Empty array
        '{"a":1}',  # Single key
        '{"a":{"b":{"c":1}}}',  # Deep nesting
        '[1,2,3,4,5]',  # Simple array
        '{"a":null,"b":true,"c":false}',  # Special values
        '{"unicode":"日本語","emoji":"🚀"}',  # Unicode
    ])
    def test_edge_cases(self, code):
        """Handle various JSON edge cases."""
        strategy = JSONStrategy()
        result = strategy.fix(code)
        
        assert result is not None
        assert result.success or result.content is not None


class TestYAMLEdgeCases:
    """Test YAML strategy with edge cases."""
    
    @pytest.mark.parametrize("code", [
        "",  # Empty
        "---\n",  # Document start only
        "key: value",  # Single key
        "a: 1\nb: 2\nc: 3",  # Multiple keys
        "list:\n  - item1\n  - item2",  # Simple list
        "nested:\n  key: value",  # Nested object
        "unicode: 日本語\nemoji: 🚀",  # Unicode
    ])
    def test_edge_cases(self, code):
        """Handle various YAML edge cases."""
        strategy = YAMLStrategy()
        result = strategy.fix(code)
        
        assert result is not None
        assert result.content is not None


class TestErrorHandling:
    """Test error handling paths."""
    
    def test_invalid_python_graceful(self):
        """Invalid Python should not crash, should report error."""
        strategy = PythonStrategy()
        invalid = "def f():\nif True:\n  pass"  # Missing indent
        
        result = strategy.fix(invalid)
        
        assert result is not None
        # Should either fix or report error gracefully
        assert result.content is not None
    
    def test_invalid_json_graceful(self):
        """Invalid JSON should not crash."""
        strategy = JSONStrategy()
        invalid = "{invalid json}"
        
        result = strategy.fix(invalid)
        
        assert result is not None
        assert not result.success or result.content is not None
    
    def test_invalid_yaml_graceful(self):
        """Invalid YAML should not crash."""
        strategy = YAMLStrategy()
        invalid = "key: value\n  bad indent"
        
        result = strategy.fix(invalid)
        
        assert result is not None
        assert result.content is not None
    
    def test_malformed_jsonl(self):
        """JSONL with one bad line should handle gracefully."""
        strategy = JSONLStrategy()
        code = '{"a":1}\n{invalid}\n{"b":2}'
        
        result = strategy.fix(code)
        
        assert result is not None
        assert result.content is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
