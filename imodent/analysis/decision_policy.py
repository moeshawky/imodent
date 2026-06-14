from __future__ import annotations

from typing import TYPE_CHECKING

from .findings import ProofState

if TYPE_CHECKING:
    from .decision_models import DecisionCandidate

RUST_NON_DESTRUCTIVE_ISSUE_TYPES = frozenset(
    ["rust_diagnostic", "rust_advisory", "rust_oracle", "rust_unused_import"]
)

REVIEW_REQUIRED_PROOF_STATES = frozenset(
    [
        ProofState.REVIEW_REQUIRED.value,
        ProofState.CONFLICTING_EVIDENCE.value,
        ProofState.INSUFFICIENT_EVIDENCE.value,
        "REVIEW_PUBLIC_API",
    ]
)

SUPPRESSION_INTENTS = frozenset(["re_export", "registration", "side_effect"])

LOW_CONFIDENCE_LABELS = frozenset(["low", "medium"])

STRONG_RUFF_CODES = frozenset(["F821", "F841", "F401"])

RUST_ADVISORY_FINDING_TYPES = frozenset(
    [
        "rust_lint_policy_missing",
        "rust_clippy_config_missing",
        "rust_rustfmt_config_missing",
        "rust_residue_marker",
        "rust_project_unmanaged",
    ]
)

RUST_ORACLE_FINDING_TYPES = frozenset(
    [
        "rust_oracle_unavailable",
        "rust_oracle_failed",
    ]
)


def _has_suppression_markers(finding) -> bool:
    data = getattr(finding, "data", {}) or {}
    import_info = data.get("import_info") or {}
    intent = import_info.get("intent", "")
    return intent in SUPPRESSION_INTENTS


def _destructive_allowed(candidate: DecisionCandidate, group: list) -> bool:
    """Destructive edits require both high confidence AND explicit safety."""
    # Rust findings are never destructive
    if candidate.issue_type in RUST_NON_DESTRUCTIVE_ISSUE_TYPES:
        return False

    if candidate.confidence < 0.80:
        return False

    if candidate.proof_state in REVIEW_REQUIRED_PROOF_STATES:
        return False

    if any(_has_suppression_markers(finding) for finding in group):
        return False

    if candidate.issue_type == "duplicate_import" and any(
        getattr(f, "auto_fix_safe", False) for f in group
    ):
        return True

    if candidate.issue_type == "unused_import":
        for finding in group:
            import_info = getattr(finding, "data", {}).get("import_info", {})
            has_import_info = bool(import_info.get("name") or import_info.get("module"))
            if has_import_info and not _has_suppression_markers(finding):
                return True

    # undefined_api (F821): permitted when backed by Ruff oracle evidence
    # and confidence is high.  The fixer adds an import, which is not
    # destructive — it extends the file.
    if candidate.issue_type == "undefined_api":
        for finding in group:
            if getattr(finding, "lint_code", None) == "F821":
                return True

    return False


def _requires_decision(candidate: DecisionCandidate, group: list) -> bool:
    """Ambiguous candidates require user/agent decision."""
    if candidate.confidence_label in LOW_CONFIDENCE_LABELS:
        return True

    proof_state = candidate.proof_state
    return proof_state in REVIEW_REQUIRED_PROOF_STATES
