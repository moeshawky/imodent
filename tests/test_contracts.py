"""Tests for ABC contracts — LanguageStrategy, Processor, Advisor, Analyzer, Fixer."""

from pathlib import Path

import pytest

from imodent.advisors.base import Advisor
from imodent.analysis.context import AnalysisContext
from imodent.analysis.findings import Finding, FixOption, Severity
from imodent.analyzers.base import Analyzer, AnalyzerCapability
from imodent.fixers.base import Fixer
from imodent.interfaces import FixResult, LanguageStrategy, Processor

# ---------------------------------------------------------------------------
# FixResult tests
# ---------------------------------------------------------------------------


def test_fix_result_fields():
    """FixResult is a dataclass with expected fields and values."""
    result = FixResult(
        success=True,
        content='{"key": "value"}',
        errors=[],
        warnings=[],
        original_valid=False,
        fixed_valid=True,
    )
    assert result.success is True
    assert result.content == '{"key": "value"}'
    assert result.errors == []
    assert result.warnings == []
    assert result.original_valid is False
    assert result.fixed_valid is True


def test_fix_result_failure():
    """FixResult with failure status carries error messages."""
    result = FixResult(
        success=False,
        content="",
        errors=["Syntax error at line 5: unexpected token"],
        warnings=["Possible indentation mismatch"],
        original_valid=False,
        fixed_valid=False,
    )
    assert result.success is False
    assert len(result.errors) == 1
    assert len(result.warnings) == 1
    assert "Syntax error" in result.errors[0]


# ---------------------------------------------------------------------------
# LanguageStrategy ABC tests
# ---------------------------------------------------------------------------


def test_language_strategy_abc_enforces_contract():
    """Subclass without all abstract methods raises TypeError on instantiation."""

    class PartialStrategy(LanguageStrategy):
        @property
        def name(self) -> str:
            return "partial"

        @property
        def extensions(self) -> list[str]:
            return [".xyz"]

        # Missing: detect, fix, validate

    with pytest.raises(TypeError):
        PartialStrategy()  # type: ignore[abstract]


def test_language_strategy_full_implementation():
    """Full LanguageStrategy subclass can be instantiated and used."""

    class FullStrategy(LanguageStrategy):
        @property
        def name(self) -> str:
            return "full"

        @property
        def extensions(self) -> list[str]:
            return [".ful"]

        def detect(self, content: str) -> bool:
            return "FULL" in content

        def fix(
            self, content: str, indent_size: int = 4, force: bool = False
        ) -> FixResult:
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

        def validate(self, content: str) -> tuple[bool, str | None]:
            return True, None

    instance = FullStrategy()
    assert instance.name == "full"
    assert instance.extensions == [".ful"]
    assert instance.detect("FULL content") is True
    assert instance.detect("OTHER") is False
    is_valid, error = instance.validate("anything")
    assert is_valid is True
    assert error is None


# ---------------------------------------------------------------------------
# Processor ABC tests
# ---------------------------------------------------------------------------


def test_processor_abc_enforces_contract():
    """Subclass without abstract methods raises TypeError on instantiation."""

    class PartialProcess(Processor):
        @property
        def name(self) -> str:
            return "partial-processor"

        # Missing: process

    with pytest.raises(TypeError):
        PartialProcess()  # type: ignore[abstract]


def test_processor_full_implementation():
    """Full Processor subclass can be instantiated."""

    class FullProcess(Processor):
        @property
        def name(self) -> str:
            return "full-processor"

        def process(self, content: str, strategy: LanguageStrategy) -> FixResult:
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

    # Need a strategy instance for process()
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

    proc = FullProcess()
    assert proc.name == "full-processor"
    result = proc.process("hello", DummyStrategy())
    assert result.success is True


# ---------------------------------------------------------------------------
# Advisor ABC tests
# ---------------------------------------------------------------------------


def test_advisor_base_defaults():
    """Advisor ABC has priority default of 5."""

    class ConcreteAdvisor(Advisor):
        @property
        def name(self) -> str:
            return "test-advisor"

        def should_advise(self, findings, context) -> bool:
            return bool(findings)

        def advise(self, findings, context):
            return []

    advisor = ConcreteAdvisor()
    assert advisor.priority == 5


def test_advisor_base_can_handle():
    """Concrete Advisor subclass instantiates and can override priority."""

    class HighPrioAdvisor(Advisor):
        @property
        def name(self) -> str:
            return "high-prio"

        @property
        def priority(self) -> int:
            return 10

        def should_advise(self, findings, context) -> bool:
            return True

        def advise(self, findings, context):
            from imodent.analysis.findings import Advice

            return [
                Advice(
                    finding_ids=[],
                    category="test",
                    summary="Test advice",
                    explanation="Test explanation",
                    recommendation="Test recommendation",
                )
            ]

    advisor = HighPrioAdvisor()
    assert advisor.priority == 10
    assert advisor.should_advise([], AnalysisContext()) is True


# ---------------------------------------------------------------------------
# Analyzer ABC and AnalyzerCapability tests
# ---------------------------------------------------------------------------


def test_analyzer_capability_enum():
    """AnalyzerCapability enum has expected values."""
    assert AnalyzerCapability.SYNTAX.value == "syntax"
    assert AnalyzerCapability.IMPORTS.value == "imports"
    assert AnalyzerCapability.LINT.value == "lint"
    assert AnalyzerCapability.TYPES.value == "types"
    assert AnalyzerCapability.STYLE.value == "style"
    assert AnalyzerCapability.SECURITY.value == "security"
    assert AnalyzerCapability.RESIDUE.value == "residue"


def test_analyzer_base_defaults():
    """Analyzer ABC has correct default values for non-abstract properties."""

    class ConcreteAnalyzer(Analyzer):
        @property
        def name(self) -> str:
            return "test-analyzer"

        @property
        def capabilities(self) -> set[AnalyzerCapability]:
            return {AnalyzerCapability.LINT}

        def analyze(self, context):
            return []

    analyzer = ConcreteAnalyzer()
    assert analyzer.languages == set()
    assert analyzer.requires_ast is False


def test_analyzer_can_analyze_no_languages():
    """Analyzer.can_analyze returns True when languages filter is empty (all languages)."""

    class UniversalAnalyzer(Analyzer):
        @property
        def name(self) -> str:
            return "universal"

        @property
        def capabilities(self) -> set[AnalyzerCapability]:
            return {AnalyzerCapability.SYNTAX}

        def analyze(self, context):
            return []

    from imodent.analysis.context import FileInfo

    analyzer = UniversalAnalyzer()
    fi = FileInfo(path=Path("/a.rs"), content="", language="rust")
    assert analyzer.can_analyze(fi) is True


def test_analyzer_can_analyze_language_filter():
    """Analyzer.can_analyze returns False when language doesn't match."""

    class PythonOnlyAnalyzer(Analyzer):
        @property
        def name(self) -> str:
            return "python-only"

        @property
        def languages(self) -> set[str]:
            return {"python"}

        @property
        def capabilities(self) -> set[AnalyzerCapability]:
            return {AnalyzerCapability.SYNTAX}

        def analyze(self, context):
            return []

    from imodent.analysis.context import FileInfo

    analyzer = PythonOnlyAnalyzer()
    py_info = FileInfo(path=Path("/a.py"), content="x=1", language="python")
    rs_info = FileInfo(path=Path("/a.rs"), content="fn main(){}", language="rust")

    assert analyzer.can_analyze(py_info) is True
    assert analyzer.can_analyze(rs_info) is False


def test_analyzer_abc_enforces_contract():
    """Analyzer subclass without abstract methods raises TypeError."""

    class PartialAnalyzer(Analyzer):
        @property
        def name(self) -> str:
            return "partial"

        # Missing: capabilities, analyze

    with pytest.raises(TypeError):
        PartialAnalyzer()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Fixer ABC tests
# ---------------------------------------------------------------------------


def test_fixer_abc_contracts():
    """Fixer ABC — subclass must implement can_handle, get_options, apply_fix."""

    class PartialFixer(Fixer):
        @property
        def name(self) -> str:
            return "partial"

        @property
        def handles(self) -> set[str]:
            return {"unused_import"}

        # Missing: can_auto_fix, get_options, apply_fix

    with pytest.raises(TypeError):
        PartialFixer()  # type: ignore[abstract]


def test_fixer_full_implementation():
    """Full Fixer subclass can be instantiated and its methods called."""

    class ConcreteFixer(Fixer):
        @property
        def name(self) -> str:
            return "concrete-fixer"

        @property
        def handles(self) -> set[str]:
            return {"unused_import"}

        def can_auto_fix(self, finding: Finding) -> bool:
            return True

        def get_options(self, finding, context) -> list[FixOption]:
            return [
                FixOption(
                    id="delete",
                    label="Remove import",
                    description="Remove the unused import",
                    action="delete",
                    is_safe=True,
                )
            ]

        def apply_fix(self, finding, option, content: str) -> FixResult:
            return FixResult(
                success=True,
                content=content,
                errors=[],
                warnings=[],
                original_valid=True,
                fixed_valid=True,
            )

    fixer = ConcreteFixer()
    assert fixer.name == "concrete-fixer"
    assert fixer.handles == {"unused_import"}

    # Test can_handle (non-abstract, implemented on base)
    f_ok = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/a/b.py"),
        message="test",
    )
    f_other = Finding.create(
        type="unused_variable",
        severity=Severity.WARNING,
        file=Path("/a/b.py"),
        message="test",
    )
    assert fixer.can_handle(f_ok) is True
    assert fixer.can_handle(f_other) is False


# ---------------------------------------------------------------------------
# Coverage-pushing: abstract body lines (pass / ...) exercised via super()
# ---------------------------------------------------------------------------


def test_language_strategy_abc_properties():
    """LanguageStrategy.name and .extensions abstract bodies covered via super().

    Covers interfaces.py lines 38 (name pass), 44 (extensions pass).
    """

    class Probe(LanguageStrategy):
        @property
        def name(self) -> str:
            super().name  # exercises line 38: pass
            return "probe"

        @property
        def extensions(self) -> list[str]:
            super().extensions  # exercises line 44: pass
            return [".prb"]

        def detect(self, content: str) -> bool:
            return True

        def fix(
            self, content: str, indent_size: int = 4, force: bool = False
        ) -> FixResult:
            return FixResult(True, content, [], [], True, True)

        def validate(self, content: str) -> tuple[bool, str | None]:
            return True, None

    p = Probe()
    assert p.name == "probe"
    assert p.extensions == [".prb"]


def test_language_strategy_abc_detect_abstract():
    """LanguageStrategy detect/fix/validate abstract bodies covered via super().

    Covers interfaces.py lines 57 (detect pass), 71 (fix pass), 85 (validate pass).
    """

    class Probe(LanguageStrategy):
        @property
        def name(self) -> str:
            return "probe"

        @property
        def extensions(self) -> list[str]:
            return [".prb"]

        def detect(self, content: str) -> bool:
            super().detect(content)  # exercises line 57: pass
            return content == "yes"

        def fix(
            self, content: str, indent_size: int = 4, force: bool = False
        ) -> FixResult:
            super().fix(content, indent_size, force)  # exercises line 71: pass
            return FixResult(True, content, [], [], True, True)

        def validate(self, content: str) -> tuple[bool, str | None]:
            super().validate(content)  # exercises line 85: pass
            return True, None

    p = Probe()
    assert p.detect("yes") is True
    assert p.detect("no") is False
    result = p.fix("hello")
    assert isinstance(result, FixResult)
    is_valid, err = p.validate("x")
    assert is_valid is True
    assert err is None


def test_fixer_abc_can_handle_abstract():
    """Fixer abstract bodies covered via super(); can_handle overridable.

    Covers fixers/base.py lines 17, 23, 37, 55 (...).
    """

    class Probe(Fixer):
        @property
        def name(self) -> str:
            super().name  # exercises line 17: ...
            return "probe"

        @property
        def handles(self) -> set[str]:
            super().handles  # exercises line 23: ...
            return {"test_type"}

        def can_auto_fix(self, finding: Finding) -> bool:
            super().can_auto_fix(finding)  # exercises line 37: ...
            return True

        def get_options(self, finding, context) -> list[FixOption]:
            super().get_options(finding, context)  # exercises line 55: ...
            return [
                FixOption(
                    id="k",
                    label="Keep",
                    description="keep it",
                    action="keep",
                    is_safe=True,
                )
            ]

        def apply_fix(self, finding, option, content: str) -> FixResult:
            return FixResult(True, content, [], [], True, True)

        # Override can_handle (concrete base method) to prove it's overridable
        def can_handle(self, finding: Finding) -> bool:
            return finding.type == "custom_test_type"

    f = Probe()
    assert f.name == "probe"
    assert f.handles == {"test_type"}

    # Exercise can_auto_fix (line 37) and get_options (line 55) through Probe
    finding_test = Finding.create(
        type="test_type",
        severity=Severity.WARNING,
        file=Path("/a.py"),
        message="test",
    )
    assert f.can_auto_fix(finding_test) is True
    opts = f.get_options(finding_test, AnalysisContext())
    assert len(opts) == 1
    assert opts[0].id == "k"

    # Test overridden can_handle
    finding_ok = Finding.create(
        type="custom_test_type",
        severity=Severity.WARNING,
        file=Path("/a.py"),
        message="test",
    )
    assert f.can_handle(finding_ok) is True

    finding_bad = Finding.create(
        type="other",
        severity=Severity.WARNING,
        file=Path("/a.py"),
        message="test",
    )
    assert f.can_handle(finding_bad) is False


def test_fixer_abc_apply_fix_returns_fixresult():
    """Fixer.apply_fix abstract body covered via super(); returns FixResult.

    Covers fixers/base.py line 72 (...).
    """

    class Probe(Fixer):
        @property
        def name(self) -> str:
            return "probe"

        @property
        def handles(self) -> set[str]:
            return {"test_type"}

        def can_auto_fix(self, finding: Finding) -> bool:
            return True

        def get_options(self, finding, context) -> list[FixOption]:
            return [
                FixOption(
                    id="k",
                    label="Keep",
                    description="keep it",
                    action="keep",
                    is_safe=True,
                )
            ]

        def apply_fix(self, finding, option, content: str) -> FixResult:
            super().apply_fix(finding, option, content)  # exercises line 72: ...
            return FixResult(True, content, [], [], True, True)

    finding = Finding.create(
        type="test_type",
        severity=Severity.WARNING,
        file=Path("/a.py"),
        message="test",
    )
    option = FixOption(id="k", label="K", description="d", action="keep", is_safe=True)

    p = Probe()
    result = p.apply_fix(finding, option, "hello")
    assert isinstance(result, FixResult)
    assert result.success is True
    assert result.content == "hello"


def test_advisor_abc_should_advise_default_true():
    """Advisor name + should_advise abstract bodies covered; override returns True.

    Covers advisors/base.py lines 16 (...), 35 (...).
    """

    class Probe(Advisor):
        @property
        def name(self) -> str:
            super().name  # exercises line 16: ...
            return "probe"

        def should_advise(self, findings, context) -> bool:
            super().should_advise(findings, context)  # exercises line 35: ...
            return True

        def advise(self, findings, context):
            return []

    p = Probe()
    assert p.name == "probe"
    assert p.priority == 5  # default priority from base
    assert p.should_advise([], AnalysisContext()) is True


def test_advisor_abc_advise_abstract():
    """Advisor.advise() abstract body covered via super().

    Covers advisors/base.py line 49 (...).
    """

    class Probe(Advisor):
        @property
        def name(self) -> str:
            return "probe"

        def should_advise(self, findings, context) -> bool:
            return True

        def advise(self, findings, context):
            super().advise(findings, context)  # exercises line 49: ...
            return []

    p = Probe()
    result = p.advise([], AnalysisContext())
    assert result == []


def test_analyzer_abc_capabilities_property():
    """Analyzer name + capabilities abstract bodies covered via super().

    Covers analyzers/base.py lines 29 (...), 35 (...).
    """

    class Probe(Analyzer):
        @property
        def name(self) -> str:
            super().name  # exercises line 29: ...
            return "probe"

        @property
        def capabilities(self) -> set[AnalyzerCapability]:
            super().capabilities  # exercises line 35: ...
            return {AnalyzerCapability.LINT, AnalyzerCapability.SYNTAX}

        def analyze(self, context):
            return []

    p = Probe()
    assert p.name == "probe"
    assert p.capabilities == {AnalyzerCapability.LINT, AnalyzerCapability.SYNTAX}


def test_analyzer_abc_languages_property():
    """Analyzer.analyze() abstract body covered via super(); languages default.

    Covers analyzers/base.py line 65 (...).
    """

    class Probe(Analyzer):
        @property
        def name(self) -> str:
            return "probe"

        @property
        def capabilities(self) -> set[AnalyzerCapability]:
            return {AnalyzerCapability.SYNTAX}

        def analyze(self, context):
            super().analyze(context)  # exercises line 65: ...
            return []

    p = Probe()
    assert p.languages == set()  # default when not overridden
    assert p.requires_ast is False  # default
    result = p.analyze(AnalysisContext())
    assert result == []


def test_processor_abc():
    """Processor ABC name + process abstract bodies covered via super().

    Covers interfaces.py lines 105 (name pass), 119 (process pass).
    """

    class Probe(Processor):
        @property
        def name(self) -> str:
            super().name  # exercises line 105: pass
            return "probe"

        def process(self, content: str, strategy: LanguageStrategy) -> FixResult:
            super().process(content, strategy)  # exercises line 119: pass
            return FixResult(True, content, [], [], True, True)

    class Dummy(LanguageStrategy):
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

    p = Probe()
    assert p.name == "probe"
    result = p.process("hello", Dummy())
    assert isinstance(result, FixResult)
    assert result.success is True


def test_fixresult_repr():
    """FixResult.__repr__ produces the expected string representation."""
    fr = FixResult(
        success=True,
        content="hi",
        errors=[],
        warnings=[],
        original_valid=True,
        fixed_valid=True,
    )
    r = repr(fr)
    assert "FixResult" in r
    assert "success=True" in r
    assert "content='hi'" in r

    # Also test the failure case repr
    fr2 = FixResult(
        success=False,
        content="",
        errors=["err1"],
        warnings=["warn1"],
        original_valid=False,
        fixed_valid=False,
    )
    r2 = repr(fr2)
    assert "FixResult" in r2
    assert "success=False" in r2
    assert "err1" in r2


def test_interfaces_type_checking_lazy_load():
    """Interfaces.__getattr__ lazy-loads re-exported analysis types properly.

    Covers interfaces.py lines 169-174 (importlib, getattr, globals cache).

    Accesses re-exported names via the interfaces module to trigger
    __getattr__ on first access and the globals cache path on second access.
    """
    import imodent.interfaces as ifaces

    # --- Severity (from .analysis.findings) ---
    sev = ifaces.Severity
    assert sev is not None
    from imodent.analysis.findings import Severity as DirectSeverity

    assert sev is DirectSeverity

    # Second access hits globals cache (line 173)
    sev2 = ifaces.Severity
    assert sev2 is sev

    # --- Finding ---
    f = ifaces.Finding
    assert f is not None

    # --- Evidence ---
    ev = ifaces.Evidence
    assert ev is not None

    # --- AnalysisContext ---
    ctx = ifaces.AnalysisContext
    assert ctx is not None

    # --- Unknown attribute raises ---
    with pytest.raises(AttributeError):
        _ = ifaces.NonExistentName12345
