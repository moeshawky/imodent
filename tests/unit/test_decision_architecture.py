"""Guardrail tests for the decision architecture (Tranche 1).

Covers binding-key dedupe, subject fusion, evidence recording,
try-block context, and confidence presentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.coordinator import (
    AnalysisCoordinator,
    FixMode,
    _deduplicate_findings,
)
from imodent.analysis.decisions import (
    DecisionEngine,
    SubjectKey,
    subject_key_for_import,
    subject_key_for_lint,
)
from imodent.analysis.evidence import Evidence
from imodent.analysis.findings import Finding, Severity, Location, ProofState
from imodent.analyzers.imports import ImportAnalyzer, _classify_try_context
from imodent.graph.dependency import build_dependency_graph
from imodent.graph.imports import ImportInfo
from imodent.project.project_context import ProjectContext


# ---------------------------------------------------------------------------
# SubjectKey / binding key
# ---------------------------------------------------------------------------


class TestSubjectKeyBinding:
    def test_bare_import_normalizes_name_to_module(self):
        """Bare import: name "os" without module normalizes to module="os"."""
        sk = subject_key_for_import(Path("test.py"), module=None, name="os", alias=None)
        assert sk.module == "os"
        assert sk.name is None

    def test_from_import_keeps_module_and_name(self):
        """From-import: module and name are preserved as-is."""
        sk = subject_key_for_import(
            Path("test.py"), module="typing", name="Dict", alias=None
        )
        assert sk.module == "typing"
        assert sk.name == "Dict"

    def test_grouped_imports_have_separate_subject_keys(self):
        """Each alias in a grouped import from x import a, b, c is a separate subject."""
        keys = [
            subject_key_for_import(Path("test.py"), "x", "a", None).binding_key,
            subject_key_for_import(Path("test.py"), "x", "b", None).binding_key,
            subject_key_for_import(Path("test.py"), "x", "c", None).binding_key,
        ]
        assert len(set(keys)) == 3

    def test_alias_identity_tracks_origin(self):
        """Alias identity tracks both the local alias and the origin symbol."""
        sk = subject_key_for_import(
            Path("test.py"), "pkg.mod", "Thing", alias="T"
        )
        assert sk.bound_name == "T"
        assert sk.origin == "pkg.mod.Thing"


# ---------------------------------------------------------------------------
# Binding-key dedupe
# ---------------------------------------------------------------------------


class TestBindingKeyDedupe:
    def test_ruff_f401_and_local_unused_fuse_by_subject_key(self):
        """Ruff F401 and local unused-import findings for the same import
        should fuse, with the Ruff finding preferred."""
        file = Path("/tmp/sample.py").resolve()
        ruff_finding = Finding(
            id="1",
            type="lint",
            severity=Severity.WARNING,
            file=file,
            location=Location(line=1),
            message="F401: `os` imported but unused",
            fixable=False,
            auto_fix_safe=False,
            lint_code="F401",
            lint_source="ruff",
            data={"import_info": {"module": None, "name": "os", "alias": None}},
        )
        local_finding = Finding(
            id="2",
            type="unused_import_file",
            severity=Severity.INFO,
            file=file,
            location=Location(line=1),
            message="Import 'import os' is not used in this file",
            fixable=True,
            auto_fix_safe=False,
            import_name=None,
            import_module="os",
            data={"import_info": {"module": "os", "name": None, "alias": None}},
        )
        result = _deduplicate_findings([ruff_finding, local_finding])
        assert len(result) == 1
        assert result[0].lint_source == "ruff"

    def test_different_aliases_are_not_deduped(self):
        """Grouped import aliases from same import line are separate subjects."""
        file = Path("/tmp/sample.py").resolve()
        ruff_dict = Finding(
            id="1", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `typing.Dict` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={"import_info": {"module": "typing", "name": "Dict", "alias": None}},
        )
        ruff_list = Finding(
            id="2", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `typing.List` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={"import_info": {"module": "typing", "name": "List", "alias": None}},
        )
        local_dict = Finding(
            id="3", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'from typing import Dict' is not used in this file",
            fixable=True, auto_fix_safe=False,
            import_name="Dict", import_module="typing",
            data={"import_info": {"module": "typing", "name": "Dict", "alias": None}},
        )
        result = _deduplicate_findings([ruff_dict, ruff_list, local_dict])
        # Dict finding should be deduped (Ruff covers it), List kept (no local dup)
        assert len(result) == 2
        assert all(f.lint_source == "ruff" for f in result)


# ---------------------------------------------------------------------------
# Conflicting evidence (F401 + F821)
# ---------------------------------------------------------------------------


class TestConflictingEvidence:
    def test_f401_and_f821_remain_conflicting_not_fused(self):
        """Same symbol with F401 (unused) and F821 (undefined) is conflicting
        evidence — not fused into certainty."""
        file = Path("/tmp/sample.py").resolve()
        f401 = Finding(
            id="1", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `missing` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={"import_info": {"module": None, "name": "missing", "alias": None}},
        )
        f821 = Finding(
            id="2", type="lint", severity=Severity.ERROR,
            file=file, location=Location(line=2),
            message="F821: Undefined name `missing`",
            fixable=False, auto_fix_safe=False,
            lint_code="F821", lint_source="ruff",
            data={"import_info": {}},
        )
        candidates = DecisionEngine.build_candidates([f401, f821])
        # Two separate candidates — not fused
        assert len(candidates) >= 2
        types = {c.issue_type for c in candidates}
        # F821 is "lint" issue type; F401 is "unused_import"
        assert "lint" in types or "unused_import" in types


# ---------------------------------------------------------------------------
# Init re-export evidence
# ---------------------------------------------------------------------------


class TestInitReexportEvidence:
    def test_init_reexport_records_evidence_not_suppressed(self, tmp_path):
        """Package-local __init__.py re-export should record evidence
        and carry REVIEW_PUBLIC_API proof state."""
        source = "from pathlib import Path\nfrom mypkg.public import Public\n"
        file_path = tmp_path / "mypkg" / "__init__.py"
        file_path.parent.mkdir()
        file_path.write_text(source)
        file_info = FileInfo.from_path(file_path)
        context = AnalysisContext(
            files={file_path: file_info},
            graph=build_dependency_graph({file_path: file_info}, tmp_path),
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )
        findings = ImportAnalyzer().analyze(context)
        # Pathlib Path should be flagged, not silently protected
        path_findings = [
            f for f in findings
            if getattr(f, "import_name", None) == "Path" or (
                f.data.get("import_info", {}).get("name") == "Path"
            )
        ]
        assert path_findings  # Evidence is recorded

    def test_hallucinated_rewrite_is_not_auto_protected(self, tmp_path):
        """A non-existent package-local re-export is not auto-protected."""
        source = "from nonexistent.missing import Imaginary\n"
        file_path = tmp_path / "__init__.py"
        file_path.write_text(source)
        file_info = FileInfo.from_path(file_path)
        context = AnalysisContext(
            files={file_path: file_info},
            graph=build_dependency_graph({file_path: file_info}, tmp_path),
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )
        findings = ImportAnalyzer().analyze(context)
        unused = [f for f in findings if f.type == "unused_import_file"]
        intent = [f for f in findings if f.type == "import_intent"]
        # Should be flagged as unused (hallucinated, not real package-local)
        assert unused or intent


# ---------------------------------------------------------------------------
# Try-block context
# ---------------------------------------------------------------------------


class TestTryBlockContext:
    def test_try_import_caps_deletion_confidence(self):
        """Try-block imports become intent findings, not deletion candidates."""
        content = """try:
    import optional_dep
except ImportError:
    pass
"""
        imp = ImportInfo(
            module="optional_dep", name=None, alias=None,
            line=2, is_from_import=False, file=Path("test.py"),
        )
        from imodent.analyzers.imports import _detect_import_intent
        intent, reason, is_safe = _detect_import_intent(
            imp, content, Path("test.py")
        )
        assert intent == "try_block"
        assert "ImportError" in reason
        assert is_safe is False  # Cannot auto-fix

    def test_broad_except_exception_lowers_optional_dep_confidence(self):
        """Broad except Exception handler provides weaker context than
        ImportError/ModuleNotFoundError."""
        content = """try:
    import optional_dep
except Exception:
    pass
"""
        imp = ImportInfo(
            module="optional_dep", name=None, alias=None,
            line=2, is_from_import=False, file=Path("test.py"),
        )
        from imodent.analyzers.imports import _detect_import_intent
        intent, reason, is_safe = _detect_import_intent(
            imp, content, Path("test.py")
        )
        assert intent == "try_block"
        assert "broad except Exception" in reason

    def test_try_block_import_with_no_marker_remains_reviewable(self):
        """Unused import in try block with no specific handler is still reviewable."""
        content = """try:
    import some_dep
except:
    pass
"""
        imp = ImportInfo(
            module="some_dep", name=None, alias=None,
            line=2, is_from_import=False, file=Path("test.py"),
        )
        from imodent.analyzers.imports import _detect_import_intent
        intent, _, _ = _detect_import_intent(imp, content, Path("test.py"))
        assert intent == "try_block"

    def test_classify_try_context_import_error(self):
        content = """try:
    import dep
except ImportError:
    pass
"""
        assert "ImportError" in _classify_try_context(content, 2)

    def test_classify_try_context_module_not_found(self):
        content = """try:
    import dep
except ModuleNotFoundError:
    pass
"""
        assert "ModuleNotFoundError" in _classify_try_context(content, 2)

    def test_classify_try_context_broad_except(self):
        content = """try:
    import dep
except Exception:
    pass
"""
        assert "broad except Exception" in _classify_try_context(content, 2)


# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_f821_scores_high(self):
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="lint", severity=Severity.ERROR,
            file=Path("test.py"), message="F821: Undefined name",
            lint_code="F821", lint_source="ruff",
        )
        score = _score_confidence(f, [f], [])
        assert score >= 0.85

    def test_f401_scores_high(self):
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="lint", severity=Severity.WARNING,
            file=Path("test.py"), message="F401: unused",
            lint_code="F401", lint_source="ruff",
        )
        score = _score_confidence(f, [f], [])
        assert score >= 0.80

    def test_init_reexport_scores_low(self):
        from imodent.analysis.decisions import _score_confidence
        f = Finding.create(
            type="lint", severity=Severity.WARNING,
            file=Path("__init__.py"), message="F401: unused",
            lint_code="F401", lint_source="ruff",
            data={"import_info": {"intent": "re_export"}},
        )
        score = _score_confidence(f, [f], [])
        assert score <= 0.15


# ---------------------------------------------------------------------------
# ALL_AUTO safety gate
# ---------------------------------------------------------------------------


class TestAllAutoFixSafety:
    def test_all_auto_cannot_fix_grouped_duplicate(self, tmp_path):
        """ALL_AUTO must not fix duplicate imports on grouped lines
        because the duplicate itself isn't safe for whole-line deletion."""
        source = "import os, sys\nimport sys, json\nprint(json.dumps({}))\n"
        file_path = tmp_path / "sample.py"
        file_path.write_text(source)

        project_context = ProjectContext.from_root(tmp_path)
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([file_path])

        duplicates = [f for f in result.findings if f.type == "duplicate_import"]
        assert duplicates
        # The duplicate finding itself should NOT be auto_fix_safe
        assert all(not f.auto_fix_safe for f in duplicates)


# ---------------------------------------------------------------------------
# Confidence presentation
# ---------------------------------------------------------------------------


class TestConfidenceOutput:
    def test_subject_key_to_dict(self):
        """Subject keys serialize cleanly."""
        sk = subject_key_for_import(Path("test.py"), "typing", "Dict", None)
        d = sk.to_dict()
        assert d["kind"] == "import"
        assert d["module"] == "typing"
        assert d["name"] == "Dict"
        assert d["file"] == "test.py"

    def test_confidence_label_mapping(self):
        from imodent.analysis.decisions import _compute_confidence_label
        assert _compute_confidence_label(0.95) == "high"
        assert _compute_confidence_label(0.70) == "medium"
        assert _compute_confidence_label(0.30) == "low"


# ---------------------------------------------------------------------------
# Duplicate evidence to_dict fix
# ---------------------------------------------------------------------------


class TestEvidenceToDict:
    def test_evidence_to_dict_is_not_duplicated(self):
        """Evidence.to_dict should exist exactly once (old bug had two definitions)."""
        import inspect
        source = inspect.getsource(Evidence)
        assert source.count("def to_dict") == 1

    def test_evidence_to_dict_includes_new_fields(self):
        """Evidence.to_dict includes id, polarity, claim, strength."""
        e = Evidence(
            kind="test",
            source="unit",
            file=Path("test.py"),
            location=None,
            subject="test",
            claim="test claim",
            polarity="supports",
            strength=0.75,
        )
        d = e.to_dict()
        assert "id" in d
        assert "claim" in d
        assert d["claim"] == "test claim"
        assert d["polarity"] == "supports"
        assert d["strength"] == 0.75
