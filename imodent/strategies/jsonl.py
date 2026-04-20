"""
JSONL (JSON Lines) Language Strategy.

Handles JSONL formatting and validation.
"""
import json
import re
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
        return ['.jsonl', '.ndjson']

    def detect(self, content: str) -> bool:
        """Detect if content is JSONL."""
        stripped = content.strip()
        if not stripped:
            return False
        
        # Multiple lines, each valid JSON
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        
        if len(lines) < 2:
            return False
        
        # All lines must be valid JSON
        return all(self._is_valid_json_line(line) for line in lines)

    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        """Fix JSONL formatting (each line is compact JSON)."""
        errors = []
        warnings = []
        fixed_lines = []
        
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                fixed_lines.append("")
                continue
            
            try:
                obj = json.loads(stripped)
                # JSONL lines are typically compact
                fixed_lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError:
                errors.append(f"Invalid JSON on line: {stripped[:50]}...")
                fixed_lines.append(stripped)
        
        return FixResult(
            success=len(errors) == 0,
            content="\n".join(fixed_lines) + "\n",
            errors=errors,
            warnings=warnings,
            original_valid=len(errors) == 0,
            fixed_valid=len(errors) == 0
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

    def _is_valid_json_line(self, line: str) -> bool:
        """Check if a single line is valid JSON."""
        try:
            json.loads(line)
            return True
        except json.JSONDecodeError:
            return False
