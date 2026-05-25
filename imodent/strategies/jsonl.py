"""JSONL (JSON Lines) Language Strategy.

Handles JSONL formatting and validation.
Each line must be valid JSON — broken lines are flagged, not silently rejected.
"""

import json
from typing import List, Optional, Tuple

from ..interfaces import LanguageStrategy, FixResult
from ..registry import StrategyRegistry


@StrategyRegistry.register
class JSONLStrategy(LanguageStrategy):
    """Strategy for fixing JSONL indentation."""

    @property
    def name(self) -> str:
        return "jsonl"

    @property
    def extensions(self) -> List[str]:
        return [".jsonl", ".ndjson"]

    def detect(self, content: str) -> bool:
        """Detect if content is JSONL.

        Detects the LANGUAGE, not validity. Broken JSONL is still JSONL —
        it just needs fixing. Returns True if content has multiple lines
        where most look like JSON objects/arrays.
        """
        stripped = content.strip()
        if not stripped:
            return False

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < 1:
            return False

        # At least one line must start with { or [ — that's what makes it JSONL
        jsonish = sum(
            1 for line in lines if line.startswith("{") or line.startswith("[")
        )
        # Majority of lines should look like JSON
        if len(lines) == 1:
            return jsonish == 1
        return jsonish > len(lines) * 0.5

    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        """Fix JSONL formatting (each line is compact JSON)."""
        errors = []
        warnings = []
        fixed_lines = []

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                fixed_lines.append("")
                continue
            try:
                obj = json.loads(stripped)
                # JSONL lines are compact
                fixed_lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: {e}")
                fixed_lines.append(stripped)  # keep original on error

        return FixResult(
            success=len(errors) == 0,
            content="\n".join(fixed_lines) + "\n",
            errors=errors,
            warnings=warnings,
            original_valid=len(errors) == 0,
            fixed_valid=len(errors) == 0,
        )

    def validate(self, content: str) -> Tuple[bool, Optional[str]]:
        """Validate JSONL syntax (each line must be valid JSON)."""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for i, line in enumerate(lines, 1):
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                return False, f"Line {i}: {e}"
        return True, None
