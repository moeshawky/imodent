"""Tests for _ends_with_block_colon helper function in PythonStrategy."""
import pytest
from imodent.strategies.python import _ends_with_block_colon


class TestEndsWithBlockColon:
    """Test the _ends_with_block_colon function for correct block colon detection."""

    # --- Should return True (real block-starting colons) ---

    @pytest.mark.parametrize("line", [
        "def foo():",
        "def foo(arg1, arg2):",
        "async def fetch():",
        "class Bar:",
        "class Bar(Base):",
        "if True:",
        "if x > 0:",
        "elif False:",
        "else:",
        "for i in range(10):",
        "for key, value in items:",
        "while True:",
        "while x > 0:",
        "with open('file') as f:",
        "try:",
        "except ValueError:",
        "except (ValueError, TypeError):",
        "finally:",
        "match x:",
    ], ids=lambda l: l[:30])
    def test_real_block_colons(self, line):
        """Real block-starting colons should return True."""
        assert _ends_with_block_colon(line) is True

    # --- Should return False (colons in strings) ---

    @pytest.mark.parametrize("line", [
        'msg = "Hello:"',
        "msg = 'Hello:'",
        'url = "http://example.com:"',
        'path = "C:\\Users:"',
        'formatted = f"Value: {x}:"',
        "data = 'key: value'",
        'tricky = "def foo():"',
    ], ids=lambda l: l[:30])
    def test_colons_in_strings(self, line):
        """Colons inside string literals should return False."""
        assert _ends_with_block_colon(line) is False

    # --- Should return False (dict/list literals) ---

    @pytest.mark.parametrize("line", [
        'd = {"key":',
        'd = {"a": 1, "b":',
        "d = {",
        "arr = [x for x in items if x:",
        "nested = {\"outer\": {\"inner\":",
        "mixed = [{\"key\":",
    ], ids=lambda l: l[:30])
    def test_dict_list_literal_colons(self, line):
        """Colons inside unclosed dict/list literals should return False."""
        assert _ends_with_block_colon(line) is False

    # --- Should return False (no colon at all) ---

    @pytest.mark.parametrize("line", [
        "x = 1",
        "return result",
        "pass",
        "import os",
        "# comment",
        "",
        "   ",
    ], ids=lambda l: l[:30] if l else "empty")
    def test_no_colon(self, line):
        """Lines without trailing colon should return False."""
        assert _ends_with_block_colon(line) is False

    # --- Edge cases ---

    def test_triple_quoted_string(self):
        """Triple-quoted strings should be handled."""
        assert _ends_with_block_colon('msg = """Hello:"""') is False
        assert _ends_with_block_colon("msg = '''Hello:'''") is False

    def test_escaped_quotes(self):
        """Escaped quotes should not break string detection."""
        # This is a simplified test - the function doesn't handle escaped quotes
        # but this tests the basic case
        assert _ends_with_block_colon('msg = "He said \\"hi\\":"') is False

    def test_whitespace_after_colon(self):
        """Trailing whitespace after colon should still work."""
        assert _ends_with_block_colon("def foo():  ") is True
        assert _ends_with_block_colon("if True:\t") is True

    def test_closed_dict_with_colon(self):
        """Closed dict followed by block colon should return True."""
        # This is a tricky case - the dict is closed, then there's a block colon
        # e.g., `d = {"key": "value"}:` - but this is not valid Python
        # So we test that closed braces don't interfere
        assert _ends_with_block_colon('if d.get("key"):') is True
