"""
Code intelligence tool — fix, scan, and advise on Python, JSON, YAML, and Rust.
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
    SymbolUsage,
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

from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("imodent")
except PackageNotFoundError:
    __version__ = "0.0.0dev"

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
    "SymbolUsage",
    "Finding",
    "Severity",
    "Location",
    "FixOption",
    "Advice",
    "Change",
    "AnalysisCoordinator",
    "AnalysisResult",
    "FixMode",
    # Version
    "__version__",
]
