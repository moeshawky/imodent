"""Performance tests — marked as @pytest.mark.slow.

These tests verify that key operations complete within reasonable time bounds.
Run with: pytest -m slow
Skip with: pytest -m "not slow"
"""

import time
from pathlib import Path

import pytest

from imodent.analysis.context import FileInfo
from imodent.analysis.decision_engine import DecisionEngine
from imodent.analysis.findings import Finding, Location, Severity


pytestmark = pytest.mark.slow


@pytest.mark.slow
def test_fileinfo_large_file_performance(tmp_path):
    """FileInfo.from_path on a 10K-line Python file completes in under 3 seconds."""
    # Generate 10K-line Python file
    large_file = tmp_path / "large.py"
    lines = ["x = 1\n"] * 10_000
    large_file.write_text("".join(lines))

    start = time.perf_counter()
    info = FileInfo.from_path(large_file)
    elapsed = time.perf_counter() - start

    assert info.language == "python"
    assert elapsed < 3.0, (
        f"FileInfo.from_path on 10K-line file took {elapsed:.2f}s (limit 3.0s)"
    )


@pytest.mark.slow
def test_decision_engine_100_findings():
    """DecisionEngine.build_candidates with 100 findings completes in under 5 seconds."""
    file = Path("/fake/project/module.py")

    # Generate 100 findings with different import names
    findings = []
    for i in range(100):
        f = Finding.create(
            type="unused_import",
            severity=Severity.WARNING,
            file=file,
            message=f"Unused import: module_{i}",
            location=Location(line=i + 1, column=1),
            import_name=f"module_{i}",
            data={
                "import_info": {
                    "module": None,
                    "name": f"module_{i}",
                    "alias": None,
                    "intent": "usage",
                }
            },
        )
        findings.append(f)

    engine = DecisionEngine()
    start = time.perf_counter()
    candidates = engine.build_candidates(findings, [])
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, (
        f"DecisionEngine.build_candidates on 100 findings took {elapsed:.2f}s (limit 5.0s)"
    )
    # Each finding has a unique binding key, so 100 candidates
    assert len(candidates) == 100
