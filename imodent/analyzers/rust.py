"""Rust advisory analyzer — fast config scan + optional Cargo/Clippy oracle."""

# NOTE: This file exceeds the 500-line structural review threshold (825 lines).
# Consider splitting into smaller modules when this module next undergoes major changes.

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

from ..analysis.evidence import Evidence
from ..analysis.findings import Finding, Location, Severity
from .base import Analyzer, AnalyzerCapability

if TYPE_CHECKING:
    from pathlib import Path

    from ..analysis.context import AnalysisContext


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
            path for path, fi in context.files.items() if fi.language == "rust"
        ]
        toml_files = [
            path for path, fi in context.files.items() if fi.language == "toml"
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
            ws_root = (
                context.project_root if context.project_root in cargo_roots else None
            )
            findings.extend(_scan_cargo_root(context, root, workspace_root=ws_root))

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

_UNSAFE_NO_SAFETY_RE = re.compile(r"unsafe\s*\{")
_SAFETY_COMMENT_RE = re.compile(r"//\s*SAFETY:")
_UNWRAP_RE = re.compile(r"\.(?:unwrap|expect)\s*\(")


def _scan_cargo_root(
    context: AnalysisContext,
    root: Path,
    workspace_root: Path | None = None,
) -> list[Finding]:
    """Scan a Cargo root for config advisory findings.

    If workspace_root is provided and differs from root, this is a workspace member
    — skip config checks that are inherited from the workspace root.
    """
    findings: list[Finding] = []
    is_workspace_member = workspace_root is not None and workspace_root != root
    cargo_toml_path = root / "Cargo.toml"
    cargo_content = _read_file(cargo_toml_path)

    # Determine what the workspace root has for config inheritance
    ws_cargo_content = None
    ws_has_clippy = False
    if workspace_root and workspace_root != root:
        ws_cargo = workspace_root / "Cargo.toml"
        ws_cargo_content = _read_file(ws_cargo)
        if ws_cargo_content:
            ws_has_clippy = (
                "clippy" in ws_cargo_content.lower()
                and "[lints" in ws_cargo_content.lower()
            )
        # Check for clippy.toml at workspace root
        if not ws_has_clippy:
            ws_has_clippy = (workspace_root / "clippy.toml").exists()

    # 5.1 Lint policy missing
    if not is_workspace_member:
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
                    file=(
                        cargo_toml_path
                        if cargo_toml_path.exists()
                        else root / "Cargo.toml"
                    ),
                    message=(
                        "No [lints] or [workspace.lints] section in Cargo.toml. "
                        "Consider defining an explicit Rust/Clippy lint policy."
                    ),
                    fixable=False,
                    auto_fix_safe=False,
                    data={"proof_state": "RAW"},
                )
            )

    # 5.2 Clippy config missing (skip for workspace members inheriting from root)
    if not is_workspace_member:
        clippy_toml_path = root / "clippy.toml"
        clippy_exists = clippy_toml_path.exists() or clippy_toml_path in context.files
        has_clippy_policy = clippy_exists
        if not has_clippy_policy and cargo_content is not None:
            has_clippy_policy = (
                "clippy" in cargo_content.lower() and "[lints" in cargo_content.lower()
            )

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

    # 5.3 Rustfmt config missing (skip for workspace members inheriting from root)
    if not is_workspace_member:
        rustfmt_toml_path = root / "rustfmt.toml"
        rustfmt_exists = (
            rustfmt_toml_path.exists() or rustfmt_toml_path in context.files
        )
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

    # 5.4 Tooling checks (skip for workspace members — config is at root)
    if not is_workspace_member:
        # rust-toolchain.toml
        toolchain_path = root / "rust-toolchain.toml"
        if not toolchain_path.exists() and not (root / "rust-toolchain").exists():
            findings.append(
                Finding.create(
                    type="rust_toolchain_missing",
                    severity=Severity.HINT,
                    file=root / "rust-toolchain.toml",
                    message=(
                        "No rust-toolchain.toml found. Pinning the Rust toolchain "
                        "ensures reproducible builds across environments."
                    ),
                    fixable=False,
                    auto_fix_safe=False,
                    data={"proof_state": "RAW"},
                )
            )

        # cargo-deny
        deny_toml = root / "deny.toml"
        if not deny_toml.exists() and not (root / "deny.lock").exists():
            findings.append(
                Finding.create(
                    type="rust_cargo_deny_missing",
                    severity=Severity.HINT,
                    file=deny_toml,
                    message=(
                        "No deny.toml found. cargo-deny checks licenses, "
                        "advisories, and duplicate dependencies."
                    ),
                    fixable=False,
                    auto_fix_safe=False,
                    data={"proof_state": "RAW"},
                )
            )

        # cargo-machete
        has_machete = False
        if cargo_content and "cargo-machete" in cargo_content.lower():
            has_machete = True
        if not has_machete:
            findings.append(
                Finding.create(
                    type="rust_cargo_machete_missing",
                    severity=Severity.HINT,
                    file=cargo_toml_path,
                    message=(
                        "No cargo-machete reference found. "
                        "cargo-machete detects unused dependencies."
                    ),
                    fixable=False,
                    auto_fix_safe=False,
                    data={"proof_state": "RAW"},
                )
            )

        # Release profile tuning
        if cargo_content:
            has_release_profile = (
                "[profile.release]" in cargo_content
                or "[profile.dist]" in cargo_content
            )
            if not has_release_profile:
                findings.append(
                    Finding.create(
                        type="rust_release_profile_missing",
                        severity=Severity.HINT,
                        file=cargo_toml_path,
                        message=(
                            "No [profile.release] or [profile.dist] found. "
                            "Consider tuning LTO, codegen-units, and strip for smaller binaries."
                        ),
                        fixable=False,
                        auto_fix_safe=False,
                        data={"proof_state": "RAW"},
                    )
                )

    # 5.5 Broad allow in .rs source
    rust_files = [
        path
        for path, fi in context.files.items()
        if fi.language == "rust" and _is_under_root(path, root)
    ]
    for rs_path in rust_files:
        content = _read_file(rs_path)
        if content is None:
            continue
        lines = content.splitlines()
        for line_no, line in enumerate(lines, 1):
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
        lines = content.splitlines()
        # Skip test files and examples for residue markers — these are expected there
        rel = (
            str(rs_path.relative_to(root))
            if _is_under_root(rs_path, root)
            else str(rs_path)
        )
        rel_parts = set(rel.split("/"))
        is_test_or_example = (
            "tests" in rel_parts
            or "examples" in rel_parts
            or "benches" in rel_parts
            or "/tests/" in rel
            or "/examples/" in rel
            or "/benches/" in rel
            or rs_path.name.startswith("test_")
            or rs_path.name.endswith("_test.rs")
        )
        # Also skip #[cfg(test)] modules — heuristic: file is under tests/ or name contains test
        in_test_context = is_test_or_example

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip lines inside #[cfg(test)] blocks (heuristic: skip in test files)
            if in_test_context:
                continue
            for marker, label in _RESIDUE_MARKERS.items():
                if marker in stripped:
                    # dbg! outside test/example is genuinely worth flagging
                    sev = Severity.WARNING if marker == "dbg!" else Severity.INFO
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

            # 5.6 unsafe without SAFETY comment (skip test/example files)
            if not in_test_context and _UNSAFE_NO_SAFETY_RE.search(stripped):
                # Check if the NEXT non-empty line has a SAFETY comment
                has_safety = False
                for future_idx in range(line_no, min(line_no + 3, len(lines))):
                    future_line = lines[future_idx].strip()
                    if future_line and _SAFETY_COMMENT_RE.search(future_line):
                        has_safety = True
                        break
                    if future_line and future_line != stripped:
                        break  # Non-empty, non-matching line — no SAFETY comment
                if not has_safety:
                    findings.append(
                        Finding.create(
                            type="rust_unsafe_no_safety",
                            severity=Severity.WARNING,
                            file=rs_path,
                            location=Location(line=line_no),
                            message="unsafe block without // SAFETY: comment. Add a SAFETY comment explaining why this is safe.",
                            fixable=False,
                            auto_fix_safe=False,
                            data={"proof_state": "RAW"},
                        )
                    )

            # 5.7 unwrap() in library code (skip test/example/bench files)
            if not in_test_context and _UNWRAP_RE.search(stripped):
                findings.append(
                    Finding.create(
                        type="rust_unwrap_in_library",
                        severity=Severity.WARNING,
                        file=rs_path,
                        location=Location(line=line_no),
                        message="unwrap() or expect() in library code. Use error handling (Result/Option) instead.",
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
    return _run_cargo_command(
        context, root, "check", ["cargo", "check", "--message-format=json"]
    )


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
        completed = subprocess.run(  # noqa: S603
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
        diag_code = (
            diag_code_obj.get("code", "") if isinstance(diag_code_obj, dict) else ""
        )
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
        evidence_kind = (
            "CargoDiagnostic" if command_name == "check" else "ClippyDiagnostic"
        )
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
    """Best-effort UTF-8 file reader.

    Returns file content, or None if the path is a symlink/fifo/socket,
    exceeds 10 MB, or cannot be accessed due to I/O or encoding errors.

    Log levels by failure mode:
      - DEBUG: file-type skip (symlink, FIFO, socket, oversized)
      - WARNING: file-type inspection failure (cannot lstat)
      - WARNING: stat failure (cannot get metadata)
      - WARNING: read failure (I/O error)
      - WARNING: encoding error (UnicodeDecodeError)

    Callers can distinguish intentional skips (DEBUG) from unexpected
    failures (WARNING) via log level filtering.
    """
    try:
        if path.is_symlink():
            logging.debug("Skipping symlink: %s", path)
            return None
        if path.is_fifo():
            logging.debug("Skipping FIFO: %s", path)
            return None
        if path.is_socket():
            logging.debug("Skipping socket: %s", path)
            return None
    except OSError as e:
        logging.warning("Cannot inspect file type for %s: %s", path, e)
        return None

    # Guard against oversized files — stat the path first.
    try:
        size = path.stat().st_size
    except OSError as e:
        logging.warning("Cannot stat %s: %s", path, e)
        return None

    if size > 10_000_000:
        logging.debug("Skipping oversized file (%d bytes): %s", size, path)
        return None

    # Read the content.
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        logging.warning("Encoding error reading %s: %s", path, e)
        return None
    except OSError as e:
        logging.warning("Cannot read %s: %s", path, e)
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
