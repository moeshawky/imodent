"""Tests for type checking via external tools (mypy, pyright).

Covers: _run_type_checker (OSError, TimeoutExpired), _parse_mypy_output
(stderr, complex messages, empty lines), _parse_pyright_output
(invalid JSON, empty diagnostics).
"""

import json
import subprocess as subprocess_module
from pathlib import Path

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.findings import Finding, Severity


def test_check_mypy_not_installed(monkeypatch):
    """check_mypy returns [] when mypy binary is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    from imodent.analyzers.types import check_mypy

    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
        has_syntax_errors=False,
    )
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(),
    )
    findings = check_mypy(context)
    assert findings == []


def test_check_mypy_no_python_files(monkeypatch):
    """check_mypy returns [] when no Python files are in context."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/mypy" if cmd == "mypy" else None
    )

    from imodent.analyzers.types import check_mypy

    json_file = Path("/fake/data.json")
    file_info = FileInfo(
        path=json_file,
        content='{"a": 1}',
        language="json",
    )
    context = AnalysisContext(
        files={json_file: file_info},
        config=AnalysisConfig(),
    )
    findings = check_mypy(context)
    assert findings == []


def test_check_mypy_with_output(tmp_path):
    """check_mypy returns Findings when mypy produces diagnostics."""
    from unittest.mock import patch

    from imodent.analyzers.types import check_mypy

    py_file = tmp_path / "test.py"
    py_file.write_text("x = 1\n")
    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(),
    )

    with (
        patch("shutil.which", return_value="/usr/bin/mypy"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            f"{py_file}:1:1: error: Name 'x' is not defined  [name-defined]"
        )
        mock_run.return_value.stderr = ""

        findings = check_mypy(context)

    assert isinstance(findings, list)
    assert len(findings) >= 1, f"Expected >=1 finding, got {findings}"
    assert all(isinstance(f, Finding) for f in findings)


def test_check_pyright_not_installed(monkeypatch):
    """check_pyright returns [] when pyright binary is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    from imodent.analyzers.types import check_pyright

    py_file = Path("/fake/test.py")
    file_info = FileInfo(
        path=py_file,
        content="x = 1\n",
        language="python",
        has_syntax_errors=False,
    )
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(),
    )
    findings = check_pyright(context)
    assert findings == []


def test_check_pyright_no_python_files(monkeypatch):
    """check_pyright returns [] when no Python files are in context."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/pyright" if cmd == "pyright" else None
    )

    from imodent.analyzers.types import check_pyright

    json_file = Path("/fake/data.json")
    file_info = FileInfo(
        path=json_file,
        content='{"a": 1}',
        language="json",
    )
    context = AnalysisContext(
        files={json_file: file_info},
        config=AnalysisConfig(),
    )
    findings = check_pyright(context)
    assert findings == []


def test_check_pyright_with_output(tmp_path):
    """check_pyright returns Findings when pyright produces diagnostics."""
    from unittest.mock import patch

    from imodent.analyzers.types import check_pyright

    py_file = tmp_path / "test.py"
    py_file.write_text("x = 1\n")
    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(),
    )

    with (
        patch("shutil.which", return_value="/usr/bin/pyright"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": str(py_file),
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "message": "Type 'str' is not assignable to type 'int'",
                        "severity": "error",
                        "rule": "reportGeneralTypeIssues",
                    }
                ]
            }
        )
        mock_run.return_value.stderr = ""

        findings = check_pyright(context)

    assert isinstance(findings, list)
    assert len(findings) >= 1
    assert all(isinstance(f, Finding) for f in findings)


# ============================================================================
# Type checker — coverage push tests (lines targeted in types.py)
# ============================================================================


# --- 9. test_run_type_checker_oserror: OSError → type_check_failure ---


def test_run_type_checker_oserror(monkeypatch):
    """subprocess.run raises OSError → single type_check_failure finding."""
    from imodent.analyzers.types import _run_type_checker

    def _raise_oserror(*args, **kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr("subprocess.run", _raise_oserror)

    config = AnalysisConfig()
    context = AnalysisContext(config=config)

    findings = _run_type_checker(["mypy", "test.py"], context, "mypy")

    assert len(findings) == 1
    assert findings[0].type == "type_check_failure"
    assert "mypy failed to start" in findings[0].message
    assert findings[0].severity == Severity.INFO
    assert findings[0].fixable is False


# --- 10. test_run_type_checker_timeout: TimeoutExpired → type_check_timeout ---


def test_run_type_checker_timeout(monkeypatch):
    """subprocess.run raises TimeoutExpired → single type_check_timeout finding."""
    from imodent.analyzers.types import _run_type_checker

    def _raise_timeout(*args, **kwargs):
        raise subprocess_module.TimeoutExpired(cmd=["mypy"], timeout=120)

    monkeypatch.setattr("subprocess.run", _raise_timeout)

    config = AnalysisConfig()
    context = AnalysisContext(config=config)

    findings = _run_type_checker(["mypy", "test.py"], context, "mypy")

    assert len(findings) == 1
    assert findings[0].type == "type_check_timeout"
    assert "timed out" in findings[0].message
    assert findings[0].severity == Severity.WARNING
    assert findings[0].fixable is False


# --- 11. test_parse_mypy_output_with_stderr: stderr → type_check_failure ---


def test_parse_mypy_output_with_stderr():
    """mypy stderr produces additional type_check_failure finding."""
    from imodent.analyzers.types import _parse_mypy_output

    findings = _parse_mypy_output(
        "test.py:1:1: error: Name 'x' is not defined  [name-defined]",
        "mypy: can't find module 'foo'\n",
    )

    type_errors = [f for f in findings if f.type == "type_error"]
    stderr_findings = [f for f in findings if f.type == "type_check_failure"]
    assert len(type_errors) == 1, f"Expected 1 type_error, got {len(type_errors)}"
    assert len(stderr_findings) == 1, (
        f"Expected 1 type_check_failure from stderr, got {len(stderr_findings)}"
    )
    assert "stderr" in stderr_findings[0].message
    assert stderr_findings[0].severity == Severity.WARNING


# --- 12. test_parse_mypy_output_complex_message: multiple colons in message ---


def test_parse_mypy_output_complex_message():
    """mypy line with >3 colons (e.g., file path with colons) → parsed correctly."""
    from imodent.analyzers.types import _parse_mypy_output

    # Message body contains colons — split(":", 3) handles this
    findings = _parse_mypy_output(
        "/Users/me/project/file.py:10:5: error: Incompatible types in assignment "
        "(expression has type 'str', variable has type 'int')  [assignment]",
        "",
    )

    assert len(findings) == 1
    f = findings[0]
    assert f.type == "type_error"
    assert "file.py" in str(f.file)
    assert f.location is not None
    assert f.location.line == 10
    assert f.location.column == 5
    assert "Incompatible types" in f.message
    assert "assignment" in f.data.get("code", "")


# --- 13. test_parse_mypy_output_empty_lines: blank lines skipped ---


def test_parse_mypy_output_empty_lines():
    """mypy output with blank/whitespace-only lines → those lines ignored."""
    from imodent.analyzers.types import _parse_mypy_output

    findings = _parse_mypy_output(
        "\n\nfile.py:3:2: error: undefined name  [code]\n\n   \n",
        "",
    )

    assert len(findings) == 1
    assert findings[0].type == "type_error"
    assert findings[0].location is not None
    assert findings[0].location.line == 3


# --- 14. test_parse_pyright_output_invalid_json: non-JSON → [] ---


def test_parse_pyright_output_invalid_json():
    """pyright returns invalid JSON → _parse_pyright_output returns []."""
    from imodent.analyzers.types import _parse_pyright_output

    findings = _parse_pyright_output("not valid json {{{")
    assert findings == []


# --- 15. test_parse_pyright_output_no_diagnostics: valid JSON, empty diagnostics → [] ---


def test_parse_pyright_output_no_diagnostics():
    """pyright returns valid JSON with empty generalDiagnostics → []."""
    from imodent.analyzers.types import _parse_pyright_output

    findings = _parse_pyright_output('{"version": "1.1.300", "generalDiagnostics": []}')
    assert findings == []
