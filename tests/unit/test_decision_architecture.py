"""Guardrail tests for the decision architecture (Tranche 1).

Covers binding-key dedupe, subject fusion, evidence recording,
try-block context, and confidence presentation.
"""

from __future__ import annotations

from pathlib import Path

from imodent.analysis.context import AnalysisConfig, AnalysisContext, FileInfo
from imodent.analysis.coordinator import (
    AnalysisCoordinator,
    FixMode,
    _deduplicate_findings,
)
from imodent.analysis.decisions import (
    DecisionEngine,
    subject_key_for_import,
)
from imodent.analysis.evidence import Evidence
from imodent.analysis.findings import Finding, Severity, Location
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


class TestReviewPublicApiGate:
    def test_proof_state_review_public_api_requires_decision(self):
        from imodent.analysis.decisions import (
            _requires_decision,
            DecisionCandidate,
            subject_key_for_import,
        )
        sk = subject_key_for_import(
            Path("mypkg/__init__.py"), "mypkg", "Public", None
        )
        candidate = DecisionCandidate(
            issue_type="unused_import",
            subject_key=sk,
            confidence=0.85,
            confidence_label="high",
            proof_state="REVIEW_PUBLIC_API",
        )
        assert _requires_decision(candidate, None, []) is True

    def test_confidence_0_10_from_context_evidence(self):
        from imodent.analysis.decisions import _score_confidence
        ev = Evidence(
            kind="ContextEvidence",
            file=Path("mypkg/__init__.py"),
            location=None,
            claim="public_api_reexport",
            polarity="context",
            strength=0.30,
        )
        f = Finding.create(
            type="lint",
            severity=Severity.WARNING,
            file=Path("mypkg/__init__.py"),
            message="F401: `mypkg.Public` imported but unused",
            lint_code="F401",
            lint_source="ruff",
        )
        score = _score_confidence(f, [f], [ev])
        assert score <= 0.15


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
            file=Path("test.py"),
            location=None,
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


# ---------------------------------------------------------------------------
# F401 → UNUSED_IMPORT classification  (Bug 3 regression)
# ---------------------------------------------------------------------------


class TestF401ClassifiedAsUnusedImport:
    def test_f401_issue_type_is_unused_import(self):
        """Ruff F401 should be classified as UNUSED_IMPORT, not generic LINT."""
        from imodent.analysis.decisions import _issue_type_from_finding
        f = Finding.create(
            type="lint", severity=Severity.WARNING,
            file=Path("test.py"), message="F401: unused",
            lint_code="F401", lint_source="ruff",
        )
        assert _issue_type_from_finding(f) == "unused_import"

    def test_f821_remains_lint_type(self):
        """F821 remains lint type (not an import issue)."""
        from imodent.analysis.decisions import _issue_type_from_finding
        f = Finding.create(
            type="lint", severity=Severity.ERROR,
            file=Path("test.py"), message="F821: Undefined name",
            lint_code="F821", lint_source="ruff",
        )
        assert _issue_type_from_finding(f) == "lint"

    def test_ruff_f401_single_alias_is_destructive_allowed(self, tmp_path):
        """Ruff F401 can only become an auto-delete candidate through DecisionEngine."""
        file_path = tmp_path / "sample.py"
        file_path.write_text("import os\n\nx = 1\n")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_imports = True
        project_context.config.check_lint = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([file_path])

        candidates = [c for c in result.candidates if c.issue_type == "unused_import"]
        assert candidates
        assert candidates[0].destructive_allowed is True

        fix_results = coordinator.fix(
            result.findings,
            result.context,
            mode=FixMode.SAFE_AUTO,
            candidates=result.candidates,
        )
        assert file_path in fix_results
        assert fix_results[file_path].content == "x = 1\n"

    def test_grouped_ruff_f401_is_not_destructive_allowed(self, tmp_path):
        """Grouped imports stay blocked until imodent has alias-level rewrite."""
        file_path = tmp_path / "sample.py"
        file_path.write_text("import os, sys\n\nprint(sys.version)\n")

        project_context = ProjectContext.from_root(tmp_path)
        project_context.config.check_imports = True
        project_context.config.check_lint = True
        coordinator = AnalysisCoordinator(project_context=project_context)
        result = coordinator.analyze([file_path])

        candidates = [c for c in result.candidates if c.issue_type == "unused_import"]
        assert candidates
        assert all(not c.destructive_allowed for c in candidates)

        fix_results = coordinator.fix(
            result.findings,
            result.context,
            mode=FixMode.SAFE_AUTO,
            candidates=result.candidates,
        )
        assert fix_results == {}


# ---------------------------------------------------------------------------
# Evidence rehydration  (Bug 2 regression)
# ---------------------------------------------------------------------------


class TestEvidenceRehydration:
    def test_candidate_has_evidence_for_from_evidence_list(self):
        """Evidence index should rehydrate evidence_for from evidence_list."""
        from imodent.analysis.decisions import DecisionEngine
        file = Path("/tmp/sample.py").resolve()
        ev = Evidence(
            id=42, kind="RuffDiagnostic",
            file=file, location=Location(line=1),
            claim="unused_import", polarity="supports", strength=0.85,
        )
        finding = Finding(
            id="f1", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `os` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "evidence": [{"id": 42}],
                "import_info": {"module": None, "name": "os", "alias": None},
            },
        )
        candidates = DecisionEngine.build_candidates([finding], [ev])
        assert len(candidates) == 1
        assert len(candidates[0].evidence_for) >= 1
        assert candidates[0].evidence_for[0].id == 42
        assert candidates[0].evidence_for[0].polarity == "supports"

    def test_opposes_evidence_goes_to_evidence_against(self):
        """Evidence with polarity=opposes goes to evidence_against."""
        from imodent.analysis.decisions import DecisionEngine
        file = Path("/tmp/sample.py").resolve()
        ev = Evidence(
            id=99, kind="ContextEvidence",
            file=file, location=Location(line=1),
            claim="public_api_reexport", polarity="opposes", strength=0.3,
        )
        finding = Finding(
            id="f2", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `mypkg.Foo` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "evidence": [{"id": 99}],
                "import_info": {"module": "mypkg", "name": "Foo", "alias": None},
            },
        )
        candidates = DecisionEngine.build_candidates([finding], [ev])
        assert len(candidates[0].evidence_against) >= 1
        assert candidates[0].evidence_against[0].id == 99


# ---------------------------------------------------------------------------
# Subject-key fusion for specific import forms  (Bug 4 regression)
# ---------------------------------------------------------------------------


class TestImportFormFusion:
    def test_bare_dotted_import_fuses_with_ruff(self):
        """import os.path should fuse when Ruff and AST use different module splits."""
        file = Path("/tmp/sample.py").resolve()
        ruff = Finding(
            id="r1", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `os.path` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "os.path", "name": None, "alias": None},
            },
        )
        # local finding: same as what ImportAnalyzer produces for bare import
        local = Finding(
            id="l1", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'import os.path' is not used in this file",
            fixable=True, auto_fix_safe=False,
            import_module="os.path", import_name=None,
            data={"import_info": {"module": "os.path", "name": None, "alias": None}},
        )
        result = _deduplicate_findings([ruff, local])
        assert len(result) == 1
        assert result[0].lint_source == "ruff"

    def test_bare_aliased_import_fuses_with_ruff(self):
        """import numpy as np: alias should not block fusion."""
        file = Path("/tmp/sample.py").resolve()
        ruff = Finding(
            id="r2", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `numpy` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "numpy", "name": None, "alias": None},
            },
        )
        local = Finding(
            id="l2", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'import numpy as np' is not used in this file",
            fixable=True, auto_fix_safe=False,
            import_module="numpy", import_name=None,
            data={"import_info": {"module": "numpy", "name": None, "alias": "np"}},
        )
        result = _deduplicate_findings([ruff, local])
        assert len(result) == 1
        assert result[0].lint_source == "ruff"

    def test_from_import_alias_fuses_with_ruff(self):
        """from typing import Dict as D: alias should not block fusion."""
        file = Path("/tmp/sample.py").resolve()
        ruff = Finding(
            id="r3", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `typing.Dict` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "typing", "name": "Dict", "alias": None},
            },
        )
        local = Finding(
            id="l3", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'from typing import Dict as D' is not used in this file",
            fixable=True, auto_fix_safe=False,
            import_module="typing", import_name="Dict",
            data={"import_info": {"module": "typing", "name": "Dict", "alias": "D"}},
        )
        result = _deduplicate_findings([ruff, local])
        assert len(result) == 1
        assert result[0].lint_source == "ruff"

    def test_grouped_one_used_one_unused_not_fused(self):
        """from x import a, b where a is used and b is unused: each is separate subject."""
        file = Path("/tmp/sample.py").resolve()
        ruff_a = Finding(
            id="ra", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `x.a` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "x", "name": "a", "alias": None},
            },
        )
        ruff_b = Finding(
            id="rb", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `x.b` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "x", "name": "b", "alias": None},
            },
        )
        local_a = Finding(
            id="la", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'from x import a' is not used",
            fixable=True, auto_fix_safe=False,
            import_module="x", import_name="a",
            data={"import_info": {"module": "x", "name": "a", "alias": None}},
        )
        result = _deduplicate_findings([ruff_a, ruff_b, local_a])
        # a should be deduped (Ruff covers it), b is Ruff-only
        assert len(result) == 2
        assert all(f.lint_source == "ruff" for f in result)

    def test_unused_import_and_unused_import_file_both_deduped(self):
        """Both unused_import and unused_import_file local types should be deduped."""
        file = Path("/tmp/sample.py").resolve()
        ruff = Finding(
            id="r", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `typing.Dict` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "import_info": {"module": "typing", "name": "Dict", "alias": None},
            },
        )
        local_file = Finding(
            id="lf", type="unused_import_file", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'from typing import Dict' is not used in this file",
            fixable=True, auto_fix_safe=False,
            import_module="typing", import_name="Dict",
            data={"import_info": {"module": "typing", "name": "Dict", "alias": None}},
        )
        local_usage = Finding(
            id="lu", type="unused_import", severity=Severity.INFO,
            file=file, location=Location(line=1),
            message="Import 'from typing import Dict' may be unused",
            fixable=True, auto_fix_safe=False,
            import_module="typing", import_name="Dict",
            data={"import_info": {"module": "typing", "name": "Dict", "alias": None, "is_from": True, "intent": "usage"}},
        )
        result = _deduplicate_findings([ruff, local_file, local_usage])
        assert len(result) == 1
        assert result[0].lint_source == "ruff"


# ---------------------------------------------------------------------------
# G-ERR-2: AST guard — unanalyzable_file emitted when ast_tree is None
# ---------------------------------------------------------------------------


class TestUnanalyzableFileGuard:
    """ImportAnalyzer emits unanalyzable_file whenever ast_tree is None."""

    def test_emits_finding_when_ast_tree_none_and_no_syntax_error_flag(self, tmp_path):
        """Emit unanalyzable_file when ast_tree is None regardless of has_syntax_errors.

        The old guard required `has_syntax_errors AND ast_tree is None`.
        This test constructs a FileInfo with ast_tree=None, has_syntax_errors=False
        (an artificial state that could arise from external construction) and
        verifies the new guard still emits the diagnostic.
        """
        py_file = tmp_path / "broken.py"
        py_file.write_text("def f():\n    pass\n")

        file_info = FileInfo(
            path=py_file,
            content="def f():\n    pass\n",
            language="python",
            has_syntax_errors=False,  # NOT flagged as syntax error
            ast_tree=None,            # but ast_tree is None — the new guard catches this
        )

        context = AnalysisContext(
            files={py_file: file_info},
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )
        build_dependency_graph(context.files, tmp_path)

        findings = ImportAnalyzer().analyze(context)
        unanalyzable = [f for f in findings if f.type == "unanalyzable_file"]
        assert len(unanalyzable) == 1
        assert unanalyzable[0].file == py_file

    def test_no_finding_when_ast_tree_present(self, tmp_path):
        """When ast_tree is present, no unanalyzable_file finding is emitted."""
        import ast

        py_file = tmp_path / "ok.py"
        py_file.write_text("import os\n")

        file_info = FileInfo(
            path=py_file,
            content="import os\n",
            language="python",
            has_syntax_errors=False,
            ast_tree=ast.parse("import os\n"),
        )

        context = AnalysisContext(
            files={py_file: file_info},
            config=AnalysisConfig(check_imports=True, check_lint=False),
        )
        build_dependency_graph(context.files, tmp_path)

        findings = ImportAnalyzer().analyze(context)
        assert not any(f.type == "unanalyzable_file" for f in findings)


# ---------------------------------------------------------------------------
# context.py — AnalysisContext.findings type annotation
# ---------------------------------------------------------------------------


class TestAnalysisContextTyping:
    def test_findings_field_annotation_is_list_of_finding(self):
        """AnalysisContext.findings must be annotated as list[Finding], not plain list."""
        import dataclasses
        import typing

        fields = {f.name: f for f in dataclasses.fields(AnalysisContext)}
        assert "findings" in fields, "AnalysisContext must have a 'findings' field"

        hint = typing.get_type_hints(AnalysisContext).get("findings")
        assert hint is not None, "findings field must have a type annotation"

        # Accept both list[Finding] and List[Finding] (generic alias forms)
        args = getattr(hint, "__args__", None)
        assert args is not None and Finding in args, (
            f"findings must be annotated as list[Finding], got {hint!r}"
        )


# ---------------------------------------------------------------------------
# Ruff fix applicability is evidence, not edit authorization  (W6)
# ---------------------------------------------------------------------------


class TestRuffFixApplicability:
    def test_ruff_safe_fix_does_not_authorize_imodent_without_single_alias(self):
        """Ruff "safe" applies to Ruff's patch, not imodent's whole-line edit."""
        from imodent.analysis.decisions import DecisionEngine
        file = Path("/tmp/sample.py").resolve()
        ev = Evidence(
            id=43, kind="RuffDiagnostic",
            file=file, location=Location(line=1),
            claim="unused_import", polarity="supports", strength=0.85,
            data={"fix": {"applicability": "safe"}},
        )
        finding = Finding(
            id="f1", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `os` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "evidence": [{"id": 43}],
                "import_info": {"module": None, "name": "os", "alias": None, "single_alias": False},
            },
        )
        candidates = DecisionEngine.build_candidates([finding], [ev])
        assert len(candidates) == 1
        assert candidates[0].ruff_fix_applicability == "safe"
        assert candidates[0].destructive_allowed is False

    def test_ruff_unsafe_fix_blocks_destructive_without_single_alias(self):
        """Ruff "unsafe" fix → destructive_allowed=False unless single_alias."""
        from imodent.analysis.decisions import DecisionEngine
        file = Path("/tmp/sample.py").resolve()
        ev = Evidence(
            id=44, kind="RuffDiagnostic",
            file=file, location=Location(line=1),
            claim="unused_import", polarity="supports", strength=0.85,
            data={"fix": {"applicability": "unsafe"}},
        )
        finding = Finding(
            id="f2", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `os` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "evidence": [{"id": 44}],
                "import_info": {"module": None, "name": "os", "alias": None, "single_alias": False},
            },
        )
        candidates = DecisionEngine.build_candidates([finding], [ev])
        assert len(candidates) == 1
        assert candidates[0].ruff_fix_applicability == "unsafe"
        assert candidates[0].destructive_allowed is False

    def test_ruff_unsafe_fix_with_single_alias_allows_destructive(self):
        """Ruff "unsafe" fix + single_alias=True → destructive_allowed=True."""
        from imodent.analysis.decisions import DecisionEngine
        file = Path("/tmp/sample.py").resolve()
        ev = Evidence(
            id=45, kind="RuffDiagnostic",
            file=file, location=Location(line=1),
            claim="unused_import", polarity="supports", strength=0.85,
            data={"fix": {"applicability": "unsafe"}},
        )
        finding = Finding(
            id="f3", type="lint", severity=Severity.WARNING,
            file=file, location=Location(line=1),
            message="F401: `os` imported but unused",
            fixable=False, auto_fix_safe=False,
            lint_code="F401", lint_source="ruff",
            data={
                "evidence": [{"id": 45}],
                "import_info": {"module": None, "name": "os", "alias": None, "single_alias": True},
            },
        )
        candidates = DecisionEngine.build_candidates([finding], [ev])
        assert len(candidates) == 1
        assert candidates[0].ruff_fix_applicability == "unsafe"
        assert candidates[0].destructive_allowed is True
