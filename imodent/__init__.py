"""
Code intelligence tool — fix, scan, and advise on Python, JSON, YAML, and Rust.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Analysis components
from .analysis import (
    Advice,
    AnalysisConfig,
    AnalysisContext,
    AnalysisCoordinator,
    AnalysisResult,
    Change,
    DependencyGraph,
    FileInfo,
    Finding,
    FixMode,
    FixOption,
    Location,
    Severity,
    SymbolUsage,
)
from .interfaces import FixResult, LanguageStrategy, Processor
from .pipeline import FixPipeline
from .registry import ProcessorRegistry, StrategyRegistry

try:
    __version__ = _pkg_version("imodent")
except PackageNotFoundError:
    __version__ = "0.0.0dev"

__all__ = [
    "Advice",
    "AnalysisConfig",
    # Analysis
    "AnalysisContext",
    "AnalysisCoordinator",
    "AnalysisResult",
    "Change",
    "DependencyGraph",
    "FileInfo",
    "Finding",
    "FixMode",
    "FixOption",
    "FixPipeline",
    "FixResult",
    # Core
    "LanguageStrategy",
    "Location",
    "Processor",
    "ProcessorRegistry",
    "Severity",
    "StrategyRegistry",
    "SymbolUsage",
    # Version
    "__version__",
]
