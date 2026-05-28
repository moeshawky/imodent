"""YAML Language Strategy.

Handles YAML formatting and validation using PyYAML.
Broken YAML is flagged with diagnostics, not silently rejected.
"""

import json
import re
from typing import List, Optional, Tuple

from ..interfaces import LanguageStrategy, FixResult
from ..registry import StrategyRegistry


@StrategyRegistry.register
class YAMLStrategy(LanguageStrategy):
    """Strategy for fixing YAML indentation."""

    @property
    def name(self) -> str:
        return "yaml"

    @property
    def extensions(self) -> List[str]:
        return [".yaml", ".yml"]

    def detect(self, content: str) -> bool:
        """Detect if content is YAML.

        Detects the LANGUAGE, not validity. Broken YAML is still YAML —
        it just needs fixing. Returns True if content has YAML patterns
        (key: value, --- document start), even if it has syntax errors.

        Discrimination strategy for ``{``/``[``-prefixed content:
        1. Try ``json.loads()`` — if it succeeds, it is valid JSON, not YAML.
        2. If JSON parsing fails, the content is either broken JSON or
           flow-style YAML.  Decide by looking for unambiguous YAML signals
           (bare unquoted key-colon, ``---``, anchor/alias markers).
        """
        stripped = content.strip()
        if not stripped:
            return False

        # Content starting with { or [ could be JSON or flow-style YAML.
        # Use the actual JSON parser to discriminate rather than heuristics.
        if re.search(r"^\s*[\{\[]", stripped):
            try:
                json.loads(stripped)
                # Parsed as valid JSON — this is JSON, not YAML.
                return False
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON.  Check for unambiguous YAML signals:
                #   - document start marker
                #   - bare unquoted key followed by colon+space (flow mapping)
                #   - YAML anchor (&) or alias (*) markers
                # NOTE: no ^ anchor — the signal may appear inside braces.
                return bool(
                    "---" in stripped
                    or re.search(r"[a-zA-Z_][a-zA-Z0-9_.-]*\s*:\s", stripped)
                    or "&" in stripped
                )

        # Standard (non-JSON-like) YAML indicators.
        if stripped.startswith("---"):
            return True

        # Bare key: value pattern anchored to line start.
        if re.search(r"^[a-zA-Z_][a-zA-Z0-9_.-]*\s*:", stripped, re.MULTILINE):
            return True

        return False

    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        """Fix YAML formatting using ruamel.yaml first, then PyYAML as fallback."""
        import yaml

        errors = []
        warnings = []
        original_valid, original_error = self.validate(content)

        if not original_valid:
            warnings.append(f"Original YAML has error: {original_error}")

        # STAGE 1: Try ruamel.yaml first (preserves comments, better formatting)
        try:
            from ruamel.yaml import YAML

            yaml_obj = YAML()
            yaml_obj.indent(mapping=indent_size, sequence=indent_size, offset=2)
            yaml_obj.preserve_quotes = True

            data = yaml_obj.load(content)
            if data is None:
                return FixResult(
                    success=True,
                    content=content,
                    errors=errors,
                    warnings=warnings,
                    original_valid=original_valid,
                    fixed_valid=True,
                )

            import io

            output = io.StringIO()
            yaml_obj.dump(data, output)
            fixed = output.getvalue()

            # Validate ruamel's output
            try:
                yaml.safe_load(fixed)
                return FixResult(
                    success=True,
                    content=fixed,
                    errors=errors,
                    warnings=warnings,
                    original_valid=original_valid,
                    fixed_valid=True,
                )
            except yaml.YAMLError as e:
                warnings.append(
                    f"ruamel.yaml output invalid: {e}, falling back to PyYAML"
                )
        except ImportError:
            warnings.append("ruamel.yaml not installed, using PyYAML")
        except Exception as e:
            warnings.append(f"ruamel.yaml failed: {e}, falling back to PyYAML")

        # STAGE 2: Fallback to PyYAML
        try:
            data = yaml.safe_load(content)
            if data is None:
                return FixResult(
                    success=True,
                    content=content,
                    errors=errors,
                    warnings=warnings,
                    original_valid=original_valid,
                    fixed_valid=True,
                )

            # Custom dumper that indents lists properly
            class IndentDumper(yaml.Dumper):
                pass

            def increase_indent(self, flow=False, indentless=False):
                return super(IndentDumper, self).increase_indent(flow, False)

            IndentDumper.increase_indent = increase_indent

            fixed = yaml.dump(
                data,
                Dumper=IndentDumper,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                indent=indent_size,
            )
            if not fixed.endswith("\n"):
                fixed += "\n"

            return FixResult(
                success=True,
                content=fixed,
                errors=errors,
                warnings=warnings,
                original_valid=original_valid,
                fixed_valid=True,
            )
        except yaml.YAMLError as e:
            errors.append(f"Cannot fix YAML automatically: {e}")
            return FixResult(
                success=False,
                content=content,
                errors=errors,
                warnings=warnings,
                original_valid=original_valid,
                fixed_valid=False,
            )

    def validate(self, content: str) -> Tuple[bool, Optional[str]]:
        """Validate YAML syntax."""
        import yaml

        try:
            yaml.safe_load(content)
            return True, None
        except yaml.YAMLError as e:
            return False, f"YAMLError: {e}"
