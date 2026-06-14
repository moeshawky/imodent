from __future__ import annotations

from .decision_actions import _default_actions_for_issue_type
from .decision_confidence import _compute_confidence_label, _score_confidence
from .decision_models import DecisionCandidate
from .decision_policy import _destructive_allowed, _requires_decision
from .decision_proof import _resolve_proof_state
from .decision_subjects import _subject_key_from_finding
from .evidence import Evidence


def _attach_evidence_by_polarity(
    candidate: DecisionCandidate, evidence: Evidence
) -> None:
    """Attach evidence to candidate based on its polarity."""
    if evidence.polarity == "opposes":
        candidate.evidence_against.append(evidence)
    else:
        # "supports" and "context" both go to evidence_for with polarity preserved
        candidate.evidence_for.append(evidence)


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
        "rust_diagnostic": "rust_diagnostic",
        "rust_lint_policy_missing": "rust_advisory",
        "rust_clippy_config_missing": "rust_advisory",
        "rust_rustfmt_config_missing": "rust_advisory",
        "rust_broad_allow": "rust_advisory",
        "rust_residue_marker": "rust_advisory",
        "rust_project_unmanaged": "rust_advisory",
        "rust_oracle_unavailable": "rust_oracle",
        "rust_oracle_failed": "rust_oracle",
    }
    f_type = getattr(finding, "type", "unknown")
    # Ruff F401 is an unused import, not generic lint
    if f_type == "lint" and getattr(finding, "lint_code", None) == "F401":
        return "unused_import"
    # Ruff F821 is an undefined name — route to undefined_api
    if f_type == "lint" and getattr(finding, "lint_code", None) == "F821":
        return "undefined_api"
    # Clippy unused_imports is the Rust equivalent of F401
    if (
        f_type == "rust_diagnostic"
        and getattr(finding, "lint_code", None) == "unused_imports"
    ):
        return "rust_unused_import"
    return type_map.get(f_type, "unknown")


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

        for _key, group in grouped.items():
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
                for ev_dict in (
                    f_evidence if isinstance(f_evidence, list) else [f_evidence]
                ):
                    if isinstance(ev_dict, Evidence):
                        candidate.evidence_ids.append(ev_dict.id)
                        _attach_evidence_by_polarity(candidate, ev_dict)
                    elif isinstance(ev_dict, dict) and "id" in ev_dict:
                        candidate.evidence_ids.append(ev_dict["id"])
                        # Rehydrate from evidence_index
                        ev = evidence_index.get(ev_dict["id"])
                        if ev is not None:
                            _attach_evidence_by_polarity(candidate, ev)

            # Extract Ruff fix applicability from evidence
            for ev in candidate.evidence_for + candidate.evidence_against:
                if ev.kind == "RuffDiagnostic" and ev.data.get("fix"):
                    applicability = ev.data["fix"].get("applicability")
                    if applicability:
                        candidate.ruff_fix_applicability = applicability
                        break

            # Score confidence
            candidate.confidence = _score_confidence(
                rep,
                group,
                candidate.evidence_for + candidate.evidence_against,
            )
            candidate.confidence_label = _compute_confidence_label(candidate.confidence)
            candidate.proof_state = _resolve_proof_state(rep, group)

            # Determine actions and safety
            candidate.suggested_actions = _default_actions_for_issue_type(issue_type)
            candidate.destructive_allowed = _destructive_allowed(candidate, group)
            candidate.requires_user_decision = _requires_decision(candidate, group)

            candidates.append(candidate)

        return candidates
