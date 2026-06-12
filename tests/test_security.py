"""Security tests — verify no dangerous patterns in imodent source.

These are static analysis tests that scan the source code for security
anti-patterns. They are NOT testing runtime behavior.
"""

from pathlib import Path

# Regex patterns that would be dangerous outside of specific safe contexts
_SAFE_EVAL_PATTERNS = [
    "ast.literal_eval(",  # Safe — only evaluates literals
]
_SAFE_EXEC_PATTERNS = []  # No safe exec() usage expected
_SAFE_SHELL_TRUE_PATTERNS = []  # No safe shell=True usage expected


def _all_source_files() -> list[Path]:
    """Return all .py files in the imodent source tree (non-test)."""
    project_root = Path(__file__).resolve().parent.parent
    source_dir = project_root / "imodent"
    if not source_dir.is_dir():
        return []
    return sorted(source_dir.rglob("*.py"))


def test_no_eval_exec_in_source():
    """Verify no eval() or exec() usage in imodent source files.

    Scans all .py files in imodent/ for eval( or exec( usage.
    Flags any that aren't in the approved safe-pattern list.
    ast.literal_eval() is exempted as it only evaluates literals.
    """
    violations: list[str] = []

    for py_file in _all_source_files():
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Skip string literals containing eval/exec (not actual calls)
            if stripped.startswith(('"', "'", '"""', "'''")):
                continue

            # Check for eval()
            if "eval(" in stripped and not any(
                safe in stripped for safe in _SAFE_EVAL_PATTERNS
            ):
                violations.append(
                    f"{py_file.relative_to(py_file.parents[2])}:{lineno}: "
                    f"eval() usage: {stripped.strip()[:80]}"
                )

            # Check for exec()
            if "exec(" in stripped and not any(
                safe in stripped for safe in _SAFE_EXEC_PATTERNS
            ):
                violations.append(
                    f"{py_file.relative_to(py_file.parents[2])}:{lineno}: "
                    f"exec() usage: {stripped.strip()[:80]}"
                )

    assert len(violations) == 0, "Dangerous eval/exec patterns found:\n" + "\n".join(
        violations
    )


def test_subprocess_shell_false():
    """Verify no subprocess calls with shell=True in imodent source.

    Scans all .py files for subprocess calls that use shell=True,
    which is a command injection risk. Flags any violations.
    """
    violations: list[str] = []

    for py_file in _all_source_files():
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue

            # Check for shell=True patterns
            if "shell=True" in stripped:
                violations.append(
                    f"{py_file.relative_to(py_file.parents[2])}:{lineno}: "
                    f"shell=True usage: {stripped.strip()[:120]}"
                )
            # Also check for the older shell=1 form
            if "shell=1" in stripped and "shell=True" not in stripped:
                violations.append(
                    f"{py_file.relative_to(py_file.parents[2])}:{lineno}: "
                    f"shell=1 usage: {stripped.strip()[:120]}"
                )

    assert len(violations) == 0, "Dangerous shell=True patterns found:\n" + "\n".join(
        violations
    )
