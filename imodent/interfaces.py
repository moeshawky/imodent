"""
Abstract Base Classes for the Indentation Fixer.

These define the contracts that all language strategies and processors must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple, Literal


@dataclass
class FixResult:
    """Result of a fix operation."""

    success: bool
    content: str
    errors: List[str]
    warnings: List[str]
    original_valid: bool
    fixed_valid: bool


class LanguageStrategy(ABC):
    """
    Abstract base class for language-specific indentation fixing.

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
    def extensions(self) -> List[str]:
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
    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        """
        Fix indentation issues in the content.

        Args:
            content: The source code content to fix.
            indent_size: Number of spaces per indentation level.

        Returns:
            FixResult with success status, fixed content, and any errors/warnings.
        """
        pass

    @abstractmethod
    def validate(self, content: str) -> Tuple[bool, Optional[str]]:
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
