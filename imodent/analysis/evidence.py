"""Evidence records used to derive analysis findings.

Evidence is intentionally lower-level than a finding. An analyzer can emit
facts such as "this import binds Dict" or "Ruff reported F401"; a later
decision layer can combine those facts into a proof state.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .findings import Location

if TYPE_CHECKING:
    from .decisions import SubjectKey

_EVIDENCE_ID_COUNTER = itertools.count(1)


def _next_evidence_id() -> int:
    return next(_EVIDENCE_ID_COUNTER)


@dataclass(frozen=True)
class Evidence:
    """A replayable fact collected during analysis."""

    kind: str
    file: Path
    location: Location | None
    source: str = ""
    subject: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    polarity: str = "context"
    claim: str = ""
    strength: float = 0.5
    subject_key: SubjectKey | None = None
    id: int = field(default_factory=_next_evidence_id)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "file": str(self.file),
            "subject": self.subject,
            "polarity": self.polarity,
            "claim": self.claim,
            "strength": self.strength,
            "data": self.data,
        }
        if self.subject_key is not None:
            payload["subject_key"] = self.subject_key.to_dict()
        if self.location is not None:
            payload["location"] = {
                "line": self.location.line,
                "column": self.location.column,
                "end_line": self.location.end_line,
                "end_column": self.location.end_column,
            }
        return payload
