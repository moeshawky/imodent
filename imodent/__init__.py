"""
Indentation Fixer - Modular Architecture

Core package for language detection, fixing, and validation.
"""
from .interfaces import LanguageStrategy, Processor, FixResult
from .registry import StrategyRegistry, ProcessorRegistry
from .pipeline import FixPipeline

__all__ = [
    'LanguageStrategy',
    'Processor',
    'FixResult',
    'StrategyRegistry',
    'ProcessorRegistry',
    'FixPipeline',
]
