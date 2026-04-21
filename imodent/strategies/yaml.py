"""
YAML Language Strategy.

Handles YAML formatting and validation using PyYAML.
"""

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
        """Detect if content is YAML."""
        stripped = content.strip()
        if not stripped:
            return False

        # YAML indicators: key: value patterns, --- document start
        if stripped.startswith("---"):
            return True

        # Must have key: value patterns but NOT be JSON (no leading { or [)
        if re.search(r"^\s*[\{\[]", stripped):
            return False

        # Look for YAML key patterns
        if re.search(r"^[a-zA-Z_][a-zA-Z0-9_.-]*\s*:", stripped, re.MULTILINE):
            try:
                import yaml

                yaml.safe_load(content)
                return True
            except Exception:
                return False

        return False

    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        """Fix YAML formatting using ruamel.yaml first, then our logic as fallback."""
        import yaml

        errors = []
        warnings = []

        # STAGE 1: Try ruamel.yaml first (preserves comments, better formatting)
        try:
            from ruamel.yaml import YAML

            yaml_obj = YAML()
            yaml_obj.indent(mapping=indent_size, sequence=indent_size, offset=0)
            yaml_obj.preserve_quotes = True

            # Load and dump
            data = yaml_obj.load(content)

            if data is None:
                return FixResult(
                    success=True,
                    content=content,
                    errors=errors,
                    warnings=warnings,
                    original_valid=True,
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
                    original_valid=True,
                    fixed_valid=True,
                )
            except yaml.YAMLError as e:
                warnings.append(
                    f"ruamel.yaml output invalid: {e}, falling back to internal logic"
                )

        except ImportError:
            warnings.append("ruamel.yaml not installed, using PyYAML")
        except Exception as e:
            warnings.append(f"ruamel.yaml failed: {e}, falling back to internal logic")

        # STAGE 2: Fallback to PyYAML
        try:
            data = yaml.safe_load(content)

            if data is None:
                return FixResult(
                    success=True,
                    content=content,
                    errors=errors,
                    warnings=warnings,
                    original_valid=True,
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
                original_valid=True,
                fixed_valid=True,
            )

        except yaml.YAMLError as e:
            errors.append(f"Invalid YAML: {e}")
            return FixResult(
                success=False,
                content=content,
                errors=errors,
                warnings=warnings,
                original_valid=False,
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
