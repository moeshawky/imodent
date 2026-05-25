"""Decision layer: SubjectKey, DecisionCandidate, ActionOption, DecisionEngine.

Contract-first typed identity and confidence-weighted decision support.
Evidence is collected by analyzers; the decision engine fuses observations
into candidates with confidence, proof state, and action options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence import Evidence, _next_evidence_id  # noqa: F401
from .findings import Location, ProofState


# ---------------------------------------------------------------------------
#  SubjectKey  —  stable identity for the thing being judged
# ---------------------------------------------------------------------------


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


def subject_key_for_import(
    file: Path,
    module: str | None,
    name: str | None,
    alias: str | None,
    scope: str = "module",
) -> SubjectKey:
    """Convenience factory for import-binding subject keys.

    Normalizes bare imports: when only *name* is known (e.g. from a Ruff
    F401 message) and it has no dot, treat it as a bare ``import X`` so that
    it matches the AST analyzer's module-centric representation.
    """
    # Normalize: bare import when name is known but module is not
    if module is None and name is not None and "." not in name:
        module, name = name, None
    bound_name = alias or name or (module.split(".")[0] if module else None)
    return SubjectKey(
        kind="import",
        file=file,
        scope=scope,
        module=module,
        name=name,
        alias=alias,
        bound_name=bound_name,
        origin=f"{module}.{name}" if module and name else module or None,
    )


def subject_key_for_lint(
    file: Path,
    code: str,
    module: str | None = None,
    name: str | None = None,
    scope: str = "module",
) -> SubjectKey:
    """Convenience factory for lint-diagnostic subject keys."""
    return SubjectKey(
        kind="lint",
        file=file,
        scope=scope,
        module=module,
        name=name or code,
        alias=None,
        bound_name=name,
        origin=None,
    )


# ---------------------------------------------------------------------------
#  ActionOption  —  possible action for a decision candidate
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
#  DecisionCandidate  —  actionable / reviewable interpretation of evidence
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
#  Confidence helpers  —  transparent weighted-rule scoring
# ---------------------------------------------------------------------------


def _compute_confidence_label(confidence: float) -> str:
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.50:
        return "medium"
    return "low"


def _default_actions_for_issue_type(
    issue_type: str,
) -> list[ActionOption]:
    """Return sensible defaults for a given issue type."""
    if issue_type == "unused_import":
        return [
            ActionOption(
                id="delete",
                label="Remove import",
                description="Remove the unused import statement",
                destructive=True,
                safe_auto=False,
                requires_decision=True,
            ),
            ActionOption(
                id="keep",
                label="Keep import",
                description="Preserve the import (typing, re-export, side-effect)",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
            ActionOption(
                id="investigate",
                label="Investigate",
                description="Search codebase for actual usage before deciding",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
        ]
    if issue_type == "undefined_api":
        return [
            ActionOption(
                id="implement",
                label="Implement or import",
                description="Define the missing symbol or add the import",
                destructive=False,
                safe_auto=False,
                requires_decision=True,
            ),
            ActionOption(
                id="quarantine",
                label="Quarantine",
                description="Mark as known-hallucinated, exclude from validation",
                destructive=False,
                safe_auto=False,
                requires_decision=True,
            ),
        ]
    if issue_type in ("duplicate_import",):
        return [
            ActionOption(
                id="remove",
                label="Remove duplicate",
                description="Remove the duplicate import keeping the first occurrence",
                destructive=True,
                safe_auto=False,
                requires_decision=False,
            ),
        ]
    return [
        ActionOption(
            id="review",
            label="Review manually",
            description="No automated action available; human review required",
            destructive=False,
            safe_auto=False,
            requires_decision=True,
        ),
    ]


# ---------------------------------------------------------------------------
#  DecisionEngine  —  fuse evidence into decision candidates
# ---------------------------------------------------------------------------


class DecisionEngine:
    """Fuse findings and evidence into weighted decision candidates."""

    @staticmethod
    def build_candidates(
        findings: list,
        evidence_list: list[Evidence] | None = None,
    ) -> list[DecisionCandidate]:
        """Build decision candidates from findings and evidence."""
        evidence_list = evidence_list or []
        evidence_index: dict[int, Evidence] = {
            e.id: e for e in evidence_list if hasattr(e, "id")
        }

        candidates: list[DecisionCandidate] = []
        grouped: dict[tuple, list] = {}

        # Group findings by subject key / binding key
        for f in findings:
            sk = _subject_key_from_finding(f)
            key = sk.binding_key if sk else (f.file.resolve(), f.type, f.id)
            grouped.setdefault(key, []).append(f)

        for key, group in grouped.items():
            rep = group[0]
            sk = _subject_key_from_finding(rep)
            if sk is None:
                continue

            issue_type = _issue_type_from_finding(rep)
            candidate = DecisionCandidate(
                issue_type=issue_type,
                subject_key=sk,
                finding_ids=[getattr(f, "id", "") for f in group],
            )

            # Attach evidence: collect from finding data AND rehydrate from evidence_index
            for f in group:
                f_evidence = f.data.get("evidence") if hasattr(f, "data") else []
                for ev_dict in f_evidence if isinstance(f_evidence, list) else [f_evidence]:
                    if isinstance(ev_dict, Evidence):
                        candidate.evidence_ids.append(ev_dict.id)
                        _attach_evidence_by_polarity(candidate, ev_dict)
                    elif isinstance(ev_dict, dict) and "id" in ev_dict:
                        candidate.evidence_ids.append(ev_dict["id"])
                        # Rehydrate from evidence_index
                        ev = evidence_index.get(ev_dict["id"])
                        if ev is not None:
                            _attach_evidence_by_polarity(candidate, ev)

            # Score confidence
            candidate.confidence = _score_confidence(rep, group, evidence_list)
            candidate.confidence_label = _compute_confidence_label(candidate.confidence)
            candidate.proof_state = _resolve_proof_state(rep, group)

            # Determine actions and safety
            candidate.suggested_actions = _default_actions_for_issue_type(issue_type)
            candidate.destructive_allowed = _destructive_allowed(
                candidate, rep, group
            )
            candidate.requires_user_decision = _requires_decision(
                candidate, rep, group
            )

            candidates.append(candidate)

        return candidates


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------


def _attach_evidence_by_polarity(
    candidate: DecisionCandidate, evidence: Evidence
) -> None:
    """Attach evidence to candidate based on its polarity."""
    if evidence.polarity == "opposes":
        candidate.evidence_against.append(evidence)
    else:
        # "supports" and "context" both go to evidence_for with polarity preserved
        candidate.evidence_for.append(evidence)


def _subject_key_from_finding(finding) -> SubjectKey | None:
    """Extract a SubjectKey from a Finding using its data fields.

    For Ruff F401 diagnostics, normalizes to import-style key so that
    the subject identity matches what the local analyzer uses.
    """
    file = getattr(finding, "file", Path("."))
    f_type = getattr(finding, "type", "unknown")
    lint_code = getattr(finding, "lint_code", None)
    lint_source = getattr(finding, "lint_source", None)
    import_module = getattr(finding, "import_module", None)
    import_name = getattr(finding, "import_name", None)
    data = getattr(finding, "data", {}) or {}
    import_info = data.get("import_info") or {}

    if lint_source == "ruff" and lint_code == "F401":
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=None,
        )

    if lint_source == "ruff" and lint_code:
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        return subject_key_for_lint(
            file=file,
            code=lint_code,
            module=module,
            name=name,
        )

    if f_type in ("unused_import", "unused_import_file", "import_intent"):
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        alias = import_info.get("alias")
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=alias,
        )

    if f_type == "duplicate_import":
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        alias = import_info.get("alias")
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=alias,
        )

    # Fallback
    return SubjectKey(
        kind="unknown",
        file=file,
        scope="unknown",
        name=getattr(finding, "message", None),
    )


def _issue_type_from_finding(finding) -> str:
    """Map finding type to issue_type for DecisionCandidate."""
    type_map = {
        "lint": "lint",
        "unused_import": "unused_import",
        "unused_import_file": "unused_import",
        "import_intent": "unused_import",
        "duplicate_import": "duplicate_import",
        "undefined_api": "undefined_api",
        "declared_behavior_unwired": "declared_behavior_unwired",
    }
    f_type = getattr(finding, "type", "unknown")
    # Ruff F401 is an unused import, not generic lint
    if f_type == "lint" and getattr(finding, "lint_code", None) == "F401":
        return "unused_import"
    return type_map.get(f_type, "unknown")


def _resolve_proof_state(finding_or_rep, group: list) -> str:
    """Resolve the best proof state from a group of findings."""
    rep = finding_or_rep
    states = [
        getattr(f, "proof_state", ProofState.RAW.value) for f in group
    ]
    states = [s for s in states if s]

    # If any Ruff finding gives an externally verified state, prefer it
    for f in group:
        if getattr(f, "lint_source", None) == "ruff":
            ps = getattr(f, "proof_state", None) or f.data.get("proof_state", "")
            if ps in ("PROVEN_UNUSED", "EXTERNALLY_VERIFIED"):
                return ps

    if states:
        return states[0]
    return getattr(rep, "proof_state", ProofState.RAW.value) or ProofState.RAW.value


def _score_confidence(
    rep, group: list, evidence_list: list[Evidence]
) -> float:
    """Score confidence using transparent weighted rules (no ML).

    Rules per the Decision Architecture Plan:
    - F821 + no local def + no import: ~0.90
    - F401 normal module + no AST use: ~0.85
    - F401 in __init__.py + package-local + __all__: ~0.10
    - Try-block import + no use + no marker: ~0.65
    """
    f_type = getattr(rep, "type", "unknown")
    lint_code = getattr(rep, "lint_code", None)
    file = getattr(rep, "file", Path("."))
    data = getattr(rep, "data", {}) or {}
    import_info = data.get("import_info") or {}
    intent = import_info.get("intent", "")

    # Strong Ruff signals
    if lint_code:
        if lint_code == "F821":
            return 0.90
        if lint_code == "F841":
            return 0.90
        if lint_code == "F401":
            # Check if it's a package-local __init__.py re-export
            if file.name == "__init__.py" and intent == "re_export":
                return 0.10
            return 0.85

    # AST-based unused import
    if f_type in ("unused_import", "unused_import_file"):
        if intent == "side_effect":
            return 0.30
        if intent == "re_export":
            return 0.10
        if intent == "typing":
            return 0.40
        return 0.75

    if f_type == "import_intent":
        return 0.30

    if f_type == "duplicate_import":
        return 0.95

    return 0.50


def _destructive_allowed(candidate: DecisionCandidate, rep, group: list) -> bool:
    """Destructive edits require both high confidence AND explicit safety."""
    if candidate.confidence < 0.80:
        return False

    f_type = getattr(rep, "type", "unknown")
    is_single_alias = rep.data.get("import_info", {}).get(
        "single_alias", False
    )
    auto_fix_safe = getattr(rep, "auto_fix_safe", False)

    if f_type == "duplicate_import" and auto_fix_safe:
        return True

    if f_type in ("unused_import", "unused_import_file"):
        if is_single_alias and not _has_suppression_markers(rep):
            return True

    return False


def _requires_decision(
    candidate: DecisionCandidate, rep, group: list
) -> bool:
    """Ambiguous candidates require user/agent decision."""
    if candidate.confidence_label in ("low", "medium"):
        return True

    proof_state = candidate.proof_state
    if proof_state in (
        ProofState.CONFLICTING_EVIDENCE.value,
        ProofState.REVIEW_REQUIRED.value,
        ProofState.INSUFFICIENT_EVIDENCE.value,
    ):
        return True

    return False


def _has_suppression_markers(finding) -> bool:
    data = getattr(finding, "data", {}) or {}
    import_info = data.get("import_info") or {}
    intent = import_info.get("intent", "")
    return intent in ("re_export", "registration", "side_effect")
