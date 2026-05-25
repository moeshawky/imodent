"""Tests for architecture advisor."""
import pytest
from pathlib import Path
from imodent.advisors.architecture import ArchitectureAdvisor
from imodent.analysis.context import AnalysisContext, DependencyGraph
from imodent.analysis.findings import Finding, Severity, Location


class TestArchitectureAdvisor:
    """Test the ArchitectureAdvisor class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.advisor = ArchitectureAdvisor()

    def test_should_advise_many_import_issues(self):
        """Should advise when there are many import issues."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path("main.py"),
                message=f"Unused import {i}",
                location=Location(line=1),
            )
            for i in range(6)
        ]
        context = AnalysisContext()
        assert self.advisor.should_advise(findings, context) is True

    def test_should_not_advise_few_import_issues(self):
        """Should not advise when there are few import issues."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path("main.py"),
                message="Unused import",
                location=Location(line=1),
            )
        ]
        context = AnalysisContext()
        assert self.advisor.should_advise(findings, context) is False

    def test_should_advise_circular_deps(self):
        """Should advise when circular dependencies exist."""
        findings = []
        context = AnalysisContext()
        # Create circular dependency
        context.graph.add_import("module_a", "module_b")
        context.graph.add_import("module_b", "module_a")
        assert self.advisor.should_advise(findings, context) is True

    def test_should_advise_many_unused_in_one_file(self):
        """Should advise when one file has many unused imports."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path("main.py"),
                message=f"Unused import {i}",
                location=Location(line=i),
            )
            for i in range(1, 5)
        ]
        context = AnalysisContext()
        assert self.advisor.should_advise(findings, context) is True

    def test_advise_import_clustering(self):
        """Should generate advice for import clustering."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path(f"file{i}.py"),
                message=f"Unused import {i}",
                location=Location(line=1),
            )
            for i in range(11)
        ]
        context = AnalysisContext()
        advices = self.advisor.advise(findings, context)
        assert len(advices) >= 1
        # Should have import clustering advice
        clustering = [a for a in advices if "import" in a.summary.lower()]
        assert len(clustering) >= 1

    def test_advise_circular_dependency(self):
        """Should generate advice for circular dependencies."""
        findings = []
        context = AnalysisContext()
        context.graph.add_import("module_a", "module_b")
        context.graph.add_import("module_b", "module_a")

        advices = self.advisor.advise(findings, context)
        circular = [a for a in advices if "circular" in a.summary.lower()]
        assert len(circular) >= 1

    def test_advise_file_with_many_unused(self):
        """Should generate advice for files with many unused imports."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path("main.py"),
                message=f"Unused import {i}",
                location=Location(line=i),
            )
            for i in range(1, 5)
        ]
        context = AnalysisContext()
        advices = self.advisor.advise(findings, context)
        file_advice = [a for a in advices if "main.py" in a.summary]
        assert len(file_advice) >= 1

    def test_has_circular_deps_no_cycle(self):
        """Should detect no cycle in linear dependencies."""
        context = AnalysisContext()
        context.graph.add_import("main", "utils")
        context.graph.add_import("utils", "helpers")
        assert self.advisor._has_circular_deps(context) is False

    def test_has_circular_deps_with_cycle(self):
        """Should detect cycle in circular dependencies."""
        context = AnalysisContext()
        context.graph.add_import("a", "b")
        context.graph.add_import("b", "c")
        context.graph.add_import("c", "a")
        assert self.advisor._has_circular_deps(context) is True

    def test_has_circular_deps_self_import(self):
        """Should handle self-imports."""
        context = AnalysisContext()
        context.graph.add_import("a", "a")
        assert self.advisor._has_circular_deps(context) is True

    def test_advice_has_required_fields(self):
        """Generated advice should have all required fields."""
        findings = [
            Finding.create(
                type="unused_import",
                severity=Severity.INFO,
                file=Path(f"file{i}.py"),
                message=f"Unused import {i}",
                location=Location(line=1),
            )
            for i in range(11)
        ]
        context = AnalysisContext()
        advices = self.advisor.advise(findings, context)
        for advice in advices:
            assert advice.category is not None
            assert advice.summary is not None
            assert advice.explanation is not None
            assert advice.recommendation is not None
            assert advice.priority is not None

    def test_priority_property(self):
        """Advisor should have priority property."""
        assert self.advisor.priority == 7

    def test_name_property(self):
        """Advisor should have name property."""
        assert self.advisor.name == "architecture"
