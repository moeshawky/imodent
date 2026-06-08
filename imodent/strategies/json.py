"""JSON Language Strategy.

Handles JSON formatting and validation.
Uses json-repair for broken JSON, standard json for valid JSON.
"""

import json
import re

from ..interfaces import FixResult, LanguageStrategy
from ..registry import StrategyRegistry


@StrategyRegistry.register
class JSONStrategy(LanguageStrategy):
    """Strategy for fixing JSON indentation and structure."""

    @property
    def name(self) -> str:
        return "json"

    @property
    def extensions(self) -> list[str]:
        return [".json"]

    def detect(self, content: str) -> bool:
        """Detect if content is JSON (single object/array).

        Detects the LANGUAGE, not validity. Broken JSON is still JSON —
        it just needs fixing. Returns True if content looks like a single
        JSON document (starts with { or [), even if it has syntax errors.

        Rejects JSONL (multiple lines each starting with { or [),
        which has its own JSONLStrategy.
        """
        stripped = content.strip()
        if not stripped:
            return False
        # Must start with { or [ — that's what makes it JSON
        if not re.search(r"^\s*[\{\[]", stripped):
            return False
        # Reject JSONL: multiple lines each starting with { or [
        # JSONL has one JSON object per line, JSON has one for the whole file
        non_empty = [line.strip() for line in stripped.splitlines() if line.strip()]
        if len(non_empty) > 1:
            jsonish_lines = sum(
                1 for line in non_empty if line.startswith("{") or line.startswith("[")
            )
            # If most lines start with { or [, it's JSONL not JSON
            if jsonish_lines > 1 and jsonish_lines >= len(non_empty) * 0.5:
                return False
        return True

    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        """Fix JSON formatting using json-repair first, then standard json."""
        import json

        errors = []
        warnings = []
        original_valid, original_error = self.validate(content)

        if not original_valid:
            warnings.append(f"Original JSON has error: {original_error}")

        # STAGE 1: Try json-repair first (fixes broken JSON)
        try:
            import json_repair

            repaired = json_repair.repair_json(content, return_objects=False)
            # Validate repaired JSON
            try:
                data = json.loads(repaired)
                fixed = json.dumps(data, indent=indent_size, ensure_ascii=False) + "\n"
                return FixResult(
                    success=True,
                    content=fixed,
                    errors=errors,
                    warnings=warnings,
                    original_valid=original_valid,
                    fixed_valid=True,
                )
            except json.JSONDecodeError as e:
                warnings.append(
                    f"json-repair output invalid: {e}, falling back to standard json"
                )
        except ImportError:
            warnings.append("json-repair not installed, using standard json")
        except Exception as e:
            warnings.append(f"json-repair failed: {e}, falling back to standard json")

        # STAGE 2: Standard json (only works on valid JSON)
        try:
            data = json.loads(content)
            fixed = json.dumps(data, indent=indent_size, ensure_ascii=False) + "\n"
            return FixResult(
                success=True,
                content=fixed,
                errors=errors,
                warnings=warnings,
                original_valid=original_valid,
                fixed_valid=True,
            )
        except json.JSONDecodeError as e:
            errors.append(f"Cannot fix JSON automatically: {e}")
            errors.append(
                "Install json-repair for automatic broken JSON fixing: pip install json-repair"
            )
            return FixResult(
                success=False,
                content=content,
                errors=errors,
                warnings=warnings,
                original_valid=original_valid,
                fixed_valid=False,
            )

    def validate(self, content: str) -> tuple[bool, str | None]:
        """Validate JSON syntax."""
        try:
            json.loads(content)
            return True, None
        except json.JSONDecodeError as e:
            return False, f"JSONDecodeError: {e}"
