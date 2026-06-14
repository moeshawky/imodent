"""
Processing Pipeline for Indentation Fixing.

Orchestrates the detection, fixing, and validation steps.
"""

import logging

from .interfaces import FixResult, LanguageStrategy
from .registry import StrategyRegistry


class FixPipeline:
    """
    Orchestrates the code fixing process.

    Steps:
    1. Detect the language using registered strategies
    2. Fix code using the detected strategy
    3. Validate the result
    4. Return the FixResult
    """

    def __init__(self, indent_size: int = 4):
        """
        Initialize the pipeline.

        Args:
            indent_size: Default formatting size for fixing.
        """
        self.indent_size = indent_size
        self._strategy: LanguageStrategy | None = None

    def detect(self, content: str) -> LanguageStrategy | None:
        """
        Detect the language for the given content.

        Checks strategies in order: JSON, JSONL, YAML, Python.

        Args:
            content: The source code content.

        Returns:
            The detected LanguageStrategy, or None if no match.
        """
        # Extensionless content detection must check YAML before Python because
        # plain key-value YAML can be valid Python annotation syntax.
        for name in ("json", "jsonl", "yaml", "python"):
            strategy_class = StrategyRegistry.get(name)
            if strategy_class and strategy_class().detect(content):
                self._strategy = strategy_class()
                return self._strategy

        return None

    def fix(
        self,
        content: str,
        strategy: LanguageStrategy | None = None,
        force: bool = False,
    ) -> FixResult:
        """Fix the code content.

        Args:
            content: The source code content to fix.
            strategy: Optional strategy to use. If None, auto-detect.
            force: If True, attempt heuristic fix even on structurally
                broken code. When language detection also fails, force
                causes the pipeline to try every registered strategy
                (brute-force attempt) before returning an error.

        Returns:
            FixResult with the fixed content and validation status. When
            language detection fails, returns success=False with
            original_valid=False and fixed_valid=False (validity is
            unknown; False is the conservative choice).
        """
        if strategy is None:
            strategy = self.detect(content)

        if strategy is None:
            # When force=True, try every registered strategy as a last-resort
            # brute-force attempt before giving up.
            if force:
                StrategyRegistry._load_builtins()
                for _name, strategy_cls in StrategyRegistry._strategies.items():
                    try:
                        result = strategy_cls().fix(content, self.indent_size, force=True)
                        if result.success:
                            return result
                    except Exception as exc:
                        logging.debug(
                            "force=True: strategy %s failed on undetectable content: %s",
                            _name,
                            exc,
                        )
                        continue
                # None of the strategies could fix the content.
                return FixResult(
                    success=False,
                    content=content,
                    errors=["Unable to detect language format"],
                    warnings=["force=True: no registered strategy could fix content"],
                    original_valid=False,
                    fixed_valid=False,
                )

            return FixResult(
                success=False,
                content=content,
                errors=["Unable to detect language format"],
                warnings=[],
                original_valid=False,
                fixed_valid=False,
            )

        # Fix the content
        return strategy.fix(content, self.indent_size, force=force)

    def validate(
        self, content: str, strategy: LanguageStrategy | None = None
    ) -> FixResult:
        """
        Validate the content without fixing.

        Args:
            content: The source code content to validate.
            strategy: Optional strategy to use. If None, auto-detect.

        Returns:
            FixResult with validation status.
        """
        if strategy is None:
            strategy = self.detect(content)

        if strategy is None:
            return FixResult(
                success=False,
                content=content,
                errors=["Unable to detect language format"],
                warnings=[],
                original_valid=False,
                fixed_valid=False,
            )

        is_valid, error = strategy.validate(content)

        return FixResult(
            success=is_valid,
            content=content,
            errors=[error] if error else [],
            warnings=[],
            original_valid=is_valid,
            fixed_valid=is_valid,
        )
