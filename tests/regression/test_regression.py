"""
Regression Tests: G-DRIFT (Golden File Drift).

Ensure output doesn't drift unexpectedly between versions.
"""

import pytest
from imodent.pipeline import FixPipeline


# Golden inputs and expected outputs
GOLDEN_TESTS = [
    {
        "name": "python_basic",
        "input": "def hello():\nif True:\nprint('world')",
        "expected_contains": ["def hello():", "    if True:", "        print('world')"],
    },
    {
        "name": "json_basic",
        "input": '{"a":1,"b":2}',
        "expected_contains": ['{\n    "a": 1,\n    "b": 2\n}'],
    },
    {
        "name": "yaml_basic",
        "input": "key: value\nlist:\n  - item1",
        "expected_contains": ["key: value", "list:", "  - item1"],
    },
    {
        "name": "python_async",
        "input": "async def fetch():\nasync with session:\nasync for item in items:\nprint(item)",
        "expected_contains": [
            "async def fetch():",
            "    async with session:",
            "        async for item in items:",
            "            print(item)",
        ],
    },
    {
        "name": "python_match",
        "input": "match x:\ncase 1:\nprint('one')\ncase _:\nprint('other')",
        "expected_contains": [
            "match x:",
            "    case 1:",
            "        print('one')",
            "    case _:",
            "        print('other')",
        ],
    },
]


@pytest.mark.parametrize("test_case", GOLDEN_TESTS, ids=lambda tc: tc["name"])
def test_golden_output(test_case):
    """Test that output matches expected golden values."""
    pipeline = FixPipeline(indent_size=4)
    result = pipeline.fix(test_case["input"])

    assert result is not None
    assert result.content is not None

    # Check that expected strings are in the output
    for expected in test_case["expected_contains"]:
        assert (
            expected in result.content
        ), f"Expected '{expected}' not found in output:\n{result.content}"


def test_idempotency():
    """Running fix twice should produce same result."""
    pipeline = FixPipeline(indent_size=4)

    test_inputs = ["def f():\nif True:\npass", '{"a":1,"b":2}', "key: value"]

    for code in test_inputs:
        result1 = pipeline.fix(code)
        result2 = pipeline.fix(result1.content)

        assert result1.content == result2.content, f"Output not idempotent for:\n{code}"


def test_consistent_indent_size():
    """Output should use consistent indent size."""
    pipeline = FixPipeline(indent_size=2)

    code = "def f():\nif True:\nif True:\npass"
    result = pipeline.fix(code)

    # Count leading spaces on each line
    lines = result.content.split("\n")
    for line in lines:
        if line.strip():
            spaces = len(line) - len(line.lstrip())
            # Should be multiple of indent_size
            assert spaces % 2 == 0, f"Line has inconsistent indent: {repr(line)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
