"""Residue analyzer for declared but unwired behavior.

This pass treats dead-looking code as evidence of unfinished intent. It does
not delete or fix; it emits a cluster-shaped finding that a later planner can
wire, export, register, test, or quarantine.
"""

from __future__ import annotations

import re
import ast
from pathlib import Path

from .base import Analyzer, AnalyzerCapability
from ..analysis.context import AnalysisContext
from ..analysis.findings import Finding, Location, Severity


class ResidueAnalyzer(Analyzer):
    """Detect LLM residue patterns that should be wired before deleted."""

    @property
    def name(self) -> str:
        return "residue"

    @property
    def capabilities(self):
        return {AnalyzerCapability.RESIDUE}

    def analyze(self, context: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []

        if context.config.check_lint:
            lint_finding = self._find_declared_lint_without_executor(context)
            if lint_finding is not None:
                findings.append(lint_finding)

        return findings

    def _find_declared_lint_without_executor(
        self, context: AnalysisContext
    ) -> Finding | None:
        cli_flag = self._find_argparse_flag(context, "--lint")
        analyze_param = self._find_text(context, "analyze_lint")
        config_field = self._find_text(context, "check_lint")
        lint_executor = self._find_regex(
            context, r"class\s+LintAnalyzer\b|def\s+analyze_lint\b"
        )

        if not (cli_flag and analyze_param and config_field):
            return None
        if lint_executor is not None:
            return None

        evidence = {
            "cluster": "lint",
            "signals": {
                "cli_flag": self._evidence_dict(cli_flag),
                "analysis_parameter": self._evidence_dict(analyze_param),
                "config_field": self._evidence_dict(config_field),
            },
            "missing_executor": "No LintAnalyzer class or analyze_lint executor found",
            "recommended_actions": ["wire", "implement", "mark_unsupported", "test"],
            "destructive_allowed": False,
        }

        return Finding.create(
            type="declared_behavior_unwired",
            severity=Severity.WARNING,
            file=cli_flag[0],
            location=Location(line=cli_flag[1]),
            message=(
                "Lint behavior is declared by CLI/config plumbing but has no "
                "executor; wire the capability or mark it unsupported."
            ),
            fixable=False,
            auto_fix_safe=False,
            data=evidence,
        )

    @staticmethod
    def _find_text(
        context: AnalysisContext, needle: str
    ) -> tuple[Path, int, str] | None:
        for path, file_info in context.files.items():
            for lineno, line in enumerate(file_info.content.splitlines(), 1):
                if needle in line:
                    return path, lineno, line.strip()
        return None

    @staticmethod
    def _find_regex(
        context: AnalysisContext, pattern: str
    ) -> tuple[Path, int, str] | None:
        compiled = re.compile(pattern)
        for path, file_info in context.files.items():
            for lineno, line in enumerate(file_info.content.splitlines(), 1):
                if compiled.search(line):
                    return path, lineno, line.strip()
        return None

    @staticmethod
    def _find_argparse_flag(
        context: AnalysisContext, flag: str
    ) -> tuple[Path, int, str] | None:
        """Find argparse add_argument calls even when split across lines."""
        for path, file_info in context.files.items():
            if file_info.ast_tree is None:
                continue
            lines = file_info.content.splitlines()
            for node in ast.walk(file_info.ast_tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "add_argument"
                ):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == flag:
                        line = lines[node.lineno - 1].strip()
                        return path, node.lineno, line
        return None

    @staticmethod
    def _evidence_dict(match: tuple[Path, int, str]) -> dict[str, str | int]:
        path, line, text = match
        return {"file": str(path), "line": line, "text": text}
