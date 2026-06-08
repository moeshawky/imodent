"""Analysis module - coordinates all analysis operations.

Import directly from submodules in source code:
    from .decision_engine import DecisionEngine
    from .decision_models import SubjectKey

Do NOT re-create a decisions.py facade that re-exports from decision_*.py
submodules. The facade was removed in WD-40 Phase 2 — see git history.
"""

from .context import (
    AnalysisConfig,
    AnalysisContext,
    DependencyGraph,
    FileInfo,
    SymbolUsage,
)
from .coordinator import AnalysisCoordinator, AnalysisResult, FixMode
from .decision_engine import DecisionEngine
from .decision_models import ActionOption, DecisionCandidate, SubjectKey
from .decision_subjects import subject_key_for_import, subject_key_for_lint
from .findings import Advice, Change, Finding, FixOption, Location, Severity

__all__ = [
    "ActionOption",
    "Advice",
    "AnalysisConfig",
    "AnalysisContext",
    "AnalysisCoordinator",
    "AnalysisResult",
    "Change",
    "DecisionCandidate",
    "DecisionEngine",
    "DependencyGraph",
    "FileInfo",
    "Finding",
    "FixMode",
    "FixOption",
    "Location",
    "Severity",
    "SubjectKey",
    "SymbolUsage",
    "subject_key_for_import",
    "subject_key_for_lint",
]
