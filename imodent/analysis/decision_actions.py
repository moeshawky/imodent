from __future__ import annotations

from .decision_models import ActionOption


def _default_actions_for_issue_type(
    issue_type: str,
) -> list[ActionOption]:
    """Return sensible defaults for a given issue type."""
    if issue_type == "unused_import":
        return [
            ActionOption(
                id="investigate",
                label="Investigate / wire usage",
                description="Search codebase for actual usage before deciding",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
            ActionOption(
                id="keep",
                label="Keep import",
                description="Preserve the import (typing, re-export, side-effect, future wiring)",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
            ActionOption(
                id="delete",
                label="Remove import",
                description="Remove the unused import statement",
                destructive=True,
                safe_auto=False,
                requires_decision=True,
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
                id="investigate",
                label="Investigate duplicates",
                description="Review duplicate imports before removing",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
            ActionOption(
                id="remove",
                label="Remove duplicate",
                description="Remove the duplicate import keeping the first occurrence",
                destructive=True,
                safe_auto=False,
                requires_decision=False,
            ),
        ]
    if issue_type == "rust_advisory":
        return [
            ActionOption(
                id="review_policy",
                label="Review Rust policy",
                description="Decide Cargo/Clippy/Rustfmt policy before changing code",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
        ]
    if issue_type == "rust_diagnostic":
        return [
            ActionOption(
                id="fix_in_source",
                label="Fix in Rust source",
                description="Use Cargo/Clippy diagnostic as evidence; imodent does not edit Rust code",
                destructive=False,
                safe_auto=False,
                requires_decision=True,
            ),
        ]
    if issue_type == "rust_unused_import":
        return [
            ActionOption(
                id="investigate",
                label="Investigate unused `use`",
                description="Search codebase for actual usage before removing the use statement",
                destructive=False,
                safe_auto=True,
                requires_decision=False,
            ),
            ActionOption(
                id="fix_in_source",
                label="Remove `use` in Rust source",
                description="Remove the unused use statement; imodent does not edit Rust code",
                destructive=False,
                safe_auto=False,
                requires_decision=True,
            ),
        ]
    if issue_type == "rust_oracle":
        return [
            ActionOption(
                id="check_toolchain",
                label="Check Rust toolchain",
                description="Verify cargo/clippy is installed and project compiles",
                destructive=False,
                safe_auto=True,
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
