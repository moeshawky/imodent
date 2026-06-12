"""Tests for StrategyRegistry — registration, lookup, and isolation."""

import pytest

from imodent.interfaces import LanguageStrategy
from imodent.registry import StrategyRegistry
from imodent.strategies.python import PythonStrategy


def test_registry_get_python(isolated_registry):
    """StrategyRegistry.get('python') returns PythonStrategy class."""
    cls = StrategyRegistry.get("python")
    assert cls is not None
    assert issubclass(cls, LanguageStrategy)
    assert cls is PythonStrategy


def test_registry_get_by_extension(isolated_registry):
    """StrategyRegistry.get_by_extension('.py') returns PythonStrategy."""
    cls = StrategyRegistry.get_by_extension(".py")
    assert cls is not None
    assert cls is PythonStrategy


def test_registry_get_by_extension_case_insensitive(isolated_registry):
    """StrategyRegistry.get_by_extension is case-insensitive."""
    cls = StrategyRegistry.get_by_extension(".PY")
    assert cls is PythonStrategy


def test_registry_register_invalid_raises(isolated_registry):
    """StrategyRegistry.register with non-LanguageStrategy raises TypeError."""
    with pytest.raises(TypeError, match="must be a subclass of LanguageStrategy"):
        StrategyRegistry.register(str)  # type: ignore[arg-type]


def test_registry_all_returns_4(isolated_registry):
    """StrategyRegistry.all() returns list of 4 built-in strategy classes."""
    all_strategies = StrategyRegistry.all()
    assert len(all_strategies) == 4
    names = {cls().name for cls in all_strategies}
    assert names == {"python", "json", "jsonl", "yaml"}


def test_registry_clear_reload(isolated_registry):
    """StrategyRegistry.clear() then get('python') still works (lazy reload)."""
    StrategyRegistry.clear()
    # After clear, builtins should re-register on next access
    cls = StrategyRegistry.get("python")
    assert cls is not None
    assert cls is PythonStrategy


def test_registry_detect(isolated_registry):
    """StrategyRegistry.detect() returns non-None strategy for known formats.

    Note: JSON and JSONL content can also parse as valid Python expressions,
    so StrategyRegistry.detect() may return PythonStrategy depending on
    registration order. For unambiguous detection, use FixPipeline.detect()
    which has explicit ordering (json → jsonl → yaml → python).
    """
    # Python: uses keywords that should not match
    cls = StrategyRegistry.detect("def f(): pass\n")
    assert cls is not None
    assert cls().name == "python"

    # Should return SOME strategy (not None) for recognizable content
    cls = StrategyRegistry.detect('{"a":1}\n{"b":2}\n')
    assert cls is not None, "Should detect SOME strategy for JSONL-like content"

    # Unknown content → None
    cls = StrategyRegistry.detect("?!@#$%^&*()")
    assert cls is None


def test_registry_get_nonexistent(isolated_registry):
    """StrategyRegistry.get('nosuch') returns None."""
    assert StrategyRegistry.get("nosuch") is None


def test_registry_get_by_extension_nonexistent(isolated_registry):
    """StrategyRegistry.get_by_extension('.nosuch') returns None."""
    assert StrategyRegistry.get_by_extension(".nosuch") is None


# ---------------------------------------------------------------------------
# ProcessorRegistry tests
# ---------------------------------------------------------------------------


def test_registry_processor_registry():
    """ProcessorRegistry.register and get work correctly with a valid Processor subclass."""
    from imodent.interfaces import FixResult, LanguageStrategy, Processor
    from imodent.registry import ProcessorRegistry

    ProcessorRegistry.clear()

    class DummyStrategy(LanguageStrategy):
        @property
        def name(self) -> str:
            return "dummy"

        @property
        def extensions(self) -> list[str]:
            return [".dum"]

        def detect(self, content: str) -> bool:
            return True

        def fix(
            self, content: str, indent_size: int = 4, force: bool = False
        ) -> FixResult:
            return FixResult(True, content, [], [], True, True)

        def validate(self, content: str) -> tuple[bool, str | None]:
            return True, None

    class DummyProcessor(Processor):
        @property
        def name(self) -> str:
            return "dummy-proc"

        def process(self, content: str, strategy: LanguageStrategy) -> FixResult:
            return FixResult(True, content, [], [], True, True)

    # Register
    cls = ProcessorRegistry.register(DummyProcessor)
    assert cls is DummyProcessor

    # Get
    retrieved = ProcessorRegistry.get("dummy-proc")
    assert retrieved is DummyProcessor

    # All
    all_procs = ProcessorRegistry.all()
    assert len(all_procs) == 1
    assert all_procs[0] is DummyProcessor

    # Clear
    ProcessorRegistry.clear()
    assert len(ProcessorRegistry.all()) == 0
    assert ProcessorRegistry.get("dummy-proc") is None


def test_registry_processor_register_invalid():
    """ProcessorRegistry.register with non-Processor subclass raises TypeError."""
    from imodent.registry import ProcessorRegistry

    with pytest.raises(TypeError, match="must be a subclass of Processor"):
        ProcessorRegistry.register(str)  # type: ignore[arg-type]
