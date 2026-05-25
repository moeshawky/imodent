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
            if _is_package_init_reexport_f401(diagnostic, file_path):
                continue
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
            proof_state = _proof_state_for_ruff_code(code, file_path)

            evidence = Evidence(
                kind="RuffDiagnostic",
                source="ruff",
                file=file_path,
                location=location,
                subject=code,
                data=diagnostic,
            )
            context.add_evidence(evidence)

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
                        "proof_state": proof_state,
                        "evidence": [evidence.to_dict()],
                        "ruff": diagnostic,
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
