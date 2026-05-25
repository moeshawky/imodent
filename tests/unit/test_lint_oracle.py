"""Tests for the Ruff-backed lint oracle."""

from __future__ import annotations

from types import SimpleNamespace

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analyzers import lint as lint_module
from imodent.analyzers.lint import LintAnalyzer


def test_lint_oracle_handles_missing_ruff(monkeypatch, tmp_path):
    """Missing Ruff is reported as unavailable, not as a crash."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("x = 1\n")
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: None)

    findings = LintAnalyzer().analyze(context)

    assert len(findings) == 1
    assert findings[0].type == "lint_oracle_unavailable"
    assert findings[0].lint_source == "ruff"


def test_lint_oracle_ignores_no_python_files_when_ruff_missing(monkeypatch, tmp_path):
    """No Python inputs means no Ruff oracle call is needed."""
    file_path = tmp_path / "data.json"
    file_path.write_text('{"ok": true}\n')
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: None)

    assert LintAnalyzer().analyze(context) == []


def test_lint_oracle_handles_invalid_json(monkeypatch, tmp_path):
    """Malformed Ruff JSON becomes an oracle failure finding."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("x = 1\n")
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: ["ruff"])
    monkeypatch.setattr(
        lint_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="{not-json",
            stderr="",
        ),
    )

    findings = LintAnalyzer().analyze(context)

    assert len(findings) == 1
    assert findings[0].type == "lint_oracle_failed"
    assert "invalid JSON" in findings[0].message


def test_lint_oracle_records_ruff_diagnostic_evidence(monkeypatch, tmp_path):
    """Ruff diagnostics become findings plus replayable evidence records."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("def f():\n    unused = 1\n")
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
        project_root=tmp_path,
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: ["ruff"])
    monkeypatch.setattr(
        lint_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                '[{"filename": "'
                + str(file_path)
                + '", "code": "F841", "message": "Local variable `unused` is assigned to but never used",'
                + ' "location": {"row": 2, "column": 5}, "end_location": {"row": 2, "column": 11},'
                + ' "fix": null}]'
            ),
            stderr="",
        ),
    )

    findings = LintAnalyzer().analyze(context)

    assert len(findings) == 1
    assert findings[0].type == "lint"
    assert findings[0].lint_code == "F841"
    assert findings[0].data["proof_state"] == "PROVEN_UNUSED"
    assert len(context.evidence) == 1
    assert context.evidence[0].kind == "RuffDiagnostic"


def test_init_file_f401_is_public_api_review_not_proven_unused(monkeypatch, tmp_path):
    """Unused imports in __init__.py need API review rather than proven deletion."""
    file_path = tmp_path / "__init__.py"
    file_path.write_text("from .thing import Thing\n")
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
        project_root=tmp_path,
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: ["ruff"])
    monkeypatch.setattr(
        lint_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                '[{"filename": "'
                + str(file_path)
                + '", "code": "F401", "message": "`thing.Thing` imported but unused; consider adding to `__all__`",'
                + ' "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 25},'
                + ' "fix": null}]'
            ),
            stderr="",
        ),
    )

    findings = LintAnalyzer().analyze(context)

    assert len(findings) == 1
    assert findings[0].data["proof_state"] == "REVIEW_PUBLIC_API"


def test_lint_oracle_suppresses_package_init_reexport_f401(monkeypatch, tmp_path):
    """Ruff F401 in __init__.py records evidence; package-local re-export
    findings carry REVIEW_PUBLIC_API state instead of being hidden."""
    package = tmp_path / "pkg"
    package.mkdir()
    file_path = package / "__init__.py"
    file_path.write_text(
        "from pathlib import Path\n"
        "from pkg.public import Public\n"
    )
    context = AnalysisContext(
        files={file_path: FileInfo.from_path(file_path)},
        config=AnalysisConfig(check_imports=False, check_lint=True),
        project_root=tmp_path,
    )
    monkeypatch.setattr(lint_module, "_ruff_command_prefix", lambda: ["ruff"])
    monkeypatch.setattr(
        lint_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                '[{"filename": "'
                + str(file_path)
                + '", "code": "F401", "message": "`pathlib.Path` imported but unused",'
                + ' "location": {"row": 1, "column": 21}, "end_location": {"row": 1, "column": 25},'
                + ' "fix": null},'
                + '{"filename": "'
                + str(file_path)
                + '", "code": "F401", "message": "`pkg.public.Public` imported but unused",'
                + ' "location": {"row": 2, "column": 24}, "end_location": {"row": 2, "column": 30},'
                + ' "fix": null}]'
            ),
            stderr="",
        ),
    )

    findings = LintAnalyzer().analyze(context)

    # Both findings are recorded (no more pre-ledger suppression).
    assert len(findings) == 2
    pathlib_finding = next(f for f in findings if "pathlib" in f.message)
    pkg_finding = next(f for f in findings if "pkg.public" in f.message)
    assert pathlib_finding.data["proof_state"] == "REVIEW_PUBLIC_API"
    assert pkg_finding.data["proof_state"] == "REVIEW_PUBLIC_API"
    # Evidence context distinguishes the package-local re-export.
    assert len(pkg_finding.data["evidence"]) == 2  # primary + context
