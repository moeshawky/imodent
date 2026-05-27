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

# global monotonically-increasing counter seeded at 1.
# itertools.count is thread-safe for next() but NOT for iteration.
# Evidence.id assignment calls _next_evidence_id via field(default_factory=...),
# which calls next() once per instance — safe unless instantiated concurrently.
_EVIDENCE_ID_COUNTER = itertools.count(1)


def _next_evidence_id() -> int:
    """Returns the next integer from the global monotonic counter.

    Called by Evidence.__init__ via field(default_factory=...) — each new
    Evidence instance gets a unique incrementing id.
    """
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
    polarity: str = "context"  # "context"=neutral, "for"=supports, "against"=counters
    claim: str = ""
    strength: float = 0.5  # Weight of this evidence (0.0–1.0), default 0.5 neutral
    subject_key: SubjectKey | None = None  # TYPE_CHECKING only — not JSON-serializable
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
