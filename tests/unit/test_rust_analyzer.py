"""Tests for the Rust advisory analyzer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
from imodent.analysis.decisions import DecisionEngine, _issue_type_from_finding, _default_actions_for_issue_type
from imodent.analysis.findings import Finding, Severity, Location
from imodent.analyzers.rust import RustAnalyzer
from imodent.project.project_context import ProjectContext


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
        assert _destructive_allowed(candidate, None, []) is False


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
