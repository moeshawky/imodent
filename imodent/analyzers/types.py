"""Type checking via external tools (mypy, pyright)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..analysis.findings import Finding, Location, ProofState, Severity

if TYPE_CHECKING:
    from ..analysis.context import AnalysisContext


def check_mypy(context: AnalysisContext) -> list[Finding]:
    """
    Runs mypy type checker against all Python files with parsed AST trees.
    Returns empty list if mypy not on PATH or no Python files found.
    Uses --ignore-missing-imports --no-error-summary --show-error-codes flags.
    Delegates subprocess execution to _run_type_checker, parse to _parse_mypy_output.
    """
    executable = shutil.which("mypy")
    if executable is None:
        return []

    py_files = [
        path
        for path, info in context.files.items()
        if info.language == "python" and info.ast_tree is not None
    ]
    if not py_files:
        return []

    command = [
        executable,
        "--ignore-missing-imports",
        "--no-error-summary",
        "--show-error-codes",
        *[str(p) for p in py_files],
    ]
    return _run_type_checker(command, context, "mypy")


def check_pyright(context: AnalysisContext) -> list[Finding]:
    """
    Runs pyright type checker against all Python files with parsed AST trees.
    Returns empty list if pyright not on PATH or no Python files found.
    Uses --outputjson for machine-parseable output.
    Delegates subprocess execution to _run_type_checker, parse to _parse_pyright_output.
    """
    executable = shutil.which("pyright")
    if executable is None:
        return []

    py_files = [
        path
        for path, info in context.files.items()
        if info.language == "python" and info.ast_tree is not None
    ]
    if not py_files:
        return []

    command = [
        executable,
        "--outputjson",
        *[str(p) for p in py_files],
    ]
    return _run_type_checker(command, context, "pyright")


def _run_type_checker(
    command: list[str], context: AnalysisContext, tool: str
) -> list[Finding]:
    """
    Shared subprocess runner for mypy and pyright. Runs command with 120s timeout.
    Error handling: OSError → type_check_failure finding, TimeoutExpired → type_check_timeout finding.
    Routes to tool-specific parser: mypy → _parse_mypy_output, pyright → _parse_pyright_output.
    Returns empty list for unknown tools.
    """
    project_root = context.project_root or Path.cwd()
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=str(project_root),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except OSError as exc:
        return [
            Finding.create(
                type="type_check_failure",
                severity=Severity.INFO,
                file=project_root,
                message=f"{tool} failed to start: {exc}",
                fixable=False,
                auto_fix_safe=False,
            )
        ]
    except subprocess.TimeoutExpired:
        return [
            Finding.create(
                type="type_check_timeout",
                severity=Severity.WARNING,
                file=project_root,
                message=f"{tool} timed out after 120s",
                fixable=False,
                auto_fix_safe=False,
            )
        ]

    if tool == "mypy":
        return _parse_mypy_output(completed.stdout, completed.stderr)
    if tool == "pyright":
        return _parse_pyright_output(completed.stdout)
    return []


def _parse_mypy_output(stdout: str, stderr: str) -> list[Finding]:
    """
    Parses mypy text output (file:line:col: message format).
    Splits each line on ':' with max 3 splits (line 105).
    Extracts error code from bracketed suffix: "message [error-code]".
    Severity: "error:" in message → ERROR, "warning:" → WARNING, else → INFO.
    If stderr is non-empty, appends a type_check_failure finding with first 200 chars.
    """
    findings = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        try:
            file_path = Path(parts[0])
            line_no = int(parts[1])
            col_offset = int(parts[2]) if parts[2].strip().isdigit() else 0
            message = parts[3].strip()
        except (ValueError, IndexError, OSError):
            continue

        severity = Severity.INFO
        if "error:" in message.lower():
            severity = Severity.ERROR
        elif "warning:" in message.lower():
            severity = Severity.WARNING

        error_code = ""
        if "[" in message and message.rstrip().endswith("]"):
            bracket = message.rfind("[")
            error_code = message[bracket + 1 :].rstrip("]")
            message = message[:bracket].strip()

        findings.append(
            Finding.create(
                type="type_error",
                severity=severity,
                file=file_path,
                location=Location(line=line_no, column=col_offset),
                message=f"mypy: {message}",
                fixable=False,
                auto_fix_safe=False,
                proof_state=ProofState.EXTERNALLY_VERIFIED.value,
                data={"tool": "mypy", "code": error_code} if error_code else None,
            )
        )
    if stderr.strip():
        findings.append(
            Finding.create(
                type="type_check_failure",
                severity=Severity.WARNING,
                file=Path.cwd(),
                message=f"mypy stderr: {stderr.strip()[:200]}",
                fixable=False,
                auto_fix_safe=False,
            )
        )
    return findings


def _parse_pyright_output(stdout: str) -> list[Finding]:
    """
    Parses pyright JSON output. Returns empty list on JSON decode failure.
    Reads generalDiagnostics array from JSON root.
    For each diagnostic: extracts file, line (+1 for 0→1-index), column (+1),
    message, rule code, and severity string.
    Maps severity strings: error → ERROR, warning → WARNING, else → INFO.
    """
    findings = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    diagnostics = data.get("generalDiagnostics", [])
    for diag in diagnostics:
        file_path = Path(diag.get("file", ""))
        line_no = diag.get("range", {}).get("start", {}).get("line", 0) + 1
        col_offset = diag.get("range", {}).get("start", {}).get("character", 0) + 1
        message = diag.get("message", "")
        rule = diag.get("rule", "")
        severity_str = diag.get("severity", "information")

        severity = Severity.INFO
        if severity_str == "error":
            severity = Severity.ERROR
        elif severity_str == "warning":
            severity = Severity.WARNING

        findings.append(
            Finding.create(
                type="type_error",
                severity=severity,
                file=file_path,
                location=Location(line=line_no, column=col_offset),
                message=f"pyright: {message}",
                fixable=False,
                auto_fix_safe=False,
                proof_state=ProofState.EXTERNALLY_VERIFIED.value,
                data={"tool": "pyright", "code": rule} if rule else None,
            )
        )
    return findings
