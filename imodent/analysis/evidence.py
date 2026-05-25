"""Evidence records used to derive analysis findings.

Evidence is intentionally lower-level than a finding. An analyzer can emit
facts such as "this import binds Dict" or "Ruff reported F401"; a later
decision layer can combine those facts into a proof state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .findings import Location


@dataclass(frozen=True)
class Evidence:
    """A replayable fact collected during analysis."""

    kind: str
    source: str
    file: Path
    location: Location | None
    subject: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for finding metadata."""
        return {
            "kind": self.kind,
            "source": self.source,
            "file": str(self.file),
            "location": str(self.location) if self.location else None,
            "subject": self.subject,
            "data": self.data,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload: dict[str, Any] = {
            "kind": self.kind,
            "source": self.source,
            "file": str(self.file),
            "subject": self.subject,
            "data": self.data,
        }
        if self.location is not None:
            payload["location"] = {
                "line": self.location.line,
                "column": self.location.column,
                "end_line": self.location.end_line,
                "end_column": self.location.end_column,
            }
        return payload
