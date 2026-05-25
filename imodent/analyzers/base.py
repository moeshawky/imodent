"""Base classes for analyzers."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Set as typing_Set  # For type hints

from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding


class AnalyzerCapability(Enum):
    """What an analyzer can detect."""

    SYNTAX = "syntax"
    IMPORTS = "imports"
    LINT = "lint"
    TYPES = "types"
    STYLE = "style"
    SECURITY = "security"
    RESIDUE = "residue"


class Analyzer(ABC):
    """Base class for all analyzers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this analyzer."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> typing_Set[AnalyzerCapability]:
        """What this analyzer can detect."""
        ...

    @property
    def languages(self) -> typing_Set[str]:
        """Languages this analyzer handles. Empty = all languages."""
        return set()

    @property
    def requires_ast(self) -> bool:
        """Does this analyzer require parsed AST?"""
        return False

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> list[Finding]:
        """
        Analyze files in context.

        Pre-conditions:
        - context.files is populated
        - If requires_ast, context.files[*].ast is populated

        Post-conditions:
        - Returns list of Finding objects
        - Each finding has unique id
        - No side effects on context

        Error handling:
        - On error, return finding with severity=ERROR
        - Never raise exceptions for analysis failures
        """
        ...

    def can_analyze(self, file_info) -> bool:
        """Check if this analyzer can handle the file."""
        if not self.languages:
            return True
        return file_info.language in self.languages
