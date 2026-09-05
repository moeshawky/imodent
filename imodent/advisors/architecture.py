"""Architecture advisor - provides architectural recommendations."""

from collections import Counter

from ..analysis.context import AnalysisContext
from ..analysis.findings import Advice, Finding
from .base import Advisor


class ArchitectureAdvisor(Advisor):
    """Advisor for architectural issues."""

    @property
    def name(self) -> str:
        """
        DFS cycle detection on the dependency graph. For each unvisited module,
        traverses importees with a recursion stack: if a neighbor is already
        in rec_stack, a cycle exists (back-edge in the directed graph).
        O(V + E) where V = modules, E = import edges.
        """
        return "architecture"

    @property
    def priority(self) -> int:
        """
        Activation gate. Returns True when:
        - >5 total import-related findings (threshold for systemic issue)
        - Circular dependency detected via _find_cycles()
        - Any single file has >3 unused-import findings
        These are weighted heuristics, not exhaustive — designed to avoid noise
        on small projects with few findings.
        """
        return 7  # Higher priority

    def should_advise(self, findings: list[Finding], context: AnalysisContext) -> bool:
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
        return bool(file_counts and max(file_counts.values()) > 3)

    def advise(self, findings: list[Finding], context: AnalysisContext) -> list[Advice]:
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
        dep_cycles = self._find_cycles(context)
        if dep_cycles:
            for cycle_idx, cycle in enumerate(dep_cycles, 1):
                cycle_path = " → ".join(cycle)
                cycle_modules = sorted(set(cycle))
                # Prefer the last module in the cycle (the one that closes it)
                # as a suggested extraction target
                break_module = cycle[-2] if len(cycle) >= 2 else cycle[0]
                advices.append(
                    Advice(
                        finding_ids=[],
                        category="architecture",
                        summary=f"Circular dependency #{cycle_idx}: {cycle[0]} ↔ {cycle[-2]}",
                        explanation=(
                            f"Modules import each other in a cycle: {cycle_path}. "
                            f"This can cause ImportError at runtime and makes "
                            f"dependencies unclear.  Involved modules: "
                            f"{', '.join(cycle_modules)}."
                        ),
                        recommendation=(
                            f"Extract shared code from '{break_module}' into a "
                            "separate module that both can import, or use late "
                            "imports inside functions to break the cycle."
                        ),
                        example=(
                            "# Instead of:\n"
                            f"# {cycle[0]}: from {cycle[1]} import func\n"
                            f"# {cycle[1]}: from {cycle[0]} import other\n\n"
                            "# Do:\n"
                            f"# {cycle[0]}:\n"
                            f"def func():\n"
                            f"    from {cycle[1]} import func\n"
                            "    return func()"
                        ),
                        impact=(
                            f"Circular imports among {len(cycle_modules)} modules "
                            "can cause ImportError at runtime."
                        ),
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
        """Check whether the dependency graph contains any import cycle.

        Delegates to ``_find_cycles()`` and returns True if any cycle exists.

        Args:
            context: AnalysisContext whose ``.graph`` holds the dependency data.

        Returns:
            True if at least one cycle is detected, False otherwise.
        """
        cycles = self._find_cycles(context)
        return len(cycles) > 0

    def _find_cycles(self, context: AnalysisContext) -> list[list[str]]:
        """Detect all import cycles in the dependency graph using DFS.

        Walks ``context.graph.imports`` with a depth-first search.  When a
        back edge is discovered (neighbor already in the current recursion
        path), extracts the cycle from the path and appends it to the result.

        Args:
            context: AnalysisContext whose ``.graph`` holds the dependency data.

        Returns:
            A list of cycles, each being a ``list[str]`` of module names in
            traversal order starting from the earliest visited node in the
            cycle and ending with the node that closes the cycle (duplicate of
            the first node).  For example, a cycle A→B→C→A produces::

                [["A", "B", "C", "A"]]

            Returns an empty list when no cycles exist.

        Note:
            Does not attempt to report minimal cycles or eliminate
            duplicates that share the same set of nodes but differ in
            starting point.  The first cycle found for each starting
            module is recorded.
        """
        graph = context.graph
        cycles: list[list[str]] = []
        known = set(getattr(graph, "module_to_file", {}))
        restrict = len(known) > 0

        def dfs(
            node: str, visited: set[str], in_path: set[str], path: list[str]
        ) -> None:
            visited.add(node)
            in_path.add(node)
            path.append(node)

            for neighbor in graph.get_importees(node):
                if restrict and neighbor not in known:
                    continue
                if neighbor not in visited:
                    dfs(neighbor, visited, in_path, path)
                elif neighbor in in_path:
                    # Back edge → extract cycle from current path
                    cycle_start = path.index(neighbor)
                    cycle = [*list(path[cycle_start:]), neighbor]
                    cycles.append(cycle)

            path.pop()
            in_path.remove(node)

        visited: set[str] = set()
        for module in graph.imports:
            if restrict and module not in known:
                continue
            if module not in visited:
                dfs(module, visited, set(), [])

        return cycles
