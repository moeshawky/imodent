"""
Indentation Fixer - Modular Architecture

Core package for language detection, fixing, and validation.
Now with multi-file analysis, import analysis, and advisory capabilities.
"""

from .interfaces import LanguageStrategy, Processor, FixResult
from .registry import StrategyRegistry, ProcessorRegistry
from .pipeline import FixPipeline

# Analysis components
from .analysis import (
    AnalysisContext,
    AnalysisConfig,
    FileInfo,
    DependencyGraph,
    Finding,
    Severity,
    Location,
    FixOption,
    Advice,
    Change,
    AnalysisCoordinator,
    AnalysisResult,
    FixMode,
)

__all__ = [
    # Core
    "LanguageStrategy",
    "Processor",
    "FixResult",
    "StrategyRegistry",
    "ProcessorRegistry",
    "FixPipeline",
    # Analysis
    "AnalysisContext",
    "AnalysisConfig",
    "FileInfo",
    "DependencyGraph",
    "Finding",
    "Severity",
    "Location",
    "FixOption",
    "Advice",
    "Change",
    "AnalysisCoordinator",
    "AnalysisResult",
    "FixMode",
]
