"""Smoke tests — verify project imports, lint, and build pass cleanly.

These are the first tests to run. If they fail, nothing else matters.
"""

import subprocess
from pathlib import Path


def test_import_imodent():
    """Verify all top-level imodent sub-packages import without error."""
    modules = [
        "imodent",
        "imodent.cli",
        "imodent.pipeline",
        "imodent.registry",
        "imodent.interfaces",
        "imodent.analysis",
        "imodent.analysis.coordinator",
        "imodent.analysis.decision_engine",
        "imodent.analysis.decision_models",
        "imodent.analysis.decision_subjects",
        "imodent.analysis.decision_actions",
        "imodent.analysis.decision_confidence",
        "imodent.analysis.decision_policy",
        "imodent.analysis.decision_proof",
        "imodent.analysis.findings",
        "imodent.analysis.evidence",
        "imodent.analysis.context",
        "imodent.analyzers",
        "imodent.analyzers.base",
        "imodent.analyzers.imports",
        "imodent.analyzers.lint",
        "imodent.analyzers.residue",
        "imodent.analyzers.rust",
        "imodent.strategies",
        "imodent.strategies.python",
        "imodent.strategies.json",
        "imodent.strategies.jsonl",
        "imodent.strategies.yaml",
        "imodent.fixers",
        "imodent.fixers.base",
        "imodent.fixers.imports",
        "imodent.graph",
        "imodent.graph.imports",
        "imodent.graph.dependency",
        "imodent.project",
        "imodent.project.config",
        "imodent.project.project_context",
        "imodent.project.discovery",
        "imodent.advisors",
        "imodent.advisors.architecture",
    ]
    errors = []
    for mod_name in modules:
        try:
            __import__(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")
    assert not errors, "Import failures:\n" + "\n".join(errors)


def test_ruff_check_imodent():
    """Verify ruff check on imodent/ source exits 0 (clean lint)."""
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["ruff", "check", "imodent/"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff check failed with code {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_uv_build():
    """Verify `uv build` exits 0 (package builds cleanly)."""
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["uv", "build"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"uv build failed with code {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
