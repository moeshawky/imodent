# Monotonically increasing unique identifier, assigned by _next_evidence_id().
# Global counter seeded at 1 — IDs increment across the entire process lifetime.
# Used for audit trails and evidence deduplication.
# Optional SubjectKey linking this evidence to a specific code subject
# (e.g., a particular import binding). Used by DecisionEngine to fuse
# evidence from multiple analyzers about the same subject.
# Not included in JSON serialization (SubjectKey is a dataclass, not JSON-serializable).
# Human-readable assertion about what this evidence proves.
# Displayed in reports and decision summaries.
# Example: 'import os is unused' or 'no clippy.toml found'.
# Weight of this evidence in decision fusion, range 0.0 to 1.0.
# 0.0 = negligible, 0.5 = neutral (default), 1.0 = conclusive.
# DecisionEngine multiplies strength into confidence scoring.
# Directional weight of this evidence toward a conclusion.
# "context" = neutral background fact (default).
# "for" = supports a proposed action or conclusion.
# "against" = counters a proposed action or conclusion.
# Category tag for this evidence record.
# Examples: "ruff_diagnostic", "import_binding", "lint_finding", "cargo_diagnostic", "clippy_lint".
# Used by DecisionEngine to filter and weight evidence by source type.
"""Evidence records used to derive analysis findings.

Evidence is intentionally lower-level than a finding. An analyzer can emit
facts such as "this import binds Dict" or "Ruff reported F401"; a later
decision layer can combine those facts into a proof state.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .decision_models import SubjectKey
    from .findings import Location

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
    strength: float = 0.5  # Weight of this evidence (0.0-1.0), default 0.5 neutral
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
