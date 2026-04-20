"""
JSON Language Strategy.

Handles JSON formatting and validation.
"""
import json
import re
from typing import List, Optional, Tuple

from ..interfaces import LanguageStrategy, FixResult
from ..registry import StrategyRegistry


@StrategyRegistry.register
class JSONStrategy(LanguageStrategy):
    """Strategy for fixing JSON indentation."""

    @property
    def name(self) -> str:
        return "json"

    @property
    def extensions(self) -> List[str]:
        return ['.json']

    def detect(self, content: str) -> bool:
        """Detect if content is JSON."""
        stripped = content.strip()
        if not stripped:
            return False
        
        # Must start with { or [
        if not re.search(r'^\s*[\{\[]', stripped):
            return False
        
        try:
            json.loads(stripped)
            return True
        except json.JSONDecodeError:
            return False

    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        """Fix JSON formatting."""
        errors = []
        warnings = []
        
        try:
            data = json.loads(content)
            fixed = json.dumps(data, indent=indent_size, ensure_ascii=False) + "\n"
            
            return FixResult(
                success=True,
                content=fixed,
                errors=errors,
                warnings=warnings,
                original_valid=True,
                fixed_valid=True
            )
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON: {e}")
            return FixResult(
                success=False,
                content=content,
                errors=errors,
                warnings=warnings,
                original_valid=False,
                fixed_valid=False
            )

    def validate(self, content: str) -> Tuple[bool, Optional[str]]:
        """Validate JSON syntax."""
        try:
            json.loads(content)
            return True, None
        except json.JSONDecodeError as e:
            return False, f"JSONDecodeError: {e}"
