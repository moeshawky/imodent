"""Architecture advisor - provides architectural recommendations."""

from typing import List
from collections import Counter

from .base import Advisor
from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, Advice


class ArchitectureAdvisor(Advisor):
    """Advisor for architectural issues."""

    @property
    def name(self) -> str:
        return "architecture"

    @property
    def priority(self) -> int:
        return 7  # Higher priority

    def should_advise(self, findings: List[Finding], context: AnalysisContext) -> bool:
        """Check if there are architectural issues to advise on."""
        # Advise if there are import issues that suggest architectural problems
        import_issues = [f for f in findings if "import" in f.type]

        # More than 5 import issues might indicate architectural problem
        if len(import_issues) > 5:
            return True

        # Circular dependencies
        if self._has_circular_deps(context):
            return True

        # Many unused imports in one file
        file_counts = Counter(f.file for f in import_issues if "unused" in f.type)
        if file_counts and max(file_counts.values()) > 3:
            return True

        return False

    def advise(self, findings: List[Finding], context: AnalysisContext) -> List[Advice]:
        """Generate architectural advice."""
        advices = []

        # Check for import clustering
        import_issues = [f for f in findings if "import" in f.type]
        if len(import_issues) > 10:
            advices.append(
                Advice(
                    finding_ids=[f.id for f in import_issues[:5]],
                    category="architecture",
                    summary="High number of import issues detected",
                    explanation="Multiple files have import-related issues, which may indicate unclear module boundaries or missing __init__.py exports.",
                    recommendation="Consider reviewing module structure and ensuring public APIs are clearly defined in __init__.py files.",
                    example="# In __init__.py:\nfrom .public_module import PublicClass\n__all__ = ['PublicClass']",
                    impact="Poor module structure makes code harder to understand and maintain.",
                    priority=8,
                )
            )

        # Check for circular dependencies
        if self._has_circular_deps(context):
            advices.append(
                Advice(
                    finding_ids=[],
                    category="architecture",
                    summary="Circular dependency detected",
                    explanation="Modules import each other in a cycle, which can cause import errors and makes code harder to reason about.",
                    recommendation="Extract shared code into a separate module that both can import, or use late imports inside functions.",
                    example="# Instead of:\n# module_a.py: from module_b import func_b\n# module_b.py: from module_a import func_a\n\n# Do:\n# module_a.py:\ndef func_a():\n    from module_b import func_b\n    return func_b()",
                    impact="Circular imports can cause ImportError at runtime and make dependencies unclear.",
                    priority=9,
                )
            )

        # Check for files with many unused imports
        file_counts = Counter(f.file for f in import_issues if "unused" in f.type)
        for file, count in file_counts.items():
            if count > 3:
                advices.append(
                    Advice(
                        finding_ids=[f.id for f in import_issues if f.file == file][:3],
                        category="maintenance",
                        summary=f"File {file.name} has {count} unused imports",
                        explanation=f"The file '{file.name}' has multiple unused imports, which may indicate refactoring was done without cleanup.",
                        recommendation="Run 'imodent --fix' with review to clean up unused imports, or verify they're not used in type hints.",
                        impact="Unused imports clutter code and can slow down IDE completion.",
                        priority=5,
                    )
                )

        return advices

    def _has_circular_deps(self, context: AnalysisContext) -> bool:
        """Check for circular dependencies in the graph."""
        graph = context.graph

        # Simple cycle detection using DFS
        def has_cycle(node, visited, rec_stack):
            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph.get_importees(node):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        visited = set()
        for module in graph.imports.keys():
            if module not in visited:
                if has_cycle(module, visited, set()):
                    return True

        return False
