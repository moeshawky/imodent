from __future__ import annotations

from .findings import ProofState


def _resolve_proof_state(finding_or_rep, group: list) -> str:
    """Resolve the best proof state from a group of findings."""
    rep = finding_or_rep
    states = [getattr(f, "proof_state", ProofState.RAW.value) for f in group]
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
