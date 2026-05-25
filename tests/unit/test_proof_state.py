"""Tests for finding proof-state lifecycle."""

from pathlib import Path

from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analysis.findings import Finding, ProofState, Severity
from imodent.project.project_context import ProjectContext


def test_finding_proof_state_is_backward_compatible():
    """Findings can still be created without explicit proof state."""
    finding = Finding.create(
        type="example",
        severity=Severity.INFO,
        file=Path("sample.py"),
        message="example",
    )

    assert finding.proof_state is None


def test_coordinator_initializes_raw_proof_state(tmp_path):
    """Analyzer findings get an explicit RAW proof state by default."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("import os\n")

    project_context = ProjectContext.from_root(tmp_path)
    project_context.config.check_lint = False
    coordinator = AnalysisCoordinator(project_context=project_context)
    result = coordinator.analyze([file_path])

    assert result.findings
    assert all(f.proof_state == ProofState.RAW.value for f in result.findings)


def test_coordinator_preserves_oracle_proof_state(tmp_path):
    """Findings with proof state in their evidence data keep that state."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("def f():\n    unused = 1\n")

    project_context = ProjectContext.from_root(tmp_path)
    project_context.config.check_imports = False
    project_context.config.check_lint = True
    coordinator = AnalysisCoordinator(project_context=project_context)
    result = coordinator.analyze([file_path])

    f841 = next(f for f in result.findings if f.lint_code == "F841")
    assert f841.proof_state == ProofState.PROVEN_UNUSED.value


def test_applied_fix_marks_finding_accepted(tmp_path):
    """A successfully applied fix transitions the finding to ACCEPTED."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("import os\nimport os\n")

    project_context = ProjectContext.from_root(tmp_path)
    project_context.config.check_lint = False
    coordinator = AnalysisCoordinator(project_context=project_context)
    result = coordinator.analyze([file_path])
    duplicate = next(f for f in result.findings if f.type == "duplicate_import")

    coordinator.fix([duplicate], result.context, mode=FixMode.SAFE_AUTO)

    assert duplicate.proof_state == ProofState.ACCEPTED.value
