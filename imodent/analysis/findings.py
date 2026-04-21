"""Core data types for analysis findings."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import uuid


class Severity(Enum):
    """Finding severity levels."""
    ERROR = "error"      # Must fix - code won't run
    WARNING = "warning"  # Should fix - potential bug
    INFO = "info"        # Know about - style/optimization
    HINT = "hint"        # Suggestion - optional improvement


@dataclass
class Location:
    """Location in a source file."""
    line: int
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    
    def __str__(self) -> str:
        if self.column is not None:
            return f"{self.line}:{self.column}"
        return str(self.line)


@dataclass
class Finding:
    """A single issue or observation from analysis."""
    id: str
    type: str
    severity: Severity
    file: Path
    location: Location | None
    message: str
    fixable: bool
    auto_fix_safe: bool
    data: dict[str, Any] = field(default_factory=dict)
    
    # Import-specific fields
    import_name: str | None = None
    import_module: str | None = None
    usage_count: int = 0
    usage_locations: list[Location] = field(default_factory=list)
    
    # Lint-specific fields
    lint_code: str | None = None
    lint_source: str | None = None
    
    @classmethod
    def create(
        cls,
        type: str,
        severity: Severity,
        file: Path,
        message: str,
        location: Location | None = None,
        fixable: bool = True,
        auto_fix_safe: bool = False,
        **kwargs
    ) -> "Finding":
        """Factory method to create a finding with auto-generated ID."""
        return cls(
            id=str(uuid.uuid4())[:8],
            type=type,
            severity=severity,
            file=file,
            location=location,
            message=message,
            fixable=fixable,
            auto_fix_safe=auto_fix_safe,
            **kwargs
        )


@dataclass
class FixOption:
    """An option for fixing a finding."""
    id: str
    label: str
    description: str
    action: str  # 'delete', 'keep', 'investigate', 'use', 'custom'
    is_safe: bool
    preview: str | None = None
    requires_input: bool = False


@dataclass
class Change:
    """A single change made to content."""
    type: str  # 'add', 'remove', 'modify'
    location: Location
    old_text: str | None
    new_text: str | None
    reason: str


@dataclass
class Advice:
    """Advisory output for a finding or set of findings."""
    finding_ids: list[str]
    category: str
    summary: str
    explanation: str
    recommendation: str
    example: str | None = None
    impact: str = ""
    priority: int = 5
