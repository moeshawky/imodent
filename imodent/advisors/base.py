"""Base classes for advisors."""

from abc import ABC, abstractmethod
from typing import List

from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, Advice


class Advisor(ABC):
    """Base class for advisory modules."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier."""
        ...

    @property
    def priority(self) -> int:
        """Advisory priority (higher = more important)."""
        return 5

    @abstractmethod
    def should_advise(self, findings: List[Finding], context: AnalysisContext) -> bool:
        """
        Check if advisor has relevant advice.

        Pre-conditions:
        - findings may be empty
        - context is populated

        Post-conditions:
        - Returns True if advisor can contribute
        """
        ...

    @abstractmethod
    def advise(self, findings: List[Finding], context: AnalysisContext) -> List[Advice]:
        """
        Generate advice based on findings.

        Pre-conditions:
        - should_advise returned True

        Post-conditions:
        - Returns non-empty list
        - Each advice has unique finding_ids
        """
        ...
