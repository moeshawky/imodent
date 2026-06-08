from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .decision_policy import (
    RUST_ADVISORY_FINDING_TYPES,
    RUST_ORACLE_FINDING_TYPES,
    STRONG_RUFF_CODES,
)

if TYPE_CHECKING:
    from .evidence import Evidence


def _compute_confidence_label(confidence: float) -> str:
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.50:
        return "medium"
    return "low"


def _score_confidence(rep, group: list, evidence_list: list[Evidence]) -> float:
    """Score confidence using transparent weighted rules (no ML).

    Rules per the Decision Architecture Plan:
    - F821 + no local def + no import: ~0.90
    - F401 normal module + no AST use: ~0.85
    - F401 in __init__.py + package-local + __all__: ~0.10
    - Try-block import + no use + no marker: ~0.65
    """
    strongest_lint = next(
        (f for f in group if getattr(f, "lint_code", None) in STRONG_RUFF_CODES),
        None,
    )
    scorer = strongest_lint or rep

    f_type = getattr(scorer, "type", "unknown")
    lint_code = getattr(scorer, "lint_code", None)
    file = getattr(scorer, "file", Path())
    data = getattr(scorer, "data", {}) or {}
    import_info = data.get("import_info") or {}
    intent = import_info.get("intent", "")

    # Fusion-aware: when scorer has no intent (e.g. lint finding fused with
    # import_intent finding), scan other group members for non-usage intent.
    if not intent or intent == "usage":
        for f in group:
            d = getattr(f, "data", {}) or {}
            info = d.get("import_info") or {}
            gi = info.get("intent", "")
            if gi and gi != "usage":
                intent = gi
                break

    # Strong Ruff signals
    if lint_code:
        if lint_code == "F821":
            return 0.90
        if lint_code == "F841":
            return 0.90
        if lint_code == "F401":
            # Defense-in-depth: evidence-driven public_api_reexport detection
            for ev in evidence_list:
                if (
                    ev.claim == "public_api_reexport" or ev.polarity == "context"
                ) and ev.strength < 0.50:
                    return 0.10
                if ev.kind == "ReExport" and ev.strength >= 0.60:
                    return 0.15
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

    # Rust findings
    if f_type == "rust_diagnostic":
        for ev in evidence_list:
            if ev.kind in ("CargoDiagnostic", "ClippyDiagnostic"):
                level = ev.data.get("level", "error")
                if level == "error":
                    return 0.90
                if level == "warning":
                    return 0.80
        return 0.80

    if f_type == "rust_broad_allow":
        return 0.75

    if f_type in RUST_ADVISORY_FINDING_TYPES:
        return 0.50

    if f_type in RUST_ORACLE_FINDING_TYPES:
        return 0.40

    return 0.50
