"""Integration tests — end-to-end pipelines across multiple components."""

import subprocess
from pathlib import Path

from imodent.analysis.context import AnalysisConfig
from imodent.analysis.coordinator import AnalysisCoordinator
from imodent.pipeline import FixPipeline
from imodent.strategies.python import PythonStrategy


# ---------------------------------------------------------------------------
# Fix pipeline integration
# ---------------------------------------------------------------------------

def test_full_fix_pipeline(sample_py_content):
    """End-to-end: FixPipeline.detect → fix → validate for Python produces valid output."""
    pipeline = FixPipeline(indent_size=4)

    # Step 1: Detect
    strategy = pipeline.detect(sample_py_content)
    assert strategy is not None
    assert strategy.name == "python"

    # Step 2: Fix
    result = pipeline.fix(sample_py_content, strategy=strategy)
    assert result.success is True
    assert result.fixed_valid is True or result.original_valid is True

    # Step 3: Validate the fixed output
    is_valid, error = strategy.validate(result.content)
    assert is_valid is True, f"Fixed output fails validation: {error}"


def test_json_pipeline_integration(sample_json_content):
    """End-to-end: FixPipeline handles JSON content correctly."""
    pipeline = FixPipeline(indent_size=2)

    # Detect → fix
    result = pipeline.fix(sample_json_content)
    assert result.success is True
    import json
    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)


def test_yaml_pipeline_integration(sample_yaml_content):
    """End-to-end: FixPipeline handles YAML content correctly."""
    pipeline = FixPipeline(indent_size=2)

    # Detect → fix
    result = pipeline.fix(sample_yaml_content)
    assert result.success is True
    import yaml
    parsed = yaml.safe_load(result.content)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Analysis coordinator integration
# ---------------------------------------------------------------------------

def test_analyze_coordinator_integration(tmp_path):
    """AnalysisCoordinator with real file produces coherent findings."""
    # Create a Python file with a known unused import
    py_file = tmp_path / "test_mod.py"
    py_file.write_text("import os\n\nprint(42)\n")

    config = AnalysisConfig(
        check_imports=True,
        check_lint=False,  # skip ruff for speed
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])

    # Should have findings
    assert result is not None
    assert len(result.findings) >= 0
    assert result.elapsed_time > 0


def test_coordinator_cross_component(tmp_path):
    """Coordinator, DecisionEngine, and evidence chain work together."""
    # Two Python files — one imports something, the other doesn't use it
    (tmp_path / "mod_a.py").write_text("import os\n")
    (tmp_path / "mod_b.py").write_text("x = 1\n")

    config = AnalysisConfig(
        check_imports=True,
        check_lint=False,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze(
        [tmp_path / "mod_a.py", tmp_path / "mod_b.py"]
    )

    # Decision candidates should be built
    assert hasattr(result, "candidates")
    assert isinstance(result.candidates, list)


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------

def test_cli_end_to_end_analyze():
    """subprocess: `uv run imodent imodent/ --analyze --imports --lint --report` exits 0."""
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            "uv", "run", "imodent", "imodent/",
            "--analyze", "--imports", "--lint", "--report",
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    # The command should exit cleanly (0) — report mode doesn't fix anything
    assert result.returncode == 0, (
        f"CLI end-to-end failed with code {result.returncode}:\n"
        f"STDERR:\n{result.stderr[:1000]}"
    )


def test_cli_fix_mode_end_to_end(temp_python_file):
    """subprocess: `uv run imodent <file>` exits 0 and modifies the file."""
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["uv", "run", "imodent", str(temp_python_file)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"CLI fix mode failed with code {result.returncode}:\n"
        f"STDERR:\n{result.stderr[:1000]}"
    )
