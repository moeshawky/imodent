"""Ruff-backed lint analyzer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .base import Analyzer, AnalyzerCapability
from ..analysis.context import AnalysisContext
from ..analysis.evidence import Evidence
from ..analysis.findings import Finding, Location, Severity
from ..analysis.decisions import subject_key_for_lint  # noqa: E402


class LintAnalyzer(Analyzer):
    """Run Ruff and convert JSON diagnostics into findings."""

    @property
    def name(self) -> str:
        return "lint"

    @property
    def capabilities(self):
        return {AnalyzerCapability.LINT}

    @property
    def languages(self):
        return {"python"}

    def analyze(self, context: AnalysisContext) -> list[Finding]:
        if not context.config.use_ruff:
            return []

        files = [
            path
            for path, file_info in context.files.items()
            if file_info.language == "python"
        ]
        if not files:
            return []

        command_prefix = _ruff_command_prefix()
        if command_prefix is None:
            return [
                Finding.create(
                    type="lint_oracle_unavailable",
                    severity=Severity.WARNING,
                    file=_first_file(context),
                    location=None,
                    message="Ruff lint oracle is enabled but Ruff is not available.",
                    fixable=False,
                    auto_fix_safe=False,
                    lint_source="ruff",
                    data={"proof_state": "INSUFFICIENT_EVIDENCE"},
                )
            ]

        project_root = context.project_root or min(
            (path.parent for path in files), key=lambda p: len(p.parts)
        )

        command = [
            *command_prefix,
            "check",
            "--output-format=json",
            "--no-fix",
            *[str(path) for path in files],
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(project_root) if isinstance(project_root, Path) else None,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            return [
                Finding.create(
                    type="lint_oracle_failed",
                    severity=Severity.WARNING,
                    file=_first_file(context),
                    location=None,
                    message=f"Ruff lint oracle could not run: {exc}",
                    fixable=False,
                    auto_fix_safe=False,
                    lint_source="ruff",
                    data={"proof_state": "INSUFFICIENT_EVIDENCE"},
                )
            ]

        if completed.returncode not in (0, 1):
            return [
                Finding.create(
                    type="lint_oracle_failed",
                    severity=Severity.WARNING,
                    file=_first_file(context),
                    location=None,
                    message=completed.stderr.strip()
                    or f"Ruff exited with status {completed.returncode}.",
                    fixable=False,
                    auto_fix_safe=False,
                    lint_source="ruff",
                    data={
                        "proof_state": "INSUFFICIENT_EVIDENCE",
                        "returncode": completed.returncode,
                    },
                )
            ]

        try:
            diagnostics = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            return [
                Finding.create(
                    type="lint_oracle_failed",
                    severity=Severity.WARNING,
                    file=_first_file(context),
                    location=None,
                    message=f"Ruff returned invalid JSON: {exc}",
                    fixable=False,
                    auto_fix_safe=False,
                    lint_source="ruff",
                    data={"proof_state": "INSUFFICIENT_EVIDENCE"},
                )
            ]

        findings = []
        for diagnostic in diagnostics:
            file_path = Path(diagnostic["filename"]).resolve()
            location_data = diagnostic.get("location") or {}
            end_location_data = diagnostic.get("end_location") or {}
            location = Location(
                line=int(location_data.get("row") or 1),
                column=location_data.get("column"),
                end_line=end_location_data.get("row"),
                end_column=end_location_data.get("column"),
            )
            code = diagnostic.get("code") or "RUF"
            message = diagnostic.get("message") or "Ruff lint diagnostic"
            proof_state_raw = _proof_state_for_ruff_code(code, file_path)

            # Build subject key from diagnostic message (for fusion support)
            sk = _subject_key_from_diagnostic(diagnostic, file_path, code)

            primary_evidence = Evidence(
                kind="RuffDiagnostic",
                source="ruff",
                file=file_path,
                location=location,
                subject=code,
                data=diagnostic,
                claim=_claim_for_ruff_code(code),
                polarity="supports",
                strength=_strength_for_ruff_code(code),
                subject_key=sk,
            )
            context.add_evidence(primary_evidence)
            evidence_entries = [primary_evidence.to_dict()]

            # Package-local __init__.py re-exports: record additional
            # context evidence instead of suppressing the finding entirely.
            is_pkg_reexport = _is_package_init_reexport_f401(diagnostic, file_path)
            if is_pkg_reexport:
                proof_state_raw = "REVIEW_PUBLIC_API"
                context_ev = Evidence(
                    kind="RuffDiagnostic",
                    source="ruff",
                    file=file_path,
                    location=location,
                    subject=code,
                    data=diagnostic,
                    claim="public_api_reexport",
                    polarity="context",
                    strength=0.3,
                    subject_key=sk,
                )
                context.add_evidence(context_ev)
                evidence_entries.append(context_ev.to_dict())

            findings.append(
                Finding.create(
                    type="lint",
                    severity=_severity_for_ruff_code(code),
                    file=file_path,
                    location=location,
                    message=f"{code}: {message}",
                    fixable=bool(diagnostic.get("fix")),
                    auto_fix_safe=False,
                    lint_code=code,
                    lint_source="ruff",
                    data={
                        "proof_state": proof_state_raw,
                        "evidence": evidence_entries,
                        "ruff": diagnostic,
                        "import_info": _import_info_from_diagnostic(
                            diagnostic, file_path
                        ),
                    },
                )
            )

        return findings


def _first_file(context: AnalysisContext) -> Path:
    return next(iter(context.files), Path.cwd())


def _ruff_command_prefix() -> list[str] | None:
    executable = shutil.which("ruff")
    if executable:
        return [executable]

    try:
        import ruff  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "ruff"]


def _severity_for_ruff_code(code: str) -> Severity:
    if code.startswith(("E9", "F8")):
        return Severity.ERROR
    if code.startswith(("F", "B", "S")):
        return Severity.WARNING
    return Severity.INFO


def _proof_state_for_ruff_code(code: str, file_path: Path) -> str:
    if code == "F401" and file_path.name == "__init__.py":
        return "REVIEW_PUBLIC_API"
    if code in {"F401", "F841"}:
        return "PROVEN_UNUSED"
    return "EXTERNALLY_VERIFIED"


def _is_package_init_reexport_f401(diagnostic: dict, file_path: Path) -> bool:
    """Suppress Ruff F401 for package-local __init__.py re-exports."""
    if diagnostic.get("code") != "F401" or file_path.name != "__init__.py":
        return False

    package_name = file_path.parent.name
    message = diagnostic.get("message") or ""
    imported = _first_backtick_value(message)
    return imported.startswith(f"{package_name}.")


def _first_backtick_value(message: str) -> str:
    parts = message.split("`")
    if len(parts) >= 3:
        return parts[1]
    return ""


def _subject_key_from_diagnostic(
    diagnostic: dict, file_path: Path, code: str
) -> "SubjectKey | None":
    """Build a SubjectKey from a Ruff diagnostic for fusion support."""
    if code == "F401":
        message = diagnostic.get("message") or ""
        imported = _first_backtick_value(message)
        module = ".".join(imported.split(".")[:-1]) if "." in imported else None
        name = imported.split(".")[-1] if imported else None
        return subject_key_for_lint(file=file_path, code=code, module=module, name=name)
    return subject_key_for_lint(file=file_path, code=code)


def _claim_for_ruff_code(code: str) -> str:
    if code == "F401":
        return "unused_import"
    if code == "F821":
        return "undefined_name"
    if code == "F841":
        return "unused_variable"
    if code == "F811":
        return "redefined_name"
    return "lint_violation"


def _strength_for_ruff_code(code: str) -> float:
    if code in ("F821", "F841"):
        return 0.90
    if code == "F401":
        return 0.85
    if code == "F811":
        return 0.80
    return 0.70


def _import_info_from_diagnostic(
    diagnostic: dict, file_path: Path
) -> dict:
    """Extract import_info from diagnostic message for fusion with local analyzer."""
    code = diagnostic.get("code")
    if code != "F401":
        return {}
    message = diagnostic.get("message") or ""
    imported = _first_backtick_value(message)
    module = ".".join(imported.split(".")[:-1]) if "." in imported else None
    name = imported.split(".")[-1] if imported else None
    return {
        "module": module,
        "name": name,
        "alias": None,
        "source": "ruff",
        "raw_message": message,
    }
