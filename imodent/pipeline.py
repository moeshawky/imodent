"""
Processing Pipeline for Indentation Fixing.

Orchestrates the detection, fixing, and validation steps.
"""
from typing import Optional, List
from .interfaces import LanguageStrategy, FixResult
from .registry import StrategyRegistry


class FixPipeline:
    """
    Orchestrates the indentation fixing process.
    
    Steps:
    1. Detect the language using registered strategies
    2. Fix the indentation using the detected strategy
    3. Validate the result
    4. Return the FixResult
    """
    
    def __init__(self, indent_size: int = 4):
        """
        Initialize the pipeline.
        
        Args:
            indent_size: Default indentation size for fixing.
        """
        self.indent_size = indent_size
        self._strategy: Optional[LanguageStrategy] = None
    
    def detect(self, content: str) -> Optional[LanguageStrategy]:
        """
        Detect the language for the given content.
        
        Checks strategies in order: JSON, JSONL, Python (most specific first).
        
        Args:
            content: The source code content.
            
        Returns:
            The detected LanguageStrategy, or None if no match.
        """
        # Check JSON first (most specific - must parse successfully)
        json_strategy = StrategyRegistry.get('json')
        if json_strategy and json_strategy().detect(content):
            self._strategy = json_strategy()
            return self._strategy
        
        # Check JSONL second (multiple lines of JSON)
        jsonl_strategy = StrategyRegistry.get('jsonl')
        if jsonl_strategy and jsonl_strategy().detect(content):
            self._strategy = jsonl_strategy()
            return self._strategy
        
        # Check Python last (most general)
        python_strategy = StrategyRegistry.get('python')
        if python_strategy and python_strategy().detect(content):
            self._strategy = python_strategy()
            return self._strategy
        
        return None
    
    def fix(self, content: str, strategy: Optional[LanguageStrategy] = None) -> FixResult:
        """
        Fix the indentation of the content.
        
        Args:
            content: The source code content to fix.
            strategy: Optional strategy to use. If None, auto-detect.
            
        Returns:
            FixResult with the fixed content and validation status.
        """
        if strategy is None:
            strategy = self.detect(content)
        
        if strategy is None:
            return FixResult(
                success=False,
                content=content,
                errors=["Unable to detect language format"],
                warnings=[],
                original_valid=True,
                fixed_valid=True
            )
        
        # Fix the content
        result = strategy.fix(content, self.indent_size)
        
        return result
    
    def validate(self, content: str, strategy: Optional[LanguageStrategy] = None) -> FixResult:
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
                fixed_valid=False
            )
        
        is_valid, error = strategy.validate(content)
        
        return FixResult(
            success=is_valid,
            content=content,
            errors=[error] if error else [],
            warnings=[],
            original_valid=is_valid,
            fixed_valid=is_valid
        )
