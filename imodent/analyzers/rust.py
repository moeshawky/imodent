"""Rust advisory analyzer — fast config scan + optional Cargo/Clippy oracle."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Analyzer, AnalyzerCapability
from ..analysis.context import AnalysisContext
from ..analysis.evidence import Evidence
from ..analysis.findings import Finding, Location, Severity

if TYPE_CHECKING:
    pass


class RustAnalyzer(Analyzer):
    """Advisory analyzer for Rust projects.

    Does three things:
    1. Cargo root discovery
    2. Fast config/source advisory (no external tools)
    3. Optional Cargo/Clippy oracle (when enabled via config)
    """

    @property
    def name(self) -> str:
        return "rust"

    @property
    def capabilities(self):
        return {AnalyzerCapability.LINT, AnalyzerCapability.STYLE}

    @property
    def languages(self):
        return {"rust", "toml"}

    def analyze(self, context: AnalysisContext) -> list[Finding]:
        if not context.config.check_rust:
            return []

        rust_files = [
            path
            for path, fi in context.files.items()
            if fi.language == "rust"
        ]
        toml_files = [
            path
            for path, fi in context.files.items()
            if fi.language == "toml"
        ]
        if not rust_files and not toml_files:
            return []

        findings: list[Finding] = []
        cargo_roots = _discover_cargo_roots(context, rust_files, toml_files)

        if rust_files and not cargo_roots:
            findings.append(
                Finding.create(
                    type="rust_project_unmanaged",
                    severity=Severity.INFO,
                    file=rust_files[0],
                    message="Rust files found outside a Cargo project (no Cargo.toml discovered).",
                    fixable=False,
                    auto_fix_safe=False,
                    data={"proof_state": "INSUFFICIENT_EVIDENCE"},
                )
            )

        for root in cargo_roots:
            findings.extend(_scan_cargo_root(context, root))

        if context.config.run_cargo or context.config.run_cargo_check:
            for root in cargo_roots:
                findings.extend(_run_cargo_check(context, root))
        if context.config.run_cargo or context.config.run_cargo_clippy:
            for root in cargo_roots:
                findings.extend(_run_cargo_clippy(context, root))

        return findings


# ---------------------------------------------------------------------------
# Cargo root discovery
# ---------------------------------------------------------------------------


def _discover_cargo_roots(
    context: AnalysisContext,
    rust_files: list[Path],
    toml_files: list[Path],
) -> list[Path]:
    """Find stable sorted set of Cargo roots.

    Discovers:
    - Project root Cargo.toml
    - Cargo.toml files discovered from .rs files (walk up)
    - Workspace members from root Cargo.toml [workspace] section
    """
    roots: set[Path] = set()

    # Check project root
    if context.project_root is not None:
        cargo_toml = context.project_root / "Cargo.toml"
        if cargo_toml.exists() or cargo_toml in context.files:
            roots.add(context.project_root)

    # Check Cargo.toml files in context
    for p in toml_files:
        if p.name == "Cargo.toml":
            roots.add(p.parent)

    # Walk upward from .rs files
    for rs_path in rust_files:
        current = rs_path.parent
        while current != current.parent:
            if (current / "Cargo.toml").exists():
                roots.add(current)
                break
            current = current.parent

    # Discover workspace members from root Cargo.toml
    if context.project_root is not None:
        root_cargo = context.project_root / "Cargo.toml"
        if root_cargo.exists():
            content = _read_file(root_cargo)
            if content and "[workspace]" in content:
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("members") and "=" in stripped:
                        # Parse: members = ["crate1", "crate2"] or members = ["crate1"]
                        import re as _re
                        for match in _re.finditer(r'"([^"]+)"', stripped):
                            member = match.group(1)
                            member_path = context.project_root / member / "Cargo.toml"
                            if member_path.exists():
                                roots.add(context.project_root / member)

    return sorted(roots)


# ---------------------------------------------------------------------------
# Fast config advisory (no external tools)
# ---------------------------------------------------------------------------

_BROAD_ALLOW_RE = re.compile(
    r"#!?\[allow\(\s*(?:warnings|clippy::all|clippy::nursery|clippy::pedantic)\s*\)\]"
)

_RESIDUE_MARKERS = {
    "todo!": "todo!",
    "unimplemented!": "unimplemented!",
    "dbg!": "dbg!",
    "panic!": "panic!",
}


def _scan_cargo_root(
    context: AnalysisContext,
    root: Path,
) -> list[Finding]:
    """Scan a Cargo root for config advisory findings."""
    findings: list[Finding] = []
    cargo_toml_path = root / "Cargo.toml"
    clippy_toml_path = root / "clippy.toml"
    rustfmt_toml_path = root / "rustfmt.toml"

    cargo_content = _read_file(cargo_toml_path)
    clippy_exists = clippy_toml_path.exists() or clippy_toml_path in context.files
    rustfmt_exists = rustfmt_toml_path.exists() or rustfmt_toml_path in context.files

    # 5.1 Lint policy missing
    has_lint_policy = False
    if cargo_content is not None:
        has_lint_policy = (
            "[lints]" in cargo_content
            or "[workspace.lints]" in cargo_content
            or "[workspace.lints." in cargo_content
            or "[lints." in cargo_content
        )

    if not has_lint_policy:
        findings.append(
            Finding.create(
                type="rust_lint_policy_missing",
                severity=Severity.INFO,
                file=cargo_toml_path if cargo_toml_path.exists() else root / "Cargo.toml",
                message=(
                    "No [lints] or [workspace.lints] section in Cargo.toml. "
                    "Consider defining an explicit Rust/Clippy lint policy."
                ),
                fixable=False,
                auto_fix_safe=False,
                data={"proof_state": "RAW"},
            )
        )

    # 5.2 Clippy config missing
    has_clippy_policy = clippy_exists
    if not has_clippy_policy and cargo_content is not None:
        has_clippy_policy = "clippy" in cargo_content.lower() and "[lints" in cargo_content.lower()

    if not has_clippy_policy:
        findings.append(
            Finding.create(
                type="rust_clippy_config_missing",
                severity=Severity.HINT,
                file=clippy_toml_path if clippy_exists else (root / "clippy.toml"),
                message=(
                    "No clippy.toml or Clippy lint policy found. "
                    "Consider adding clippy.toml or [workspace.lints.clippy] in Cargo.toml."
                ),
                fixable=False,
                auto_fix_safe=False,
                data={"proof_state": "RAW"},
            )
        )

    # 5.3 Rustfmt config missing
    if not rustfmt_exists:
        findings.append(
            Finding.create(
                type="rust_rustfmt_config_missing",
                severity=Severity.HINT,
                file=root / "rustfmt.toml",
                message=(
                    "No rustfmt.toml found. Rustfmt defaults are valid, "
                    "but an explicit config helps teams enforce consistent style."
                ),
                fixable=False,
                auto_fix_safe=False,
                data={"proof_state": "RAW"},
            )
        )

    # 5.4 Broad allow in .rs source
    rust_files = [
        path
        for path, fi in context.files.items()
        if fi.language == "rust" and _is_under_root(path, root)
    ]
    for rs_path in rust_files:
        content = _read_file(rs_path)
        if content is None:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if _BROAD_ALLOW_RE.search(line):
                findings.append(
                    Finding.create(
                        type="rust_broad_allow",
                        severity=Severity.WARNING,
                        file=rs_path,
                        location=Location(line=line_no),
                        message=(
                            f"Broad suppression found: {line.strip()}. "
                            "Consider narrowing to specific lints."
                        ),
                        fixable=False,
                        auto_fix_safe=False,
                        data={
                            "proof_state": "RAW",
                            "evidence": [
                                Evidence(
                                    kind="SourcePattern",
                                    file=rs_path,
                                    location=Location(line=line_no),
                                    source="rust-analyzer",
                                    subject="broad_allow",
                                    claim="rust_broad_suppression",
                                    polarity="supports",
                                    strength=0.75,
                                ).to_dict(),
                            ],
                        },
                    )
                )

    # 5.5 Residue markers
    for rs_path in rust_files:
        content = _read_file(rs_path)
        if content is None:
            continue
        # Skip test files and examples for residue markers — these are expected there
        rel = str(rs_path.relative_to(root)) if _is_under_root(rs_path, root) else str(rs_path)
        is_test_or_example = (
            "/tests/" in rel
            or "/examples/" in rel
            or "/benches/" in rel
            or rs_path.name.startswith("test_")
            or rs_path.name.endswith("_test.rs")
        )
        # Also skip #[cfg(test)] modules — heuristic: file is under tests/ or name contains test
        in_test_context = is_test_or_example

        for line_no, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Skip lines inside #[cfg(test)] blocks (heuristic: skip in test files)
            if in_test_context:
                continue
            for marker, label in _RESIDUE_MARKERS.items():
                if marker in stripped:
                    # dbg! outside test/example is genuinely worth flagging
                    if marker == "dbg!":
                        sev = Severity.WARNING
                    else:
                        sev = Severity.INFO
                    findings.append(
                        Finding.create(
                            type="rust_residue_marker",
                            severity=sev,
                            file=rs_path,
                            location=Location(line=line_no),
                            message=f"Residue marker `{label}` found. Verify intentional use.",
                            fixable=False,
                            auto_fix_safe=False,
                            data={"proof_state": "RAW"},
                        )
                    )

    return findings


# ---------------------------------------------------------------------------
# Optional Cargo/Clippy oracle
# ---------------------------------------------------------------------------


def _run_cargo_check(
    context: AnalysisContext,
    root: Path,
) -> list[Finding]:
    """Run cargo check --message-format=json and parse diagnostics."""
    return _run_cargo_command(context, root, "check", ["cargo", "check", "--message-format=json"])


def _run_cargo_clippy(
    context: AnalysisContext,
    root: Path,
) -> list[Finding]:
    """Run cargo clippy --message-format=json and parse diagnostics."""
    return _run_cargo_command(
        context,
        root,
        "clippy",
        ["cargo", "clippy", "--message-format=json", "--all-targets", "--all-features"],
    )


def _run_cargo_command(
    context: AnalysisContext,
    root: Path,
    command_name: str,
    command: list[str],
) -> list[Finding]:
    """Run a cargo command, parse JSON diagnostics, return findings."""
    cargo_bin = shutil.which("cargo")
    if cargo_bin is None:
        return [
            Finding.create(
                type="rust_oracle_unavailable",
                severity=Severity.WARNING,
                file=root / "Cargo.toml",
                message=f"cargo is not available on PATH; cannot run {command_name}.",
                fixable=False,
                auto_fix_safe=False,
                lint_source=f"cargo-{command_name}",
                data={"proof_state": "INSUFFICIENT_EVIDENCE"},
            )
        ]

    command[0] = cargo_bin
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return [
            Finding.create(
                type="rust_oracle_failed",
                severity=Severity.WARNING,
                file=root / "Cargo.toml",
                message=f"cargo {command_name} timed out after 120s.",
                fixable=False,
                auto_fix_safe=False,
                lint_source=f"cargo-{command_name}",
                data={"proof_state": "INSUFFICIENT_EVIDENCE"},
            )
        ]
    except OSError as exc:
        return [
            Finding.create(
                type="rust_oracle_failed",
                severity=Severity.WARNING,
                file=root / "Cargo.toml",
                message=f"cargo {command_name} could not run: {exc}",
                fixable=False,
                auto_fix_safe=False,
                lint_source=f"cargo-{command_name}",
                data={"proof_state": "INSUFFICIENT_EVIDENCE"},
            )
        ]

    stdout = completed.stdout or ""
    findings = _parse_cargo_json_output(context, root, stdout, command_name)

    if completed.returncode != 0 and not findings:
        findings.append(
            Finding.create(
                type="rust_oracle_failed",
                severity=Severity.WARNING,
                file=root / "Cargo.toml",
                message=completed.stderr.strip()
                or f"cargo {command_name} exited with status {completed.returncode}.",
                fixable=False,
                auto_fix_safe=False,
                lint_source=f"cargo-{command_name}",
                data={
                    "proof_state": "INSUFFICIENT_EVIDENCE",
                    "returncode": completed.returncode,
                },
            )
        )

    return findings


def _parse_cargo_json_output(
    context: AnalysisContext,
    root: Path,
    stdout: str,
    command_name: str,
) -> list[Finding]:
    """Parse newline-delimited JSON from cargo output.

    Filters non-JSON lines (progress bars, download status, etc.)
    and only processes compiler-message diagnostics.
    """
    findings: list[Finding] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("reason") != "compiler-message":
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue

        diag_code_obj = message.get("code")
        diag_code = diag_code_obj.get("code", "") if isinstance(diag_code_obj, dict) else ""
        level = message.get("level", "error")
        msg_text = message.get("message", "")
        spans = message.get("spans", [])

        primary_span = None
        if isinstance(spans, list):
            for span in spans:
                if isinstance(span, dict) and span.get("is_primary"):
                    primary_span = span
                    break
            if primary_span is None and spans:
                primary_span = spans[0]

        file_name = None
        location = None
        if isinstance(primary_span, dict):
            file_name = primary_span.get("file_name")
            line_start = primary_span.get("line_start")
            col_start = primary_span.get("column_start")
            line_end = primary_span.get("line_end")
            col_end = primary_span.get("column_end")
            if file_name and line_start:
                file_path = (root / file_name).resolve()
                location = Location(
                    line=int(line_start),
                    column=int(col_start) if col_start else None,
                    end_line=int(line_end) if line_end else None,
                    end_column=int(col_end) if col_end else None,
                )
            else:
                file_path = root / "Cargo.toml"
        else:
            file_path = root / "Cargo.toml"

        severity = _severity_for_cargo_level(level)
        evidence_kind = "CargoDiagnostic" if command_name == "check" else "ClippyDiagnostic"
        evidence_claim = _claim_for_cargo_level(level, diag_code)

        ev = Evidence(
            kind=evidence_kind,
            file=file_path,
            location=location,
            source=f"cargo {command_name}",
            subject=diag_code,
            data={
                "code": diag_code,
                "level": level,
                "message": msg_text,
            },
            claim=evidence_claim,
            polarity="supports",
            strength=_strength_for_cargo_level(level, diag_code),
        )
        context.add_evidence(ev)

        findings.append(
            Finding.create(
                type="rust_diagnostic",
                severity=severity,
                file=file_path,
                location=location,
                message=f"{diag_code}: {msg_text}" if diag_code else msg_text,
                fixable=False,
                auto_fix_safe=False,
                lint_code=diag_code or None,
                lint_source=f"cargo-{command_name}",
                data={
                    "proof_state": "EXTERNALLY_VERIFIED",
                    "evidence": [ev.to_dict()],
                },
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_file(path: Path) -> str | None:
    """Read a file, returning None on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _is_under_root(path: Path, root: Path) -> bool:
    """Check if path is under root directory."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _severity_for_cargo_level(level: str) -> Severity:
    """Map cargo diagnostic level to Severity."""
    if level == "error":
        return Severity.ERROR
    if level == "warning":
        return Severity.WARNING
    if level in ("note", "help"):
        return Severity.HINT
    return Severity.INFO


# High-signal Clippy lints that deserve specific claims
_CLIPPY_SPECIFIC_CLAIMS = {
    "unused_imports": "rust_unused_import",
    "dead_code": "rust_dead_code",
    "clippy::dbg_macro": "rust_dbg_in_production",
    "clippy::unwrap_used": "rust_unwrap_in_production",
    "clippy::expect_used": "rust_expect_in_production",
    "clippy::panic": "rust_panic_in_production",
    "clippy::todo": "rust_todo_marker",
    "clippy::unimplemented": "rust_unimplemented_marker",
    "clippy::needless_return": "rust_needless_return",
    "clippy::redundant_clone": "rust_redundant_clone",
    "clippy::single_match": "rust_single_match",
    "clippy::needless_borrow": "rust_needless_borrow",
}


def _strength_for_cargo_level(level: str, diag_code: str = "") -> float:
    """Map cargo diagnostic level and code to evidence strength."""
    # High-signal Clippy lints get elevated strength
    if diag_code in _CLIPPY_SPECIFIC_CLAIMS:
        return 0.85
    if level == "error":
        return 0.90
    if level == "warning":
        return 0.80
    return 0.50


def _claim_for_cargo_level(level: str, diag_code: str = "") -> str:
    """Map cargo diagnostic level and code to evidence claim."""
    if diag_code in _CLIPPY_SPECIFIC_CLAIMS:
        return _CLIPPY_SPECIFIC_CLAIMS[diag_code]
    if level == "error":
        return "rust_compile_error"
    if level == "warning":
        return "rust_lint_warning"
    return "rust_tooling_advice"
