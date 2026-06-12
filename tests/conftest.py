"""Shared fixtures for imodent tests.

Provides sample content strings, temporary file fixtures, Finding/Evidence
fixtures, and a CLI runner helper used across all test modules.
"""

import sys
from pathlib import Path

import pytest

from imodent.analysis.context import AnalysisConfig, AnalysisContext
from imodent.analysis.evidence import Evidence
from imodent.analysis.findings import Finding, Location, ProofState, Severity
from imodent.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Content fixtures — valid strings
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_py_content() -> str:
    """Return a valid Python source string."""
    return "x = 1\ny = 2\nprint(x + y)\n"


@pytest.fixture
def sample_json_content() -> str:
    """Return a valid JSON source string."""
    return '{"a": 1, "b": [2, 3], "c": {"d": "hello"}}\n'


@pytest.fixture
def sample_jsonl_content() -> str:
    """Return a valid JSONL source string (one JSON object per line)."""
    return '{"a": 1}\n{"b": 2}\n{"c": 3}\n'


@pytest.fixture
def sample_yaml_content() -> str:
    """Return a valid YAML source string."""
    return "key: value\nlist:\n  - item1\n  - item2\n"


# ---------------------------------------------------------------------------
# Content fixtures — broken / malformed strings
# ---------------------------------------------------------------------------

@pytest.fixture
def malformed_py_content() -> str:
    """Return Python with a structural syntax error (not indentation)."""
    return "class F o:\n    pass\n"


@pytest.fixture
def malformed_json_content() -> str:
    """Return broken JSON that json.loads rejects."""
    return "{broken: true, items: [1, 2,\n"


@pytest.fixture
def malformed_jsonl_content() -> str:
    """Return JSONL with one invalid line."""
    return '{"a": 1}\nnotjson\n{"b": 2}\n'


@pytest.fixture
def malformed_yaml_content() -> str:
    """Return YAML with an unclosed bracket."""
    return "key: [unclosed\n  - item\n"


@pytest.fixture
def empty_content() -> str:
    """Return an empty string."""
    return ""


@pytest.fixture
def binary_content() -> bytes:
    """Return bytes that cannot be decoded as UTF-8 (causes UnicodeDecodeError)."""
    return b"\x80\x81\x82"


# ---------------------------------------------------------------------------
# Temporary file fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_python_file(tmp_path: Path) -> Path:
    """Create a temporary .py file with valid Python content."""
    p = tmp_path / "test_module.py"
    p.write_text("def hello():\n    return 'world'\n\nprint(hello())\n")
    return p


@pytest.fixture
def temp_json_file(tmp_path: Path) -> Path:
    """Create a temporary .json file with valid JSON content."""
    p = tmp_path / "data.json"
    p.write_text('{"name": "test", "value": 42}\n')
    return p


@pytest.fixture
def temp_yaml_file(tmp_path: Path) -> Path:
    """Create a temporary .yaml file with valid YAML content."""
    p = tmp_path / "config.yaml"
    p.write_text("server:\n  host: localhost\n  port: 8080\n")
    return p


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a tmp_path directory with a simulated project structure.

    Returns the project root path.
    """
    project_root = tmp_path / "project"
    src = project_root / "src"
    src.mkdir(parents=True)
    (src / "main.py").write_text("import os\nimport sys\n\nprint('hello')\n")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    (project_root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\n"
    )
    return project_root


# ---------------------------------------------------------------------------
# Registry isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_registry() -> None:
    """Clear StrategyRegistry before test, reload builtins after.

    Ensures tests that modify the registry don't leak into other tests.
    """
    StrategyRegistry.clear()
    yield
    StrategyRegistry.clear()
    StrategyRegistry._load_builtins()


# ---------------------------------------------------------------------------
# Finding / Evidence fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_finding_import() -> Finding:
    """Return a Finding representing an unused import with realistic data."""
    return Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/project/main.py"),
        message="Unused import: os",
        location=Location(line=1, column=1),
        fixable=True,
        auto_fix_safe=False,
        import_name="os",
        import_module=None,
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
                "source": "ast",
            }
        },
        proof_state=ProofState.RAW.value,
    )


@pytest.fixture
def sample_finding_lint() -> Finding:
    """Return a Finding representing a Ruff F401 lint diagnostic."""
    return Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=Path("/fake/project/main.py"),
        message="F401: `os` imported but unused",
        location=Location(line=1, column=1),
        fixable=True,
        auto_fix_safe=False,
        lint_code="F401",
        lint_source="ruff",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
                "single_alias": True,
                "source": "ruff",
            },
            "proof_state": "PROVEN_UNUSED",
        },
        proof_state="PROVEN_UNUSED",
    )


@pytest.fixture
def sample_evidence() -> Evidence:
    """Return an Evidence instance with known values."""
    return Evidence(
        kind="RuffDiagnostic",
        file=Path("/fake/project/main.py"),
        location=Location(line=1, column=1),
        source="ruff",
        subject="F401",
        polarity="supports",
        claim="unused_import",
        strength=0.85,
    )


@pytest.fixture
def sample_analysis_context(tmp_path: Path) -> AnalysisContext:
    """Return an AnalysisContext with a single Python file loaded."""
    py_file = tmp_path / "test.py"
    py_file.write_text("import os\n\nprint(os.getcwd())\n")
    from imodent.analysis.context import FileInfo

    file_info = FileInfo.from_path(py_file)
    return AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(),
    )


# ---------------------------------------------------------------------------
# CLI runner helper
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner(capsys):
    """Return a callable that invokes main() with given argv and captures output.

    Usage:
        def test_example(cli_runner):
            exit_code = cli_runner(["--version"])
            captured = capsys.readouterr()
    """

    def _run(argv: list[str]) -> int:
        """Run imodent CLI main() with argv, return exit code (0 on success).

        Uses sys.argv override to pass arguments into argparse.
        Captures stdout/stderr via capsys.
        """
        from imodent.cli import main

        old_argv = sys.argv[:]
        try:
            sys.argv = ["imodent"] + argv
            main()
            return 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            return code
        finally:
            sys.argv = old_argv

    return _run
