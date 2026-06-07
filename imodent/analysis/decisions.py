"""Decision layer facade.

Contract-first typed identity and confidence-weighted decision support.
This module has been modularized and now serves as a compatibility facade.
"""

from __future__ import annotations

from .decision_actions import _default_actions_for_issue_type  # noqa: F401
from .decision_confidence import _compute_confidence_label, _score_confidence  # noqa: F401
from .decision_engine import (
    DecisionEngine,
    _attach_evidence_by_polarity,
    _issue_type_from_finding,
)  # noqa: F401
from .decision_models import ActionOption, DecisionCandidate, SubjectKey
from .decision_policy import (
    _destructive_allowed,
    _has_suppression_markers,
    _requires_decision,
)  # noqa: F401
from .decision_proof import _resolve_proof_state  # noqa: F401
from .decision_subjects import (
    _subject_key_from_finding,
    subject_key_for_import,
    subject_key_for_lint,
)  # noqa: F401

__all__ = [
    "ActionOption",
    "DecisionCandidate",
    "DecisionEngine",
    "SubjectKey",
    "subject_key_for_import",
    "subject_key_for_lint",
]
