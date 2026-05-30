"""Analysis module - coordinates all analysis operations."""

from .context import AnalysisContext, AnalysisConfig, FileInfo, DependencyGraph, SymbolUsage
from .findings import Finding, Severity, Location, FixOption, Advice, Change
from .coordinator import AnalysisCoordinator, AnalysisResult, FixMode
from .decisions import (
    SubjectKey,
    DecisionCandidate,
    ActionOption,
    DecisionEngine,
    subject_key_for_import,
    subject_key_for_lint,
)

__all__ = [
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
    "SubjectKey",
    "DecisionCandidate",
    "ActionOption",
    "DecisionEngine",
    "subject_key_for_import",
    "subject_key_for_lint",
]
