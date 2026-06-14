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


def test_pipeline_fix_and_validate_consistent_for_undetectable():
    """fix() and validate() return identical validity flags for undetectable input.

    Regression guard: both methods should report original_valid=False and
    fixed_valid=False when the language cannot be detected.  This ensures
    callers receive consistent signals regardless of which method they use.
    """
    pipeline = FixPipeline()
    undetectable = "\x00\x01\x02"

    fix_result = pipeline.fix(undetectable, strategy=None)
    val_result = pipeline.validate(undetectable, strategy=None)

    # Both should report failure
    assert fix_result.success is False
    assert val_result.success is False

    # Validity flags must be identical between fix() and validate()
    assert fix_result.original_valid == val_result.original_valid, (
        f"original_valid mismatch: fix={fix_result.original_valid}, "
        f"validate={val_result.original_valid}"
    )
    assert fix_result.fixed_valid == val_result.fixed_valid, (
        f"fixed_valid mismatch: fix={fix_result.fixed_valid}, "
        f"validate={val_result.fixed_valid}"
    )

    # Conservative default: validity is unknown, so False
    assert fix_result.original_valid is False
    assert fix_result.fixed_valid is False


def test_pipeline_fix_force_on_undetectable():
    """fix(force=True) on truly undetectable content warns when no strategy works.

    Uses a null-byte string that no registered strategy can parse or fix.
    """
    pipeline = FixPipeline()
    # Content with embedded null bytes — no strategy can handle this
    undetectable = "\x00\x01\x02"

    result = pipeline.fix(undetectable, strategy=None, force=True)

    # No strategy can fix this content — force had no effect
    assert result.success is False
    assert any("Unable to detect" in e for e in result.errors)
    assert any("force" in w.lower() for w in result.warnings), (
        f"Expected a warning about force=True having no effect, got: {result.warnings}"
    )


def test_pipeline_fix_force_positive_fallback():
    """force=True brute-force loop succeeds when a non-default strategy can fix content.

    Registers two test strategies that both refuse auto-detection.  Strategy
    ``fail_first`` always returns fix failure; ``force_winner`` returns success
    for a known marker string when ``force=True``.  The pipeline's auto-detect
    returns None (no built-in strategy matches), so the brute-force loop in
    FixPipeline.fix() iterates all registered strategies.  It hits
    ``fail_first`` first (fail → skip), then ``force_winner`` (success →
    return).  Verifies that ``result.success`` is True and the strategy that
    succeeded is identifiable via the result content.
    """
    from imodent.interfaces import FixResult, LanguageStrategy
    from imodent.registry import StrategyRegistry

    # Marker must escape ALL four built-in detect() methods:
    #   - JSON:      starts with { or [                → "FORCE TEST" avoids
    #   - JSONL:     multiple JSON-like lines           → single line avoids
    #   - YAML:      key:value, ---, bare unquoted key  → no colon avoids
    #   - Python:    ast.parse() on a bare identifier succeeds;
    #                a multi-word phrase raises SyntaxError and also
    #                misses the keyword regex + assignment/funcall patterns
    MARKER = "FORCE TEST MARKER"
    FIXED_MARKER = "FIXED_BY_FORCE_WINNER"

    class ForceWinner(LanguageStrategy):
        @property
        def name(self) -> str:
            return "force_winner"

        @property
        def extensions(self) -> list[str]:
            return [".fwin"]

        def detect(self, content: str) -> bool:
            return False

        def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
            if force and content.strip() == MARKER:
                return FixResult(
                    success=True,
                    content=FIXED_MARKER,
                    errors=[],
                    warnings=[],
                    original_valid=False,
                    fixed_valid=True,
                )
            return FixResult(
                success=False,
                content=content,
                errors=["Cannot fix"],
                warnings=[],
                original_valid=False,
                fixed_valid=False,
            )

        def validate(self, content: str) -> tuple[bool, str | None]:
            return (True, None)

    class FailFirst(LanguageStrategy):
        @property
        def name(self) -> str:
            return "fail_first"

        @property
        def extensions(self) -> list[str]:
            return [".fail"]

        def detect(self, content: str) -> bool:
            return False

        def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
            return FixResult(
                success=False,
                content=content,
                errors=["Always fails"],
                warnings=[],
                original_valid=False,
                fixed_valid=False,
            )

        def validate(self, content: str) -> tuple[bool, str | None]:
            return (True, None)

    # Clear registry so only our test strategies are present
    StrategyRegistry.clear()

    # Register in order: fail_first FIRST, force_winner SECOND
    StrategyRegistry.register(FailFirst)
    StrategyRegistry.register(ForceWinner)

    pipeline = FixPipeline()
    result = pipeline.fix(MARKER, strategy=None, force=True)

    assert result.success is True, (
        f"force=True should succeed via ForceWinner, got: {result.errors}"
    )
    assert result.content == FIXED_MARKER, (
        f"Expected fixed content from ForceWinner, got: {result.content!r}"
    )
    assert len(result.errors) == 0
