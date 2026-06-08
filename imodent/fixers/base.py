"""Base classes for fixers."""

from abc import ABC, abstractmethod

from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, FixOption
from ..interfaces import FixResult


class Fixer(ABC):
    """Base class for all fixers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this fixer."""
        ...

    @property
    @abstractmethod
    def handles(self) -> set[str]:
        """Finding types this fixer can handle."""
        ...

    @abstractmethod
    def can_auto_fix(self, finding: Finding) -> bool:
        """
        Check if finding can be safely auto-fixed.

        Pre-conditions:
        - finding is not None

        Post-conditions:
        - Returns True if fix is safe without review
        - Never raises exceptions
        """
        ...

    @abstractmethod
    def get_options(
        self, finding: Finding, context: AnalysisContext
    ) -> list[FixOption]:
        """
        Get fix options for a finding.

        Pre-conditions:
        - finding is not None
        - finding.type in handles

        Post-conditions:
        - Returns at least one option
        - First option is safest/recommended
        - Each option has unique id
        """
        ...

    @abstractmethod
    def apply_fix(self, finding: Finding, option: FixOption, content: str) -> FixResult:
        """
        Apply fix to content.

        Pre-conditions:
        - finding is not None
        - option is from get_options for this finding
        - content is the file content

        Post-conditions:
        - Returns FixResult with success status
        - If success, content is valid
        - If failure, errors list explains why
        """
        ...

    def can_handle(self, finding: Finding) -> bool:
        """Check if this fixer handles the finding type."""
        return finding.type in self.handles
