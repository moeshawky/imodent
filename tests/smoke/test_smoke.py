"""
Smoke Tests: G-HALL (Hallucinated APIs) and G-SEC (Security).

Fastest tests to run. If these fail, stop and don't run the rest.
"""

import pytest
import sys
from pathlib import Path


def test_no_hallucinated_imports():
    """Verify all imports in the codebase exist."""
    # Check main modules
    import imodent
    import imodent.cli
    import imodent.interfaces
    import imodent.pipeline
    import imodent.registry
    import imodent.strategies
    import imodent.strategies.python
    import imodent.strategies.json
    import imodent.strategies.jsonl
    import imodent.strategies.yaml

    # Check external dependencies
    import black
    import ruamel.yaml
    import json_repair
    import yaml
    import ast
    import json


def test_no_security_vulnerabilities():
    """Test for basic security issues (code injection, etc.)."""
    from imodent.pipeline import FixPipeline

    pipeline = FixPipeline()

    # Test that we don't execute arbitrary code
    malicious_code = """
import os
os.system("rm -rf /")
def f():
    pass
"""
    result = pipeline.fix(malicious_code)

    # Should not crash, should preserve the code structure
    assert result is not None
    # The malicious code should still be there (we don't execute it)
    assert "os.system" in result.content or "import os" in result.content


def test_handles_empty_input():
    """Empty input should not crash."""
    from imodent.pipeline import FixPipeline

    pipeline = FixPipeline()
    result = pipeline.fix("")

    assert result is not None
    assert result.content == "" or result.content.strip() == ""


def test_handles_whitespace_only():
    """Whitespace-only input should not crash."""
    from imodent.pipeline import FixPipeline

    pipeline = FixPipeline()
    result = pipeline.fix("   \n\t\n  \n")

    assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
