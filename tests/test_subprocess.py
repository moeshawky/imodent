"""Tests for subprocess-dependent analyzers — LintAnalyzer and RustAnalyzer.

Includes cargo check / cargo clippy oracle path tests (mocked subprocess.run),
_read_file safety tests (symlink, FIFO, large file, normal read),
and edge-case coverage for _parse_cargo_json_output, workspace discovery,
and oracle integration.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.findings import Severity
from imodent.analyzers.base import AnalyzerCapability
from imodent.analyzers.lint import (
    LintAnalyzer,
    _claim_for_ruff_code,
    _first_backtick_value,
    _import_info_from_diagnostic,
    _is_package_init_reexport_f401,
    _is_single_alias_source_line,
    _proof_state_for_ruff_code,
    _ruff_command_prefix,
    _severity_for_ruff_code,
    _strength_for_ruff_code,
    _subject_key_from_diagnostic,
)
from imodent.analyzers.residue import ResidueAnalyzer
from imodent.analyzers.rust import (
    RustAnalyzer,
    _discover_cargo_roots,
    _parse_cargo_json_output,
    _read_file,
    _run_cargo_check,
    _run_cargo_clippy,
    _run_cargo_command,
)

# ---------------------------------------------------------------------------
# LintAnalyzer tests
# ---------------------------------------------------------------------------


def test_lint_analyzer_no_ruff(monkeypatch):
    """LintAnalyzer returns [] when ruff is not available on PATH."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    # Also prevent import of the ruff module
    monkeypatch.setattr("imodent.analyzers.lint.shutil.which", lambda _cmd: None)

    # Replace _ruff_command_prefix to simulate ruff not found
    # (both binary and module import fail)
    def _no_ruff():
        return None

    monkeypatch.setattr("imodent.analyzers.lint._ruff_command_prefix", _no_ruff)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=False)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)
    # use_ruff=False means no lint analysis — returns []
    assert findings == []


def test_lint_analyzer_ruff_unavailable_warning(monkeypatch):
    """LintAnalyzer returns lint_oracle_unavailable when use_ruff=True but ruff missing."""
    monkeypatch.setattr("imodent.analyzers.lint._ruff_command_prefix", lambda: None)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)
    # Should produce a lint_oracle_unavailable finding
    unavailable = [f for f in findings if f.type == "lint_oracle_unavailable"]
    assert len(unavailable) >= 1


def test_lint_analyzer_with_mock_ruff(monkeypatch):
    """LintAnalyzer with mocked ruff subprocess produces correct Finding objects."""
    # Mock the ruff command prefix pointing to a fake binary
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    # Mock subprocess.run to return a known JSON response
    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "/fake/test.py",
            "location": {"row": 1, "column": 8},
            "end_location": {"row": 1, "column": 10},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="import os\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    # Should have at least one lint finding
    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f401 = lint_findings[0]
    assert f401.lint_code == "F401"
    assert f401.lint_source == "ruff"


def test_lint_analyzer_no_python_files(monkeypatch):
    """LintAnalyzer returns [] when context has no Python files."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    analyzer = LintAnalyzer()
    json_file = Path("/fake/data.json")
    file_info = FileInfo(
        path=json_file,
        content='{"a": 1}',
        language="json",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={json_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)
    assert findings == []


# ---------------------------------------------------------------------------
# RustAnalyzer tests
# ---------------------------------------------------------------------------


def test_rust_analyzer_no_cargo(tmp_path):
    """RustAnalyzer with no Cargo.toml returns empty (graceful degradation)."""
    analyzer = RustAnalyzer()
    rs_file = tmp_path / "main.rs"
    rs_file.write_text('fn main() {\n    println!("hello");\n}\n')
    file_info = FileInfo(
        path=rs_file,
        content=rs_file.read_text(),
        language="rust",
    )
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(
        files={rs_file: file_info},
        config=config,
        project_root=tmp_path,
    )
    findings = analyzer.analyze(context)
    # Should run without crashing; might produce config-missing advisories
    assert isinstance(findings, list)


def test_rust_analyzer_not_enabled(tmp_path):
    """RustAnalyzer returns [] when check_rust is False."""
    analyzer = RustAnalyzer()
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")
    file_info = FileInfo(
        path=rs_file,
        content=rs_file.read_text(),
        language="rust",
    )
    config = AnalysisConfig(check_rust=False)
    context = AnalysisContext(
        files={rs_file: file_info},
        config=config,
        project_root=tmp_path,
    )
    findings = analyzer.analyze(context)
    assert findings == []


# ---------------------------------------------------------------------------
# _scan_cargo_root: config advisory tests (lines 183-492)
# ---------------------------------------------------------------------------


def _make_cargo_project(tmp_path, cargo_content, rs_content=None, rs_name="main.rs"):
    """Helper: create a minimal Cargo project in tmp_path.

    Returns (cargo_path, rs_path) — both Path objects.
    Creates tmp_path/src/ for the Rust source file.
    """
    cargo_path = tmp_path / "Cargo.toml"
    cargo_path.write_text(cargo_content)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    rs_path = None
    if rs_content is not None:
        rs_path = src_dir / rs_name
        rs_path.write_text(rs_content)
    return cargo_path, rs_path


def _build_context(
    tmp_path,
    cargo_content,
    rs_content=None,
    rs_name="main.rs",
    project_root=None,
    extra_files=None,
):
    """Helper: build AnalysisContext for a Cargo project.

    Creates Cargo.toml + optional .rs file on disk, builds FileInfo objects,
    and returns an AnalysisContext with check_rust=True.

    extra_files: optional list of (Path, str, str) tuples for additional
                 (file_path, content, language) entries.
    """
    cargo_path, rs_path = _make_cargo_project(
        tmp_path, cargo_content, rs_content, rs_name
    )
    files: dict[Path, FileInfo] = {}
    # Cargo.toml
    files[cargo_path] = FileInfo(
        path=cargo_path,
        content=cargo_content,
        language="toml",
    )
    # Rust source
    if rs_path is not None:
        files[rs_path] = FileInfo(
            path=rs_path,
            content=rs_content,
            language="rust",
        )
    # Extra files (e.g., clippy.toml, additional .rs files)
    if extra_files:
        for fpath, fcontent, flang in extra_files:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(fcontent)
            files[fpath] = FileInfo(
                path=fpath,
                content=fcontent,
                language=flang,
            )

    config = AnalysisConfig(check_rust=True)
    return AnalysisContext(
        files=files,
        config=config,
        project_root=project_root if project_root is not None else tmp_path,
    )


def _findings_by_type(findings, type_name):
    """Return all findings matching the given type string."""
    return [f for f in findings if f.type == type_name]


# ---------------------------------------------------------------------------
# Test 1: Cargo.toml without [lints] section
# ---------------------------------------------------------------------------


def test_rust_analyzer_with_cargo_toml_no_lints(tmp_path):
    """Cargo.toml missing [lints] → rust_lint_policy_missing finding generated."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n'
    rs_content = "fn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    lint_missing = _findings_by_type(findings, "rust_lint_policy_missing")
    assert len(lint_missing) == 1, (
        f"Expected 1 rust_lint_policy_missing, got {len(lint_missing)}: "
        f"{[f.type for f in findings]}"
    )
    assert lint_missing[0].severity == Severity.INFO
    assert lint_missing[0].fixable is False


# ---------------------------------------------------------------------------
# Test 2: clippy.toml present suppresses clippy config missing
# ---------------------------------------------------------------------------


def test_rust_analyzer_with_clippy_toml(tmp_path):
    """With clippy.toml present, no rust_clippy_config_missing finding."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n'
    rs_content = "fn main() {}\n"
    clippy_path = tmp_path / "clippy.toml"
    extra = [(clippy_path, "# clippy config\n", "toml")]
    context = _build_context(tmp_path, cargo_content, rs_content, extra_files=extra)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    clippy_missing = _findings_by_type(findings, "rust_clippy_config_missing")
    assert len(clippy_missing) == 0, (
        f"clippy.toml present but rust_clippy_config_missing still generated: "
        f"{clippy_missing}"
    )
    # Sanity: other config advisories still fire
    rustfmt_missing = _findings_by_type(findings, "rust_rustfmt_config_missing")
    assert len(rustfmt_missing) == 1, (
        "rust_rustfmt_config_missing should still fire independently of clippy.toml"
    )


# ---------------------------------------------------------------------------
# Test 3: #![allow(warnings)] in .rs file
# ---------------------------------------------------------------------------


def test_rust_analyzer_broad_allow_warnings(tmp_path):
    """#![allow(warnings)] in .rs file → rust_broad_allow finding."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = "#![allow(warnings)]\nfn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    broad = _findings_by_type(findings, "rust_broad_allow")
    assert len(broad) == 1, (
        f"Expected 1 rust_broad_allow, got {len(broad)}: {[f.type for f in findings]}"
    )
    assert broad[0].severity == Severity.WARNING
    assert broad[0].location is not None
    assert broad[0].location.line == 1
    # Verify evidence attached
    assert "evidence" in broad[0].data, (
        "rust_broad_allow finding should carry evidence in data"
    )


# ---------------------------------------------------------------------------
# Test 4: #![allow(clippy::all)] in .rs file
# ---------------------------------------------------------------------------


def test_rust_analyzer_broad_allow_clippy_all(tmp_path):
    """#![allow(clippy::all)] in .rs file → rust_broad_allow finding."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = "#![allow(clippy::all)]\nfn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    broad = _findings_by_type(findings, "rust_broad_allow")
    assert len(broad) == 1, (
        f"Expected 1 rust_broad_allow for clippy::all, got {len(broad)}"
    )
    assert broad[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Test 5: todo!() marker
# ---------------------------------------------------------------------------


def test_rust_analyzer_todo_marker(tmp_path):
    """todo!() in non-test .rs file → rust_residue_marker (INFO)."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = "fn main() {\n    todo!();\n}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    residue = _findings_by_type(findings, "rust_residue_marker")
    todos = [r for r in residue if "todo!" in r.message]
    assert len(todos) == 1, (
        f"Expected 1 todo marker finding, got {len(todos)} from {len(residue)} residue markers"
    )
    assert todos[0].severity == Severity.INFO
    assert todos[0].location is not None
    assert todos[0].location.line == 2


# ---------------------------------------------------------------------------
# Test 6: unimplemented!() marker
# ---------------------------------------------------------------------------


def test_rust_analyzer_unimplemented_marker(tmp_path):
    """unimplemented!() in non-test .rs file → rust_residue_marker."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = "fn main() {\n    unimplemented!();\n}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    residue = _findings_by_type(findings, "rust_residue_marker")
    um = [r for r in residue if "unimplemented!" in r.message]
    assert len(um) == 1, f"Expected 1 unimplemented marker finding, got {len(um)}"
    assert um[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Test 7: dbg!() marker (WARNING severity)
# ---------------------------------------------------------------------------


def test_rust_analyzer_dbg_marker(tmp_path):
    """dbg!() in non-test .rs file → rust_residue_marker with WARNING severity."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = 'fn main() {\n    dbg!("hello");\n}\n'
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    residue = _findings_by_type(findings, "rust_residue_marker")
    dbgs = [r for r in residue if "dbg!" in r.message]
    assert len(dbgs) == 1, f"Expected 1 dbg! marker finding, got {len(dbgs)}"
    assert dbgs[0].severity == Severity.WARNING, (
        f"dbg!() marker should be WARNING, got {dbgs[0].severity}"
    )


# ---------------------------------------------------------------------------
# Test 8: orphan .rs file (no Cargo.toml nearby)
# ---------------------------------------------------------------------------


def test_rust_analyzer_orphan_rs_no_cargo(tmp_path):
    """.rs file without Cargo.toml → rust_project_unmanaged finding."""
    analyzer = RustAnalyzer()
    rs_path = tmp_path / "orphan.rs"
    rs_content = "fn main() {}\n"
    rs_path.write_text(rs_content)
    file_info = FileInfo(
        path=rs_path,
        content=rs_content,
        language="rust",
    )
    config = AnalysisConfig(check_rust=True)
    # project_root is set but has no Cargo.toml — orphan should be detected
    context = AnalysisContext(
        files={rs_path: file_info},
        config=config,
        project_root=tmp_path,
    )
    findings = analyzer.analyze(context)

    unmanaged = _findings_by_type(findings, "rust_project_unmanaged")
    assert len(unmanaged) == 1, (
        f"Expected 1 rust_project_unmanaged for orphan .rs, got {len(unmanaged)}: "
        f"{[f.type for f in findings]}"
    )
    assert unmanaged[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Test 9: missing rustfmt.toml
# ---------------------------------------------------------------------------


def test_rust_analyzer_missing_rustfmt_toml(tmp_path):
    """Cargo project without rustfmt.toml → rust_rustfmt_config_missing."""
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n\n[lints]\nrust.missing_docs = "allow"\n'
    rs_content = "fn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    rustfmt_missing = _findings_by_type(findings, "rust_rustfmt_config_missing")
    assert len(rustfmt_missing) == 1, (
        f"Expected 1 rust_rustfmt_config_missing, got {len(rustfmt_missing)}"
    )
    assert rustfmt_missing[0].severity == Severity.HINT


# ---------------------------------------------------------------------------
# Test 10: Cargo.toml with [features] but no [lints]
# ---------------------------------------------------------------------------


def test_rust_analyzer_missing_config_with_features(tmp_path):
    """Cargo.toml has [features] but no [lints] → still rust_lint_policy_missing."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n[features]\ndefault = []\n'
    )
    rs_content = "fn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    lint_missing = _findings_by_type(findings, "rust_lint_policy_missing")
    assert len(lint_missing) == 1, (
        f"[features] present but [lints] absent — expected 1 rust_lint_policy_missing, "
        f"got {len(lint_missing)}"
    )
    assert lint_missing[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# LintAnalyzer — error paths (invalid JSON, non-list, error exit)
# ---------------------------------------------------------------------------


def test_lint_analyzer_ruff_invalid_json(monkeypatch):
    """LintAnalyzer returns lint_oracle_failed when ruff stdout is not valid JSON."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = "not valid json {{{"
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    failed = [f for f in findings if f.type == "lint_oracle_failed"]
    assert len(failed) == 1, (
        f"Expected 1 lint_oracle_failed, got {[f.type for f in findings]}"
    )
    assert "invalid json" in failed[0].message.lower()


def test_lint_analyzer_ruff_non_list_output(monkeypatch):
    """LintAnalyzer returns lint_oracle_failed when ruff returns JSON object (not list)."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = '{"message": "some error", "code": 1}'
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    failed = [f for f in findings if f.type == "lint_oracle_failed"]
    assert len(failed) == 1, (
        f"Expected 1 lint_oracle_failed, got {[f.type for f in findings]}"
    )
    assert "unexpected shape" in failed[0].message.lower()


def test_lint_analyzer_ruff_error_exit(monkeypatch):
    """LintAnalyzer returns lint_oracle_failed when ruff exits with returncode=2."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_completed = MagicMock()
    mock_completed.returncode = 2  # ruff lint oracle crash
    mock_completed.stdout = ""
    mock_completed.stderr = "error: ruff failed to parse configuration"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    failed = [f for f in findings if f.type == "lint_oracle_failed"]
    assert len(failed) == 1, (
        f"Expected 1 lint_oracle_failed, got {[f.type for f in findings]}"
    )
    assert "ruff" in failed[0].message.lower()


# ---------------------------------------------------------------------------
# LintAnalyzer — F541 severity mapping, F401 subject key mapping
# ---------------------------------------------------------------------------


def test_lint_analyzer_f541_to_hint(monkeypatch):
    """LintAnalyzer assigns Severity.HINT to Ruff F541 (f-string no placeholders)."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F541",
            "message": "f-string without any placeholders",
            "filename": "/fake/test.py",
            "location": {"row": 3, "column": 1},
            "end_location": {"row": 3, "column": 20},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = f'hello'\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f541 = lint_findings[0]
    assert f541.lint_code == "F541"
    assert f541.severity == Severity.HINT, f"Expected F541 → HINT, got {f541.severity}"


def test_lint_analyzer_f401_mapping(monkeypatch):
    """LintAnalyzer maps F401 → correct subject_key with module/name for fusion."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "/fake/test.py",
            "location": {"row": 1, "column": 8},
            "end_location": {"row": 1, "column": 10},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="import os\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f401 = lint_findings[0]
    assert f401.lint_code == "F401"

    # Verify import info was extracted
    import_info = f401.data.get("import_info", {})
    assert import_info.get("name") == "os" or import_info.get("module") == "os", (
        f"F401 should map name/module, got {import_info}"
    )
    assert import_info.get("source") == "ruff"

    # Verify evidence with subject_key was attached
    evidence_entries = f401.data.get("evidence", [])
    assert len(evidence_entries) >= 1
    first_ev = evidence_entries[0]
    assert first_ev.get("subject") == "F401"
    assert first_ev.get("claim") == "unused_import"


# ---------------------------------------------------------------------------
# _severity_for_ruff_code unit tests
# ---------------------------------------------------------------------------


def test_severity_for_ruff_code_error_codes():
    """E9/F8 codes map to ERROR severity."""
    assert _severity_for_ruff_code("E901") == Severity.ERROR
    # F821 starts with F8 → ERROR (even though it starts with F,
    # the F8 check runs first in the priority chain)
    assert _severity_for_ruff_code("F821") == Severity.ERROR


def test_severity_for_ruff_code_f541_is_hint():
    """F541 maps to HINT severity directly."""
    assert _severity_for_ruff_code("F541") == Severity.HINT


# ---------------------------------------------------------------------------
# ResidueAnalyzer tests
# ---------------------------------------------------------------------------


def test_residue_analyzer_empty_file(tmp_path):
    """ResidueAnalyzer returns [] when no residue patterns are present."""
    py_file = tmp_path / "clean.py"
    py_file.write_text("x = 1\n")

    config = AnalysisConfig(check_lint=True)
    context = AnalysisContext(
        files={py_file: FileInfo.from_path(py_file)},
        config=config,
        project_root=tmp_path,
    )
    analyzer = ResidueAnalyzer()
    findings = analyzer.analyze(context)
    assert findings == [], (
        f"Expected empty findings for clean file, got {[f.type for f in findings]}"
    )


def test_residue_detects_unwired_lint_plumbing(tmp_path):
    """ResidueAnalyzer detects declared-but-unwired lint behavior."""
    py_file = tmp_path / "residue_test.py"
    py_file.write_text("""
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lint", action="store_true", help="Enable linting")
    args = parser.parse_args()
    # analyze_lint is referenced in routing but never defined
    if args.lint:
        analyze_lint()  # This is the text match
    # check_lint appears in config but no executor
    config = {"check_lint": True}
""")

    config = AnalysisConfig(check_lint=True)
    context = AnalysisContext(
        files={py_file: FileInfo.from_path(py_file)},
        config=config,
        project_root=tmp_path,
    )
    analyzer = ResidueAnalyzer()
    findings = analyzer.analyze(context)

    unwired = [f for f in findings if f.type == "declared_behavior_unwired"]
    assert len(unwired) == 1, (
        f"Expected 1 declared_behavior_unwired, got {[f.type for f in findings]}"
    )
    assert unwired[0].data["cluster"] == "lint"
    assert not unwired[0].data["destructive_allowed"]
    assert "wire" in unwired[0].data["recommended_actions"]


def test_residue_detects_lint_when_executor_present(tmp_path):
    """ResidueAnalyzer does NOT emit unwired when LintAnalyzer class exists."""
    py_file = tmp_path / "has_executor.py"
    py_file.write_text("""
import argparse

class LintAnalyzer:
    def analyze_lint(self):
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lint", action="store_true")
""")

    config = AnalysisConfig(check_lint=True)
    context = AnalysisContext(
        files={py_file: FileInfo.from_path(py_file)},
        config=config,
        project_root=tmp_path,
    )
    analyzer = ResidueAnalyzer()
    findings = analyzer.analyze(context)

    unwired = [f for f in findings if f.type == "declared_behavior_unwired"]
    assert len(unwired) == 0, (
        f"Should not flag unwired when LintAnalyzer exists, got {len(unwired)}"
    )


def test_residue_detects_todo_marker(tmp_path):
    """ResidueAnalyzer detects # TODO markers via _find_regex."""
    py_file = tmp_path / "has_todo.py"
    py_file.write_text("x = 1\n# TODO: wire this properly\n")

    config = AnalysisConfig(check_lint=True)
    context = AnalysisContext(
        files={py_file: FileInfo.from_path(py_file)},
        config=config,
        project_root=tmp_path,
    )
    analyzer = ResidueAnalyzer()
    findings = analyzer.analyze(context)

    markers = [f for f in findings if f.type == "residue_marker"]
    assert len(markers) >= 1, (
        f"Expected residue_marker for TODO, got {[f.type for f in findings]}"
    )
    assert any("TODO" in m.message for m in markers)


# ---------------------------------------------------------------------------
# Cargo check oracle — mocked subprocess.run
# ---------------------------------------------------------------------------


# Shared helper: build a minimal cargo-compiler-message JSON line.
def _cargo_diag_line(
    code: str,
    level: str = "warning",
    message: str = "",
    file_name: str = "src/main.rs",
    line_start: int = 1,
    column_start: int = 5,
    line_end: int = 1,
    column_end: int = 30,
) -> str:
    """Return a single NDJSON line matching cargo --message-format=json output."""
    obj = {
        "reason": "compiler-message",
        "message": {
            "code": {"code": code} if code else None,
            "level": level,
            "message": message or f"{code} diagnostic",
            "spans": [
                {
                    "file_name": file_name,
                    "line_start": line_start,
                    "column_start": column_start,
                    "line_end": line_end,
                    "column_end": column_end,
                    "is_primary": True,
                }
            ],
        },
    }
    return json.dumps(obj) + "\n"


# Shared helper: build a mock subprocess.CompletedProcess for cargo output.
def _mock_cargo_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a MagicMock mimicking subprocess.CompletedProcess."""
    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = stderr
    return mock_result


def test_cargo_check_mocked(tmp_path, monkeypatch):
    """Mock subprocess.run to return cargo check JSON with diagnostics, verify findings."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    stdout = _cargo_diag_line(
        code="unused_imports",
        level="warning",
        message="unused import: `std::collections::HashMap`",
    )
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: _mock_cargo_run(stdout=stdout)
    )

    root = tmp_path / "cargo_proj"
    root.mkdir()
    (root / "Cargo.toml").write_text('[package]\nname = "test"\n')

    config = AnalysisConfig(check_rust=True, run_cargo_check=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_check(context, root)

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) >= 1, (
        f"Expected >=1 rust_diagnostic, got {[f.type for f in findings]}"
    )
    assert "unused_imports" in diags[0].message
    assert diags[0].severity == Severity.WARNING
    assert diags[0].lint_source == "cargo-check"
    # Evidence was attached
    evidence = diags[0].data.get("evidence", [])
    assert len(evidence) >= 1
    assert evidence[0]["kind"] == "CargoDiagnostic"


def test_cargo_check_no_cargo_binary(tmp_path, monkeypatch):
    """Monkeypatch shutil.which('cargo') → None, verify graceful handling."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    root = tmp_path / "cargo_proj"
    root.mkdir()

    config = AnalysisConfig(check_rust=True, run_cargo_check=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_check(context, root)

    assert len(findings) == 1
    assert findings[0].type == "rust_oracle_unavailable"
    assert "cargo is not available" in findings[0].message
    assert findings[0].severity == Severity.WARNING


def test_cargo_check_timeout(tmp_path, monkeypatch):
    """Mock subprocess.run to raise subprocess.TimeoutExpired, verify finding."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="cargo check", timeout=120)

    monkeypatch.setattr("subprocess.run", _raise_timeout)

    root = tmp_path / "cargo_proj"
    root.mkdir()

    config = AnalysisConfig(check_rust=True, run_cargo_check=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_check(context, root)

    assert len(findings) == 1
    assert findings[0].type == "rust_oracle_failed"
    assert "timed out" in findings[0].message
    assert findings[0].severity == Severity.WARNING


def test_cargo_check_oserror(tmp_path, monkeypatch):
    """Mock subprocess.run to raise OSError, verify error finding."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    def _raise_oserror(*args, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr("subprocess.run", _raise_oserror)

    root = tmp_path / "cargo_proj"
    root.mkdir()

    config = AnalysisConfig(check_rust=True, run_cargo_check=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_check(context, root)

    assert len(findings) == 1
    assert findings[0].type == "rust_oracle_failed"
    assert "could not run" in findings[0].message.lower()
    assert findings[0].severity == Severity.WARNING


def test_cargo_clippy_mocked(tmp_path, monkeypatch):
    """Mock subprocess.run for cargo clippy JSON with unused_imports."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    stdout = _cargo_diag_line(
        code="clippy::unused_imports",
        level="warning",
        message="unused import: `std::collections::HashMap`",
    )
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: _mock_cargo_run(stdout=stdout)
    )

    root = tmp_path / "cargo_proj"
    root.mkdir()
    (root / "Cargo.toml").write_text('[package]\nname = "test"\n')

    config = AnalysisConfig(check_rust=True, run_cargo_clippy=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_clippy(context, root)

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) >= 1, (
        f"Expected >=1 rust_diagnostic, got {[f.type for f in findings]}"
    )
    assert "unused_imports" in diags[0].message
    assert diags[0].lint_source == "cargo-clippy"
    # Clippy diagnostic types get specific evidence kind
    evidence = diags[0].data.get("evidence", [])
    assert len(evidence) >= 1
    assert evidence[0]["kind"] == "ClippyDiagnostic"


def test_cargo_clippy_high_signal(tmp_path, monkeypatch):
    """Verify dead_code→rust_dead_code, dbg_macro→rust_dbg_in_production mappings."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    # Two diagnostics in one output — dead_code (warning) and dbg_macro (error)
    stdout = _cargo_diag_line(
        code="dead_code",
        level="warning",
        message="struct `Unused` is never constructed",
    ) + _cargo_diag_line(
        code="clippy::dbg_macro",
        level="error",
        message="`dbg!` macro in production code",
        line_start=10,
    )
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: _mock_cargo_run(stdout=stdout)
    )

    root = tmp_path / "cargo_proj"
    root.mkdir()
    (root / "Cargo.toml").write_text('[package]\nname = "test"\n')

    config = AnalysisConfig(check_rust=True, run_cargo_clippy=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_clippy(context, root)

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 2, (
        f"Expected 2 diagnostics, got {len(diags)}: {[d.message for d in diags]}"
    )

    # Collect claims from evidence — 'subject' holds the diagnostic code
    claims: dict[str, str] = {}
    for d in diags:
        ev_list = d.data.get("evidence", [])
        if ev_list:
            code = ev_list[0].get("subject", "")
            claim = ev_list[0].get("claim", "")
            claims[code] = claim

    assert claims.get("dead_code") == "rust_dead_code", (
        f"dead_code claim mismatch: {claims}"
    )
    assert claims.get("clippy::dbg_macro") == "rust_dbg_in_production", (
        f"dbg_macro claim mismatch: {claims}"
    )

    # Verify severity mapping: error → ERROR, warning → WARNING
    severities = {d.lint_code: d.severity for d in diags if d.lint_code}
    assert severities.get("dead_code") == Severity.WARNING
    assert severities.get("clippy::dbg_macro") == Severity.ERROR


# ---------------------------------------------------------------------------
# _read_file safety tests
# ---------------------------------------------------------------------------


def test_read_file_symlink(tmp_path):
    """_read_file on symlink returns None."""
    target = tmp_path / "real.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    result = _read_file(link)
    assert result is None


@pytest.mark.skipif(
    sys.platform == "win32", reason="os.mkfifo not available on Windows"
)
def test_read_file_fifo(tmp_path):
    """_read_file on FIFO returns None."""
    fifo_path = tmp_path / "myfifo"
    os.mkfifo(str(fifo_path))

    result = _read_file(fifo_path)
    assert result is None


def test_read_file_large(tmp_path, monkeypatch):
    """_read_file on >10MB file returns None."""
    large_file = tmp_path / "large.txt"
    large_file.write_text("small content")

    # Replace Path.stat to return a stat_result with st_size > 10_000_000
    # while keeping st_mode as a regular file (S_IFREG).
    original_stat = Path.stat

    def _mock_stat(path_self, *, follow_symlinks=True):
        if path_self == large_file:
            return os.stat_result(
                (
                    0o100644,  # st_mode — regular file
                    0,  # st_ino
                    0,  # st_dev
                    0,  # st_nlink
                    0,  # st_uid
                    0,  # st_gid
                    11_000_000,  # st_size — larger than 10 MB threshold
                    0,  # st_atime
                    0,  # st_mtime
                    0,  # st_ctime
                )
            )
        return original_stat(path_self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _mock_stat)

    result = _read_file(large_file)
    assert result is None


def test_read_file_normal(tmp_path):
    """_read_file reads normal file content."""
    normal = tmp_path / "data.txt"
    content = "hello world\nline two\n"
    normal.write_text(content)

    result = _read_file(normal)
    assert result == content


# ============================================================================
# RustAnalyzer — coverage push tests (lines targeted in rust.py)
# ============================================================================


# --- 1. test_rust_analyzer_properties: name + capabilities + languages ---


def test_rust_analyzer_properties():
    """RustAnalyzer.name='rust', capabilities={LINT, STYLE}, languages={'rust','toml'}."""
    analyzer = RustAnalyzer()
    assert analyzer.name == "rust"
    assert AnalyzerCapability.LINT in analyzer.capabilities
    assert AnalyzerCapability.STYLE in analyzer.capabilities
    assert "rust" in analyzer.languages
    assert "toml" in analyzer.languages


# --- 2. test_discover_cargo_roots_workspace ---


def test_discover_cargo_roots_workspace(tmp_path):
    """_discover_cargo_roots finds workspace members from [workspace] in root Cargo.toml."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crate1", "crate2"]\n[package]\nname = "ws"\n'
    )
    # Member crate1 — has Cargo.toml + .rs file
    (root / "crate1").mkdir()
    (root / "crate1" / "Cargo.toml").write_text('[package]\nname = "crate1"\n')
    (root / "crate1" / "src").mkdir(parents=True)
    rs_file = root / "crate1" / "src" / "lib.rs"
    rs_file.write_text("pub fn foo() {}\n")
    # Member crate2 — only Cargo.toml, no .rs file in context
    (root / "crate2").mkdir()
    (root / "crate2" / "Cargo.toml").write_text('[package]\nname = "crate2"\n')

    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(
        files={
            rs_file: FileInfo(path=rs_file, content="pub fn foo() {}", language="rust"),
        },
        config=config,
        project_root=root,
    )

    roots = _discover_cargo_roots(context, rust_files=[rs_file], toml_files=[])
    # Sorted list: ws_root, ws_root/crate1, ws_root/crate2
    assert root in roots, f"Workspace root missing from {roots}"
    assert (root / "crate1") in roots, f"crate1 missing from {roots}"
    assert (root / "crate2") in roots, f"crate2 missing from {roots}"
    assert len(roots) == 3


# --- 3. test_scan_cargo_root_with_dead_code: [lints.rust] recognized as lint policy ---


def test_scan_cargo_root_with_dead_code(tmp_path):
    """Cargo.toml with [lints.rust] dead_code = 'allow' → no rust_lint_policy_missing."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints.rust]\ndead_code = "allow"\n'
    )
    rs_content = "fn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    lint_missing = _findings_by_type(findings, "rust_lint_policy_missing")
    assert len(lint_missing) == 0, (
        f"[lints.rust] should suppress rust_lint_policy_missing, "
        f"but got: {[(f.type, f.message) for f in lint_missing]}"
    )


# --- 4. test_run_cargo_command_with_check: command construction ---


def test_run_cargo_command_with_check(tmp_path, monkeypatch):
    """_run_cargo_command replaces command[0] with resolved cargo binary path."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    captured_cmd: list = []

    def _capture(*args, **kwargs):
        captured_cmd.append(list(args[0]))
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr("subprocess.run", _capture)

    root = tmp_path
    (root / "Cargo.toml").write_text('[package]\nname = "test"\n')
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    _run_cargo_command(
        context, root, "check", ["cargo", "check", "--message-format=json"]
    )

    assert len(captured_cmd) == 1
    assert captured_cmd[0][0] == "/usr/bin/cargo"
    assert "check" in captured_cmd[0]
    assert "--message-format=json" in captured_cmd[0]


# --- 5. test_parse_cargo_json_output_empty: empty NDJSON → [] ---


def test_parse_cargo_json_output_empty(tmp_path):
    """_parse_cargo_json_output on empty stdout returns empty list."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _parse_cargo_json_output(context, root, "", "check")
    assert findings == []


# --- 6. test_parse_cargo_json_output_malformed_line: non-JSON line skipped ---


def test_parse_cargo_json_output_malformed_line(tmp_path):
    """NDJSON with one non-JSON line → non-JSON line skipped, valid diags kept."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    stdout = (
        "   Compiling test v0.1.0\n"  # cargo progress — no leading {
        + _cargo_diag_line(code="dead_code", level="warning", message="dead code")
    )
    findings = _parse_cargo_json_output(context, root, stdout, "check")

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 1, f"Expected 1 diag after skipping non-JSON, got {len(diags)}"
    assert "dead_code" in diags[0].message


# --- 7. test_read_file_with_error: _read_file returns None on read failure ---


def test_read_file_with_error(tmp_path, monkeypatch):
    """_read_file returns None when read_text raises an exception."""
    path = tmp_path / "unreadable.txt"
    path.write_text("will fail to read")

    # Override read_text to simulate I/O failure
    def _fail_read_text(self, *args, **kwargs):
        raise OSError("Simulated I/O error")

    monkeypatch.setattr(Path, "read_text", _fail_read_text)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_fifo", lambda self: False)
    monkeypatch.setattr(Path, "is_socket", lambda self: False)

    result = _read_file(path)
    assert result is None


# --- 7a. test_read_file_logging: verify log emission for early-return and error paths ---


def test_read_file_debug_log_on_symlink(tmp_path, caplog):
    """_read_file emits DEBUG log when skipping a symlink."""
    import logging

    target = tmp_path / "real.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with caplog.at_level(logging.DEBUG):
        result = _read_file(link)

    assert result is None
    assert any("Skipping symlink" in rec.message for rec in caplog.records)


def test_read_file_debug_log_on_fifo(tmp_path, caplog):
    """_read_file emits DEBUG log when skipping a FIFO."""
    import logging
    import os as _os

    if sys.platform == "win32":
        pytest.skip("os.mkfifo not available on Windows")

    fifo_path = tmp_path / "myfifo"
    _os.mkfifo(str(fifo_path))

    with caplog.at_level(logging.DEBUG):
        result = _read_file(fifo_path)

    assert result is None
    assert any("Skipping FIFO" in rec.message for rec in caplog.records)


def test_read_file_debug_log_on_oversized(tmp_path, caplog, monkeypatch):
    """_read_file emits DEBUG log when skipping oversized (>10MB) file."""
    import logging

    large_file = tmp_path / "large.txt"
    large_file.write_text("small content")

    # Mock stat to return a large file size
    original_stat = Path.stat

    def _mock_stat(path_self, *, follow_symlinks=True):
        if path_self == large_file:
            return os.stat_result(
                (
                    0o100644,  # st_mode — regular file
                    0,
                    0,
                    0,
                    0,
                    0,
                    11_000_000,  # st_size — larger than 10 MB threshold
                    0,
                    0,
                    0,
                )
            )
        return original_stat(path_self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _mock_stat)

    with caplog.at_level(logging.DEBUG):
        result = _read_file(large_file)

    assert result is None
    assert any("Skipping oversized file" in rec.message for rec in caplog.records)


def test_read_file_warning_on_stat_failure(tmp_path, caplog, monkeypatch):
    """_read_file emits WARNING log when stat() fails."""
    import logging

    path = tmp_path / "no_stat.txt"
    path.write_text("content")

    # Disable type checks so we reach stat()
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_fifo", lambda self: False)
    monkeypatch.setattr(Path, "is_socket", lambda self: False)
    # Make stat fail
    monkeypatch.setattr(
        Path, "stat", lambda self, **kw: (_ for _ in ()).throw(OSError("stat denied"))
    )

    with caplog.at_level(logging.WARNING):
        result = _read_file(path)

    assert result is None
    assert any("Cannot stat" in rec.message for rec in caplog.records)


def test_read_file_warning_on_read_failure(tmp_path, caplog, monkeypatch):
    """_read_file emits WARNING log when read_text() fails with I/O error."""
    import logging

    path = tmp_path / "unreadable.txt"
    path.write_text("will fail")

    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_fifo", lambda self: False)
    monkeypatch.setattr(Path, "is_socket", lambda self: False)

    def _fail_read(self, *args, **kwargs):
        raise OSError("read denied")

    monkeypatch.setattr(Path, "read_text", _fail_read)

    with caplog.at_level(logging.WARNING):
        result = _read_file(path)

    assert result is None
    assert any("Cannot read" in rec.message for rec in caplog.records)


def test_read_file_warning_on_encoding_error(tmp_path, caplog, monkeypatch):
    """_read_file emits WARNING log when read_text() hits UnicodeDecodeError."""
    import logging

    path = tmp_path / "bad_encoding.txt"
    path.write_text("will fail")

    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_fifo", lambda self: False)
    monkeypatch.setattr(Path, "is_socket", lambda self: False)

    def _fail_decode(self, *args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    monkeypatch.setattr(Path, "read_text", _fail_decode)

    with caplog.at_level(logging.WARNING):
        result = _read_file(path)

    assert result is None
    assert any("Encoding error" in rec.message for rec in caplog.records)


# --- 8. test_cargo_oracle_integration: run_cargo=True triggers oracle via analyze() ---


def test_cargo_oracle_integration(tmp_path, monkeypatch):
    """run_cargo=True triggers cargo check + cargo clippy through analyze()."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n'
    rs_content = "fn main() {}\n"
    _cargo_path, rs_path = _make_cargo_project(tmp_path, cargo_content, rs_content)

    file_info = FileInfo(path=rs_path, content=rs_content, language="rust")
    config = AnalysisConfig(check_rust=True, run_cargo=True)
    context = AnalysisContext(
        files={rs_path: file_info},
        config=config,
        project_root=tmp_path,
    )

    # Capture subprocess.run calls to verify oracle was triggered
    run_calls: list = []

    def _capture(*args, **kwargs):
        run_calls.append(kwargs)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr("subprocess.run", _capture)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    assert isinstance(findings, list)
    # run_cargo=True should trigger both check AND clippy (2 subprocess calls)
    assert len(run_calls) >= 1, (
        f"Expected >=1 subprocess.run call, got {len(run_calls)}"
    )
    # Verify cwd is set to the project root for at least one call
    cwds = [call.get("cwd") for call in run_calls]
    assert str(tmp_path) in cwds, f"cwd={cwds} should contain {tmp_path}"


# ============================================================================
# LintAnalyzer — coverage push tests (pushing 73% → 90%+)
# ============================================================================


# --- Properties (lines 28, 32, 36 in lint.py) ---


def test_lint_analyzer_properties():
    """LintAnalyzer name, capabilities, and languages properties return expected values."""
    analyzer = LintAnalyzer()
    assert analyzer.name == "lint"
    assert analyzer.capabilities == {AnalyzerCapability.LINT}
    assert analyzer.languages == {"python"}


# --- OSError path (lines 96-97 in lint.py) ---


def test_lint_oracle_unavailable_oserror(monkeypatch):
    """LintAnalyzer returns lint_oracle_failed when subprocess.run raises OSError."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    def _raise_oserror(*args, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr("subprocess.run", _raise_oserror)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    failed = [f for f in findings if f.type == "lint_oracle_failed"]
    assert len(failed) == 1, (
        f"Expected 1 lint_oracle_failed for OSError, got {[f.type for f in findings]}"
    )
    assert "could not run" in failed[0].message.lower()
    assert failed[0].data.get("proof_state") == "INSUFFICIENT_EVIDENCE"


# --- _ruff_command_prefix import fallback (lines 261-264 in lint.py) ---


def test_ruff_command_prefix_import_fallback(monkeypatch):
    """_ruff_command_prefix falls back to [sys.executable, '-m', 'ruff'] when binary missing but module importable."""
    import sys as _sys

    # Binary not found on PATH
    monkeypatch.setattr("imodent.analyzers.lint.shutil.which", lambda _cmd: None)
    # ruff module IS importable in this environment — fallback path should succeed
    result = _ruff_command_prefix()
    assert result is not None, "Fallback should succeed when ruff module is importable"
    assert result[0] == _sys.executable
    assert result[1] == "-m"
    assert result[2] == "ruff"


def test_ruff_command_prefix_returns_none_when_all_fail(monkeypatch):
    """_ruff_command_prefix returns None when both binary and module import fail."""
    monkeypatch.setattr("imodent.analyzers.lint.shutil.which", lambda _cmd: None)
    # Patch builtins.__import__ to raise ImportError for 'ruff'
    original_import = __import__

    def _failing_import(name, *args, **kwargs):
        if name == "ruff":
            raise ImportError("No module named ruff")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _failing_import)

    # Need to reload the module so the patched __import__ takes effect,
    # since ruff may already be cached in sys.modules.
    import sys as _sys

    _ruff_mod = _sys.modules.pop("ruff", None)
    try:
        result = _ruff_command_prefix()
    finally:
        if _ruff_mod is not None:
            _sys.modules["ruff"] = _ruff_mod

    assert result is None


# --- _ruff_command_prefix binary found (line 259 in lint.py) ---


def test_ruff_command_prefix_binary_found(monkeypatch):
    """_ruff_command_prefix returns [executable] when ruff binary is on PATH."""
    monkeypatch.setattr(
        "imodent.analyzers.lint.shutil.which", lambda _cmd: "/usr/bin/ruff"
    )
    result = _ruff_command_prefix()
    assert result == ["/usr/bin/ruff"]


# --- Package init re-export (lines 211-225, 286, 297-300 in lint.py) ---


def test_package_init_reexport_f401(monkeypatch):
    """F401 in __init__.py with package-local re-export → context evidence with public_api_reexport."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`mypackage.submodule` imported but unused",
            "filename": "/fake/mypackage/__init__.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 30},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    init_file = Path("/fake/mypackage/__init__.py")
    file_info = FileInfo(
        path=init_file,
        content="from mypackage.submodule import something\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={init_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f401 = lint_findings[0]
    assert f401.lint_code == "F401"

    # proof_state should be REVIEW_PUBLIC_API for re-exports
    assert f401.data.get("proof_state") == "REVIEW_PUBLIC_API"

    # At least two evidence entries: primary + context
    evidence_entries = f401.data.get("evidence", [])
    assert len(evidence_entries) >= 2, (
        f"Expected >=2 evidence entries for re-export, got {len(evidence_entries)}"
    )

    # Primary evidence
    primary = [e for e in evidence_entries if e.get("polarity") == "supports"]
    assert len(primary) >= 1
    assert primary[0].get("claim") == "unused_import"

    # Context evidence with public_api_reexport
    context_ev = [
        e for e in evidence_entries if e.get("claim") == "public_api_reexport"
    ]
    assert len(context_ev) == 1, (
        f"Expected 1 context evidence with public_api_reexport, got {context_ev}"
    )
    assert context_ev[0].get("polarity") == "context"
    assert context_ev[0].get("strength") == 0.3


# --- _is_package_init_reexport_f401 unit tests ---


def test_is_package_init_reexport_f401_matching():
    """_is_package_init_reexport_f401 returns True for package-local F401 in __init__.py."""
    diagnostic = {
        "code": "F401",
        "message": "`mypackage.submodule` imported but unused",
    }
    file_path = Path("/fake/mypackage/__init__.py")
    assert _is_package_init_reexport_f401(diagnostic, file_path) is True


def test_is_package_init_reexport_f401_not_matching():
    """_is_package_init_reexport_f401 returns False when imported name doesn't match package."""
    diagnostic = {
        "code": "F401",
        "message": "`os` imported but unused",
    }
    file_path = Path("/fake/mypackage/__init__.py")
    assert _is_package_init_reexport_f401(diagnostic, file_path) is False


def test_is_package_init_reexport_f401_wrong_code():
    """_is_package_init_reexport_f401 returns False for non-F401 codes."""
    diagnostic = {
        "code": "F841",
        "message": "`x` assigned but unused",
    }
    file_path = Path("/fake/mypackage/__init__.py")
    assert _is_package_init_reexport_f401(diagnostic, file_path) is False


def test_is_package_init_reexport_f401_not_init():
    """_is_package_init_reexport_f401 returns False when file is not __init__.py."""
    diagnostic = {
        "code": "F401",
        "message": "`mypackage.submodule` imported but unused",
    }
    file_path = Path("/fake/mypackage/module.py")
    assert _is_package_init_reexport_f401(diagnostic, file_path) is False


# --- Truncated/invalid JSON (user test #1, lines 131-145 in lint.py) ---


def test_ruff_invalid_json_decoder_error(monkeypatch):
    """LintAnalyzer returns lint_oracle_failed when ruff returns truncated JSON (JSONDecodeError)."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    # Truncated JSON — missing closing bracket and quote
    mock_completed.stdout = '[{"code": "F401", "message": "truncated'
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="import os\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    failed = [f for f in findings if f.type == "lint_oracle_failed"]
    assert len(failed) == 1, (
        f"Expected 1 lint_oracle_failed for truncated JSON, got {[f.type for f in findings]}"
    )
    assert "invalid json" in failed[0].message.lower()


# --- Unknown Ruff code → default severity (user test #2, line 275) ---


def test_ruff_codes_not_in_severity_map():
    """Unknown Ruff codes default to Severity.INFO."""
    assert _severity_for_ruff_code("RUF100") == Severity.INFO
    assert _severity_for_ruff_code("XYZ999") == Severity.INFO
    assert _severity_for_ruff_code("C901") == Severity.INFO


# --- Diagnostic missing filename (user test #3) ---


def test_ruff_diagnostic_with_no_file(monkeypatch):
    """Ruff diagnostic missing 'filename' key raises KeyError (current behavior)."""
    import pytest as _pytest

    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    # Diagnostic with no 'filename' key
    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "location": {"row": 1, "column": 8},
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="import os\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )

    with _pytest.raises(KeyError, match="filename"):
        analyzer.analyze(context)


# --- Evidence with claim field (user test #4) ---


def test_ruff_finding_has_evidence_attached(monkeypatch):
    """LintAnalyzer attaches Evidence with claim='unused_import' for F401 findings."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "/fake/test.py",
            "location": {"row": 1, "column": 8},
            "end_location": {"row": 1, "column": 10},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="import os\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f401 = lint_findings[0]

    # Evidence entries must exist and carry the claim field
    evidence_entries = f401.data.get("evidence", [])
    assert len(evidence_entries) >= 1, (
        "F401 finding must have at least 1 evidence entry"
    )
    first_ev = evidence_entries[0]
    assert "claim" in first_ev, f"Evidence entry missing 'claim' field: {first_ev}"
    assert first_ev["claim"] == "unused_import"
    assert first_ev["kind"] == "RuffDiagnostic"
    assert first_ev["source"] == "ruff"
    assert "file" in first_ev, "Evidence entry must carry file field"
    assert "subject_key" in first_ev, "Evidence entry must carry subject_key for fusion"


# --- Location parsing (user test #5, lines 166-173 in lint.py) ---


def test_ruff_diagnostic_with_location(monkeypatch):
    """LintAnalyzer correctly parses location row/column from Ruff diagnostic."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F841",
            "message": "Local variable `x` is assigned to but never used",
            "filename": "/fake/test.py",
            "location": {"row": 5, "column": 5},
            "end_location": {"row": 5, "column": 6},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="def foo():\n    x = 1\n    return 42\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f841 = lint_findings[0]
    assert f841.lint_code == "F841"

    # Verify location was correctly parsed
    assert f841.location is not None
    assert f841.location.line == 5, f"Expected line=5, got {f841.location.line}"
    assert f841.location.column == 5, f"Expected column=5, got {f841.location.column}"
    assert f841.location.end_line == 5, (
        f"Expected end_line=5, got {f841.location.end_line}"
    )
    assert f841.location.end_column == 6, (
        f"Expected end_column=6, got {f841.location.end_column}"
    )


# --- All error code severity mappings (user test #6) ---


def test_severity_for_ruff_all_error_codes():
    """Known Ruff codes produce their expected severity levels.

    Priority chain in _severity_for_ruff_code:
      1. F541 → HINT
      2. E9* / F8* → ERROR  (any code starting with E9 or F8)
      3. F* / B* / S* → WARNING
      4. everything else → INFO

    Note: F841 starts with 'F8' so it is ERROR. F821 also starts with 'F8' → ERROR.
    """
    assert _severity_for_ruff_code("F401") == Severity.WARNING
    assert _severity_for_ruff_code("F841") == Severity.ERROR  # starts with F8
    assert _severity_for_ruff_code("F811") == Severity.ERROR  # starts with F8
    assert _severity_for_ruff_code("F821") == Severity.ERROR  # starts with F8
    assert _severity_for_ruff_code("E501") == Severity.INFO


def test_severity_for_ruff_code_f8_substrings():
    """All F8xx codes map to ERROR (code.startswith('F8') check fires first)."""
    assert _severity_for_ruff_code("F821") == Severity.ERROR
    assert _severity_for_ruff_code("F822") == Severity.ERROR
    assert _severity_for_ruff_code("F823") == Severity.ERROR
    assert _severity_for_ruff_code("F801") == Severity.ERROR  # F80 still starts with F8
    assert _severity_for_ruff_code("F841") == Severity.ERROR  # F84 still starts with F8
    assert (
        _severity_for_ruff_code("F401") == Severity.WARNING
    )  # F40 doesn't start with F8


# --- Wildcard import info (user test #7, lines 366-410) ---


def test_extract_import_info_wildcard(monkeypatch):
    """_import_info_from_diagnostic handles 'from X import *' source lines."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    # Ruff F401 on a wildcard import: from os import *
    # The diagnostic message from ruff for `from os import *` would reference 'os'
    mock_diagnostic = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "/fake/test.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 20},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    # Source line with wildcard import
    file_info = FileInfo(
        path=py_file,
        content="from os import *\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f401 = lint_findings[0]

    # import_info should still be extracted from the message (not the source line)
    import_info = f401.data.get("import_info", {})
    assert import_info.get("source") == "ruff"
    # The message backtick value is "os", source line starts with "from os import *"
    # import_info_from_diagnostic extracts "os" from message
    # source_line.strip().startswith("import ") → False for "from os import *"
    # "." in "os" → False, so module=None, name="os"
    assert import_info.get("name") == "os", f"Expected name='os', got {import_info}"
    assert import_info.get("module") is None


def test_import_info_from_diagnostic_wildcard_source_line():
    """_import_info_from_diagnostic with source line containing wildcard import."""
    diagnostic = {
        "code": "F401",
        "message": "`os` imported but unused",
    }
    file_path = Path("/fake/test.py")
    source_line = "from os import *"
    result = _import_info_from_diagnostic(diagnostic, file_path, source_line)
    # source_line starts with "from os import *", not "import "
    # "os" doesn't contain "." → module=None, name="os"
    assert result["name"] == "os"
    assert result["module"] is None
    assert result["source"] == "ruff"
    # AST parses `from os import *` as ImportFrom with 1 name ("*") → single_alias=True
    assert result["single_alias"] is True


# --- _claim_for_ruff_code coverage (lines 335, 337, 339 in lint.py) ---


def test_claim_for_ruff_code_values():
    """_claim_for_ruff_code returns correct claim strings for known codes."""
    assert _claim_for_ruff_code("F401") == "unused_import"
    assert _claim_for_ruff_code("F821") == "undefined_name"
    assert _claim_for_ruff_code("F841") == "unused_variable"
    assert _claim_for_ruff_code("F811") == "redefined_name"
    assert _claim_for_ruff_code("E501") == "lint_violation"
    assert _claim_for_ruff_code("RUF100") == "lint_violation"


# --- _strength_for_ruff_code coverage (lines 345, 349 in lint.py) ---


def test_strength_for_ruff_code_values():
    """_strength_for_ruff_code returns correct confidence strengths for known codes."""
    assert _strength_for_ruff_code("F821") == 0.90
    assert _strength_for_ruff_code("F841") == 0.90
    assert _strength_for_ruff_code("F401") == 0.85
    assert _strength_for_ruff_code("F811") == 0.80
    assert _strength_for_ruff_code("E501") == 0.70
    assert _strength_for_ruff_code("RUF100") == 0.70


# --- _proof_state_for_ruff_code coverage (lines 278-289 in lint.py) ---


def test_proof_state_for_ruff_code_review_public_api():
    """F401 in __init__.py → REVIEW_PUBLIC_API (line 286)."""
    file_path = Path("/fake/mypackage/__init__.py")
    assert _proof_state_for_ruff_code("F401", file_path) == "REVIEW_PUBLIC_API"


def test_proof_state_for_ruff_code_proven_unused():
    """F401 and F841 → PROVEN_UNUSED (line 288)."""
    assert (
        _proof_state_for_ruff_code("F401", Path("/fake/module.py")) == "PROVEN_UNUSED"
    )
    assert (
        _proof_state_for_ruff_code("F841", Path("/fake/module.py")) == "PROVEN_UNUSED"
    )


def test_proof_state_for_ruff_code_externally_verified():
    """Non-F401/F841 codes → EXTERNALLY_VERIFIED (line 289)."""
    assert (
        _proof_state_for_ruff_code("F811", Path("/fake/module.py"))
        == "EXTERNALLY_VERIFIED"
    )
    assert (
        _proof_state_for_ruff_code("E501", Path("/fake/module.py"))
        == "EXTERNALLY_VERIFIED"
    )
    assert (
        _proof_state_for_ruff_code("F821", Path("/fake/module.py"))
        == "EXTERNALLY_VERIFIED"
    )


# --- _first_backtick_value empty (line 307 in lint.py) ---


def test_first_backtick_value_no_backticks():
    """_first_backtick_value returns '' when message has no backticks."""
    assert _first_backtick_value("no backticks here") == ""
    assert _first_backtick_value("") == ""
    assert _first_backtick_value("single ` backtick") == ""  # only 1 backtick, no pair


# --- _subject_key_from_diagnostic dotted (lines 318-323 in lint.py) ---


def test_subject_key_from_diagnostic_dotted_import():
    """_subject_key_from_diagnostic with bare import containing dots (line 318-319)."""
    diagnostic = {
        "code": "F401",
        "message": "`os.path` imported but unused",
    }
    file_path = Path("/fake/test.py")
    # Source line: import os.path (bare import with dots)
    source_line = "import os.path"
    result = _subject_key_from_diagnostic(diagnostic, file_path, "F401", source_line)
    assert result is not None
    sk = result.to_dict()
    assert sk["module"] == "os.path"
    # subject_key_for_lint uses `name or code` — when name=None, falls back to code="F401"
    assert sk["name"] == "F401"


def test_subject_key_from_diagnostic_dotted_from_import():
    """_subject_key_from_diagnostic with from-import containing dots (lines 321-323)."""
    diagnostic = {
        "code": "F401",
        "message": "`os.path.join` imported but unused",
    }
    file_path = Path("/fake/test.py")
    # Source line: from os.path import join
    source_line = "from os.path import join"
    result = _subject_key_from_diagnostic(diagnostic, file_path, "F401", source_line)
    assert result is not None
    sk = result.to_dict()
    assert sk["module"] == "os.path"
    assert sk["name"] == "join"


def test_subject_key_from_diagnostic_simple_import():
    """_subject_key_from_diagnostic with simple bare import (module=None, name=os)."""
    diagnostic = {
        "code": "F401",
        "message": "`os` imported but unused",
    }
    file_path = Path("/fake/test.py")
    result = _subject_key_from_diagnostic(diagnostic, file_path, "F401", "import os")
    assert result is not None
    sk = result.to_dict()
    # source line "import os" starts with "import " and "os" doesn't contain "."
    # → module=None, name="os"
    assert sk["module"] is None
    assert sk["name"] == "os"


def test_subject_key_from_diagnostic_non_f401():
    """_subject_key_from_diagnostic for non-F401 codes uses subject_key_for_lint with just code."""
    diagnostic = {
        "code": "F841",
        "message": "Local variable `x` is assigned to but never used",
    }
    file_path = Path("/fake/test.py")
    result = _subject_key_from_diagnostic(diagnostic, file_path, "F841")
    assert result is not None
    sk = result.to_dict()
    # For non-F401, subject_key_for_lint(file, code) → name falls back to code
    assert sk["module"] is None
    assert sk["name"] == "F841"


# --- _import_info_from_diagnostic non-F401 (lines 366-367 in lint.py) ---


def test_import_info_from_diagnostic_non_f401():
    """_import_info_from_diagnostic returns {} for non-F401 codes."""
    diagnostic = {
        "code": "F841",
        "message": "Local variable `x` is assigned to but never used",
    }
    file_path = Path("/fake/test.py")
    result = _import_info_from_diagnostic(diagnostic, file_path, "x = 1")
    assert result == {}


# --- _import_info_from_diagnostic re_export intent (lines 379-381 in lint.py) ---


def test_import_info_from_diagnostic_re_export():
    """_import_info_from_diagnostic sets intent='re_export' for __init__.py with package-local import.

    The function splits dotted names: 'mypackage.submodule'.rsplit('.', 1)
    → module='mypackage', name='submodule'.
    """
    diagnostic = {
        "code": "F401",
        "message": "`mypackage.submodule` imported but unused",
    }
    file_path = Path("/fake/mypackage/__init__.py")
    source_line = "from mypackage.submodule import something"
    result = _import_info_from_diagnostic(diagnostic, file_path, source_line)
    assert result["intent"] == "re_export"
    # Dotted name split: rsplit('.', 1) → module='mypackage', name='submodule'
    assert result["module"] == "mypackage"
    assert result["name"] == "submodule"
    assert result["source"] == "ruff"


def test_import_info_from_diagnostic_bare_dotted_import():
    """_import_info_from_diagnostic with bare import containing dots (lines 370-372)."""
    diagnostic = {
        "code": "F401",
        "message": "`os.path` imported but unused",
    }
    file_path = Path("/fake/test.py")
    source_line = "import os.path"
    result = _import_info_from_diagnostic(diagnostic, file_path, source_line)
    assert result["module"] == "os.path"
    assert result["name"] is None
    assert result["intent"] == ""  # not __init__.py


# --- _is_single_alias_source_line coverage (lines 394-410 in lint.py) ---


def test_is_single_alias_source_line_syntax_error():
    """_is_single_alias_source_line returns False on SyntaxError (line 402-403)."""
    assert _is_single_alias_source_line("import os path") is False
    assert _is_single_alias_source_line("from os import (") is False


def test_is_single_alias_source_line_multiline():
    """_is_single_alias_source_line returns False for multi-line imports (line 407)."""
    # Single-line with newline inside parens creates multi-line
    multiline = "from os import (\n    path,\n)\n"
    assert _is_single_alias_source_line(multiline) is False


def test_is_single_alias_source_line_multiple_names():
    """_is_single_alias_source_line returns False when import has multiple names (line 410)."""
    assert _is_single_alias_source_line("from os import path, environ") is False
    assert _is_single_alias_source_line("import os, sys") is False


def test_is_single_alias_source_line_true():
    """_is_single_alias_source_line returns True for single-alias, single-line imports."""
    assert _is_single_alias_source_line("import os") is True
    assert _is_single_alias_source_line("from os import path") is True
    assert _is_single_alias_source_line("from os import path as p") is True


def test_is_single_alias_source_line_non_import():
    """_is_single_alias_source_line returns False for non-import AST nodes."""
    assert _is_single_alias_source_line("x = 1") is False
    assert _is_single_alias_source_line("def foo(): pass") is False


# --- Full flow: non-F401 diagnostic with proof_state and severity ---


def test_lint_analyzer_f841_full_flow(monkeypatch):
    """LintAnalyzer correctly processes F841 (unused variable) diagnostic end-to-end."""
    monkeypatch.setattr(
        "imodent.analyzers.lint._ruff_command_prefix",
        lambda: ["ruff"],
    )

    mock_diagnostic = [
        {
            "code": "F841",
            "message": "Local variable `x` is assigned to but never used",
            "filename": "/fake/test.py",
            "location": {"row": 2, "column": 5},
            "end_location": {"row": 2, "column": 6},
            "fix": None,
        }
    ]

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = json.dumps(mock_diagnostic)
    mock_completed.stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_completed)

    analyzer = LintAnalyzer()
    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="def foo():\n    x = 1\n    return 42\n",
        language="python",
    )
    config = AnalysisConfig(use_ruff=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
    )
    findings = analyzer.analyze(context)

    lint_findings = [f for f in findings if f.type == "lint"]
    assert len(lint_findings) >= 1
    f841 = lint_findings[0]
    assert f841.lint_code == "F841"
    assert f841.severity == Severity.ERROR  # F841 → ERROR (starts with 'F8')
    assert f841.data.get("proof_state") == "PROVEN_UNUSED"

    # Evidence should have claim "unused_variable"
    evidence_entries = f841.data.get("evidence", [])
    assert len(evidence_entries) >= 1
    first_ev = evidence_entries[0]
    assert first_ev.get("claim") == "unused_variable"
    assert first_ev.get("strength") == 0.90

    # import_info should be empty for non-F401
    assert f841.data.get("import_info") == {}


# ============================================================================
# Additional edge-case tests to push rust.py coverage to 90%+
# ============================================================================


# --- _severity_for_cargo_level / _strength_for_cargo_level / _claim_for_cargo_level ---


def test_severity_for_cargo_level_note_and_help():
    """_severity_for_cargo_level maps 'note'/'help' → HINT."""
    from imodent.analyzers.rust import _severity_for_cargo_level

    assert _severity_for_cargo_level("note") == Severity.HINT
    assert _severity_for_cargo_level("help") == Severity.HINT


def test_severity_for_cargo_level_default():
    """_severity_for_cargo_level maps unknown level → INFO."""
    from imodent.analyzers.rust import _severity_for_cargo_level

    assert _severity_for_cargo_level("unknown") == Severity.INFO


def test_strength_for_cargo_level_error():
    """_strength_for_cargo_level maps 'error' → 0.90."""
    from imodent.analyzers.rust import _strength_for_cargo_level

    assert _strength_for_cargo_level("error") == 0.90


def test_strength_for_cargo_level_default():
    """_strength_for_cargo_level maps unknown level → 0.50."""
    from imodent.analyzers.rust import _strength_for_cargo_level

    assert _strength_for_cargo_level("unknown") == 0.50


def test_claim_for_cargo_level_error():
    """_claim_for_cargo_level maps 'error' → 'rust_compile_error'."""
    from imodent.analyzers.rust import _claim_for_cargo_level

    assert _claim_for_cargo_level("error") == "rust_compile_error"


def test_claim_for_cargo_level_default():
    """_claim_for_cargo_level maps unknown level → 'rust_tooling_advice'."""
    from imodent.analyzers.rust import _claim_for_cargo_level

    assert _claim_for_cargo_level("unknown") == "rust_tooling_advice"


# --- _read_file: is_symlink path covered by test_read_file_symlink above ---
# --- is_fifo path covered by test_read_file_fifo above ---


def test_rust_analyzer_no_rust_or_toml_files():
    """RustAnalyzer with no .rs or .toml files returns [] (line 59)."""
    analyzer = RustAnalyzer()
    # Only a Python file — no rust or toml
    py_file = Path("/fake/data.py")
    file_info = FileInfo(path=py_file, content="x=1", language="python")
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(
        files={py_file: file_info},
        config=config,
        project_root=Path("/fake"),
    )
    findings = analyzer.analyze(context)
    assert findings == []


# --- _scan_cargo_root: cargo-machete reference (line 323) ---


def test_scan_cargo_root_cargo_machete_present(tmp_path):
    """Cargo.toml mentions 'cargo-machete' → no rust_cargo_machete_missing."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        "# uses cargo-machete for dep auditing\n"
    )
    rs_content = "fn main() {}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    machete_missing = _findings_by_type(findings, "rust_cargo_machete_missing")
    assert len(machete_missing) == 0, (
        f"cargo-machete mentioned → should suppress rust_cargo_machete_missing, "
        f"got {machete_missing}"
    )


# --- _scan_cargo_root: test file skip (line 433) ---


def test_scan_cargo_root_test_file_skipped(tmp_path):
    """Residue markers inside tests/ directory are skipped (line 433)."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints]\nrust.missing_docs = "allow"\n'
    )
    # Create a test file with a todo marker
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_rs = test_dir / "test_foo.rs"
    test_rs.write_text("#[test]\nfn it_works() {\n    todo!();\n}\n")

    files: dict[Path, FileInfo] = {}
    cargo_path = tmp_path / "Cargo.toml"
    cargo_path.write_text(cargo_content)
    files[cargo_path] = FileInfo(
        path=cargo_path, content=cargo_content, language="toml"
    )
    files[test_rs] = FileInfo(
        path=test_rs, content=test_rs.read_text(), language="rust"
    )

    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files=files, config=config, project_root=tmp_path)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    residue = _findings_by_type(findings, "rust_residue_marker")
    todos = [r for r in residue if "todo!" in r.message]
    assert len(todos) == 0, (
        f"todo!() in tests/ should be skipped, got {len(todos)} residue markers"
    )


# --- _scan_cargo_root: unsafe without safety comment (lines 454-464) ---


def test_scan_cargo_root_unsafe_no_safety(tmp_path):
    """unsafe {} without // SAFETY: comment → rust_unsafe_no_safety finding."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints]\nrust.missing_docs = "allow"\n'
    )
    rs_content = 'fn main() {\n    unsafe {\n        println!("hello");\n    }\n}\n'
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    unsafe = _findings_by_type(findings, "rust_unsafe_no_safety")
    assert len(unsafe) >= 1, (
        f"Expected >=1 rust_unsafe_no_safety, got {[f.type for f in findings]}"
    )
    assert unsafe[0].severity == Severity.WARNING
    assert unsafe[0].location is not None
    assert unsafe[0].location.line == 2


# --- _scan_cargo_root: unsafe WITH safety comment (suppress) ---


def test_scan_cargo_root_unsafe_with_safety(tmp_path):
    """unsafe {} with // SAFETY: comment → NO rust_unsafe_no_safety finding."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints]\nrust.missing_docs = "allow"\n'
    )
    # SAFETY comment must appear ON or AFTER the unsafe line for
    # forward-scan detection (lines 454-464 look ahead from the unsafe line).
    rs_content = (
        "fn main() {\n"
        "    unsafe {\n"
        "        // SAFETY: this is fine\n"
        '        println!("hello");\n'
        "    }\n"
        "}\n"
    )
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    unsafe = _findings_by_type(findings, "rust_unsafe_no_safety")
    assert len(unsafe) == 0, f"SAFETY comment present → should not flag, got {unsafe}"


# --- _scan_cargo_root: unwrap in library code (line 479) ---


def test_scan_cargo_root_unwrap_in_library(tmp_path):
    """.unwrap() in non-test code → rust_unwrap_in_library finding."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints]\nrust.missing_docs = "allow"\n'
    )
    rs_content = "fn main() {\n    Some(1).unwrap();\n}\n"
    context = _build_context(tmp_path, cargo_content, rs_content)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    unwrap_findings = _findings_by_type(findings, "rust_unwrap_in_library")
    assert len(unwrap_findings) >= 1, (
        f"Expected >=1 rust_unwrap_in_library, got {[f.type for f in findings]}"
    )
    assert unwrap_findings[0].severity == Severity.WARNING
    assert unwrap_findings[0].location is not None
    assert unwrap_findings[0].location.line == 2


# --- _parse_cargo_json_output: returncode != 0 + no findings (line 586) ---


def test_cargo_check_nonzero_returncode_no_diags(tmp_path, monkeypatch):
    """cargo exits non-zero with no compiler-message lines → rust_oracle_failed."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/cargo" if cmd == "cargo" else None
    )

    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 1
    mock_result.stdout = ""  # No compiler-message output
    mock_result.stderr = "error: build failed\n"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_result)

    root = tmp_path / "cargo_proj"
    root.mkdir()
    (root / "Cargo.toml").write_text('[package]\nname = "test"\n')

    config = AnalysisConfig(check_rust=True, run_cargo_check=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    findings = _run_cargo_check(context, root)

    # Should have 1 rust_oracle_failed + 0 rust_diagnostic
    failed = [f for f in findings if f.type == "rust_oracle_failed"]
    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 0, f"Expected 0 diagnostics, got {diags}"
    assert len(failed) == 1, f"Expected 1 rust_oracle_failed, got {failed}"


# --- _parse_cargo_json_output: non-compiler-message reason (line 628) ---


def test_parse_cargo_json_output_non_compiler_message(tmp_path):
    """NDJSON line with reason='build-finished' → skipped (line 628)."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    stdout = (
        json.dumps(
            {
                "reason": "build-finished",
                "success": True,
            }
        )
        + "\n"
    )
    findings = _parse_cargo_json_output(context, root, stdout, "check")
    assert findings == [], f"non-compiler-message should be skipped, got {findings}"


# --- _parse_cargo_json_output: message not a dict (line 631) ---


def test_parse_cargo_json_output_message_not_dict(tmp_path):
    """NDJSON with compiler-message where message is a string → skipped (line 631)."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    stdout = (
        json.dumps(
            {
                "reason": "compiler-message",
                "message": "just a string, not a dict",
            }
        )
        + "\n"
    )
    findings = _parse_cargo_json_output(context, root, stdout, "check")
    assert findings == [], f"non-dict message should be skipped, got {findings}"


# --- _parse_cargo_json_output: JSON parse error (line 624-625) ---


def test_parse_cargo_json_output_json_decode_error(tmp_path):
    """NDJSON with a line that starts with '{' but is not valid JSON → skipped."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    # Valid JSON followed by a garbled line
    stdout = (
        _cargo_diag_line(
            code="unused_imports", level="warning", message="unused import"
        )
        + "{broken json that starts with brace\n"
    )
    findings = _parse_cargo_json_output(context, root, stdout, "check")
    # Only the valid line should produce a diagnostic
    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 1, f"Expected 1 diag, got {len(diags)}"


# --- _parse_cargo_json_output: non-primary span fallback (line 648) ---


def test_parse_cargo_json_output_non_primary_span_fallback(tmp_path):
    """cargo diag with only non-primary spans → falls back to spans[0] (line 648)."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    obj = {
        "reason": "compiler-message",
        "message": {
            "code": {"code": "dead_code"},
            "level": "warning",
            "message": "function is never used",
            "spans": [
                {
                    "file_name": "src/main.rs",
                    "line_start": 5,
                    "column_start": 1,
                    "line_end": 5,
                    "column_end": 40,
                    "is_primary": False,
                },
                {
                    "file_name": "src/main.rs",
                    "line_start": 10,
                    "column_start": 1,
                    "line_end": 10,
                    "column_end": 20,
                    "is_primary": False,
                },
            ],
        },
    }
    stdout = json.dumps(obj) + "\n"
    findings = _parse_cargo_json_output(context, root, stdout, "check")

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 1, f"Expected 1 diag from fallback span, got {len(diags)}"
    # Should use first span (line 5)
    assert diags[0].location is not None
    assert diags[0].location.line == 5


# --- _parse_cargo_json_output: empty spans list → falls back to Cargo.toml (line 667-669) ---


def test_parse_cargo_json_output_empty_spans(tmp_path):
    """cargo diag with empty spans list → file falls back to Cargo.toml (line 667-669)."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    obj = {
        "reason": "compiler-message",
        "message": {
            "code": {"code": "E0001"},
            "level": "error",
            "message": "compilation failed",
            "spans": [],
        },
    }
    stdout = json.dumps(obj) + "\n"
    findings = _parse_cargo_json_output(context, root, stdout, "check")

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 1
    # File should fall back to Cargo.toml
    assert diags[0].file == (root / "Cargo.toml")
    assert diags[0].location is None


# --- _parse_cargo_json_output: span without file_name (line 667) ---


def test_parse_cargo_json_output_span_no_filename(tmp_path):
    """cargo diag span lacks file_name → file falls back to Cargo.toml (line 667)."""
    root = tmp_path
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files={}, config=config, project_root=root)

    obj = {
        "reason": "compiler-message",
        "message": {
            "code": {"code": "dead_code"},
            "level": "warning",
            "message": "unused",
            "spans": [
                {
                    "line_start": 3,
                    "column_start": 1,
                    "line_end": 3,
                    "column_end": 10,
                    "is_primary": True,
                    # file_name is missing
                }
            ],
        },
    }
    stdout = json.dumps(obj) + "\n"
    findings = _parse_cargo_json_output(context, root, stdout, "check")

    diags = [f for f in findings if f.type == "rust_diagnostic"]
    assert len(diags) == 1
    # file should fall back to Cargo.toml
    assert diags[0].file == (root / "Cargo.toml")
    assert diags[0].location is None


# --- _scan_cargo_root: rust file unreadable → continue (lines 371, 408) ---


def test_scan_cargo_root_rust_file_unreadable(tmp_path, monkeypatch):
    """Unreadable .rs file (None content) → broad-allow + residue scans skip it."""
    cargo_content = (
        '[package]\nname = "test"\nversion = "0.1.0"\n\n'
        '[lints]\nrust.missing_docs = "allow"\n'
    )
    # Create a normal project with an .rs file that will fail to read
    cargo_path, rs_path = _make_cargo_project(tmp_path, cargo_content, "fn main() {}\n")
    # Mock _read_file to return None for the .rs file ONLY
    # (we need the Cargo.toml read to succeed for config checks)
    original_read_file = _read_file

    def _mock_read_file(path):
        if path.suffix == ".rs":
            return None
        return original_read_file(path)

    monkeypatch.setattr("imodent.analyzers.rust._read_file", _mock_read_file)

    files: dict[Path, FileInfo] = {}
    files[cargo_path] = FileInfo(
        path=cargo_path, content=cargo_content, language="toml"
    )
    files[rs_path] = FileInfo(path=rs_path, content="fn main() {}\n", language="rust")

    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(files=files, config=config, project_root=tmp_path)

    analyzer = RustAnalyzer()
    findings = analyzer.analyze(context)

    # Should not crash — unreadable .rs files are skipped gracefully
    broad = _findings_by_type(findings, "rust_broad_allow")
    assert len(broad) == 0, "Unreadable .rs should produce no broad_allow findings"
    residue = _findings_by_type(findings, "rust_residue_marker")
    assert len(residue) == 0, "Unreadable .rs should produce no residue markers"


# --- RustAnalyzer: no rust files + toml only (line 59: empty return path) ---


def test_rust_analyzer_toml_only_with_check_rust(tmp_path):
    """Analyzer with only .toml files and no .rs files → still runs config scan."""
    analyzer = RustAnalyzer()
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_content = '[package]\nname = "test"\nversion = "0.1.0"\n'
    cargo_toml.write_text(cargo_content)
    file_info = FileInfo(
        path=cargo_toml,
        content=cargo_content,
        language="toml",
    )
    config = AnalysisConfig(check_rust=True)
    context = AnalysisContext(
        files={cargo_toml: file_info},
        config=config,
        project_root=tmp_path,
    )
    findings = analyzer.analyze(context)
    # Toml-only projects get config advisories (lint policy missing, etc.)
    assert isinstance(findings, list)
    # Should at least emit lint policy missing
    lint_missing = _findings_by_type(findings, "rust_lint_policy_missing")
    assert len(lint_missing) == 1
