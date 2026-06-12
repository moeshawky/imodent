"""Tests for language strategy classes: detect, fix, and validate methods.

Covers PythonStrategy, JSONStrategy, JSONLStrategy, YAMLStrategy.
"""

import builtins
import json
import sys
from importlib.abc import MetaPathFinder

import pytest

from imodent.strategies.json import JSONStrategy
from imodent.strategies.jsonl import JSONLStrategy
from imodent.strategies.python import (
    ASTStructureVisitor,
    PythonStrategy,
    _ends_with_block_colon,
)
from imodent.strategies.yaml import YAMLStrategy

# ---------------------------------------------------------------------------
# Helper: mock a module import failure
# ---------------------------------------------------------------------------


def _mock_import_blocker(blocked_names: list[str]):
    """Return a __import__ replacement that raises ImportError for blocked names.

    Used with monkeypatch.setattr(builtins, '__import__', ...) to simulate
    optional dependencies being unavailable.
    """
    _orig = builtins.__import__

    def _mock(name, *args, **kwargs):
        if name in blocked_names:
            raise ImportError(f"Mocked: No module named {name!r}")
        return _orig(name, *args, **kwargs)

    return _mock


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


def test_python_strategy_detect_valid(sample_py_content):
    """PythonStrategy.detect() returns True for valid Python source."""
    strategy = PythonStrategy()
    assert strategy.detect(sample_py_content) is True


def test_json_strategy_detect_valid():
    """JSONStrategy.detect() returns True for valid JSON."""
    strategy = JSONStrategy()
    assert strategy.detect('{"a": 1}') is True


def test_jsonl_strategy_detect_valid():
    """JSONLStrategy.detect() returns True for valid JSONL."""
    strategy = JSONLStrategy()
    assert strategy.detect('{"a": 1}\n{"b": 2}\n') is True


def test_yaml_strategy_detect_valid():
    """YAMLStrategy.detect() returns True for valid YAML."""
    strategy = YAMLStrategy()
    assert strategy.detect("key: value\n") is True


def test_strategies_detect_empty(empty_content):
    """All four strategies return False for empty content."""
    for strategy_cls in [PythonStrategy, JSONStrategy, JSONLStrategy, YAMLStrategy]:
        strategy = strategy_cls()
        assert strategy.detect(empty_content) is False, (
            f"{strategy.name} should not detect empty content"
        )


# ---------------------------------------------------------------------------
# Fix tests — valid content
# ---------------------------------------------------------------------------


def test_python_fix_valid(sample_py_content):
    """PythonStrategy.fix() returns FixResult(success=True) for valid Python."""
    result = PythonStrategy().fix(sample_py_content)
    assert result.success is True
    assert "x = 1" in result.content


def test_json_fix_valid(sample_json_content):
    """JSONStrategy.fix() returns FixResult with parseable JSON output."""
    result = JSONStrategy().fix(sample_json_content)
    assert result.success is True
    # Output must be valid JSON
    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)


def test_jsonl_fix_valid(sample_jsonl_content):
    """JSONLStrategy.fix() returns FixResult where each original object round-trips.

    Note: JSONLStrategy.fix() dumps each object with indent_size, so output
    is multi-line indented JSON objects separated by newlines, not one-per-line.
    """
    result = JSONLStrategy().fix(sample_jsonl_content)
    assert result.success is True
    # Validate the original objects can be recovered from the fixed output
    # by re-reading all multi-line JSON objects
    import re

    # Split by lines that start a JSON object ({ or [)
    chunks = re.split(r"\n(?=[\{\[])", result.content.strip())
    parsed = [
        json.loads(chunk) for chunk in chunks if chunk.strip().startswith(("{", "["))
    ]
    assert len(parsed) == 3, f"Expected 3 JSON objects, got {len(parsed)}"
    for obj in parsed:
        assert isinstance(obj, dict)


def test_yaml_fix_valid(sample_yaml_content):
    """YAMLStrategy.fix() returns FixResult with valid YAML output."""
    import yaml as pyyaml

    result = YAMLStrategy().fix(sample_yaml_content)
    assert result.success is True
    # Output must be valid YAML
    parsed = pyyaml.safe_load(result.content)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Validate tests
# ---------------------------------------------------------------------------


def test_python_validate_valid(sample_py_content):
    """PythonStrategy.validate() returns (True, None) for valid Python."""
    is_valid, error = PythonStrategy().validate(sample_py_content)
    assert is_valid is True
    assert error is None


def test_json_validate_valid(sample_json_content):
    """JSONStrategy.validate() returns (True, None) for valid JSON."""
    is_valid, error = JSONStrategy().validate(sample_json_content)
    assert is_valid is True
    assert error is None


def test_yaml_validate_valid(sample_yaml_content):
    """YAMLStrategy.validate() returns (True, None) for valid YAML."""
    is_valid, error = YAMLStrategy().validate(sample_yaml_content)
    assert is_valid is True
    assert error is None


# ---------------------------------------------------------------------------
# Fix tests — broken content without --force
# ---------------------------------------------------------------------------


def test_json_fix_broken_no_force(malformed_json_content, monkeypatch):
    """JSONStrategy.fix() with force=False returns FixResult(success=False) for broken JSON.

    Mocks json-repair as unavailable so the stdlib json fallback is tested
    (it can only fix valid JSON, not structurally broken JSON).
    """
    import builtins

    _orig_import = builtins.__import__

    def _no_json_repair(name, *args, **kwargs):
        if name == "json_repair":
            raise ImportError("mocked: json-repair not installed")
        return _orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_json_repair)
    result = JSONStrategy().fix(malformed_json_content, force=False)
    assert result.success is False
    assert len(result.errors) > 0


def test_yaml_fix_broken_no_force(malformed_yaml_content):
    """YAMLStrategy.fix() with force=False returns FixResult(success=False) for broken YAML."""
    result = YAMLStrategy().fix(malformed_yaml_content, force=False)
    assert result.success is False
    assert len(result.errors) > 0


def test_python_fix_syntax_error_no_force(malformed_py_content):
    """PythonStrategy.fix() with force=False returns FixResult(success=False) for syntax error."""
    result = PythonStrategy().fix(malformed_py_content, force=False)
    assert result.success is False


# ---------------------------------------------------------------------------
# JSONL partial fix with --force
# ---------------------------------------------------------------------------


def test_jsonl_fix_mixed_lines(malformed_jsonl_content):
    """JSONLStrategy.fix() with force=True produces partial fix for mixed valid/invalid lines."""
    result = JSONLStrategy().fix(malformed_jsonl_content, force=True)
    # With force=True, broken lines are preserved but errors are reported
    assert len(result.errors) > 0, "Should report errors for broken JSONL lines"
    # Valid lines should still be reformatted
    lines = result.content.strip().splitlines()
    assert len(lines) >= 2, (
        "Output should have at least 2 lines (including preserved broken)"
    )


# ---------------------------------------------------------------------------
# Indentation tests
# ---------------------------------------------------------------------------


def test_python_indent_2_uses_ast_fallback():
    """PythonStrategy.fix() with indent_size=2 produces 2-space indented Python.

    Since black uses 4-space indent, a non-4 indent_size falls through to
    the AST-based reindent path.
    """
    content = "def f():\n    return 42\n"
    result = PythonStrategy().fix(content, indent_size=2)
    assert result.success is True
    # With indent_size=2, the body should use 2-space indent
    assert "  return 42" in result.content


# ---------------------------------------------------------------------------
# Strategy property tests
# ---------------------------------------------------------------------------


def test_strategy_names():
    """Each strategy has a unique, non-empty name."""
    names = [
        PythonStrategy().name,
        JSONStrategy().name,
        JSONLStrategy().name,
        YAMLStrategy().name,
    ]
    assert len(names) == len(set(names)), "Strategy names must be unique"
    for name in names:
        assert isinstance(name, str) and len(name) > 0


def test_strategy_extensions():
    """Each strategy returns a reasonable extension list."""
    assert ".py" in PythonStrategy().extensions
    assert ".json" in JSONStrategy().extensions
    assert ".jsonl" in JSONLStrategy().extensions
    assert ".yaml" in YAMLStrategy().extensions


# ---------------------------------------------------------------------------
# PythonStrategy uncovered path tests (black fallback, AST _reindent,
# _ends_with_block_colon, detect paths, validate)
# ---------------------------------------------------------------------------


def test_python_strategy_black_not_installed():
    """PythonStrategy.fix() falls back to AST when black import raises ImportError.

    Removes black from sys.modules and blocks re-import via a MetaPathFinder
    so the `import black` inside fix() raises ImportError.  Verifies the
    fallback path produces a valid result with the appropriate warning.
    """
    saved_black = sys.modules.pop("black", None)

    class BlockBlack(MetaPathFinder):
        """Meta path finder that raises ImportError for the 'black' package."""

        def find_spec(self, fullname, path, target=None):
            if fullname == "black":
                raise ImportError("No module named 'black'")
            return

    finder = BlockBlack()
    sys.meta_path.insert(0, finder)
    try:
        content = "x = 1\ny = 2\nprint(x + y)\n"
        result = PythonStrategy().fix(content)
        assert result.success is True, (
            f"AST fallback should succeed, got errors: {result.errors}"
        )
        assert any("black not installed" in w for w in result.warnings), (
            f"Expected 'black not installed' warning, got: {result.warnings}"
        )
    finally:
        sys.meta_path.remove(finder)
        if saved_black is not None:
            sys.modules["black"] = saved_black


def test_python_strategy_indent_2_ast_fallback():
    """PythonStrategy.fix(indent_size=2) skips black, uses AST reindent path.

    Black always uses 4-space indentation.  When indent_size != 4 the
    strategy skips black and falls through to the ASTStructureVisitor +
    _reindent() path.
    """
    content = "def f():\n    return 42\n"
    result = PythonStrategy().fix(content, indent_size=2)
    assert result.success is True
    assert "  return 42" in result.content
    # Verify the skip-black warning is present
    assert any("skipping" in w.lower() for w in result.warnings), (
        f"Expected black-skip warning, got: {result.warnings}"
    )


def test_python_strategy_indent_8():
    """PythonStrategy.fix(indent_size=8) produces 8-space indent via AST path.

    Non-4 indent_size always falls through to the AST-based reindent.
    """
    content = "def f():\n    return 42\n"
    result = PythonStrategy().fix(content, indent_size=8)
    assert result.success is True
    assert "        return 42" in result.content, (
        f"Expected 8-space indent, got:\n{result.content!r}"
    )


def test_python_strategy_detect_complex():
    """PythonStrategy.detect() returns True for code with decorators, classes, type hints.

    The decorator (@) on the first line triggers the keyword- / pattern-
    based detection immediately (before falling back to ast.parse).
    """
    content = (
        "@dataclass\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n"
        "\n"
        "    def distance(self) -> float:\n"
        "        return (self.x ** 2 + self.y ** 2) ** 0.5\n"
    )
    assert PythonStrategy().detect(content) is True


def test_python_strategy_detect_empty_file():
    """PythonStrategy.detect() returns False for empty / whitespace-only content."""
    assert PythonStrategy().detect("") is False
    assert PythonStrategy().detect("   \n  \n") is False


def test_python_strategy_validate_syntax_error():
    """PythonStrategy.validate() returns (False, error_message) for invalid Python.

    Uses a function name starting with a digit ('def 123():') which is a
    structural syntax error, not an indentation issue.
    """
    is_valid, error = PythonStrategy().validate("def 123():\n    pass\n")
    assert is_valid is False
    assert error is not None
    assert "SyntaxError" in error
    assert "line" in error


def test_python_strategy_ends_with_block_colon_true():
    """_ends_with_block_colon returns True for lines ending in block-starting colon."""
    assert _ends_with_block_colon("def f():") is True
    assert _ends_with_block_colon("class Foo:") is True
    assert _ends_with_block_colon("if x:") is True
    assert _ends_with_block_colon("    for i in range(10):") is True


def test_python_strategy_ends_with_block_colon_false():
    """_ends_with_block_colon returns False for non-block lines."""
    assert _ends_with_block_colon("x = 1") is False
    assert _ends_with_block_colon("return 42") is False
    assert _ends_with_block_colon("import os") is False


def test_python_strategy_ends_with_block_colon_dict():
    """_ends_with_block_colon returns False for dict-literal colons.

    When a line ends with ':' but has unclosed '{' or '[' the colon is
    part of a dict/list literal, not a block-starter.
    """
    # Bare dict-literal colon with unclosed brace
    assert _ends_with_block_colon("{1:") is False
    # Multi-key dict literal: unclosed '{' with trailing colon
    assert _ends_with_block_colon("{1: 2, 3:") is False


def test_python_strategy_ends_with_block_colon_nested():
    """_ends_with_block_colon handles multi-line input via embedded newlines.

    When called with a string containing '\\n', the function scans all
    characters and the final ':' from the last line controls the result.
    A colon inside a string on the last line is ignored.
    """
    assert _ends_with_block_colon("if True:\n    def f():") is True
    assert _ends_with_block_colon("x = 1\ns = 'text:'") is False


def test_python_strategy_fix_with_comments():
    """PythonStrategy.fix() preserves comment lines while fixing indentation.

    Comments starting with '#' are kept verbatim; their indent is adjusted
    to match the surrounding block level.
    """
    content = (
        "# top comment\n"
        "def f():\n"
        "    # inside comment\n"
        "    return 42\n"
        "# trailing comment\n"
    )
    result = PythonStrategy().fix(content)
    assert result.success is True
    assert "# top comment" in result.content
    assert "# inside comment" in result.content
    assert "# trailing comment" in result.content
    assert "return 42" in result.content


def test_python_strategy_fix_empty_function_body():
    """PythonStrategy.fix() handles a minimal function with only 'pass'."""
    content = "def f():\n    pass\n"
    result = PythonStrategy().fix(content)
    assert result.success is True
    assert "pass" in result.content
    assert "def f():" in result.content


# ---------------------------------------------------------------------------
# YAML — ruamel.yaml not installed → PyYAML fallback
# ---------------------------------------------------------------------------


def test_yaml_strategy_ruamel_not_installed(monkeypatch, sample_yaml_content):
    """YAMLStrategy.fix() falls back to PyYAML when ruamel.yaml is unavailable.

    Monkeypatches __import__ to raise ImportError for 'ruamel.yaml', forcing
    the Stage-1 block to enter its except ImportError branch and fall through
    to the Stage-2 PyYAML path (yaml_strategy.py:136-139 → 141-191).
    """
    monkeypatch.setattr(
        builtins,
        "__import__",
        _mock_import_blocker(["ruamel.yaml", "ruamel"]),
    )
    result = YAMLStrategy().fix(sample_yaml_content)
    assert result.success is True
    assert result.fixed_valid is True
    # Warning should indicate the ruamel fallback was used
    assert any("ruamel.yaml not installed" in w for w in result.warnings), (
        f"Expected ruamel fallback warning, got: {result.warnings}"
    )
    # Output must still be valid YAML
    import yaml as pyyaml

    parsed = pyyaml.safe_load(result.content)
    assert isinstance(parsed, dict)


def test_yaml_strategy_pyyaml_not_installed(monkeypatch, sample_yaml_content):
    """YAMLStrategy.fix() raises ImportError when both ruamel and PyYAML are absent.

    The ``import yaml`` at yaml_strategy.py:71 is unprotected — there is no
    try/except around it.  When PyYAML (the ``yaml`` package) cannot be imported,
    the ImportError propagates to the caller.  This test documents that behaviour.
    """
    monkeypatch.setattr(
        builtins,
        "__import__",
        _mock_import_blocker(["yaml", "ruamel.yaml", "ruamel"]),
    )
    with pytest.raises(ImportError, match="yaml"):
        YAMLStrategy().fix(sample_yaml_content)


def test_yaml_strategy_fix_multiple_docs(sample_yaml_content):
    """YAMLStrategy detects but cannot auto-fix multi-document YAML.

    Multi-document YAML uses ``---`` as a document separator.  Both
    ``yaml.safe_load`` and ``ruamel.yaml.YAML.load`` reject it with
    ``ComposerError: expected a single document`` — neither parser
    handles multi-doc streams in their default single-document mode.

    detect() correctly identifies it as YAML (the ``---`` prefix on
    yaml_strategy.py:63), but fix() without ``--force`` returns failure
    because validate() fails.  Even with ``force=True`` both stages
    raise ComposerError, resulting in a failed fix.  This is a documented
    gap: multi-doc YAML requires ``load_all`` which the strategy does
    not yet support.
    """
    content = sample_yaml_content.rstrip() + "\n---\nother: stuff\n"
    strategy = YAMLStrategy()
    # Detection must accept the --- marker
    assert strategy.detect(content) is True
    # validate() rejects multi-doc (safe_load raises ComposerError)
    is_valid, error = strategy.validate(content)
    assert is_valid is False
    assert "found another document" in error
    # fix() without force → failure (structural error)
    result = strategy.fix(content)
    assert result.success is False
    assert "Cannot fix YAML automatically" in " ".join(result.errors)
    # fix() with force=True still fails — both parsers reject multi-doc
    result_force = strategy.fix(content, force=True)
    assert result_force.success is False
    assert any("Cannot fix" in e for e in result_force.errors)


# ---------------------------------------------------------------------------
# JSON — json-repair not installed → stdlib json fallback
# ---------------------------------------------------------------------------


def test_json_strategy_json_repair_not_installed(monkeypatch, sample_json_content):
    """JSONStrategy.fix() falls back to stdlib json when json-repair is absent.

    Monkeypatches __import__ for 'json_repair' so the Stage-1 try block
    enters its except ImportError branch (json.py:102-103) and falls
    through to the Stage-2 stdlib-json path (json.py:107-131).
    """
    monkeypatch.setattr(
        builtins,
        "__import__",
        _mock_import_blocker(["json_repair"]),
    )
    result = JSONStrategy().fix(sample_json_content)
    assert result.success is True
    assert result.fixed_valid is True
    # Warning should indicate json-repair was unavailable
    assert any("json-repair not installed" in w for w in result.warnings), (
        f"Expected json-repair fallback warning, got: {result.warnings}"
    )
    # Output must still be valid JSON
    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)


def test_json_strategy_fix_invalid_with_force(monkeypatch, malformed_json_content):
    """JSONStrategy.fix(force=True) on broken JSON attempts repair then stdlib.

    With force=True the early-return for invalid JSON (json.py:62-76) is
    skipped.  json-repair is tried first; if it also fails, the stdlib
    json.loads catch reports the error in the FixResult.
    """
    # Block json-repair so we exercise the full fallback chain
    monkeypatch.setattr(
        builtins,
        "__import__",
        _mock_import_blocker(["json_repair"]),
    )
    result = JSONStrategy().fix(malformed_json_content, force=True)
    # With malformed content and no json-repair, the fix will fail
    assert result.success is False
    assert result.fixed_valid is False
    assert len(result.errors) > 0
    # One of the errors should mention the JSON decode failure
    assert any("JSONDecodeError" in e or "Cannot fix" in e for e in result.errors)


# ---------------------------------------------------------------------------
# JSONL — validate error paths
# ---------------------------------------------------------------------------


def test_jsonl_strategy_validate_invalid_line():
    """JSONLStrategy.validate() returns (False, ...) for one invalid line.

    The second line is not valid JSON — validate() stops at the first
    failure and reports the line number and error (jsonl.py:98-105).
    """
    content = '{"a": 1}\nnotjson\n{"b": 2}\n'
    strategy = JSONLStrategy()
    is_valid, error = strategy.validate(content)
    assert is_valid is False
    assert error is not None
    assert "Line 2" in error


def test_jsonl_strategy_empty_content():
    """JSONLStrategy.fix() on empty content succeeds with a single newline.

    validate("") → filtered lines = [] → (True, None).  fix("") → no
    lines processed → FixResult(success=True, content="\\n", errors=[]).
    Empty content is treated as valid (nothing to validate against).
    """
    result = JSONLStrategy().fix("")
    assert result.success is True
    assert result.fixed_valid is True
    assert len(result.errors) == 0
    # Content is just a newline
    assert result.content == "\n"


def test_jsonl_strategy_single_line():
    """JSONLStrategy handles single-line JSONL content.

    A single-line JSONL file is essentially a JSON object — detect() on
    line 45-46 returns True whenever the single line starts with { or [.
    fix() should re-format it.
    """
    content = '{"a":1}'
    strategy = JSONLStrategy()
    assert strategy.detect(content) is True
    result = strategy.fix(content)
    assert result.success is True
    assert result.fixed_valid is True
    # Output should contain the reformatted object
    assert '"a"' in result.content
    assert "1" in result.content


def test_jsonl_strategy_indent_passthrough():
    """JSONLStrategy.fix() passes indent_size through to json.dumps.

    indent_size=2 → 2-space indented JSON objects.  Each line is an
    independent json.loads→json.dumps round-trip (jsonl.py:83-84).
    """
    content = '{"a": [1, 2], "b": {"c": 3}}\n'
    strategy = JSONLStrategy()
    result = strategy.fix(content, indent_size=2)
    assert result.success is True
    # indent_size=2 produced: lines with 2-space leading indent for nested keys
    # Split: first line is {"a": ..., subsequent lines have indentation
    lines = result.content.splitlines()
    assert len(lines) > 1  # More than one line → indentation was applied
    # At least one line should start with 2 spaces (indented by indent_size=2)
    assert any(line.startswith("  ") for line in lines), (
        f"Expected 2-space indentation in output, got: {result.content!r}"
    )


# ---------------------------------------------------------------------------
# PythonStrategy — keyword-based detection (lines 151-163)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword_content",
    [
        # Each tuple: (keyword description, content that should be detected)
        ("def", "def f():\n    pass\n"),
        ("class", "class Foo:\n    pass\n"),
        ("import", "import os\n"),
        ("from", "from os import path\n"),
        ("if __name__", "if __name__ == '__main__':\n    pass\n"),
        ("async def", "async def f():\n    pass\n"),
        ("@ decorator", "@property\ndef f():\n    pass\n"),
        ("return", "def f():\n    return 1\n"),
        ("pass", "pass\n"),
        ("break", "break\n"),
        ("continue", "continue\n"),
    ],
    ids=lambda x: x if isinstance(x, str) else None,
)
def test_python_detect_all_keywords(keyword_content):
    """PythonStrategy.detect() returns True for every indicator keyword.

    Each indicator keyword from lines 151-163 is tested individually
    so no short-circuit masks a keyword that would fail.
    'break' and 'continue' at module level are syntax errors but
    detection happens before ast.parse — the keyword match on the
    first line triggers True immediately.
    """
    _desc, content = keyword_content
    assert PythonStrategy().detect(content) is True


# ---------------------------------------------------------------------------
# PythonStrategy.detect() — encoding declaration path
# ---------------------------------------------------------------------------


def test_python_detect_with_encoding_declaration():
    """PythonStrategy.detect() returns True when file starts with a coding cookie.

    The encoding declaration line does not match any python_indicator
    keyword, but the second line ('x = 1') matches the assignment
    regex at python.py:172.  This path is exercised by any file that
    opens with a non-code header.
    """
    content = "# -*- coding: utf-8 -*-\nx = 1\n"
    assert PythonStrategy().detect(content) is True


# ---------------------------------------------------------------------------
# PythonStrategy.detect() — AST fallback when no keyword/regex matches
# ---------------------------------------------------------------------------


def test_python_detect_by_ast():
    """PythonStrategy.detect() falls through to ast.parse when no keyword/regex match.

    When the first 5 non-empty lines contain no python_indicator keyword,
    no assignment (``name = ...``), and no function-call pattern
    (``name(...)``), detect() skips to ast.parse() at python.py:178.
    Bare name expressions are valid Python but match no keyword line.
    """
    content = "x\ny\n"
    assert PythonStrategy().detect(content) is True


# ---------------------------------------------------------------------------
# PythonStrategy.validate() — warnings path
# ---------------------------------------------------------------------------


def test_python_validate_with_warnings():
    """PythonStrategy.validate() returns (True, None) for code that emits warnings.

    Python 3.12+ emits a SyntaxWarning for ``is`` with a literal, but
    ast.parse still succeeds — SyntaxWarning is a warning, not an error.
    validate() catches only SyntaxError + generic Exception, so
    warning-producing code passes validation.
    """
    is_valid, error = PythonStrategy().validate("1 is 1\n")
    assert is_valid is True
    assert error is None


# ---------------------------------------------------------------------------
# PythonStrategy.fix() — indent_size=3 path (skips black, uses AST)
# ---------------------------------------------------------------------------


def test_python_fix_indent_3():
    """PythonStrategy.fix(indent_size=3) produces correct output.

    The code skips black when indent_size != 4 and falls through to the
    AST-based _reindent path.  This test verifies that non-4 indent
    requests use the AST reindent correctly.
    """
    content = "def f():\n    return 42\n"
    result = PythonStrategy().fix(content, indent_size=3)
    assert result.success is True
    # AST reindent produces 3-space indent as requested
    assert "   return 42" in result.content


# ---------------------------------------------------------------------------
# Helper: build ast_info from content for _reindent tests
# ---------------------------------------------------------------------------


def _build_ast_info(content: str) -> dict:
    """Parse content with ast + ASTStructureVisitor, return ast_info dict."""
    import ast

    tree = ast.parse(content)
    visitor = ASTStructureVisitor()
    visitor.run(tree)
    return {
        "levels": visitor.line_to_level,
        "block_starts": visitor.block_starts,
    }


# ---------------------------------------------------------------------------
# _reindent — blank line preservation
# ---------------------------------------------------------------------------


def test_python_reindent_preserves_blank_lines():
    """_reindent preserves blank lines that are close to the last block line.

    Calls _reindent() directly with indent_size=2.  The _reindent loop
    at python.py:308-310 appends an empty string for blank lines when
    ``(lineno - last_block_line) <= 3``.
    """
    content = "def f():\n    x = 1\n\n    y = 2\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    lines = result.splitlines()
    blank_count = sum(1 for ln in lines if ln.strip() == "")
    assert blank_count >= 1, (
        f"Expected >=1 blank line preserved, got {blank_count}. Output:\n{result!r}"
    )


# ---------------------------------------------------------------------------
# _reindent — tab-to-space conversion
# ---------------------------------------------------------------------------


def test_python_reindent_handles_tabs():
    """_reindent converts tab-indented input to space-indented output.

    _reindent strips each line with ``str.strip()`` (python.py:306),
    which removes all leading whitespace including tabs.  Indentation
    is then reconstructed from the AST level map using only spaces.
    Calls _reindent() directly with indent_size=2.
    """
    content = "def f():\n\tx = 1\n\tif True:\n\t\treturn 42\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "\t" not in result, f"Tabs found in output:\n{result!r}"
    assert "return 42" in result


# ---------------------------------------------------------------------------
# _reindent — trailing comment at EOF
# ---------------------------------------------------------------------------


def test_python_reindent_comment_at_eof():
    """_reindent handles a comment line that is the last line of the file.

    Comment handling at python.py:313-320 computes indent from the
    level_stack when there is a preceding non-empty line, or 0 when
    the comment follows a blank line.  Calls _reindent() directly.
    """
    content = "x = 1\n# end of file comment\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "# end of file comment" in result


# ---------------------------------------------------------------------------
# _ends_with_block_colon — triple-quote string handling (lines 30-35)
# ---------------------------------------------------------------------------


def test_python_ends_with_block_colon_triple_quoted():
    """_ends_with_block_colon skips colons inside triple-quoted strings.

    When a line contains a triple-quoted string (``\"\"\"...\"\"\"`` or
    ``'''...'''``) followed by a block-starting colon, the function
    finds the closing quote (python.py:31), jumps past it (line 34),
    and only the colon outside the string is kept in ``result``.
    """
    # Triple-double-quoted string with colon inside, block colon outside
    assert _ends_with_block_colon('x = """a: b""" if True:') is True
    assert _ends_with_block_colon('x = """text""" if cond:') is True


def test_python_ends_with_block_colon_single_quoted_string():
    """_ends_with_block_colon ignores a colon inside a single-quoted string.

    Exercises python.py:42-45 (enter single-quote mode) and 47-50
    (exit single-quote mode).  The colon inside the string is not
    appended to ``result``; the trailing colon outside is.
    """
    assert _ends_with_block_colon("x = 'a: b' if True:") is True
    assert _ends_with_block_colon("flag = 'no:yes' if cond:") is True


def test_python_ends_with_block_colon_double_quoted_string():
    """_ends_with_block_colon ignores a colon inside a double-quoted string.

    Exercises python.py:37-40 (enter double-quote mode) and 47-50
    (exit double-quote mode).
    """
    assert _ends_with_block_colon('x = "a: b" if True:') is True
    assert _ends_with_block_colon('result = "key:val" if ok:') is True


# ---------------------------------------------------------------------------
# PythonStrategy.detect() — function-call regex path (line 174-175)
# ---------------------------------------------------------------------------


def test_python_detect_function_call_regex():
    """PythonStrategy.detect() returns True when the first line is a function call.

    A line like ``print('hello')`` does not start with any python_indicator
    keyword and does not match the assignment regex, but it DOES match the
    function-call pattern ``name(...)`` at python.py:174, returning True
    at line 175.
    """
    assert PythonStrategy().detect("print('hello')\n") is True


# ---------------------------------------------------------------------------
# PythonStrategy.validate() — generic Exception path (lines 301-302)
# ---------------------------------------------------------------------------


def test_python_validate_generic_exception(monkeypatch):
    """PythonStrategy.validate() catches non-SyntaxError exceptions.

    When ast.parse raises something other than SyntaxError (e.g.
    RecursionError from deeply nested input), validate() enters the
    generic ``except Exception`` block at python.py:301-302.
    We simulate this by making ast.parse raise a ValueError.
    """
    import ast

    def _raise_value_error(*args, **kwargs):
        raise ValueError("simulated parse failure")

    monkeypatch.setattr(ast, "parse", _raise_value_error)
    is_valid, error = PythonStrategy().validate("anything")
    assert is_valid is False
    assert "Unexpected error" in error


# ---------------------------------------------------------------------------
# ASTStructureVisitor — block construct coverage (lines 87-121)
# ---------------------------------------------------------------------------


def test_python_ast_visitor_if_for_while_with_try():
    """ASTStructureVisitor visits if/for/while/try blocks (python.py:87-96).

    Exercises the isinstance check for If/For/While/Try and their
    orelse/handlers/finalbody traversal.  Uses _build_ast_info() to
    verify the visitor produces correct level maps.
    """
    content = (
        "def f():\n"
        "    for x in (1,):\n"
        "        if x:\n"
        "            pass\n"
        "        else:\n"
        "            pass\n"
        "    else:\n"
        "        pass\n"
        "    while False:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    else:\n"
        "        pass\n"
        "    finally:\n"
        "        pass\n"
    )
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "def f():" in result
    # indent_size=2 * level 1 -> 2-space indent for class/block body lines
    assert "  for x in" in result
    assert 1 in ast_info["levels"]  # def f()
    assert 1 in ast_info["block_starts"]


def test_python_ast_visitor_with_statement():
    """ASTStructureVisitor visits with-blocks (python.py:86 isinstance check).

    ast.With is included alongside If/For/While/Try in the isinstance
    check at line 86.  ast.With does NOT have an ``orelse`` attribute,
    so line 90 raises AttributeError.  This test documents the crash.
    """
    content = "def f():\n    with open('f') as fh:\n        pass\n"
    with pytest.raises(AttributeError):
        _build_ast_info(content)


def test_python_ast_visitor_match_case():
    """ASTStructureVisitor visits match/case statements (python.py:99-104).

    Python 3.10+ match/case: the visitor records the match line as a
    block start (line 99) and visits each case pattern at level+1 and
    each case body at level+2 (lines 100-104).
    """
    content = (
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 'one'\n"
        "        case _:\n"
        "            return 'other'\n"
    )
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "match x:" in result


def test_python_ast_visitor_async_constructs():
    """ASTStructureVisitor visits async for/with (python.py:107-121).

    An async function containing ``async for`` and ``async with``
    exercises the AsyncFor (lines 106-116) and AsyncWith (lines 118-121)
    branches of visit_node().
    """
    content = (
        "async def f():\n"
        "    async for x in gen():\n"
        "        pass\n"
        "    async with ctx():\n"
        "        pass\n"
    )
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "async def f():" in result


def test_python_ast_visitor_decorators():
    """ASTStructureVisitor visits decorators on functions/classes (python.py:82).

    The visitor iterates ``node.decorator_list`` and visits each decorator
    at the same level as the decorated function/class (line 82-83).
    """
    content = "@decorator1\n@decorator2\ndef f():\n    pass\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "@decorator1" in result
    assert "@decorator2" in result


# ---------------------------------------------------------------------------
# _reindent — decorator lines (lines 322-325)
# ---------------------------------------------------------------------------


def test_python_reindent_decorator_lines():
    """_reindent handles decorator lines (``@`` prefix) correctly.

    Decorators are detected at python.py:322 (``stripped.startswith('@')``)
    and indented at the current ``level_stack[-1]`` level (line 323).
    Calls _reindent() directly with a top-level decorator so
    level_stack[-1] == 0.
    """
    content = "@property\ndef f():\n    return 42\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "@property" in result


# ---------------------------------------------------------------------------
# _reindent — continuation lines (lines 327-332, 346-352)
# ---------------------------------------------------------------------------


def test_python_reindent_continuation_lines():
    """_reindent handles continuation lines from open brackets.

    When a line has more opening brackets than closing (python.py:346),
    subsequent lines are indented to align with the opening delimiter
    (lines 347-352).  The indentation stays on ``continuation_indent_stack``
    and is popped when a closing bracket is encountered (line 330).
    Calls _reindent() directly.
    """
    content = "def f():\n    x = some_function(\n        arg1,\n        arg2,\n    )\n"
    ast_info = _build_ast_info(content)
    result = PythonStrategy()._reindent(content, ast_info, indent_size=2)
    assert "some_function(" in result


# ---------------------------------------------------------------------------
# PythonStrategy.fix() — black produces invalid output (line 253)
# ---------------------------------------------------------------------------


def test_python_fix_black_invalid_output(monkeypatch):
    """PythonStrategy.fix() warns when black output fails validation.

    When black runs successfully (indent_size=4) but produces output
    that does not parse as valid Python, the strategy records a warning
    at python.py:253 and falls through to the AST-based path.
    We simulate this by replacing black.format_str with a function
    that returns intentionally broken output.
    """
    import black as black_mod

    def _broken_format(*args, **kwargs):
        return "this is not valid python at all!!!"

    monkeypatch.setattr(black_mod, "format_str", _broken_format)
    content = "x = 1\n"
    result = PythonStrategy().fix(content)
    # Black output is invalid → falls through to AST; AST may or may not fix it
    assert any("Black output invalid" in w for w in result.warnings), (
        f"Expected black-output-invalid warning, got: {result.warnings}"
    )


# ---------------------------------------------------------------------------
# PythonStrategy.fix() — black raises exception (lines 258-259)
# ---------------------------------------------------------------------------


def test_python_fix_black_raises_exception(monkeypatch):
    """PythonStrategy.fix() falls back when black.format_str raises.

    When black.format_str raises an exception (e.g. for a file that
    black cannot handle), the ``except Exception`` at python.py:258
    records a warning and falls through to the AST-based path.
    """
    import black as black_mod

    def _crashing_format(*args, **kwargs):
        raise RuntimeError("black choked on this input")

    monkeypatch.setattr(black_mod, "format_str", _crashing_format)
    content = "x = 1\n"
    result = PythonStrategy().fix(content)
    assert any("Black failed" in w for w in result.warnings), (
        f"Expected black-failed warning, got: {result.warnings}"
    )
    # AST fallback should succeed on simple content
    assert result.success is True


# ---------------------------------------------------------------------------
# PythonStrategy.fix() — SyntaxError in AST parse during fix (lines 274-276)
# ---------------------------------------------------------------------------


def test_python_fix_ast_parse_syntax_error_during_fix():
    """PythonStrategy.fix() catches SyntaxError when re-parsing for reindent.

    When indent_size != 4 skips black, but the original code has a
    structural syntax error (not indentation-related) and --force
    is used, the AST parse at python.py:267 raises SyntaxError.
    The except at line 274 sets an empty ast_info dict and records
    a warning at line 276.
    """
    # Non-indentation syntax error with force=True and non-4 indent
    content = "class F o:\n    pass\n"
    result = PythonStrategy().fix(content, indent_size=2, force=True)
    # The fix may succeed or fail, but the AST-parse-SyntaxError branch
    # should produce a warning about AST parsing
    assert any("Could not parse AST" in w for w in result.warnings), (
        f"Expected AST-parse warning, got: {result.warnings}"
    )


# ---------------------------------------------------------------------------
# PythonStrategy.fix() — fixed code still invalid (line 283)
# ---------------------------------------------------------------------------


def test_python_fix_result_still_invalid():
    """PythonStrategy.fix() appends error when reindented code fails validation.

    When indent_size != 4 and force=True, the _reindent path may produce
    output that still fails ast.parse.  The error is recorded at
    python.py:283.
    """
    content = "def 123():\n    pass\n"
    result = PythonStrategy().fix(content, indent_size=2, force=True)
    assert result.success is False
    assert any(
        "still has error" in e or "cannot auto-fix" in e.lower() for e in result.errors
    ), f"Expected still-invalid error, got: {result.errors}"


# ---------------------------------------------------------------------------
# _ends_with_block_colon — unclosed triple quote (line 33) + cleaned != colon (line 56)
# ---------------------------------------------------------------------------


def test_python_ends_with_block_colon_unclosed_triple_quote():
    """_ends_with_block_colon breaks on unclosed triple-quoted strings.

    When a triple-quoted string starts but no matching close is found,
    ``stripped.find(quote, ...)`` returns -1 at python.py:31 and the
    loop breaks at line 33.  After the break, ``result`` is empty so
    ``cleaned`` doesn't end with ``:`` — reaching line 56 as well.
    """
    # Line starts with triple quotes but they never close — the line
    # must also end with ':' for the early return at line 17 to pass.
    assert _ends_with_block_colon('"""unclosed :') is False


# ---------------------------------------------------------------------------
# PythonStrategy.detect() — SyntaxError → regex fallback (lines 181-183)
# ---------------------------------------------------------------------------


def test_python_detect_syntax_error_regex_fallback():
    """PythonStrategy.detect() returns True via regex after ast.parse fails.

    When content is NOT valid Python (ast.parse raises SyntaxError at
    line 178), detect() falls through to the regex check at lines 183-188.
    If a line starts with a Python keyword (``def``, ``class``, ``if``,
    etc.), the regex matches and detect() returns True.
    """
    # Invalid syntax (no colon after 'if') but looks like Python
    content = "if True pass\n"
    assert PythonStrategy().detect(content) is True


# ---------------------------------------------------------------------------
# ASTStructureVisitor — AsyncFor orelse (line 111)
# ---------------------------------------------------------------------------


def test_python_ast_visitor_async_for_orelse():
    """ASTStructureVisitor visits the orelse clause of async for (line 111).

    async for supports an optional else clause (same as regular for).
    The orelse body is visited at the same level as the async for itself
    (python.py:110-111).
    """
    content = (
        "async def f():\n"
        "    async for x in gen():\n"
        "        pass\n"
        "    else:\n"
        "        pass\n"
    )
    ast_info = _build_ast_info(content)
    # Verify the visitor doesn't crash and produces a level map
    assert len(ast_info["levels"]) > 0
    assert len(ast_info["block_starts"]) > 0
