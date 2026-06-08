from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .findings import Location, ProofState

if TYPE_CHECKING:
    from pathlib import Path

    from .evidence import Evidence


@dataclass(frozen=True)
class SubjectKey:
    """Stable semantic identity for the thing being judged.

    Line number is evidence metadata, not identity.  The same semantic
    subject (e.g. an unused import of ``Dict`` from ``typing``) should carry
    the same SubjectKey regardless of whether Ruff or the AST analyzer
    reported it.
    """

    kind: str  # import, symbol, lint, api
    file: Path
    scope: str  # module, class, function, unknown
    module: str | None = None
    name: str | None = None
    alias: str | None = None
    bound_name: str | None = None
    origin: str | None = None

    @property
    def binding_key(self) -> tuple:
        """Returns a tuple suitable for deduplication / identity checks.

        Alias is NOT included — it is local binding metadata, not semantic
        identity.  Two findings about ``from foo import Bar as X`` and
        ``from foo import Bar as Y`` are about the same import origin and
        should fuse.
        """
        return (
            self.file.resolve(),
            self.kind,
            self.module or "",
            self.name or "",
            self.scope,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": str(self.file),
            "scope": self.scope,
            "module": self.module,
            "name": self.name,
            "alias": self.alias,
            "bound_name": self.bound_name,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class ActionOption:
    """A possible action for a decision candidate."""

    id: str
    label: str
    description: str
    destructive: bool = False
    safe_auto: bool = False
    requires_decision: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "destructive": self.destructive,
            "safe_auto": self.safe_auto,
            "requires_decision": self.requires_decision,
        }


@dataclass
class DecisionCandidate:
    """Actionable interpretation of evidence.

    Confidence answers "how likely is this diagnosis?"
    Destructive safety answers "is this edit allowed?"
    High diagnosis confidence does NOT imply destructive safety.
    """

    issue_type: str
    subject_key: SubjectKey
    finding_ids: list[str] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)
    confidence: float = 0.0
    confidence_label: str = "low"
    proof_state: str = ProofState.RAW.value
    evidence_for: list[Evidence] = field(default_factory=list)
    evidence_against: list[Evidence] = field(default_factory=list)
    suggested_actions: list[ActionOption] = field(default_factory=list)
    destructive_allowed: bool = False
    requires_user_decision: bool = False
    ruff_fix_applicability: str | None = None

    @property
    def relative_path(self) -> str:
        """Relative path for display (best-effort)."""
        return str(self.subject_key.file)

    @property
    def location(self) -> Location | None:
        """Best-guess location from evidence."""
        for ev in self.evidence_for:
            if ev.location is not None:
                return ev.location
        for ev in self.evidence_against:
            if ev.location is not None:
                return ev.location
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "subject_key": self.subject_key.to_dict(),
            "finding_ids": self.finding_ids,
            "evidence_ids": self.evidence_ids,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "proof_state": self.proof_state,
            "evidence_for": [e.to_dict() for e in self.evidence_for],
            "evidence_against": [e.to_dict() for e in self.evidence_against],
            "suggested_actions": [a.to_dict() for a in self.suggested_actions],
            "destructive_allowed": self.destructive_allowed,
            "requires_user_decision": self.requires_user_decision,
        }
