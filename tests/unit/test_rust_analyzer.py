"""Tests for the Rust advisory analyzer."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analysis.decisions import (
    DecisionEngine,
    DecisionCandidate,
    _issue_type_from_finding,
    _default_actions_for_issue_type,
    _score_confidence,
    _destructive_allowed,
    subject_key_for_lint,
)
from imodent.analysis.findings import Finding, Severity, Location
from imodent.analyzers.rust import (
    RustAnalyzer,
    _parse_cargo_json_output,
    _severity_for_cargo_level,
    _strength_for_cargo_level,
    _claim_for_cargo_level,
    _CLIPPY_SPECIFIC_CLAIMS,
    _read_file,
    _is_under_root,
)
from imodent.project.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cargo_project(root, has_lints=False, has_clippy_toml=False, has_rustfmt_toml=False):
    """Create a minimal Cargo project in root."""
    cargo_content = "[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n"
    if has_lints:
        cargo_content += "\n[lints.clippy]\ndbg_macro = \"warn\"\n"
    (root / "Cargo.toml").write_text(cargo_content)
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "lib.rs").write_text("pub fn demo() {}\n")
    if has_clippy_toml:
        (root / "clippy.toml").write_text("msrv = \"1.70\"\n")
    if has_rustfmt_toml:
        (root / "rustfmt.toml").write_text("edition = \"2021\"\n")
    return root


def _make_context(root, files=None, **config_kwargs):
    """Build an AnalysisContext for a Cargo project."""
    if files is None:
        files = {}
        for p in root.rglob("*"):
            if p.is_file():
                files[p] = FileInfo.from_path(p)
    return AnalysisContext(
        files=files,
        config=AnalysisConfig(check_rust=True, **config_kwargs),
        project_root=root,
    )


# ---------------------------------------------------------------------------
# Analyzer gating
# ---------------------------------------------------------------------------


class TestRustAnalyzerGating:
    def test_rust_analyzer_does_not_run_when_check_rust_false(self, tmp_path):
        """Rust analyzer is silent when check_rust is False."""
        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={rs_file: FileInfo.from_path(rs_file)},
            config=AnalysisConfig(check_rust=False),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        assert findings == []

    def test_rust_analyzer_runs_when_check_rust_true(self, tmp_path):
        """Rust analyzer produces findings when check_rust is True."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        # Should find lint policy missing at minimum
        assert any(f.type == "rust_lint_policy_missing" for f in findings)


# ---------------------------------------------------------------------------
# Cargo root discovery
# ---------------------------------------------------------------------------


class TestCargoRootDiscovery:
    def test_unmanaged_rust_files_emit_advisory(self, tmp_path):
        """Rust files outside a Cargo project produce rust_project_unmanaged."""
        rs_file = tmp_path / "standalone.rs"
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={rs_file: FileInfo.from_path(rs_file)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        unmanaged = [f for f in findings if f.type == "rust_project_unmanaged"]
        assert len(unmanaged) == 1
        assert unmanaged[0].severity == Severity.INFO

    def test_cargo_toml_root_is_discovered(self, tmp_path):
        """Cargo.toml in project root is discovered as a Cargo root."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        # Should NOT have unmanaged since Cargo.toml exists
        assert not any(f.type == "rust_project_unmanaged" for f in findings)


# ---------------------------------------------------------------------------
# Config advisory
# ---------------------------------------------------------------------------


class TestConfigAdvisory:
    def test_missing_lint_policy_is_info(self, tmp_path):
        """Missing [lints] section is INFO, not WARNING."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        lint_missing = [f for f in findings if f.type == "rust_lint_policy_missing"]
        assert len(lint_missing) == 1
        assert lint_missing[0].severity == Severity.INFO

    def test_missing_clippy_config_is_hint(self, tmp_path):
        """Missing clippy.toml is HINT, not WARNING."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        clippy_missing = [f for f in findings if f.type == "rust_clippy_config_missing"]
        assert len(clippy_missing) == 1
        assert clippy_missing[0].severity == Severity.HINT

    def test_missing_rustfmt_config_is_hint(self, tmp_path):
        """Missing rustfmt.toml is HINT."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        rustfmt_missing = [f for f in findings if f.type == "rust_rustfmt_config_missing"]
        assert len(rustfmt_missing) == 1
        assert rustfmt_missing[0].severity == Severity.HINT

    def test_lint_policy_present_suppresses_finding(self, tmp_path):
        """Cargo.toml with [lints] section suppresses lint_policy_missing."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            "[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n\n"
            "[workspace.lints.clippy]\ndbg_macro = \"warn\"\n"
        )
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        assert not any(f.type == "rust_lint_policy_missing" for f in findings)


# ---------------------------------------------------------------------------
# Broad allow detection
# ---------------------------------------------------------------------------


class TestBroadAllowDetection:
    def test_broad_allow_warnings_is_warning(self, tmp_path):
        """#![allow(warnings)] in .rs source is WARNING severity."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("#![allow(warnings)]\n\npub fn demo() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        broad = [f for f in findings if f.type == "rust_broad_allow"]
        assert len(broad) == 1
        assert broad[0].severity == Severity.WARNING

    def test_broad_allow_clippy_all_is_warning(self, tmp_path):
        """#![allow(clippy::all)] is WARNING severity."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("#![allow(clippy::all)]\n\npub fn demo() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        broad = [f for f in findings if f.type == "rust_broad_allow"]
        assert len(broad) == 1
        assert broad[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Residue markers
# ---------------------------------------------------------------------------


class TestResidueMarkers:
    def test_todo_is_info(self, tmp_path):
        """todo!() in source is INFO advisory."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("pub fn demo() {\n    todo!(\"implement me\");\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert len(residue) >= 1
        assert all(f.severity == Severity.INFO for f in residue)

    def test_unimplemented_is_info(self, tmp_path):
        """unimplemented!() is INFO advisory."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("pub fn demo() {\n    unimplemented!();\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert len(residue) >= 1

    def test_dbg_is_info(self, tmp_path):
        """dbg!() is INFO advisory."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("pub fn demo() {\n    dbg!(42);\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert len(residue) >= 1


# ---------------------------------------------------------------------------
# Cargo oracle
# ---------------------------------------------------------------------------


class TestCargoOracle:
    def test_cargo_unavailable_produces_oracle_unavailable(self, tmp_path):
        """Missing cargo binary produces rust_oracle_unavailable, not a crash."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value=None):
            findings = RustAnalyzer().analyze(context)
        oracle = [f for f in findings if f.type == "rust_oracle_unavailable"]
        assert len(oracle) == 1
        assert oracle[0].severity == Severity.WARNING

    def test_cargo_check_parses_json_diagnostics(self, tmp_path):
        """Cargo check JSON diagnostics are parsed into rust_diagnostic findings."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"E0425"},'
            '"level":"error","message":"cannot find value `x`",'
            '"spans":[{"file_name":"src/lib.rs","line_start":1,"column_start":1,'
            '"line_end":1,"column_end":2,"is_primary":true}]}}\n'
        )
        mock_result = SimpleNamespace(returncode=1, stdout=mock_json, stderr="")
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        diagnostics = [f for f in findings if f.type == "rust_diagnostic"]
        assert len(diagnostics) == 1
        assert diagnostics[0].lint_code == "E0425"
        assert diagnostics[0].severity == Severity.ERROR

    def test_cargo_nonzero_return_with_valid_diagnostics(self, tmp_path):
        """Cargo nonzero return with valid JSON still produces diagnostics."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"warning"},'
            '"level":"warning","message":"unused variable",'
            '"spans":[{"file_name":"src/lib.rs","line_start":1,"column_start":1,'
            '"line_end":1,"column_end":2,"is_primary":true}]}}\n'
        )
        mock_result = SimpleNamespace(returncode=1, stdout=mock_json, stderr="")
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        diagnostics = [f for f in findings if f.type == "rust_diagnostic"]
        assert len(diagnostics) >= 1

    def test_cargo_invalid_json_produces_oracle_failed(self, tmp_path):
        """Cargo returning invalid JSON produces rust_oracle_failed."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        mock_result = SimpleNamespace(returncode=1, stdout="not-json", stderr="error")
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        # Should get oracle_failed for the invalid output
        oracle_failed = [f for f in findings if f.type == "rust_oracle_failed"]
        assert len(oracle_failed) >= 1

    def test_cargo_oserror_produces_oracle_failed(self, tmp_path):
        """Cargo OSError produces rust_oracle_failed, not a crash."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", side_effect=OSError("not found")):
                findings = RustAnalyzer().analyze(context)
        oracle_failed = [f for f in findings if f.type == "rust_oracle_failed"]
        assert len(oracle_failed) == 1


# ---------------------------------------------------------------------------
# Findings safety
# ---------------------------------------------------------------------------


class TestRustFindingsSafety:
    def test_rust_findings_are_never_fixable(self, tmp_path):
        """All Rust findings have fixable=False and auto_fix_safe=False."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("#![allow(warnings)]\npub fn demo() { todo!(); }\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        for f in findings:
            assert f.fixable is False, f"Finding {f.type} should not be fixable"
            assert f.auto_fix_safe is False, f"Finding {f.type} should not be auto_fix_safe"


# ---------------------------------------------------------------------------
# Decision engine integration
# ---------------------------------------------------------------------------


class TestRustDecisionIntegration:
    def test_rust_advisory_issue_type(self):
        """Rust advisory findings map to rust_advisory issue type."""
        f = Finding.create(
            type="rust_lint_policy_missing",
            severity=Severity.INFO,
            file=__import__("pathlib").Path("Cargo.toml"),
            message="No lint policy",
            fixable=False,
            auto_fix_safe=False,
        )
        assert _issue_type_from_finding(f) == "rust_advisory"

    def test_rust_diagnostic_issue_type(self):
        """Rust diagnostic findings map to rust_diagnostic issue type."""
        f = Finding.create(
            type="rust_diagnostic",
            severity=Severity.ERROR,
            file=__import__("pathlib").Path("src/lib.rs"),
            message="E0425: cannot find value",
            fixable=False,
            auto_fix_safe=False,
        )
        assert _issue_type_from_finding(f) == "rust_diagnostic"

    def test_rust_oracle_issue_type(self):
        """Rust oracle findings map to rust_oracle issue type."""
        f = Finding.create(
            type="rust_oracle_unavailable",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("Cargo.toml"),
            message="cargo not found",
            fixable=False,
            auto_fix_safe=False,
        )
        assert _issue_type_from_finding(f) == "rust_oracle"

    def test_rust_advisory_actions_are_non_destructive(self):
        """Rust advisory suggested actions are all non-destructive."""
        actions = _default_actions_for_issue_type("rust_advisory")
        assert len(actions) == 1
        assert actions[0].id == "review_policy"
        assert actions[0].destructive is False

    def test_rust_diagnostic_actions_require_decision(self):
        """Rust diagnostic actions require user decision."""
        actions = _default_actions_for_issue_type("rust_diagnostic")
        assert len(actions) == 1
        assert actions[0].requires_decision is True
        assert actions[0].destructive is False

    def test_rust_oracle_actions_are_non_destructive(self):
        """Rust oracle actions are non-destructive."""
        actions = _default_actions_for_issue_type("rust_oracle")
        assert len(actions) == 1
        assert actions[0].destructive is False

    def test_rust_candidate_destructive_blocked(self, tmp_path):
        """Rust findings never allow destructive edits."""
        from imodent.analysis.decisions import DecisionCandidate, subject_key_for_lint
        sk = subject_key_for_lint(
            __import__("pathlib").Path("Cargo.toml"), "rust_lint_policy_missing"
        )
        candidate = DecisionCandidate(
            issue_type="rust_advisory",
            subject_key=sk,
            confidence=0.50,
            confidence_label="medium",
        )
        from imodent.analysis.decisions import _destructive_allowed
        assert _destructive_allowed(candidate, []) is False


# ---------------------------------------------------------------------------
# CLI routing
# ---------------------------------------------------------------------------


class TestRustCLIRouting:
    def test_rust_flag_triggers_scan_mode(self, tmp_path, monkeypatch):
        """--rust flag triggers scan mode."""
        import imodent.cli as cli_module
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr("sys.argv", ["imodent", str(test_file), "--analyze", "--rust"])
        cli_module.main()
        assert capture_call.kwargs is not None
        assert capture_call.kwargs["check_rust"] is True

    def test_cargo_flag_implies_rust(self, tmp_path, monkeypatch):
        """--cargo passes run_cargo=True (analyze_files internally sets all cargo oracle flags)."""
        import imodent.cli as cli_module
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr("sys.argv", ["imodent", str(test_file), "--analyze", "--cargo"])
        cli_module.main()
        assert capture_call.kwargs is not None
        assert capture_call.kwargs["run_cargo"] is True

    def test_cargo_check_flag_implies_rust(self, tmp_path, monkeypatch):
        """--cargo-check passes run_cargo_check=True."""
        import imodent.cli as cli_module
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr("sys.argv", ["imodent", str(test_file), "--analyze", "--cargo-check"])
        cli_module.main()
        assert capture_call.kwargs is not None
        assert capture_call.kwargs["run_cargo_check"] is True


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestRustLanguageDetection:
    def test_rs_file_detects_as_rust(self):
        """`.rs` files detect as language `rust`."""
        info = FileInfo(
            path=__import__("pathlib").Path("lib.rs"),
            content="fn main() {}\n",
            language=FileInfo._detect_language(__import__("pathlib").Path("lib.rs")),
        )
        assert info.language == "rust"

    def test_toml_file_detects_as_toml(self):
        """`.toml` files detect as language `toml`."""
        info = FileInfo(
            path=__import__("pathlib").Path("Cargo.toml"),
            content="[package]\n",
            language=FileInfo._detect_language(__import__("pathlib").Path("Cargo.toml")),
        )
        assert info.language == "toml"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestRustConfigLoading:
    def test_yaml_config_applies_rust_fields(self, tmp_path):
        """Rust fields from .imodent.yaml are applied."""
        from imodent.project.config import load_config
        (tmp_path / ".imodent.yaml").write_text(
            "check_rust: true\nrun_cargo: false\nrun_cargo_check: true\nrun_cargo_clippy: false\n"
        )
        config = load_config(tmp_path)
        assert config.check_rust is True
        assert config.run_cargo is False
        assert config.run_cargo_check is True
        assert config.run_cargo_clippy is False

    def test_pyproject_config_applies_rust_fields(self, tmp_path):
        """Rust fields from [tool.imodent] are applied."""
        from imodent.project.config import load_config
        (tmp_path / "pyproject.toml").write_text(
            "[tool.imodent]\ncheck_rust = true\nrun_cargo = true\n"
        )
        config = load_config(tmp_path)
        assert config.check_rust is True
        assert config.run_cargo is True


# ---------------------------------------------------------------------------
# Include patterns with --rust
# ---------------------------------------------------------------------------


class TestRustIncludePatterns:
    def test_rust_mode_discovers_rs_files(self, tmp_path):
        """--rust mode includes .rs and Cargo config files in discovery."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        src = tmp_path / "src"
        src.mkdir()
        rs_file = src / "lib.rs"
        rs_file.write_text("fn main() {}\n")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_rust = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([tmp_path])

        assert any(
            fi.language == "rust"
            for fi in result.context.files.values()
        )

    def test_rust_mode_excludes_target_directory(self, tmp_path):
        """target/ directory is excluded even with --rust mode."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        target = tmp_path / "target"
        target.mkdir()
        target_file = target / "debug"
        target_file.mkdir()
        (target_file / "lib.rlib").write_bytes(b"\x00")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_rust = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([tmp_path])

        # target files should not be in context
        for path in result.context.files:
            assert "target" not in path.parts


# ---------------------------------------------------------------------------
# Decision engine confidence
# ---------------------------------------------------------------------------


class TestRustConfidenceScoring:
    def test_rust_broad_allow_confidence(self):
        """rust_broad_allow confidence is 0.75."""
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="rust_broad_allow",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("src/lib.rs"),
            message="Broad suppression",
            fixable=False,
            auto_fix_safe=False,
        )
        score = _score_confidence(f, [f], [])
        assert score == 0.75

    def test_rust_config_advice_confidence(self):
        """Config advice findings confidence is 0.50."""
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="rust_lint_policy_missing",
            severity=Severity.INFO,
            file=__import__("pathlib").Path("Cargo.toml"),
            message="No lint policy",
            fixable=False,
            auto_fix_safe=False,
        )
        score = _score_confidence(f, [f], [])
        assert score == 0.50

    def test_rust_oracle_unavailable_confidence(self):
        """rust_oracle_unavailable confidence is 0.40."""
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="rust_oracle_unavailable",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("Cargo.toml"),
            message="cargo not found",
            fixable=False,
            auto_fix_safe=False,
        )
        score = _score_confidence(f, [f], [])
        assert score == 0.40


# ---------------------------------------------------------------------------
# Rust findings never fixable (integration)
# ---------------------------------------------------------------------------


class TestRustNoFixes:
    def test_rust_findings_not_routed_to_import_fixer(self, tmp_path):
        """Rust findings should not be routed to any fixer."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_rust = True
        project_context.config.check_imports = False
        project_context.config.check_lint = False
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([tmp_path])

        # Should have findings but no fix results
        fix_results = coordinator.fix(
            result.findings,
            result.context,
            mode=FixMode.SAFE_AUTO,
            candidates=result.candidates,
        )
        assert fix_results == {}


# ---------------------------------------------------------------------------
# Cargo Clippy oracle (CRITICAL — was completely untested)
# ---------------------------------------------------------------------------


class TestCargoClippyOracle:
    def test_clippy_unavailable_produces_oracle_unavailable(self, tmp_path):
        """Missing cargo binary with run_cargo_clippy=True produces oracle_unavailable."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_clippy=True),
            project_root=tmp_path,
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value=None):
            findings = RustAnalyzer().analyze(context)
        oracle = [f for f in findings if f.type == "rust_oracle_unavailable"]
        assert len(oracle) == 1
        assert "clippy" in oracle[0].message.lower()

    def test_clippy_json_produces_clippy_diagnostic_evidence(self, tmp_path):
        """Clippy JSON diagnostics produce ClippyDiagnostic evidence, not CargoDiagnostic."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("fn main() {}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True, run_cargo_clippy=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"clippy::dbg_macro"},'
            '"level":"warning","message":"use of dbg! macro",'
            '"spans":[{"file_name":"src/lib.rs","line_start":1,"column_start":1,'
            '"line_end":1,"column_end":2,"is_primary":true}]}}\n'
        )
        mock_result = SimpleNamespace(returncode=0, stdout=mock_json, stderr="")
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        diagnostics = [f for f in findings if f.type == "rust_diagnostic"]
        assert len(diagnostics) == 1
        # Evidence should be ClippyDiagnostic
        evidence_list = diagnostics[0].data.get("evidence", [])
        assert len(evidence_list) == 1
        assert evidence_list[0]["kind"] == "ClippyDiagnostic"

    def test_run_cargo_implies_both_check_and_clippy(self, tmp_path):
        """run_cargo=True triggers both _run_cargo_check AND _run_cargo_clippy."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo=True),
            project_root=tmp_path,
        )
        calls = []
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            calls.append(cmd[1] if len(cmd) > 1 else cmd[0])
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", side_effect=mock_run):
                RustAnalyzer().analyze(context)
        # Both check and clippy should have been invoked
        assert "check" in calls
        assert "clippy" in calls

    def test_clippy_unused_imports_maps_to_rust_unused_import(self):
        """Clippy unused_imports code maps to rust_unused_import issue type."""
        f = Finding.create(
            type="rust_diagnostic",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("src/lib.rs"),
            message="unused_imports: unused import `foo::Bar`",
            fixable=False,
            auto_fix_safe=False,
            lint_code="unused_imports",
        )
        assert _issue_type_from_finding(f) == "rust_unused_import"

    def test_rust_unused_import_actions(self):
        """rust_unused_import issue type has investigate-first, non-destructive actions."""
        actions = _default_actions_for_issue_type("rust_unused_import")
        assert len(actions) == 2
        assert actions[0].id == "investigate"
        assert actions[0].destructive is False
        assert actions[0].safe_auto is True
        assert actions[1].id == "fix_in_source"
        assert actions[1].destructive is False
        assert actions[1].requires_decision is True


# ---------------------------------------------------------------------------
# Clippy-specific claims mapping
# ---------------------------------------------------------------------------


class TestClippyClaimsMapping:
    @pytest.mark.parametrize("lint_code,expected_claim", [
        ("unused_imports", "rust_unused_import"),
        ("dead_code", "rust_dead_code"),
        ("clippy::dbg_macro", "rust_dbg_in_production"),
        ("clippy::unwrap_used", "rust_unwrap_in_production"),
        ("clippy::expect_used", "rust_expect_in_production"),
        ("clippy::panic", "rust_panic_in_production"),
        ("clippy::todo", "rust_todo_marker"),
        ("clippy::unimplemented", "rust_unimplemented_marker"),
        ("clippy::needless_return", "rust_needless_return"),
        ("clippy::redundant_clone", "rust_redundant_clone"),
        ("clippy::single_match", "rust_single_match"),
        ("clippy::needless_borrow", "rust_needless_borrow"),
    ])
    def test_clippy_specific_claims(self, lint_code, expected_claim):
        """Known Clippy lints map to specific evidence claims."""
        assert _claim_for_cargo_level("warning", lint_code) == expected_claim

    @pytest.mark.parametrize("lint_code", [
        "unused_imports", "dead_code", "clippy::dbg_macro",
        "clippy::unwrap_used", "clippy::expect_used",
    ])
    def test_clippy_specific_lints_elevated_strength(self, lint_code):
        """Known Clippy lints get elevated evidence strength (0.85)."""
        assert _strength_for_cargo_level("warning", lint_code) == 0.85

    def test_unknown_clippy_code_uses_level_fallback(self):
        """Unknown Clippy codes fall back to level-based strength."""
        assert _strength_for_cargo_level("warning", "unknown_lint") == 0.80
        assert _strength_for_cargo_level("error", "unknown_lint") == 0.90


# ---------------------------------------------------------------------------
# subprocess.TimeoutExpired (CRITICAL — was untested)
# ---------------------------------------------------------------------------


class TestCargoTimeout:
    def test_cargo_check_timeout_produces_oracle_failed(self, tmp_path):
        """Cargo check timeout produces rust_oracle_failed with timeout message."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch(
                "imodent.analyzers.rust.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="cargo check", timeout=120),
            ):
                findings = RustAnalyzer().analyze(context)
        oracle_failed = [f for f in findings if f.type == "rust_oracle_failed"]
        assert len(oracle_failed) == 1
        assert "timed out" in oracle_failed[0].message.lower()

    def test_cargo_clippy_timeout_produces_oracle_failed(self, tmp_path):
        """Cargo clippy timeout produces rust_oracle_failed."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_clippy=True),
            project_root=tmp_path,
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch(
                "imodent.analyzers.rust.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="cargo clippy", timeout=120),
            ):
                findings = RustAnalyzer().analyze(context)
        oracle_failed = [f for f in findings if f.type == "rust_oracle_failed"]
        assert len(oracle_failed) == 1


# ---------------------------------------------------------------------------
# --cargo-clippy CLI flag (CRITICAL — was untested)
# ---------------------------------------------------------------------------


class TestCargoClippyCLIFlag:
    def test_cargo_clippy_flag_implies_rust(self, tmp_path, monkeypatch):
        """--cargo-clippy passes run_cargo_clippy=True."""
        import imodent.cli as cli_module
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        def capture_call(paths, **kwargs):
            capture_call.kwargs = kwargs

        capture_call.kwargs = None
        monkeypatch.setattr(cli_module, "analyze_files", capture_call)
        monkeypatch.setattr("sys.argv", ["imodent", str(test_file), "--analyze", "--cargo-clippy"])
        cli_module.main()
        assert capture_call.kwargs is not None
        assert capture_call.kwargs["run_cargo_clippy"] is True


# ---------------------------------------------------------------------------
# Workspace member discovery
# ---------------------------------------------------------------------------


class TestWorkspaceDiscovery:
    def test_workspace_members_are_discovered(self, tmp_path):
        """Workspace members from [workspace] section are scanned as Cargo roots."""
        # Root workspace
        (tmp_path / "Cargo.toml").write_text(
            "[workspace]\nmembers = [\"crates/*\"]\n"
        )
        # Member crate
        member = tmp_path / "crates" / "my-crate"
        member.mkdir(parents=True)
        (member / "Cargo.toml").write_text(
            "[package]\nname = 'my-crate'\nversion = '0.1.0'\nedition = '2021'\n"
        )
        (member / "src").mkdir()
        (member / "src" / "lib.rs").write_text("pub fn hello() {}\n")

        rs_file = tmp_path / "crates" / "my-crate" / "src" / "lib.rs"
        context = AnalysisContext(
            files={
                tmp_path / "Cargo.toml": FileInfo.from_path(tmp_path / "Cargo.toml"),
                member / "Cargo.toml": FileInfo.from_path(member / "Cargo.toml"),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        # Should find lint policy missing for ROOT only — members inherit from root
        lint_missing = [f for f in findings if f.type == "rust_lint_policy_missing"]
        assert len(lint_missing) == 1
        assert lint_missing[0].file == tmp_path / "Cargo.toml"

    def test_workspace_without_members_is_handled(self, tmp_path):
        """Workspace with no members section doesn't crash."""
        (tmp_path / "Cargo.toml").write_text("[workspace]\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn demo() {}\n")
        rs_file = tmp_path / "src" / "lib.rs"
        context = AnalysisContext(
            files={
                tmp_path / "Cargo.toml": FileInfo.from_path(tmp_path / "Cargo.toml"),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        # Should not crash, should find lint policy missing
        assert any(f.type == "rust_lint_policy_missing" for f in findings)


# ---------------------------------------------------------------------------
# Residue marker skip logic
# ---------------------------------------------------------------------------


class TestResidueSkipLogic:
    def test_residue_skipped_in_tests_directory(self, tmp_path):
        """Residue markers in tests/ are not flagged."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_demo.rs").write_text("#[test]\nfn it_works() {\n    todo!(\"implement\");\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                tests_dir / "test_demo.rs": FileInfo.from_path(tests_dir / "test_demo.rs"),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert residue == []

    def test_residue_skipped_in_examples_directory(self, tmp_path):
        """Residue markers in examples/ are not flagged."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "demo.rs").write_text("fn main() {\n    dbg!(42);\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                examples_dir / "demo.rs": FileInfo.from_path(examples_dir / "demo.rs"),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert residue == []

    def test_residue_skipped_in_benches_directory(self, tmp_path):
        """Residue markers in benches/ are not flagged."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        benches_dir = tmp_path / "benches"
        benches_dir.mkdir()
        (benches_dir / "bench.rs").write_text("fn main() {\n    unimplemented!();\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                benches_dir / "bench.rs": FileInfo.from_path(benches_dir / "bench.rs"),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert residue == []

    def test_residue_skipped_in_test_prefixed_files(self, tmp_path):
        """Residue markers in test_*.rs files are not flagged."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        test_file = tmp_path / "test_integration.rs"
        test_file.write_text("#[test]\nfn it_works() {\n    unimplemented!();\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                test_file: FileInfo.from_path(test_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert residue == []

    def test_residue_skipped_in_suffix_test_files(self, tmp_path):
        """Residue markers in *_test.rs files are not flagged."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        test_file = tmp_path / "lib_test.rs"
        test_file.write_text("#[test]\nfn it_works() {\n    dbg!(42);\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                test_file: FileInfo.from_path(test_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        assert residue == []


# ---------------------------------------------------------------------------
# _parse_cargo_json_output edge cases
# ---------------------------------------------------------------------------


class TestCargoJsonParsing:
    def test_non_json_lines_are_skipped(self, tmp_path):
        """Lines not starting with { are filtered out without crashing."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        stdout = (
            "   Compiling test v0.1.0\n"
            "    Finished dev [unoptimized + debuginfo] target(s)\n"
        )
        findings = _parse_cargo_json_output(context, tmp_path, stdout, "check")
        assert findings == []

    def test_malformed_json_lines_are_skipped(self, tmp_path):
        """Lines that start with { but are invalid JSON are skipped."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        stdout = '{"reason": "compiler-message", "incomplete"\n'
        findings = _parse_cargo_json_output(context, tmp_path, stdout, "check")
        assert findings == []

    def test_no_primary_span_falls_back_to_first_span(self, tmp_path):
        """When no span has is_primary=True, falls back to spans[0]."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"E0425"},'
            '"level":"error","message":"cannot find value",'
            '"spans":[{"file_name":"src/lib.rs","line_start":5,"column_start":1,'
            '"line_end":5,"column_end":2,"is_primary":false}]}}\n'
        )
        findings = _parse_cargo_json_output(context, tmp_path, mock_json, "check")
        assert len(findings) == 1
        assert findings[0].location.line == 5

    def test_empty_spans_falls_back_to_cargo_toml(self, tmp_path):
        """When spans is empty, file falls back to Cargo.toml."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"E0425"},'
            '"level":"error","message":"cannot find value","spans":[]}}\n'
        )
        findings = _parse_cargo_json_output(context, tmp_path, mock_json, "check")
        assert len(findings) == 1
        assert findings[0].file == tmp_path / "Cargo.toml"

    def test_no_spans_field_falls_back_to_cargo_toml(self, tmp_path):
        """When spans key is missing entirely, file falls back to Cargo.toml."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"code":{"code":"E0425"},'
            '"level":"error","message":"cannot find value"}}\n'
        )
        findings = _parse_cargo_json_output(context, tmp_path, mock_json, "check")
        assert len(findings) == 1
        assert findings[0].file == tmp_path / "Cargo.toml"

    def test_missing_code_field_defaults_to_empty(self, tmp_path):
        """Diagnostic with no code field defaults to empty string."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        mock_json = (
            '{"reason":"compiler-message","message":{"level":"warning",'
            '"message":"some warning","spans":[]}}\n'
        )
        findings = _parse_cargo_json_output(context, tmp_path, mock_json, "check")
        assert len(findings) == 1
        assert findings[0].lint_code is None

    def test_non_compiler_message_reason_is_skipped(self, tmp_path):
        """Non-compiler-message reasons (e.g., build-script) are skipped."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        stdout = '{"reason":"build-script-executed","package_id":"test v0.1.0"}\n'
        findings = _parse_cargo_json_output(context, tmp_path, stdout, "check")
        assert findings == []


# ---------------------------------------------------------------------------
# _severity_for_cargo_level edge cases
# ---------------------------------------------------------------------------


class TestCargoSeverityMapping:
    @pytest.mark.parametrize("level,expected", [
        ("error", Severity.ERROR),
        ("warning", Severity.WARNING),
        ("note", Severity.HINT),
        ("help", Severity.HINT),
        ("message", Severity.INFO),
        ("unknown", Severity.INFO),
    ])
    def test_severity_for_cargo_level(self, level, expected):
        """Cargo diagnostic levels map to correct Severity."""
        assert _severity_for_cargo_level(level) == expected


# ---------------------------------------------------------------------------
# _read_file failure paths
# ---------------------------------------------------------------------------


class TestReadFileFailure:
    def test_read_nonexistent_file_returns_none(self):
        """Reading a nonexistent file returns None, not an exception."""
        result = _read_file(__import__("pathlib").Path("/nonexistent/file.rs"))
        assert result is None

    def test_read_binary_file_returns_none(self):
        """Reading a binary file returns None (encoding error)."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".rs", delete=False) as f:
            f.write(b"\x00\x01\x02\x03\xff\xfe")
            path = __import__("pathlib").Path(f.name)
        try:
            result = _read_file(path)
            assert result is None
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# _is_under_root
# ---------------------------------------------------------------------------


class TestIsUnderRoot:
    def test_path_under_root_returns_true(self):
        """Path that is under root returns True."""
        root = __import__("pathlib").Path("/project")
        path = __import__("pathlib").Path("/project/src/lib.rs")
        assert _is_under_root(path, root) is True

    def test_path_not_under_root_returns_false(self):
        """Path that is not under root returns False."""
        root = __import__("pathlib").Path("/project")
        path = __import__("pathlib").Path("/other/file.rs")
        assert _is_under_root(path, root) is False


# ---------------------------------------------------------------------------
# Residue: panic! marker
# ---------------------------------------------------------------------------


class TestPanicResidueMarker:
    def test_panic_is_flagged(self, tmp_path):
        """panic!() in source code is flagged as residue."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("pub fn demo() {\n    panic!(\"oh no\");\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        panic_findings = [f for f in residue if "panic!" in f.message]
        assert len(panic_findings) == 1


# ---------------------------------------------------------------------------
# dbg! severity escalation
# ---------------------------------------------------------------------------


class TestDbgSeverityEscalation:
    def test_dbg_in_production_code_is_warning(self, tmp_path):
        """dbg!() in non-test source code gets WARNING severity."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir()
        rs_file.write_text("pub fn demo() {\n    dbg!(42);\n}\n")
        context = AnalysisContext(
            files={
                cargo: FileInfo.from_path(cargo),
                rs_file: FileInfo.from_path(rs_file),
            },
            config=AnalysisConfig(check_rust=True),
            project_root=tmp_path,
        )
        findings = RustAnalyzer().analyze(context)
        residue = [f for f in findings if f.type == "rust_residue_marker"]
        dbg_findings = [f for f in residue if "dbg!" in f.message]
        assert len(dbg_findings) == 1
        assert dbg_findings[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Empty cargo output (zero diagnostics)
# ---------------------------------------------------------------------------


class TestEmptyCargoOutput:
    def test_cargo_check_empty_stdout(self, tmp_path):
        """Cargo check with empty stdout produces no diagnostics."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        mock_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        diagnostics = [f for f in findings if f.type == "rust_diagnostic"]
        assert diagnostics == []

    def test_cargo_nonzero_with_stderr_only(self, tmp_path):
        """Cargo nonzero return with stderr only (no stdout) produces oracle_failed."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text("[package]\nname = 'test'\nversion = '0.1.0'\nedition = '2021'\n")
        context = AnalysisContext(
            files={cargo: FileInfo.from_path(cargo)},
            config=AnalysisConfig(check_rust=True, run_cargo_check=True),
            project_root=tmp_path,
        )
        mock_result = SimpleNamespace(
            returncode=101, stdout="", stderr="error: could not compile `test`\n"
        )
        with patch("imodent.analyzers.rust.shutil.which", return_value="/usr/bin/cargo"):
            with patch("imodent.analyzers.rust.subprocess.run", return_value=mock_result):
                findings = RustAnalyzer().analyze(context)
        oracle_failed = [f for f in findings if f.type == "rust_oracle_failed"]
        assert len(oracle_failed) == 1
        assert "could not compile" in oracle_failed[0].message


# ---------------------------------------------------------------------------
# Config coercion error paths
# ---------------------------------------------------------------------------


class TestConfigCoercion:
    def test_coerce_bool_invalid_string_uses_default(self):
        """Invalid boolean string falls back to default."""
        from imodent.project.config import _coerce_bool
        assert _coerce_bool("maybe", "test_field", default=True) is True
        assert _coerce_bool("maybe", "test_field", default=False) is False

    def test_coerce_bool_valid_strings(self):
        """Valid boolean strings are coerced correctly."""
        from imodent.project.config import _coerce_bool
        assert _coerce_bool("true", "f", default=False) is True
        assert _coerce_bool("yes", "f", default=False) is True
        assert _coerce_bool("on", "f", default=False) is True
        assert _coerce_bool("1", "f", default=False) is True
        assert _coerce_bool("false", "f", default=True) is False
        assert _coerce_bool("no", "f", default=True) is False
        assert _coerce_bool("off", "f", default=True) is False
        assert _coerce_bool("0", "f", default=True) is False

    def test_coerce_str_list_invalid_type_uses_default(self):
        """Invalid type falls back to default."""
        from imodent.project.config import _coerce_str_list
        default = ["*.py"]
        assert _coerce_str_list(42, "test_field", default=default) == default
        assert _coerce_str_list(None, "test_field", default=default) == default

    def test_coerce_str_list_string_becomes_singleton(self):
        """A single string becomes a one-element list."""
        from imodent.project.config import _coerce_str_list
        assert _coerce_str_list("*.py", "test_field", default=[]) == ["*.py"]

    def test_coerce_str_list_valid_list(self):
        """A valid list passes through."""
        from imodent.project.config import _coerce_str_list
        assert _coerce_str_list(["*.py", "*.rs"], "test_field", default=[]) == ["*.py", "*.rs"]


# ---------------------------------------------------------------------------
# DecisionCandidate.to_dict() and ActionOption.to_dict()
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_action_option_to_dict(self):
        """ActionOption serializes to dict correctly."""
        from imodent.analysis.decisions import ActionOption
        action = ActionOption(
            id="review_policy",
            label="Review Rust policy",
            description="Decide policy",
            destructive=False,
            safe_auto=True,
            requires_decision=False,
        )
        d = action.to_dict()
        assert d["id"] == "review_policy"
        assert d["label"] == "Review Rust policy"
        assert d["destructive"] is False
        assert d["safe_auto"] is True
        assert d["requires_decision"] is False

    def test_decision_candidate_to_dict(self):
        """DecisionCandidate serializes to dict correctly."""
        sk = subject_key_for_lint(
            __import__("pathlib").Path("src/lib.rs"), "rust_broad_allow"
        )
        candidate = DecisionCandidate(
            issue_type="rust_advisory",
            subject_key=sk,
            confidence=0.75,
            confidence_label="medium",
            finding_ids=["abc123"],
        )
        d = candidate.to_dict()
        assert d["issue_type"] == "rust_advisory"
        assert d["confidence"] == 0.75
        assert d["finding_ids"] == ["abc123"]
        assert "subject_key" in d


# ---------------------------------------------------------------------------
# _display_confidence_output
# ---------------------------------------------------------------------------


class TestDisplayConfidenceOutput:
    def test_display_with_candidates(self, capsys):
        """_display_confidence_output prints candidates."""
        from imodent.cli import _display_confidence_output
        sk = subject_key_for_lint(
            __import__("pathlib").Path("src/lib.rs"), "rust_broad_allow"
        )
        candidate = DecisionCandidate(
            issue_type="rust_advisory",
            subject_key=sk,
            confidence=0.75,
            confidence_label="medium",
            suggested_actions=_default_actions_for_issue_type("rust_advisory"),
        )
        result = SimpleNamespace(candidates=[candidate])
        _display_confidence_output(result, verbose=False)
        captured = capsys.readouterr()
        assert "RUST_ADVISORY" in captured.out
        assert "0.75" in captured.out

    def test_display_with_no_candidates(self, capsys):
        """_display_confidence_output with no candidates prints message."""
        from imodent.cli import _display_confidence_output
        result = SimpleNamespace(candidates=[])
        _display_confidence_output(result)
        captured = capsys.readouterr()
        assert "No decision candidates" in captured.out

    def test_display_verbose_shows_evidence(self, capsys):
        """Verbose mode shows evidence claims."""
        from imodent.cli import _display_confidence_output
        from imodent.analysis.evidence import Evidence
        sk = subject_key_for_lint(
            __import__("pathlib").Path("src/lib.rs"), "rust_broad_allow"
        )
        ev = Evidence(
            kind="SourcePattern",
            file=__import__("pathlib").Path("src/lib.rs"),
            location=Location(line=1),
            source="rust-analyzer",
            subject="broad_allow",
            claim="rust_broad_suppression",
            polarity="supports",
            strength=0.75,
        )
        candidate = DecisionCandidate(
            issue_type="rust_advisory",
            subject_key=sk,
            confidence=0.75,
            confidence_label="medium",
            evidence_for=[ev],
        )
        result = SimpleNamespace(candidates=[candidate])
        _display_confidence_output(result, verbose=True)
        captured = capsys.readouterr()
        assert "rust_broad_suppression" in captured.out


# ---------------------------------------------------------------------------
# Confidence scoring for additional issue types
# ---------------------------------------------------------------------------


class TestConfidenceScoringExtended:
    def test_duplicate_import_confidence(self):
        """Duplicate import confidence is 0.95."""
        f = Finding.create(
            type="duplicate_import",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("mod.py"),
            message="duplicate import",
            fixable=False,
            auto_fix_safe=False,
        )
        score = _score_confidence(f, [f], [])
        assert score == 0.95

    def test_import_intent_confidence(self):
        """Import intent confidence is 0.30."""
        f = Finding.create(
            type="import_intent",
            severity=Severity.INFO,
            file=__import__("pathlib").Path("mod.py"),
            message="re-export",
            fixable=False,
            auto_fix_safe=False,
        )
        score = _score_confidence(f, [f], [])
        assert score == 0.30

    def test_rust_diagnostic_with_clippy_evidence(self):
        """Rust diagnostic with ClippyDiagnostic evidence gets elevated confidence."""
        from imodent.analysis.evidence import Evidence
        f = Finding.create(
            type="rust_diagnostic",
            severity=Severity.WARNING,
            file=__import__("pathlib").Path("src/lib.rs"),
            message="clippy warning",
            fixable=False,
            auto_fix_safe=False,
        )
        ev = Evidence(
            kind="ClippyDiagnostic",
            file=__import__("pathlib").Path("src/lib.rs"),
            location=Location(line=1),
            source="cargo clippy",
            subject="clippy::dbg_macro",
            claim="rust_dbg_in_production",
            polarity="supports",
            strength=0.85,
        )
        score = _score_confidence(f, [f], [ev])
        assert score >= 0.80

    def test_unknown_issue_type_falls_back_to_review(self):
        """Unknown issue type gets 'review' action."""
        actions = _default_actions_for_issue_type("unknown_type_xyz")
        assert len(actions) == 1
        assert actions[0].id == "review"
        assert actions[0].requires_decision is True
