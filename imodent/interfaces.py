"""
Abstract Base Classes for imodent.

These define the contracts that all language strategies and processors must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING


@dataclass
class FixResult:
    """Result of a fix operation."""

    success: bool
    content: str
    errors: list[str]
    warnings: list[str]
    original_valid: bool
    fixed_valid: bool


class LanguageStrategy(ABC):
    """
    Abstract base class for language-specific code fixing.

    Each language (Python, JSON, JSONL, YAML, etc.) implements this interface
    to provide its own detection, fixing, and validation logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the language/format."""
        pass

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """File extensions associated with this language (e.g., ['.py', '.pyw'])."""
        pass

    @abstractmethod
    def detect(self, content: str) -> bool:
        """
        Detect if the content belongs to this language.

        Args:
            content: The source code content to analyze.

        Returns:
            True if this strategy can handle the content.
        """
        pass

    @abstractmethod
    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        """
        Fix code issues in the content (indentation, formatting, structure).

        Args:
            content: The source code content to fix.
            indent_size: Number of spaces per indentation level (formatting parameter).

        Returns:
            FixResult with success status, fixed content, and any errors/warnings.
        """
        pass

    @abstractmethod
    def validate(self, content: str) -> tuple[bool, str | None]:
        """
        Validate that the content is syntactically correct.

        Args:
            content: The source code content to validate.

        Returns:
            Tuple of (is_valid, error_message).
            error_message is None if valid, otherwise contains the error.
        """
        pass


class Processor(ABC):
    """
    Abstract base class for processing operations.

    Processors handle specific operations like linting, formatting, or analysis
    that can be applied to any language.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the processor operation."""
        pass

    @abstractmethod
    def process(self, content: str, strategy: LanguageStrategy) -> FixResult:
        """
        Process the content using the given language strategy.

        Args:
            content: The source code content to process.
            strategy: The language strategy for detection/validation.

        Returns:
            FixResult with processing results.
        """
        pass


# Re-exports at module bottom: flattens namespace so callers can
# `from imodent import Finding, AnalysisContext` etc.
# These lines are in the "do not remove" protected set per AGENTS.md.
# Using lazy imports to avoid circular dependency with analysis.coordinator.

if TYPE_CHECKING:
    from .analysis.context import (
        AnalysisConfig,
        AnalysisContext,
        DependencyGraph,
        FileInfo,
    )
    from .analysis.coordinator import (
        AnalysisCoordinator,
        AnalysisResult,
        FixMode,
    )
    from .analysis.evidence import Evidence
    from .analysis.findings import (
        Advice,
        Change,
        Finding,
        FixOption,
        Location,
        ProofState,
        Severity,
    )


def __getattr__(name: str):
    """Lazy import for re-exported analysis types."""
    _lazy = {
        "AnalysisConfig": ".analysis.context",
        "AnalysisContext": ".analysis.context",
        "DependencyGraph": ".analysis.context",
        "FileInfo": ".analysis.context",
        "AnalysisCoordinator": ".analysis.coordinator",
        "AnalysisResult": ".analysis.coordinator",
        "FixMode": ".analysis.coordinator",
        "Advice": ".analysis.findings",
        "Change": ".analysis.findings",
        "Evidence": ".analysis.evidence",
        "Finding": ".analysis.findings",
        "FixOption": ".analysis.findings",
        "Location": ".analysis.findings",
        "ProofState": ".analysis.findings",
        "Severity": ".analysis.findings",
    }
    if name in _lazy:
        import importlib

        module = importlib.import_module(_lazy[name], __package__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Advice",
    # Re-exports (lazy loaded)
    "AnalysisConfig",
    "AnalysisContext",
    "AnalysisCoordinator",
    "AnalysisResult",
    "Change",
    "DependencyGraph",
    "Evidence",
    "FileInfo",
    "Finding",
    "FixMode",
    "FixOption",
    "FixResult",
    "LanguageStrategy",
    "Location",
    "Processor",
    "ProofState",
    "Severity",
]
