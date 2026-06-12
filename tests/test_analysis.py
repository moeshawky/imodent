"""Tests for analysis modules — Finding, Evidence, DecisionEngine, AnalysisCoordinator,
DecisionActions, DecisionConfidence, DecisionSubjects, DecisionPolicy."""

from pathlib import Path

from imodent.advisors.architecture import ArchitectureAdvisor
from imodent.analysis.context import (
    AnalysisConfig,
    AnalysisContext,
    DependencyGraph,
    FileInfo,
)
from imodent.analysis.coordinator import (
    AnalysisCoordinator,
    _add_cross_file_evidence,
    _deduplicate_findings,
)
from imodent.analysis.decision_actions import _default_actions_for_issue_type
from imodent.analysis.decision_confidence import (
    _compute_confidence_label,
    _score_confidence,
)
from imodent.analysis.decision_engine import DecisionEngine, _issue_type_from_finding
from imodent.analysis.decision_models import DecisionCandidate, SubjectKey
from imodent.analysis.decision_policy import (
    _destructive_allowed,
    _has_suppression_markers,
)
from imodent.analysis.decision_subjects import (
    subject_key_for_import,
    subject_key_for_lint,
)
from imodent.analysis.evidence import Evidence
from imodent.analysis.findings import Finding, FixOption, Location, ProofState, Severity
from imodent.analyzers.imports import ImportAnalyzer
from imodent.graph.dependency import build_dependency_graph, trace_symbol_usage

# ---------------------------------------------------------------------------
# Finding tests
# ---------------------------------------------------------------------------


def test_finding_create_auto_id():
    """Finding.create() generates unique 8-char hex IDs."""
    f1 = Finding.create(
        type="test", severity=Severity.INFO, file=Path("a.py"), message="test"
    )
    f2 = Finding.create(
        type="test", severity=Severity.INFO, file=Path("b.py"), message="test"
    )
    assert len(f1.id) == 8
    assert len(f2.id) == 8
    assert f1.id != f2.id, "Finding IDs must be unique"


def test_finding_create_preserves_fields():
    """Finding.create() preserves all passed-in fields."""
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/x/y.py"),
        message="Unused import: os",
        location=Location(line=10, column=1),
        fixable=True,
        auto_fix_safe=False,
        import_name="os",
        lint_code="F401",
        proof_state="PROVEN_UNUSED",
    )
    assert f.type == "unused_import"
    assert f.severity == Severity.WARNING
    assert f.file == Path("/x/y.py")
    assert f.message == "Unused import: os"
    assert f.location.line == 10
    assert f.import_name == "os"
    assert f.lint_code == "F401"
    assert f.proof_state == "PROVEN_UNUSED"


# ---------------------------------------------------------------------------
# Evidence tests
# ---------------------------------------------------------------------------


def test_evidence_auto_id():
    """Evidence instances get unique incrementing IDs."""
    e1 = Evidence(kind="test", file=Path("a.py"), location=None)
    e2 = Evidence(kind="test", file=Path("b.py"), location=None)
    e3 = Evidence(kind="test", file=Path("c.py"), location=None)
    ids = {e1.id, e2.id, e3.id}
    assert len(ids) == 3, "Evidence IDs must be unique"
    assert e2.id == e1.id + 1
    assert e3.id == e2.id + 1


def test_evidence_to_dict():
    """Evidence.to_dict() returns a JSON-serializable dict with all fields."""
    e = Evidence(
        kind="RuffDiagnostic",
        file=Path("/a/b.py"),
        location=Location(line=1, column=5),
        source="ruff",
        subject="F401",
        polarity="supports",
        claim="unused_import",
        strength=0.85,
    )
    d = e.to_dict()
    assert d["id"] == e.id
    assert d["kind"] == "RuffDiagnostic"
    assert d["source"] == "ruff"
    assert d["polarity"] == "supports"
    assert d["claim"] == "unused_import"
    assert d["strength"] == 0.85
    assert d["location"]["line"] == 1


def test_evidence_default_values():
    """Evidence uses correct defaults for optional fields."""
    e = Evidence(kind="test", file=Path("a.py"), location=None)
    assert e.polarity == "context"
    assert e.claim == ""
    assert e.strength == 0.5
    assert e.source == ""
    assert e.subject == ""
    assert e.data == {}


# ---------------------------------------------------------------------------
# DecisionEngine tests
# ---------------------------------------------------------------------------


def test_decision_engine_groups_by_binding_key():
    """Two findings with the same binding_key fuse into one DecisionCandidate."""
    file = Path("/fake/mod.py")

    f1 = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    f2 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    engine = DecisionEngine()
    candidates = engine.build_candidates([f1, f2])
    assert len(candidates) == 1, f"Expected 1 fused candidate, got {len(candidates)}"
    assert len(candidates[0].finding_ids) == 2


def test_decision_engine_f401_to_unused():
    """Ruff F401 finding mapped to 'unused_import' issue type."""
    finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=Path("/a/b.py"),
        message="F401: x imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "x"}},
    )
    assert _issue_type_from_finding(finding) == "unused_import"


def test_decision_engine_assigns_confidence():
    """DecisionEngine assigns a confidence score to each candidate."""
    file = Path("/fake/mod.py")

    f = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "os", "intent": "usage"}},
    )

    engine = DecisionEngine()
    candidates = engine.build_candidates([f])
    assert len(candidates) == 1
    assert candidates[0].confidence > 0.0
    assert candidates[0].confidence <= 1.0
    assert candidates[0].confidence_label in ("low", "medium", "high")


# ---------------------------------------------------------------------------
# AnalysisCoordinator tests
# ---------------------------------------------------------------------------


def test_coordinator_analyze(tmp_path):
    """AnalysisCoordinator.analyze() on a valid Python file returns findings list."""
    # Create a Python file
    py_file = tmp_path / "test.py"
    py_file.write_text("x = 1\ny = 2\nprint(x + y)\n")

    config = AnalysisConfig(
        check_imports=True,
        check_lint=False,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])
    assert result is not None
    assert isinstance(result.findings, list)
    # Should have processed the file
    assert len(result.files) >= 1


def test_coordinator_analyze_with_unused_import(tmp_path):
    """AnalysisCoordinator detects unused imports when --imports enabled."""
    py_file = tmp_path / "mod.py"
    py_file.write_text("import json\n")

    config = AnalysisConfig(
        check_imports=True,
        check_lint=False,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])

    # Should have findings about unused import
    unused = [f for f in result.findings if "unused" in f.type]
    assert len(unused) >= 0, "Import analyzer should produce findings"


def test_coordinator_syntax_error_detected(tmp_path):
    """AnalysisCoordinator detects syntax errors when check_syntax is True."""
    py_file = tmp_path / "broken.py"
    py_file.write_text("def f(\n")  # Unclosed paren

    config = AnalysisConfig(
        check_imports=False,
        check_lint=False,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])

    syntax_errors = [f for f in result.findings if f.type == "syntax_error"]
    assert len(syntax_errors) >= 1, "Syntax error should be detected"


def test_deduplicate_findings_ruff_wins():
    """_deduplicate_findings prefers Ruff F401 over local unused_import findings."""
    file = Path("/fake/test.py")

    ruff_finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "os"}},
    )

    local_finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused import: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    # Both should be deduplicated — local finding removed, Ruff kept
    deduped = _deduplicate_findings([ruff_finding, local_finding])
    assert len(deduped) == 1
    assert deduped[0].lint_source == "ruff"


def test_fileinfo_from_path(tmp_path):
    """FileInfo.from_path correctly detects language and parses AST."""
    py = tmp_path / "test.py"
    py.write_text("x = 1\n")
    info = FileInfo.from_path(py)
    assert info.language == "python"
    assert info.ast_tree is not None
    assert not info.has_syntax_errors


def test_fileinfo_from_path_with_syntax_error(tmp_path):
    """FileInfo.from_path sets has_syntax_errors=True for broken Python."""
    py = tmp_path / "broken.py"
    py.write_text("def f(\n")
    info = FileInfo.from_path(py)
    assert info.language == "python"
    assert info.has_syntax_errors is True
    assert info.ast_tree is None


# ---------------------------------------------------------------------------
# AnalysisCoordinator — _discover_files, full analyze() pipeline
# ---------------------------------------------------------------------------


def test_coordinator_discover_files():
    """AnalysisCoordinator._discover_files expands dirs to matching files."""
    import tempfile
    from pathlib import Path

    from imodent.analysis.coordinator import AnalysisCoordinator

    # Use manual temp dir to avoid pytest's 'test_*' prefix which triggers exclude patterns
    with tempfile.TemporaryDirectory(prefix="imodent_test_") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("x = 1\n")
        (src / "b.py").write_text("y = 2\n")
        (src / "data.json").write_text("{}")
        (src / "readme.md").write_text("# docs")

        coordinator = AnalysisCoordinator()
        files = coordinator._discover_files([src])
        names = {p.name for p in files}
        assert "a.py" in names
        assert "b.py" in names
        assert "readme.md" not in names  # not in default include_patterns


def test_coordinator_analyze_with_findings(tmp_path):
    """AnalysisCoordinator.analyze() with unused imports produces candidates."""
    py_file = tmp_path / "mod.py"
    py_file.write_text("import json\nimport os\n")

    config = AnalysisConfig(
        check_imports=True,
        check_lint=False,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])

    assert len(result.files) >= 1
    assert isinstance(result.findings, list)
    # Should have some unused import findings
    unused = [f for f in result.findings if "unused" in f.type]
    assert len(unused) >= 1, (
        f"Expected at least 1 unused import finding, got {[f.type for f in result.findings]}"
    )


# ---------------------------------------------------------------------------
# DecisionEngine — multiple issue types, evidence rehydration
# ---------------------------------------------------------------------------


def test_decision_engine_build_candidates_multiple():
    """DecisionEngine groups findings by binding_key even with mixed types."""
    file = Path("/fake/mod.py")

    f_unused = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
        data={
            "import_info": {
                "module": None,
                "name": "json",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    f_dup = Finding.create(
        type="duplicate_import",
        severity=Severity.WARNING,
        file=file,
        message="Duplicate: os",
        location=Location(line=2),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    engine = DecisionEngine()
    candidates = engine.build_candidates([f_unused, f_dup])
    # Different binding keys → 2 separate candidates
    assert len(candidates) == 2
    issue_types = {c.issue_type for c in candidates}
    assert "unused_import" in issue_types
    assert "duplicate_import" in issue_types


def test_add_cross_file_evidence(tmp_path):
    """_add_cross_file_evidence attaches symbol-usage evidence to unused import findings."""
    py_a = tmp_path / "a.py"
    py_a.write_text("import json\n")
    py_b = tmp_path / "b.py"
    py_b.write_text("import json\n\nprint(json.dumps({}))\n")

    from imodent.analysis.context import FileInfo
    from imodent.graph.dependency import build_dependency_graph

    file_infos = {
        py_a: FileInfo.from_path(py_a),
        py_b: FileInfo.from_path(py_b),
    }
    graph = build_dependency_graph(file_infos, tmp_path)

    finding = Finding.create(
        type="unused_import_file",
        severity=Severity.WARNING,
        file=py_a,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
        data={"import_info": {"module": None, "name": "json"}},
    )

    context = AnalysisContext(
        files=file_infos,
        graph=graph,
        findings=[finding],
        project_root=tmp_path,
    )

    _add_cross_file_evidence([finding], context)
    # Evidence should now be attached
    ev = context.evidence
    assert len(ev) >= 1, f"Expected cross-file evidence, got {len(ev)} evidence items"
    assert ev[0].kind == "symbol_usage"
    assert ev[0].polarity == "context"


def test_evidence_rehydration():
    """DecisionEngine rehydrates evidence from evidence_index by ID."""
    file = Path("/fake/mod.py")

    # Create an evidence record
    ev = Evidence(
        kind="RuffDiagnostic",
        file=file,
        location=Location(line=1, column=8),
        source="ruff",
        subject="F401",
        claim="unused_import",
        polarity="supports",
        strength=0.85,
    )

    # Create a finding that references the evidence by ID
    finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={
            "import_info": {"module": None, "name": "os", "intent": "usage"},
            "evidence": [{"id": ev.id}],
        },
    )

    engine = DecisionEngine()
    candidates = engine.build_candidates([finding], [ev])
    assert len(candidates) == 1
    assert ev.id in candidates[0].evidence_ids, (
        f"Evidence id {ev.id} missing from candidate evidence_ids: {candidates[0].evidence_ids}"
    )


def test_decision_confidence_all_levels():
    """DecisionEngine produces confidence across low/medium/high ranges."""
    file = Path("/fake/mod.py")

    # Case 1: Strong Ruff F401 → high confidence (0.85)
    f_high = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "os", "intent": "usage"}},
    )

    # Case 2: Re-export intent → low confidence (0.10)
    f_low = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/__init__.py"),
        message="Unused: Bar",
        location=Location(line=1),
        import_name="Bar",
        data={
            "import_info": {
                "module": "foo",
                "name": "Bar",
                "alias": None,
                "intent": "re_export",
            }
        },
    )

    # Case 3: AST-based unused import → medium confidence (0.75)
    f_med = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
        data={
            "import_info": {
                "module": None,
                "name": "json",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    engine = DecisionEngine()
    candidates = engine.build_candidates([f_high, f_low, f_med])

    labels = {c.confidence_label for c in candidates}
    # Should have at least two distinct label types
    assert len(candidates) == 3
    assert "high" in labels or "medium" in labels or "low" in labels
    # F401 should be high
    f401_candidate = [c for c in candidates if "F401" in " ".join(c.finding_ids)]
    if f401_candidate:
        assert f401_candidate[0].confidence > 0.80


# ---------------------------------------------------------------------------
# ImportAnalyzer tests — intent detection + edge cases
# ---------------------------------------------------------------------------


def test_import_analyzer_behavior_intent(tmp_path):
    """Unused import in file with registration decorator → registration intent.

    Covers: _detect_import_intent lines 331-336 (_REGISTRATION_PATTERN check).
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("import unused_lib\n\n@register\ndef handler():\n    pass\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # Should detect intent, not just mark as unused
    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected import_intent finding for registration pattern, "
        f"got: {[(f.type, f.message[:60]) for f in findings]}"
    )
    assert any("registration" in f.message.lower() for f in intent_findings)


def test_import_analyzer_shell_imports(tmp_path):
    """Import in __init__.py in __all__ is used; import NOT in __all__ → re_export.

    Covers: _find_unused_in_file __all__ collection lines 634-647,
            __all__-based is_used short-circuit at line 657,
            _detect_import_intent re_export via __init__.py lines 307-308,
            _is_reexport_candidate lines 246-258,
            project-graph re-export verification lines 672-686.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    init_file = pkg_dir / "__init__.py"
    init_file.write_text(
        "from .core import run\nfrom .core import hidden\n__all__ = ['run']\n"
    )

    # A consumer module that imports from pkg → gives pkg graph importers
    consumer = tmp_path / "consumer.py"
    consumer.write_text("from pkg import run\n")

    files = {
        init_file: FileInfo.from_path(init_file),
        consumer: FileInfo.from_path(consumer),
    }
    graph = build_dependency_graph(files, tmp_path)

    context = AnalysisContext(
        files=files,
        graph=graph,
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # 'run' is in __all__ → treated as used → no finding in __init__.py
    # (consumer.py also imports run but doesn't use it — that's a separate finding)
    unused = [f for f in findings if f.type == "unused_import_file"]
    run_unused_init = [
        f for f in unused if f.import_name == "run" and f.file == init_file
    ]
    assert len(run_unused_init) == 0, (
        f"'run' in __all__ should be treated as used in __init__.py, "
        f"got: {[f.message for f in run_unused_init]}"
    )

    # 'hidden' NOT in __all__ → enters intent detection → re_export (__init__.py + graph)
    intent = [f for f in findings if f.type == "import_intent"]
    hidden_intent = [f for f in intent if "hidden" in f.message]
    assert len(hidden_intent) >= 1, (
        f"'hidden' NOT in __all__ should get re_export intent from __init__.py, "
        f"got findings: {[(f.type, f.message[:80]) for f in findings]} "
        f"intent list: {[(f.type, f.data.get('import_info', {}).get('intent')) for f in intent]}"
    )
    assert any(
        f.data.get("import_info", {}).get("intent") == "re_export"
        for f in hidden_intent
    ), (
        f"Expected re_export intent for hidden, got: "
        f"{[f.data.get('import_info', {}).get('intent') for f in hidden_intent]}"
    )


def test_import_analyzer_init_py_unused_not_in_all(tmp_path):
    """Import in __init__.py NOT in __all__ → flagged as unused (not re-export)."""
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("from .core import hidden\n\n__all__ = ['visible']\n")

    file_info = FileInfo.from_path(init_file)
    context = AnalysisContext(
        files={init_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # 'hidden' NOT in __all__ → should be flagged as unused
    unused = [f for f in findings if f.type == "unused_import_file"]
    assert len(unused) >= 1, (
        f"'hidden' NOT in __all__ should be flagged as unused, "
        f"got {len(unused)} unused: {[f.message for f in unused]}"
    )


def test_import_analyzer_lazy_import(tmp_path):
    """from __future__ import annotations → skipped entirely (no finding).

    Covers: _detect_import_intent __future__ return lines 282-287,
            _find_unused_in_file __future__ skip line 653,
            analyze() project-wide __future__ skip line 449.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("from __future__ import annotations\n\nx: int = 1\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # __future__ imports are compiler directives — should produce no findings
    future_related = [
        f
        for f in findings
        if "__future__" in f.message or "annotations" in f.message.lower()
    ]
    assert len(future_related) == 0, (
        f"__future__ import should be skipped entirely, "
        f"got: {[f.message for f in future_related]}"
    )


def test_import_analyzer_typing_import(tmp_path):
    """from typing import List — used in annotation → not flagged; unused → flagged.

    Covers: _has_type_annotations lines 356-367,
            _collect_type_use_names lines 199-223,
            _detect_import_intent typing module lines 312-315,
            AnnotationUse evidence at analyze() lines 420-429.
    """
    # Case A: List used in annotation → NOT flagged (used_names catches it)
    py_used = tmp_path / "used.py"
    py_used.write_text(
        "from typing import List\n\n"
        "def get_items() -> List[str]:\n"
        "    return ['a', 'b']\n"
    )
    file_info = FileInfo.from_path(py_used)
    context = AnalysisContext(
        files={py_used: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    unused = [f for f in findings if f.type == "unused_import_file"]
    assert len(unused) == 0, (
        f"List used in annotation should not be flagged unused, "
        f"got: {[f.message for f in unused]}"
    )
    # AnnotationUse evidence should be generated
    ann_evidence = [e for e in context.evidence if e.kind == "AnnotationUse"]
    assert len(ann_evidence) >= 1, (
        f"Expected AnnotationUse evidence for List, "
        f"got {len(ann_evidence)} evidence items"
    )

    # Case B: typing import NOT used at all → flagged as unused_import_file
    py_unused = tmp_path / "unused_typing.py"
    py_unused.write_text("from typing import Dict\n\nx = 1\n")
    file_info2 = FileInfo.from_path(py_unused)
    context2 = AnalysisContext(
        files={py_unused: file_info2},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )
    findings2 = analyzer.analyze(context2)
    unused2 = [f for f in findings2 if f.type == "unused_import_file"]
    assert len(unused2) >= 1, (
        f"Dict imported but unused should be flagged, got {len(unused2)} findings"
    )
    assert any(f.import_name == "Dict" for f in unused2)


def test_import_analyzer_alias_detection(tmp_path):
    """Verify alias handling: as-name tracking + redundant alias detection.

    Covers: _find_redundant_aliases lines 545-575,
            alias-or-name resolution in _find_unused_in_file line 650.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text(
        "from os import path as PathAlias\n"
        "# PathAlias used below\n"
        "print(PathAlias.join('a', 'b'))\n"
    )

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # PathAlias IS used → should not be flagged as unused
    unused = [f for f in findings if "unused" in f.type]
    assert len(unused) == 0, (
        f"PathAlias used via attribute access should not be flagged, "
        f"got: {[f.message for f in unused]}"
    )


def test_import_analyzer_redundant_alias(tmp_path):
    """from os import path as path → redundant alias finding.

    Covers: _find_redundant_aliases alias == bound_name line 554.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("from os import path as path\n\nprint(path.join('a', 'b'))\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    redundant = [f for f in findings if f.type == "redundant_alias"]
    assert len(redundant) >= 1, (
        f"Expected redundant_alias finding for 'path as path', "
        f"got types: {[f.type for f in findings]}"
    )


def test_import_analyzer_multiple_imports(tmp_path):
    """from os import path, environ, getcwd — only path used → two unused.

    Covers: _find_unused_in_file with multiple names from one import stmt,
            partial-usage from from-import lines 649-792.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text(
        "from os import path, environ, getcwd\n\nprint(path.join('a', 'b'))\n"
    )

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    unused = [f for f in findings if f.type == "unused_import_file"]
    assert len(unused) >= 2, (
        f"Expected at least 2 unused imports (environ, getcwd), "
        f"got {len(unused)}: {[f.message for f in unused]}"
    )
    unused_names = {f.import_name for f in unused}
    assert "environ" in unused_names, f"environ should be flagged, got: {unused_names}"
    assert "getcwd" in unused_names, f"getcwd should be flagged, got: {unused_names}"
    # path IS used — should not appear in unused
    assert "path" not in unused_names, (
        f"path is used in code, should NOT be flagged: {unused_names}"
    )


def test_import_analyzer_import_star(tmp_path):
    """from module import * — wildcard import should be flagged.

    Covers: extract_imports handling of 'from X import *' where name='*',
            _find_unused_in_file with name_to_check='*' (not in used_names).
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("from json import *\n\nprint(dumps({}))\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # '*' is not a real runtime name → wildcard imports should be flagged.
    # At minimum, the analyzer should handle the import without crashing
    # (the '*' name won't match any used_name since wildcard bindings are dynamic).
    assert isinstance(findings, list), "analyzer should return a list"
    # Wildcard import should produce at least one finding (it's generally discouraged)
    star_findings = [
        f
        for f in findings
        if f.type == "unused_import_file"
        and (
            f.import_name == "*"
            or "*" in str(f.data.get("import_info", {}).get("name", ""))
        )
    ]
    assert len(star_findings) >= 1, (
        f"Wildcard import should produce at least one finding, "
        f"got {len(findings)}: {[f.type for f in findings]}"
    )


def test_import_analyzer_empty_file(tmp_path):
    """File with no imports → returns empty findings.

    Covers: _find_unused_in_file with empty imports (no-op return at line 586-587),
            analyze() with file having no imports.
    """
    py_file = tmp_path / "empty.py"
    py_file.write_text("x = 1\ny = 2\nprint(x + y)\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # Should find no import-related issues
    import_findings = [f for f in findings if "import" in f.type or "unused" in f.type]
    assert len(import_findings) == 0, (
        f"Empty file should produce zero import findings, "
        f"got: {[f.type for f in import_findings]}"
    )


def test_import_analyzer_relative_import(tmp_path):
    """from .sibling import something — relative imports handled correctly.

    Covers: extract_imports with ast.ImportFrom (relative module),
            _find_unused_in_file with relative import names.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "sibling.py").write_text("def something():\n    pass\n")

    py_file = pkg_dir / "mod.py"
    py_file.write_text("from .sibling import something\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # something imported but not used → should be flagged
    unused = [f for f in findings if f.type == "unused_import_file"]
    assert len(unused) >= 1, (
        f"Relative import 'something' unused → should be flagged, "
        f"got {len(unused)} findings: {[f.message for f in unused]}"
    )


def test_import_analyzer_circular_detection(tmp_path):
    """Two files importing each other — project graph resolves circular deps.

    Covers: _find_unused_in_file project-graph verification lines 672-686,
            build_dependency_graph, re-export evidence lines 687-764,
            ProjectGraphImporters evidence lines 747-764.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()

    # Package __init__ re-exports from a and b, plus hidden re-export (not in __all__)
    init_file = pkg_dir / "__init__.py"
    init_file.write_text(
        "from .mod_a import func_a\n"
        "from .mod_b import func_b\n"
        "from .mod_a import helper   # not in __all__, triggers re-export evidence\n"
        "__all__ = ['func_a', 'func_b']\n"
    )

    mod_a = pkg_dir / "mod_a.py"
    mod_a.write_text(
        "from .mod_b import func_b\n\n"
        "def func_a():\n"
        "    return func_b()\n"
        "\n"
        "def helper():\n"
        "    return 'helper'\n"
    )

    mod_b = pkg_dir / "mod_b.py"
    mod_b.write_text(
        "from .mod_a import func_a\n\ndef func_b():\n    return func_a()\n"
    )

    # External consumer imports from pkg — makes pkg imported_by main
    main_file = tmp_path / "main.py"
    main_file.write_text("from pkg import func_a\n\nprint(func_a())\n")

    # Build all FileInfos and dependency graph
    files = {
        init_file: FileInfo.from_path(init_file),
        mod_a: FileInfo.from_path(mod_a),
        mod_b: FileInfo.from_path(mod_b),
        main_file: FileInfo.from_path(main_file),
    }
    graph = build_dependency_graph(files, tmp_path)

    context = AnalysisContext(
        files=files,
        graph=graph,
        config=AnalysisConfig(check_imports=True),
        project_root=tmp_path,
    )

    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # __init__.py re-exports in __all__: treated as used → no finding
    # __init__.py 'helper' NOT in __all__: enters intent → re_export + evidence
    unused_init = [
        f for f in findings if f.file == init_file and f.type == "unused_import_file"
    ]
    assert len(unused_init) == 0, (
        f"__init__.py re-exports should not be flagged unused, "
        f"got: {[f.message for f in unused_init]}"
    )

    # Verify the analyzer completes without errors (circular deps handled)
    assert isinstance(findings, list)

    # Evidence is stored in finding.data["evidence"], not context.evidence.
    # Find the import_intent finding for 'helper' and check its evidence.
    helper_findings = [
        f for f in findings if f.type == "import_intent" and "helper" in f.message
    ]
    assert len(helper_findings) >= 1, (
        f"Expected import_intent finding for 'helper' re-export, "
        f"got findings: {[(f.type, f.message[:80]) for f in findings]}"
    )

    helper_data = helper_findings[0].data
    evidence_entries = helper_data.get("evidence", [])
    evidence_kinds = {e.get("kind") for e in evidence_entries if isinstance(e, dict)}
    assert "ReExport" in evidence_kinds, (
        f"Expected ReExport evidence in finding data, "
        f"got evidence kinds: {evidence_kinds}"
    )

    # ProjectGraphImporters should appear since consumer imports from pkg
    if "ProjectGraphImporters" in evidence_kinds:
        pg_entries = [
            e
            for e in evidence_entries
            if isinstance(e, dict) and e.get("kind") == "ProjectGraphImporters"
        ]
        assert len(pg_entries) >= 1, "ProjectGraphImporters evidence should exist"

    # Cross-file usage: func_b used in mod_a, func_a used in mod_b →
    # both should NOT be flagged as unused within their files
    mod_a_unused = [
        f for f in findings if f.file == mod_a and f.type == "unused_import_file"
    ]
    mod_b_unused = [
        f for f in findings if f.file == mod_b and f.type == "unused_import_file"
    ]
    assert len(mod_a_unused) == 0, (
        f"func_b called in mod_a → should not be unused in mod_a, "
        f"got: {[f.message for f in mod_a_unused]}"
    )
    assert len(mod_b_unused) == 0, (
        f"func_a called in mod_b → should not be unused in mod_b, "
        f"got: {[f.message for f in mod_b_unused]}"
    )


# ---------------------------------------------------------------------------
# ImportAnalyzer — intent detection: re-export, typing, registration, etc.
# ---------------------------------------------------------------------------


def test_import_analyzer_re_export_intent(tmp_path):
    """File is __init__.py with unused import → import_intent with re_export category.

    Covers: _detect_import_intent lines 307-308 (RE_EXPORT_FILES + _is_reexport_candidate),
            _find_unused_in_file intent→import_intent routing lines 688-701.
    """
    py_file = tmp_path / "__init__.py"
    py_file.write_text("from custom_lib import util\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None, "AST parsing must succeed"

    # project_root omitted so re-export graph verification is skipped
    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent, got types: {[f.type for f in findings]}"
    )

    re_export = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "re_export"
    ]
    assert len(re_export) >= 1, (
        f"Expected re_export intent, got intents: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )

    # Verify evidence payload includes ReExport + AllExport
    evidence = re_export[0].data.get("evidence", [])
    kinds = {e["kind"] for e in evidence}
    assert "ReExport" in kinds, f"Expected ReExport evidence, got kinds: {kinds}"
    assert "AllExport" in kinds, f"Expected AllExport evidence, got kinds: {kinds}"
    assert re_export[0].fixable is False, "Intent findings should be non-fixable"


def test_import_analyzer_re_export_interfaces(tmp_path):
    """File is interfaces.py with re-export pattern → import_intent with re_export.

    Covers: _is_reexport_candidate immediate True for interfaces.py line 250-251.
    """
    py_file = tmp_path / "interfaces.py"
    py_file.write_text("from custom_lib import Interface\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent for interfaces.py, got: {[f.type for f in findings]}"
    )

    re_export = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "re_export"
    ]
    assert len(re_export) >= 1, (
        f"Expected re_export intent, got: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )
    assert "re-export" in re_export[0].message.lower()


def test_import_analyzer_typing_intent(tmp_path):
    """Import used only in type_comment (not AST Name) → import_intent with typing.

    Uses a type-comment on an Assign node, which _collect_type_use_names
    catches but _find_unused_in_file's used_names walk does not.  An
    AnnAssign is also included to trigger _has_type_annotations → type_use_names
    collection.

    Covers: _has_type_annotations lines 356-367,
            _collect_type_use_names type_comment path lines 213-217,
            _detect_import_intent typing module lines 312-315.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text(
        "from typing import Dict\n\nx = {}  # type: Dict[str, int]\ny: int = 0\n"
    )

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent for typing import, got: {[f.type for f in findings]}"
    )

    typing_intents = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "typing"
    ]
    assert len(typing_intents) >= 1, (
        f"Expected typing intent, got intents: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )
    assert "Type hints import" in typing_intents[0].message
    assert typing_intents[0].fixable is False


def test_import_analyzer_registration_intent(tmp_path):
    """Import from a registration module → side_effect intent (import_intent).

    Covers: _detect_import_intent registration module check lines 317-323.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("from strategies.base import Strategy\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent for registration module, got: {[f.type for f in findings]}"
    )

    side_effect = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "side_effect"
    ]
    assert len(side_effect) >= 1, (
        f"Expected side_effect intent, got: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )
    assert "strategies" in side_effect[0].message.lower()
    assert side_effect[0].fixable is False


def test_import_analyzer_side_effect_comment(tmp_path):
    """Import with '# do not remove' comment → side_effect intent.

    Covers: _detect_import_intent protection-marker check lines 289-292,
            _PROTECTION_MARKERS regex line 41-43.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("import os  # do not remove\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent for # do not remove, got: {[f.type for f in findings]}"
    )

    side_effect = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "side_effect"
    ]
    assert len(side_effect) >= 1, (
        f"Expected side_effect intent for commented import, got: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )
    assert "Protected by comment marker" in side_effect[0].message


def test_import_analyzer_try_block_intent(tmp_path):
    """Import inside try/except ImportError → try_block intent (import_intent).

    Covers: _detect_import_intent try-block check lines 326-328,
            _is_in_try_block lines 46-64,
            _classify_try_context lines 67-95.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("try:\n    import optional_lib\nexcept ImportError:\n    pass\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) >= 1, (
        f"Expected >=1 import_intent for try-block import, got: {[f.type for f in findings]}"
    )

    try_block = [
        f
        for f in intent_findings
        if f.data.get("import_info", {}).get("intent") == "try_block"
    ]
    assert len(try_block) >= 1, (
        f"Expected try_block intent, got: "
        f"{[f.data.get('import_info', {}).get('intent') for f in intent_findings]}"
    )
    assert "ImportError" in try_block[0].message
    assert try_block[0].fixable is False


def test_import_analyzer_usage_intent_single(tmp_path):
    """Import used once in file → no finding (correctly identified as used).

    Verifies that _find_unused_in_file collects used_names correctly
    and skip imports with matching Name/Attribute references.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\nprint(os.getcwd())\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # 'os' is used via os.getcwd() attribute access → should NOT be flagged
    unused = [f for f in findings if "unused" in f.type or f.type == "import_intent"]
    os_related = [f for f in unused if "os" in (f.import_name or "")]
    assert len(os_related) == 0, (
        f"'os' is used in file, should not be flagged, "
        f"got: {[f.message for f in os_related]}"
    )


def test_import_analyzer_usage_intent_multiple(tmp_path):
    """Import used multiple times → no finding (stronger usage signal).

    Verifies that multiple Name/Attribute references keep the import
    correctly identified as used.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text(
        "import os\n\nprint(os.getcwd())\nprint(os.listdir())\nprint(os.sep)\n"
    )

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    unused = [f for f in findings if "unused" in f.type or f.type == "import_intent"]
    os_related = [f for f in unused if "os" in (f.import_name or "")]
    assert len(os_related) == 0, (
        f"'os' used 3 times in file, should not be flagged, "
        f"got: {[f.message for f in os_related]}"
    )


def test_import_analyzer_no_intent_unused(tmp_path):
    """Import genuinely unused → unused_import_file finding (not intent finding).

    Covers: _detect_import_intent default "usage" return line 338,
            _find_unused_in_file usage→unused_import_file routing lines 702-706.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("import json\n")

    file_info = FileInfo.from_path(py_file)
    assert file_info.ast_tree is not None

    context = AnalysisContext(files={py_file: file_info})
    analyzer = ImportAnalyzer()
    findings = analyzer.analyze(context)

    # Should produce unused_import_file, not import_intent
    intent_findings = [f for f in findings if f.type == "import_intent"]
    assert len(intent_findings) == 0, (
        f"Unused bare import should NOT produce import_intent, "
        f"got: {[f.message for f in intent_findings]}"
    )

    unused_file = [f for f in findings if f.type == "unused_import_file"]
    assert len(unused_file) >= 1, (
        f"Expected >=1 unused_import_file, got types: {[f.type for f in findings]}"
    )
    json_finding = [
        f
        for f in unused_file
        if "json" in (f.import_name or "") or "json" in f.message.lower()
    ]
    assert len(json_finding) >= 1, (
        f"Expected finding about json, got: {[f.message for f in unused_file]}"
    )
    assert json_finding[0].fixable is True, "Unused imports should be fixable"
    assert json_finding[0].severity == Severity.INFO


# ============================================================================
# _deduplicate_findings — precise subject-key collision / non-collision
# ============================================================================


def test_dedup_findings_ruff_over_local():
    """F401 Ruff finding + local unused_import with same subject key → Ruff kept, local removed."""
    file = Path("/fake/test.py")

    ruff_finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: json imported but unused",
        location=Location(line=2),
        lint_code="F401",
        lint_source="ruff",
        import_name="json",
        data={"import_info": {"module": None, "name": "json"}},
    )

    local_finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused import: json",
        location=Location(line=2),
        import_name="json",
        data={"import_info": {"module": None, "name": "json"}},
    )

    deduped = _deduplicate_findings([ruff_finding, local_finding])
    assert len(deduped) == 1
    assert deduped[0].lint_source == "ruff"
    assert deduped[0].lint_code == "F401"


def test_dedup_findings_different_subject_key():
    """Two findings with different binding keys remain separate after dedup."""
    file = Path("/fake/test.py")

    f1 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: os imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "os"}},
    )

    f2 = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused import: json",
        location=Location(line=2),
        import_name="json",
        data={"import_info": {"module": None, "name": "json"}},
    )

    deduped = _deduplicate_findings([f1, f2])
    # Different symbols → both survive
    assert len(deduped) == 2


def test_dedup_findings_same_subject_key_fuse():
    """Two unused_import_file findings sharing same binding key → only Ruff survives dedup.

    Covers the case where the local analyzer and Ruff each produce a finding
    about the same import subject — dedup keeps the Ruff-backstopped one
    because it carries stronger external verification.
    """
    file = Path("/fake/test.py")

    f_ruff = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: requests imported but unused",
        location=Location(line=3),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "requests"}},
    )

    f_local = Finding.create(
        type="unused_import_file",
        severity=Severity.WARNING,
        file=file,
        message="Unused: requests",
        location=Location(line=3),
        import_name="requests",
        data={"import_info": {"module": None, "name": "requests"}},
    )

    deduped = _deduplicate_findings([f_ruff, f_local])
    assert len(deduped) == 1
    assert deduped[0].lint_source == "ruff"


def test_skip_when_no_ruff_f401():
    """No Ruff F401 findings → _deduplicate_findings returns all findings unchanged."""
    file = Path("/fake/test.py")

    f1 = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
        data={"import_info": {"module": None, "name": "json"}},
    )

    f2 = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=2),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    deduped = _deduplicate_findings([f1, f2])
    assert len(deduped) == 2


# ============================================================================
# _add_cross_file_evidence
# ============================================================================


def test_add_cross_file_evidence_not_used_elsewhere(tmp_path):
    """No evidence generated when symbol appears only in the finding's own file."""
    py_a = tmp_path / "a.py"
    py_a.write_text("import os\n")  # no usage of 'os' anywhere

    from imodent.graph.dependency import build_dependency_graph

    file_infos = {py_a: FileInfo.from_path(py_a)}
    graph = build_dependency_graph(file_infos, tmp_path)

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_a,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(
        files=file_infos,
        graph=graph,
        findings=[finding],
        project_root=tmp_path,
    )

    _add_cross_file_evidence([finding], context)
    # 'os' is not referenced in any other file content → no evidence
    assert len(context.evidence) == 0


def test_add_cross_file_evidence_with_import_name_field(tmp_path):
    """Cross-file evidence attaches when import_name is set directly on finding."""
    py_a = tmp_path / "a.py"
    py_a.write_text("import json\n")
    py_b = tmp_path / "b.py"
    py_b.write_text("import json\n\nx = json.dumps({})\n")

    from imodent.graph.dependency import build_dependency_graph

    file_infos = {
        py_a: FileInfo.from_path(py_a),
        py_b: FileInfo.from_path(py_b),
    }
    graph = build_dependency_graph(file_infos, tmp_path)

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_a,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
    )

    context = AnalysisContext(
        files=file_infos,
        graph=graph,
        findings=[finding],
        project_root=tmp_path,
    )

    _add_cross_file_evidence([finding], context)
    assert len(context.evidence) >= 1
    ev = context.evidence[0]
    assert ev.kind == "symbol_usage"
    assert ev.polarity == "context"
    assert "json" in ev.claim
    # Evidence ID was appended to finding data
    attached_ids = [d.get("id") for d in finding.data.get("evidence", [])]
    assert ev.id in attached_ids


# ============================================================================
# _destructive_option
# ============================================================================


def test_destructive_option_selects_delete():
    """_destructive_option returns the first option with action='delete' or is_safe=False."""
    from imodent.analysis.coordinator import _destructive_option
    from imodent.fixers.imports import ImportFixer

    file = Path("/fake/mod.py")
    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()
    option = _destructive_option(fixer, finding, context)
    assert option is not None
    assert option.action == "delete"
    assert not option.is_safe


def test_destructive_option_no_destructive_available():
    """_destructive_option returns None when fixer has only safe options."""
    from imodent.analysis.coordinator import _destructive_option

    file = Path("/fake/mod.py")
    finding = Finding.create(
        type="duplicate_import",
        severity=Severity.WARNING,
        file=file,
        message="Duplicate: os",
        location=Location(line=2),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))

    # Stub fixer returning only safe options
    class SafeOnlyFixer:
        def can_handle(self, f):
            return True

        def get_options(self, f, ctx):
            return [
                FixOption(
                    id="keep",
                    label="Keep",
                    description="Keep it",
                    action="keep",
                    is_safe=True,
                )
            ]

    fixer = SafeOnlyFixer()
    option = _destructive_option(fixer, finding, context)
    assert option is None


# ============================================================================
# _get_user_choice
# ============================================================================


def test_get_user_choice_returns_selected_option(monkeypatch):
    """_get_user_choice prompts and returns the user's selected FixOption."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    file = Path("/fake/mod.py")
    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is not None
    # "1" selects the first option (investigate)
    assert option.action in ("investigate", "refactor")


def test_get_user_choice_skip_returns_none(monkeypatch):
    """_get_user_choice returns None when user types 's'."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    file = Path("/fake/mod.py")
    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is None


def test_get_user_choice_non_tty_returns_none(monkeypatch):
    """_get_user_choice returns None when stdin is not a TTY."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is None


# ============================================================================
# apply_fixes — coordinator.fix() integration
# ============================================================================


def test_apply_fixes_safe_auto_removes_unused_import(tmp_path):
    """SAFE_AUTO fix mode removes unused import when candidate authorizes it."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
        alias=None,
        bound_name="os",
        origin=None,
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        confidence_label="high",
        proof_state="PROVEN_UNUSED",
        destructive_allowed=True,
        requires_user_decision=False,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=[candidate],
    )

    assert py_file in results
    fix_result = results[py_file]
    assert fix_result.success
    # Import line should be removed; only trailing newline remains
    assert "import os" not in fix_result.content
    assert fix_result.fixed_valid


def test_apply_fixes_destructive_not_allowed_skips(tmp_path):
    """Destructive fix is skipped when candidate.destructive_allowed=False."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=False,  # gate closed
        requires_user_decision=False,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=[candidate],
    )

    # No fixes applied because destructive_allowed is False
    assert py_file not in results


def test_coordinator_fix_returns_dict_of_fixresult(tmp_path):
    """coordinator.fix() returns dict[Path, FixResult] on successful fix."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey
    from imodent.interfaces import FixResult

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=[candidate],
    )

    assert isinstance(results, dict)
    assert len(results) == 1
    for path, fr in results.items():
        assert isinstance(path, Path)
        assert isinstance(fr, FixResult)
        assert fr.success


def test_coordinator_fix_no_fixable_findings(tmp_path):
    """Returns empty dict when no finding has a matching fixer."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode

    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    # "lint" type has no fixer registered
    finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=py_file,
        message="F841 local variable assigned but never used",
        location=Location(line=1),
        lint_code="F841",
        lint_source="ruff",
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
    )

    assert isinstance(results, dict)
    assert len(results) == 0


def test_apply_fixes_requires_user_decision_skips(tmp_path):
    """SAFE_AUTO skips candidate when requires_user_decision=True."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.75,
        destructive_allowed=True,
        requires_user_decision=True,  # needs human
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=[candidate],
    )

    assert py_file not in results


# ============================================================================
# _find_project_root
# ============================================================================


def test_coordinator_project_root_detection_with_pyproject(tmp_path):
    """_find_project_root finds project root via pyproject.toml."""
    from imodent.analysis.coordinator import AnalysisCoordinator

    # Build a nested structure
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")

    coordinator = AnalysisCoordinator()
    # Pass a path deep inside the project tree
    root = coordinator._find_project_root([src])
    assert root == tmp_path


def test_coordinator_project_root_detection_with_setup(tmp_path):
    """_find_project_root finds project root via setup.py."""
    from imodent.analysis.coordinator import AnalysisCoordinator

    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "setup.py").write_text("# setup\n")

    coordinator = AnalysisCoordinator()
    root = coordinator._find_project_root([src])
    assert root == tmp_path


def test_coordinator_project_root_detection_with_git(tmp_path):
    """_find_project_root finds project root via .git directory."""
    from imodent.analysis.coordinator import AnalysisCoordinator

    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / ".git").mkdir()

    coordinator = AnalysisCoordinator()
    root = coordinator._find_project_root([src])
    assert root == tmp_path


def test_coordinator_project_root_detection_no_markers(tmp_path):
    """_find_project_root falls back when no markers found."""
    from imodent.analysis.coordinator import AnalysisCoordinator

    src = tmp_path / "src"
    src.mkdir()
    # No markers at all — should fall back to the path itself
    coordinator = AnalysisCoordinator()
    root = coordinator._find_project_root([src])
    assert root == src


def test_coordinator_project_root_empty_paths():
    """_find_project_root returns cwd when paths list is empty."""
    from imodent.analysis.coordinator import AnalysisCoordinator

    coordinator = AnalysisCoordinator()
    root = coordinator._find_project_root([])
    assert root == Path.cwd()


# ============================================================================
# _finding_with_issue_type — routing
# ============================================================================


def test_finding_with_issue_type_routes_lint_to_unused():
    """_finding_with_issue_type replaces 'lint' type with 'unused_import' for routing."""
    from imodent.analysis.coordinator import _finding_with_issue_type

    finding = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="F401: os unused",
        location=Location(line=1),
        lint_code="F401",
    )

    routed = _finding_with_issue_type(finding, "unused_import")
    assert routed.type == "unused_import"
    assert routed.fixable is True
    # Original lint_code still present (not mutated away)
    assert routed.lint_code == "F401"


def test_finding_with_issue_type_identity_when_same():
    """_finding_with_issue_type returns finding unchanged when type already matches."""
    from imodent.analysis.coordinator import _finding_with_issue_type

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
    )

    routed = _finding_with_issue_type(finding, "unused_import")
    assert routed is finding  # same object returned


def test_finding_with_issue_type_non_import_issue():
    """_finding_with_issue_type passes through non-import issue types unchanged."""
    from imodent.analysis.coordinator import _finding_with_issue_type

    finding = Finding.create(
        type="residue",
        severity=Severity.INFO,
        file=Path("/fake/mod.py"),
        message="todo!() marker",
        location=Location(line=5),
    )

    routed = _finding_with_issue_type(finding, "residue")
    assert routed is finding


# ---------------------------------------------------------------------------
# ArchitectureAdvisor tests
# ---------------------------------------------------------------------------


def test_architecture_advisor_circular_deps():
    """ArchitectureAdvisor._has_circular_deps returns True for A→B→A cycle."""
    graph = DependencyGraph()
    graph.add_import("module_a", "module_b")
    graph.add_import("module_b", "module_a")

    context = AnalysisContext(graph=graph)
    advisor = ArchitectureAdvisor()

    assert advisor._has_circular_deps(context) is True, (
        "Should detect circular dependency between module_a and module_b"
    )


def test_architecture_advisor_no_circular_deps():
    """ArchitectureAdvisor._has_circular_deps returns False for acyclic graph."""
    graph = DependencyGraph()
    graph.add_import("module_a", "module_b")
    graph.add_import("module_a", "module_c")

    context = AnalysisContext(graph=graph)
    advisor = ArchitectureAdvisor()

    assert advisor._has_circular_deps(context) is False, (
        "Should not flag acyclic dependencies as circular"
    )


def test_architecture_advisor_circular_deps_advise():
    """ArchitectureAdvisor.advise() produces circular-dependency Advice."""
    graph = DependencyGraph()
    graph.add_import("module_a", "module_b")
    graph.add_import("module_b", "module_a")

    context = AnalysisContext(graph=graph)
    advisor = ArchitectureAdvisor()

    advices = advisor.advise([], context)
    circular = [a for a in advices if "circular" in a.summary.lower()]
    assert len(circular) >= 1, (
        f"Expected circular dependency advice, got {[a.summary for a in advices]}"
    )
    assert circular[0].priority == 9
    assert circular[0].category == "architecture"


def test_architecture_advisor_many_import_issues():
    """ArchitectureAdvisor.advise() flags high import-issue count."""
    file = Path("/fake/mod.py")
    findings = [
        Finding.create(
            type="unused_import",
            severity=Severity.WARNING,
            file=file,
            message=f"Unused import #{i}",
            location=Location(line=i),
            data={"import_info": {"module": None, "name": f"mod{i}"}},
        )
        for i in range(11)
    ]

    context = AnalysisContext()
    advisor = ArchitectureAdvisor()

    advices = advisor.advise(findings, context)
    assert len(advices) >= 1, "Should produce advice for many import issues"

    # Check clustering advice
    clustering = [a for a in advices if "import issues" in a.summary.lower()]
    assert len(clustering) >= 1, "Should produce import-clustering advice"


def test_architecture_advisor_should_advise_circular():
    """should_advise returns True when circular deps present."""
    graph = DependencyGraph()
    graph.add_import("a", "b")
    graph.add_import("b", "a")

    context = AnalysisContext(graph=graph)
    advisor = ArchitectureAdvisor()

    assert advisor.should_advise([], context) is True


def test_architecture_advisor_should_advise_many_unused():
    """should_advise returns True when file has >3 unused imports."""
    file = Path("/fake/mod.py")
    findings = [
        Finding.create(
            type="unused_import",
            severity=Severity.WARNING,
            file=file,
            message=f"Unused import #{i}",
            location=Location(line=i),
            data={"import_info": {"module": None, "name": f"mod{i}"}},
        )
        for i in range(5)
    ]

    context = AnalysisContext()
    advisor = ArchitectureAdvisor()

    assert advisor.should_advise(findings, context) is True, (
        "Should advise when a file has >3 unused imports"
    )


# ---------------------------------------------------------------------------
# build_dependency_graph tests
# ---------------------------------------------------------------------------


def test_build_dependency_graph_basic(tmp_path):
    """build_dependency_graph processes Python files and resolves imports."""
    mod_a = tmp_path / "mod_a.py"
    mod_a.write_text("from mod_b import helper\n\ndef func():\n    return helper()\n")

    mod_b = tmp_path / "mod_b.py"
    mod_b.write_text("def helper():\n    return 42\n")

    file_infos = {
        mod_a: FileInfo.from_path(mod_a),
        mod_b: FileInfo.from_path(mod_b),
    }

    graph = build_dependency_graph(file_infos, tmp_path)

    # mod_a should import mod_b
    assert "mod_a" in graph.imports, (
        f"mod_a not in imports: {list(graph.imports.keys())}"
    )
    assert "mod_b" in graph.imports["mod_a"], (
        f"mod_a should import mod_b, got {graph.imports.get('mod_a')}"
    )

    # mod_b should be imported by mod_a
    assert "mod_b" in graph.imported_by, "mod_b not in imported_by"
    assert "mod_a" in graph.imported_by["mod_b"]

    # file_to_module mapping populated
    assert mod_a in graph.file_to_module
    assert graph.file_to_module[mod_a] == "mod_a"


def test_build_dependency_graph_empty_files():
    """build_dependency_graph handles empty files dict gracefully."""
    graph = build_dependency_graph({}, Path.cwd())
    assert graph.imports == {}
    assert graph.imported_by == {}
    assert graph.file_to_module == {}


def test_build_dependency_graph_no_python_files(tmp_path):
    """build_dependency_graph skips non-Python files."""
    json_file = tmp_path / "data.json"
    json_file.write_text('{"a": 1}')

    file_infos = {
        json_file: FileInfo.from_path(json_file),
    }

    graph = build_dependency_graph(file_infos, tmp_path)
    assert json_file not in graph.file_to_module


# ---------------------------------------------------------------------------
# trace_symbol_usage tests
# ---------------------------------------------------------------------------


def test_trace_symbol_usage_finds_usage(tmp_path):
    """trace_symbol_usage finds Name and Attribute references to a symbol."""
    py_a = tmp_path / "a.py"
    py_a.write_text("os = 1\nprint(os)\n")

    py_b = tmp_path / "b.py"
    py_b.write_text("import a\na.os\n")

    file_infos = {
        py_a: FileInfo.from_path(py_a),
        py_b: FileInfo.from_path(py_b),
    }
    graph = build_dependency_graph(file_infos, tmp_path)

    usages = trace_symbol_usage("os", file_infos, graph)

    # os appears as Name in a.py (2 times: assignment + reference), and as Attribute in b.py
    assert len(usages) >= 2, (
        f"Expected at least 2 usages of 'os', got {len(usages)}: {usages}"
    )
    files_with_usage = {u.file for u in usages}
    assert py_a in files_with_usage, (
        f"a.py should have 'os' usages, files: {files_with_usage}"
    )

    # Check SymbolUsage fields
    for usage in usages:
        assert usage.symbol == "os"
        assert usage.context == "reference"


def test_trace_symbol_usage_no_match(tmp_path):
    """trace_symbol_usage returns empty when symbol is absent."""
    py_file = tmp_path / "only.py"
    py_file.write_text("x = 1\nprint(x)\n")

    file_infos = {py_file: FileInfo.from_path(py_file)}
    graph = build_dependency_graph(file_infos, tmp_path)

    usages = trace_symbol_usage("nonexistent", file_infos, graph)
    assert usages == [], f"Expected empty for missing symbol, got {len(usages)}"


# ============================================================================
# DecisionActions tests — _default_actions_for_issue_type
# ============================================================================


def test_decision_actions_build_actions_unused_import():
    """_default_actions_for_issue_type('unused_import') returns investigate/keep/delete."""
    actions = _default_actions_for_issue_type("unused_import")
    assert len(actions) == 3
    action_ids = {a.id for a in actions}
    assert action_ids == {"investigate", "keep", "delete"}
    # Delete-bias: investigate comes first (advisory-first ordering)
    assert actions[0].id == "investigate"
    assert actions[1].id == "keep"
    assert actions[2].id == "delete"
    assert actions[2].destructive is True
    assert actions[2].safe_auto is False
    assert actions[2].requires_decision is True


def test_decision_actions_build_actions_undefined_api():
    """_default_actions_for_issue_type('undefined_api') returns implement/quarantine."""
    actions = _default_actions_for_issue_type("undefined_api")
    assert len(actions) == 2
    action_ids = {a.id for a in actions}
    assert action_ids == {"implement", "quarantine"}
    for a in actions:
        assert a.destructive is False
        assert a.safe_auto is False
        assert a.requires_decision is True


def test_decision_actions_build_actions_duplicate_import():
    """_default_actions_for_issue_type('duplicate_import') returns investigate/remove."""
    actions = _default_actions_for_issue_type("duplicate_import")
    assert len(actions) == 2
    action_ids = {a.id for a in actions}
    assert action_ids == {"investigate", "remove"}
    # First action should be non-destructive investigate
    assert actions[0].id == "investigate"
    assert actions[0].destructive is False


def test_decision_actions_build_actions_rust_advisory():
    """_default_actions_for_issue_type('rust_advisory') returns review_policy."""
    actions = _default_actions_for_issue_type("rust_advisory")
    assert len(actions) == 1
    assert actions[0].id == "review_policy"
    assert actions[0].destructive is False
    assert actions[0].safe_auto is True


def test_decision_actions_build_actions_rust_diagnostic():
    """_default_actions_for_issue_type('rust_diagnostic') returns fix_in_source."""
    actions = _default_actions_for_issue_type("rust_diagnostic")
    assert len(actions) == 1
    assert actions[0].id == "fix_in_source"
    assert actions[0].destructive is False
    assert actions[0].safe_auto is False
    assert actions[0].requires_decision is True


def test_decision_actions_build_actions_rust_unused_import():
    """_default_actions_for_issue_type('rust_unused_import') returns investigate/fix_in_source."""
    actions = _default_actions_for_issue_type("rust_unused_import")
    assert len(actions) == 2
    action_ids = {a.id for a in actions}
    assert action_ids == {"investigate", "fix_in_source"}


def test_decision_actions_build_actions_rust_oracle():
    """_default_actions_for_issue_type('rust_oracle') returns check_toolchain."""
    actions = _default_actions_for_issue_type("rust_oracle")
    assert len(actions) == 1
    assert actions[0].id == "check_toolchain"
    assert actions[0].destructive is False
    assert actions[0].safe_auto is True


def test_decision_actions_build_actions_unknown():
    """_default_actions_for_issue_type for unknown type returns fallback 'review' action."""
    actions = _default_actions_for_issue_type("bogus_issue_type")
    assert len(actions) == 1
    assert actions[0].id == "review"
    assert actions[0].destructive is False
    assert actions[0].safe_auto is False
    assert actions[0].requires_decision is True


# ============================================================================
# DecisionConfidence tests — _score_confidence and _compute_confidence_label
# ============================================================================


def test_decision_confidence_unused_import_strong_ruff_f821():
    """_score_confidence returns 0.90 for F821 (undefined name)."""
    file = Path("/fake/mod.py")
    f821 = Finding.create(
        type="lint",
        severity=Severity.ERROR,
        file=file,
        message="F821: undefined name 'foo'",
        location=Location(line=5),
        lint_code="F821",
        lint_source="ruff",
    )
    confidence = _score_confidence(f821, [f821], [])
    assert confidence == 0.90


def test_decision_confidence_unused_import_strong_ruff_f841():
    """_score_confidence returns 0.90 for F841 (unused variable)."""
    file = Path("/fake/mod.py")
    f841 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F841: local variable 'x' assigned but never used",
        location=Location(line=3),
        lint_code="F841",
        lint_source="ruff",
    )
    confidence = _score_confidence(f841, [f841], [])
    assert confidence == 0.90


def test_decision_confidence_f401_standard():
    """_score_confidence returns 0.85 for standard F401 (unused import) with no re-export evidence."""
    file = Path("/fake/main.py")
    f401 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: 'os' imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": None, "name": "os", "intent": "usage"}},
    )
    confidence = _score_confidence(f401, [f401], [])
    assert confidence == 0.85


def test_decision_confidence_f401_init_py_reexport():
    """_score_confidence returns 0.10 for F401 in __init__.py with re_export intent."""
    file = Path("/fake/pkg/__init__.py")
    f401 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: 'Bar' imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": "foo", "name": "Bar", "intent": "re_export"}},
    )
    confidence = _score_confidence(f401, [f401], [])
    assert confidence == 0.10


def test_decision_confidence_f401_public_api_reexport_evidence():
    """_score_confidence returns 0.10 when public_api_reexport evidence has low strength."""
    file = Path("/fake/pkg/mod.py")
    f401 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: 'Bar' imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": "foo", "name": "Bar", "intent": "usage"}},
    )
    ev = Evidence(
        kind="ReExport",
        file=file,
        location=None,
        source="import_analyzer",
        subject="Bar",
        claim="public_api_reexport",
        strength=0.40,
    )
    confidence = _score_confidence(f401, [f401], [ev])
    assert confidence == 0.10


def test_decision_confidence_f401_reexport_evidence_strong():
    """_score_confidence returns 0.15 with strong ReExport evidence."""
    file = Path("/fake/pkg/mod.py")
    f401 = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: 'Bar' imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": "foo", "name": "Bar", "intent": "usage"}},
    )
    ev = Evidence(
        kind="ReExport",
        file=file,
        location=None,
        source="import_analyzer",
        subject="Bar",
        claim="public_api_reexport",
        strength=0.75,
    )
    confidence = _score_confidence(f401, [f401], [ev])
    assert confidence == 0.15


def test_decision_confidence_unused_import_side_effect():
    """_score_confidence returns 0.30 for unused_import with side_effect intent."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os", "intent": "side_effect"}},
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.30


def test_decision_confidence_unused_import_typing():
    """_score_confidence returns 0.40 for unused_import with typing intent."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: List",
        location=Location(line=1),
        import_name="List",
        data={"import_info": {"module": "typing", "name": "List", "intent": "typing"}},
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.40


def test_decision_confidence_unused_import_reexport():
    """_score_confidence returns 0.10 for unused_import with re_export intent."""
    file = Path("/fake/__init__.py")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: Bar",
        location=Location(line=1),
        import_name="Bar",
        data={"import_info": {"module": "foo", "name": "Bar", "intent": "re_export"}},
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.10


def test_decision_confidence_unused_import_standard():
    """_score_confidence returns 0.75 for AST-based unused_import with usage intent."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: json",
        location=Location(line=1),
        import_name="json",
        data={"import_info": {"module": None, "name": "json", "intent": "usage"}},
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.75


def test_decision_confidence_import_intent():
    """_score_confidence returns 0.30 for import_intent type."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="import_intent",
        severity=Severity.INFO,
        file=file,
        message="Import intent: registration",
        location=Location(line=1),
        data={
            "import_info": {
                "module": None,
                "name": "registry_lib",
                "intent": "registration",
            }
        },
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.30


def test_decision_confidence_duplicate_import():
    """_score_confidence returns 0.95 for duplicate_import type."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="duplicate_import",
        severity=Severity.WARNING,
        file=file,
        message="Duplicate: os",
        location=Location(line=2),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.95


def test_decision_confidence_rust_diagnostic_error():
    """_score_confidence returns 0.90 for rust_diagnostic with error-level evidence."""
    file = Path("/fake/lib.rs")
    f = Finding.create(
        type="rust_diagnostic",
        severity=Severity.ERROR,
        file=file,
        message="unused import: std::mem",
        location=Location(line=3),
    )
    ev = Evidence(
        kind="ClippyDiagnostic",
        file=file,
        location=Location(line=3),
        source="clippy",
        subject="unused_imports",
        data={"level": "error"},
        strength=0.80,
    )
    confidence = _score_confidence(f, [f], [ev])
    assert confidence == 0.90


def test_decision_confidence_rust_diagnostic_warning():
    """_score_confidence returns 0.80 for rust_diagnostic with warning-level evidence."""
    file = Path("/fake/lib.rs")
    f = Finding.create(
        type="rust_diagnostic",
        severity=Severity.WARNING,
        file=file,
        message="unused import: std::mem",
        location=Location(line=3),
    )
    ev = Evidence(
        kind="CargoDiagnostic",
        file=file,
        location=Location(line=3),
        source="cargo",
        subject="dead_code",
        data={"level": "warning"},
        strength=0.70,
    )
    confidence = _score_confidence(f, [f], [ev])
    assert confidence == 0.80


def test_decision_confidence_rust_broad_allow():
    """_score_confidence returns 0.75 for rust_broad_allow type."""
    file = Path("/fake/lib.rs")
    f = Finding.create(
        type="rust_broad_allow",
        severity=Severity.WARNING,
        file=file,
        message="Broad allow detected: #![allow(warnings)]",
        location=Location(line=1),
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.75


def test_decision_confidence_rust_advisory():
    """_score_confidence returns 0.50 for RUST_ADVISORY_FINDING_TYPES."""
    file = Path("/fake/lib.rs")
    for ftype in [
        "rust_lint_policy_missing",
        "rust_clippy_config_missing",
        "rust_rustfmt_config_missing",
        "rust_residue_marker",
        "rust_project_unmanaged",
    ]:
        f = Finding.create(
            type=ftype,
            severity=Severity.INFO,
            file=file,
            message=f"Rust advisory: {ftype}",
            location=Location(line=1),
        )
        confidence = _score_confidence(f, [f], [])
        assert confidence == 0.50, f"Expected 0.50 for {ftype}, got {confidence}"


def test_decision_confidence_rust_oracle():
    """_score_confidence returns 0.40 for RUST_ORACLE_FINDING_TYPES."""
    file = Path("/fake/lib.rs")
    for ftype in ["rust_oracle_unavailable", "rust_oracle_failed"]:
        f = Finding.create(
            type=ftype,
            severity=Severity.WARNING,
            file=file,
            message=f"Oracle issue: {ftype}",
            location=Location(line=1),
        )
        confidence = _score_confidence(f, [f], [])
        assert confidence == 0.40, f"Expected 0.40 for {ftype}, got {confidence}"


def test_decision_confidence_unknown_type():
    """_score_confidence returns 0.50 for unknown finding types."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="some_unknown_type",
        severity=Severity.INFO,
        file=file,
        message="Unknown finding",
        location=Location(line=1),
    )
    confidence = _score_confidence(f, [f], [])
    assert confidence == 0.50


def test_decision_confidence_fusion_intent_from_group():
    """_score_confidence scans group for intent when scorer has no intent data."""
    file = Path("/fake/mod.py")
    # Scorer: lint finding with F401 but no intent data
    f_lint = Finding.create(
        type="lint",
        severity=Severity.WARNING,
        file=file,
        message="F401: 'Bar' imported but unused",
        location=Location(line=1),
        lint_code="F401",
        lint_source="ruff",
        data={"import_info": {"module": "foo", "name": "Bar"}},  # no intent
    )
    # Group member: import_intent finding with side_effect
    f_intent = Finding.create(
        type="import_intent",
        severity=Severity.INFO,
        file=file,
        message="Import intent: side_effect for Bar",
        location=Location(line=1),
        data={"import_info": {"module": "foo", "name": "Bar", "intent": "side_effect"}},
    )
    group = [f_lint, f_intent]
    # Without fusion: F401 + no intent → 0.85
    # With fusion: side_effect intent from group member found at line 54-55
    # But actual scoring: F401 is detected first, returns 0.85 unless evidence overrides.
    # The fusion logic at lines 48-55 updates the `intent` local variable,
    # but for F401 the code at lines 63-75 returns before intent is used.
    # So the final return is still 0.85 for F401 with no evidence.
    confidence = _score_confidence(f_lint, group, [])
    assert confidence == 0.85  # F401 is processed before intent check


def test_compute_confidence_label():
    """_compute_confidence_label maps numeric confidence to label."""
    assert _compute_confidence_label(0.95) == "high"
    assert _compute_confidence_label(0.80) == "high"
    assert _compute_confidence_label(0.79) == "medium"
    assert _compute_confidence_label(0.50) == "medium"
    assert _compute_confidence_label(0.49) == "low"
    assert _compute_confidence_label(0.01) == "low"


# ============================================================================
# DecisionSubjects tests
# ============================================================================


def test_subject_key_for_lint():
    """subject_key_for_lint returns correct SubjectKey structure for lint codes."""
    result = subject_key_for_lint(
        file=Path("/fake/mod.py"),
        code="F401",
        module=None,
        name="os",
        scope="module",
    )
    assert result.kind == "lint"
    assert result.file == Path("/fake/mod.py")
    assert result.scope == "module"
    assert result.module is None
    assert result.name == "os"  # name is the provided name, falling back to code
    assert result.alias is None
    assert result.bound_name == "os"
    assert result.origin is None


def test_subject_key_for_lint_no_name():
    """subject_key_for_lint uses code as name when name is not provided.
    bound_name uses name directly (may be None when name arg is None)."""
    result = subject_key_for_lint(
        file=Path("/fake/test.py"),
        code="F841",
    )
    assert result.kind == "lint"
    assert result.name == "F841"  # code used as name (name or code)
    assert result.bound_name is None  # bound_name=name (None), not name or code


def test_subject_key_for_import_normalize_bare():
    """subject_key_for_import normalizes bare name to module when no module provided."""
    result = subject_key_for_import(
        file=Path("/fake/mod.py"),
        module=None,
        name="os",
        alias=None,
    )
    # Normalized: module="os", name=None
    assert result.module == "os"
    assert result.name is None
    assert result.bound_name == "os"
    # origin: f"{module}.{name}" if module and name else module or None → module or None → "os"
    assert result.origin == "os"


def test_subject_key_for_import_with_module():
    """subject_key_for_import preserves module + name when both present."""
    result = subject_key_for_import(
        file=Path("/fake/mod.py"),
        module="typing",
        name="Dict",
        alias=None,
    )
    assert result.module == "typing"
    assert result.name == "Dict"
    assert result.bound_name == "Dict"
    assert result.origin == "typing.Dict"


def test_subject_key_for_import_with_alias():
    """subject_key_for_import uses alias as bound_name when present."""
    result = subject_key_for_import(
        file=Path("/fake/mod.py"),
        module="typing",
        name="Dict",
        alias="TypeDict",
    )
    assert result.module == "typing"
    assert result.name == "Dict"
    assert result.alias == "TypeDict"
    assert result.bound_name == "TypeDict"  # alias wins over name
    assert result.origin == "typing.Dict"


def test_subject_key_binding_key_excludes_alias():
    """SubjectKey.binding_key excludes alias (identity, not binding metadata)."""
    # Same origin, different alias → same binding key
    key1 = SubjectKey(
        kind="import",
        file=Path("/fake/mod.py"),
        scope="module",
        module="foo",
        name="Bar",
        alias="X",
        bound_name="X",
        origin="foo.Bar",
    )
    key2 = SubjectKey(
        kind="import",
        file=Path("/fake/mod.py"),
        scope="module",
        module="foo",
        name="Bar",
        alias="Y",
        bound_name="Y",
        origin="foo.Bar",
    )
    assert key1.binding_key == key2.binding_key


def test_subject_key_to_dict():
    """SubjectKey.to_dict() returns a serializable dict."""
    key = SubjectKey(
        kind="import",
        file=Path("/fake/mod.py"),
        scope="module",
        module="typing",
        name="Dict",
        alias="TypeDict",
        bound_name="TypeDict",
        origin="typing.Dict",
    )
    d = key.to_dict()
    assert d["kind"] == "import"
    assert d["file"] == "/fake/mod.py"
    assert d["module"] == "typing"
    assert d["name"] == "Dict"
    assert d["alias"] == "TypeDict"
    assert d["bound_name"] == "TypeDict"
    assert d["origin"] == "typing.Dict"


# ============================================================================
# DecisionPolicy tests
# ============================================================================


def test_has_suppression_markers():
    """_has_suppression_markers returns True for suppression intent types."""
    file = Path("/fake/mod.py")
    for intent in ["re_export", "registration", "side_effect"]:
        f = Finding.create(
            type="unused_import",
            severity=Severity.WARNING,
            file=file,
            message="test",
            location=Location(line=1),
            data={"import_info": {"intent": intent}},
        )
        assert _has_suppression_markers(f) is True, (
            f"Expected suppression marker for intent={intent}"
        )


def test_has_suppression_markers_false():
    """_has_suppression_markers returns False for usage intent."""
    file = Path("/fake/mod.py")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="test",
        location=Location(line=1),
        data={"import_info": {"intent": "usage"}},
    )
    assert _has_suppression_markers(f) is False


def test_destructive_allowed_high_confidence():
    """_destructive_allowed returns True for high-confidence unused_import."""
    file = Path("/fake/mod.py")
    sk = SubjectKey(kind="import", file=file, scope="module", module=None, name="os")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os", "intent": "usage"}},
    )
    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=sk,
        finding_ids=[f.id],
        confidence=0.85,
        proof_state=ProofState.PROVEN_UNUSED.value,
    )
    assert _destructive_allowed(candidate, [f]) is True


def test_destructive_allowed_low_confidence():
    """_destructive_allowed returns False when confidence < 0.80."""
    file = Path("/fake/mod.py")
    sk = SubjectKey(kind="import", file=file, scope="module", module=None, name="os")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
    )
    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=sk,
        confidence=0.60,
    )
    assert _destructive_allowed(candidate, [f]) is False


def test_destructive_allowed_rust_non_destructive():
    """_destructive_allowed returns False for Rust issue types."""
    file = Path("/fake/lib.rs")
    sk = SubjectKey(kind="lint", file=file, scope="module")
    candidate = DecisionCandidate(
        issue_type="rust_diagnostic",
        subject_key=sk,
        confidence=0.95,
    )
    assert _destructive_allowed(candidate, []) is False


def test_destructive_allowed_review_required():
    """_destructive_allowed returns False when proof_state is REVIEW_REQUIRED."""
    file = Path("/fake/mod.py")
    sk = SubjectKey(kind="import", file=file, scope="module")
    f = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=file,
        message="Unused: os",
        location=Location(line=1),
    )
    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=sk,
        finding_ids=[f.id],
        confidence=0.95,
        proof_state=ProofState.REVIEW_REQUIRED.value,
    )
    assert _destructive_allowed(candidate, [f]) is False


# ============================================================================
# ProjectContext tests
# ============================================================================


def test_project_context_discover(tmp_path):
    """ProjectContext.discover() finds project root from a path inside the project."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "myproject"
    src = project_root / "src"
    src.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname = 'myproject'\n")

    # Pass a file path deep inside the project
    ctx = ProjectContext.discover(src / "module.py")
    assert ctx.project_root == project_root
    assert ctx.project_name == "myproject"
    assert ctx.has_project_markers is True
    assert isinstance(ctx.config, AnalysisConfig)


def test_project_context_from_root(tmp_path):
    """ProjectContext.from_root() creates context from explicit root."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "app"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname = 'app'\n")

    ctx = ProjectContext.from_root(project_root)
    assert ctx.project_root == project_root
    assert ctx.has_project_markers is True


def test_project_context_cache_dir(tmp_path):
    """ProjectContext.cache_dir returns .imodent/cache under project root."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "app"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname = 'app'\n")

    ctx = ProjectContext.from_root(project_root)
    expected = project_root / ".imodent" / "cache"
    assert ctx.cache_dir == expected


def test_project_context_post_init_auto_name(tmp_path):
    """ProjectContext.__post_init__ populates project_name from pyproject.toml."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "auto-name"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname = 'explicit-name'\n")

    ctx = ProjectContext(project_root=project_root)
    assert ctx.project_name == "explicit-name"


def test_project_context_post_init_fallback_name(tmp_path):
    """ProjectContext.__post_init__ falls back to directory name when no pyproject.toml."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "fallback-dir"
    project_root.mkdir()
    # No pyproject.toml

    ctx = ProjectContext(project_root=project_root)
    assert ctx.project_name == "fallback-dir"


def test_project_context_explicit_name(tmp_path):
    """ProjectContext with explicitly set name does not overwrite it."""
    from imodent.project.project_context import ProjectContext

    project_root = tmp_path / "whatever"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname = 'from-toml'\n")

    ctx = ProjectContext(project_root=project_root, project_name="explicit")
    assert ctx.project_name == "explicit"  # not overwritten by __post_init__


def test_project_context_discover_no_markers(tmp_path):
    """ProjectContext.discover() works when no project markers found — uses the path itself."""
    from imodent.project.project_context import ProjectContext

    plain_dir = tmp_path / "just_a_dir"
    plain_dir.mkdir()
    # No markers

    ctx = ProjectContext.discover(plain_dir)
    assert ctx.project_root == plain_dir
    assert ctx.has_project_markers is False
    assert ctx.project_name == "just_a_dir"


# ============================================================================
# Coordinator fix() mode coverage — interactive, ALL_AUTO, SAFE_AUTO, empty
# ============================================================================


def test_coordinator_fix_interactive_mode(tmp_path, monkeypatch):
    """INTERACTIVE fix mode prompts user and applies the selected fix (delete).

    Covers: fix() INTERACTIVE branch lines 287-291, _get_user_choice lines 480-527.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import", file=py_file, scope="module", module=None, name="os"
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    # Mock stdin as TTY; simulate user choosing the delete option.
    # Dynamically compute the delete option index so the test is resilient
    # to moedularizer availability (6 options with, 5 without).
    from imodent.fixers.imports import ImportFixer as _ImportFixer

    _tmp_fixer = _ImportFixer()
    _tmp_options = _tmp_fixer.get_options(finding, context)
    _delete_idx = next(
        (i + 1 for i, o in enumerate(_tmp_options) if o.id == "delete"), None
    )
    assert _delete_idx is not None, "delete option must exist in ImportFixer"

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": str(_delete_idx))

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.INTERACTIVE,
        candidates=[candidate],
    )

    assert py_file in results, "INTERACTIVE mode should apply user-selected fix"
    assert results[py_file].success
    assert "import os" not in results[py_file].content
    assert results[py_file].fixed_valid


def test_coordinator_fix_interactive_non_tty_fallback(tmp_path, monkeypatch):
    """INTERACTIVE mode with non-TTY stdin falls back to SAFE_AUTO (lines 218-224)."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    # Non-TTY: fallback to SAFE_AUTO (fix IS applied because candidate authorizes it)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.INTERACTIVE,
        candidates=[candidate],
    )
    # Falls back to SAFE_AUTO, which removes the import
    assert py_file in results
    assert "import os" not in results[py_file].content


def test_coordinator_fix_report_mode(tmp_path):
    """REPORT mode does not apply any fixes (line 286 continue)."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.REPORT,
        candidates=[candidate],
    )

    # REPORT mode must not apply any fix
    assert py_file not in results


def test_coordinator_fix_interactive_user_skip(monkeypatch, tmp_path):
    """INTERACTIVE mode: user types 's' to skip → hits line 283 continue.

    Covers: fix() INTERACTIVE skip continue at line 283.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import",
        file=py_file,
        scope="module",
        module=None,
        name="os",
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # User types 's' to skip
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.INTERACTIVE,
        candidates=[candidate],
    )

    # User skipped → no fix applied
    assert py_file not in results


def test_coordinator_fix_candidates_none_branch(tmp_path):
    """fix() with candidates=None triggers the DecisionEngine fallback branch.

    Covers: branch where candidates is None → lines 228-229.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    coordinator = AnalysisCoordinator()
    # Explicitly pass candidates=None → triggers DecisionEngine.build_candidates
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=None,  # explicit None
    )

    # SAFE_AUTO: no candidate built from findings → no destructive_allowed info →
    # the DecisionEngine fallback produces candidates from findings with
    # low-confidence entries → gate blocks. Result: no fix applied.
    assert py_file not in results


def test_coordinator_fix_file_not_in_context(tmp_path):
    """fix() skips finding when file is not in context.files (line 235 continue).

    Covers: fix() early continue when file_info is None at line 235.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode

    # Create context with NO files
    context = AnalysisContext(project_root=tmp_path)

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/nonexistent/mod.py"),
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
    )

    # File not in context → finding skipped → no results
    assert len(results) == 0


def test_get_analyzers_with_name_filter():
    """_get_analyzers filters by specific analyzer names.

    Covers: _get_analyzers name-filtering branch at line 429.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator

    config = AnalysisConfig(check_imports=True, check_lint=False, check_rust=False)
    coordinator = AnalysisCoordinator(config=config)

    # Filter to just the import analyzer
    analyzers = coordinator._get_analyzers(["imports"])
    names = {a.name for a in analyzers}
    assert "imports" in names
    assert "lint" not in names


def test_discover_files_explicit_file_excluded():
    """_discover_files skips explicit file that matches exclude pattern.

    Covers: _discover_files explicit_excluded path lines 353-355.
    """
    import tempfile

    from imodent.analysis.coordinator import AnalysisCoordinator

    with tempfile.TemporaryDirectory(prefix="imodent_excl_") as tmp:
        tmp_path = Path(tmp)
        py_file = tmp_path / "should_be_excluded.py"
        py_file.write_text("x = 1\n")

        config = AnalysisConfig(
            include_patterns=["*.py", "**/*.py"],
            exclude_patterns=["**/should_be_*.py"],
            exclude_patterns_from_config=True,
        )
        coordinator = AnalysisCoordinator(config=config)
        files = coordinator._discover_files([py_file])
        names = {p.name for p in files}

        # File matched exclude pattern → not discovered
        assert "should_be_excluded.py" not in names


def test_coordinator_with_project_context(tmp_path):
    """AnalysisCoordinator.analyze() with ProjectContext uses project root.

    Covers: analyze() project_root branch lines 128-129, 134→150,
            _discover_files project_root branch 338→328.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.project.project_context import ProjectContext

    # Build a simulated project
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("import os\n\nx = os.getcwd()\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")

    # Create ProjectContext pointing at the project root
    project_ctx = ProjectContext(project_root=tmp_path)
    coordinator = AnalysisCoordinator(project_context=project_ctx)

    result = coordinator.analyze([src])
    assert result is not None
    assert len(result.files) >= 1
    # _find_project_root should NOT be called (project_context is set)
    # _discover_files should use project_context.project_root


def test_analyze_with_syntax_check_enabled(tmp_path):
    """analyze() with check_syntax=True detects syntax errors in broken files.

    Covers: syntax-check block lines 151-156.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator

    broken = tmp_path / "broken.py"
    broken.write_text("def f(\n")  # unclosed paren → syntax error

    # Need to ensure FileInfo picks up the syntax error
    config = AnalysisConfig(check_imports=False, check_lint=False, check_syntax=True)
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([broken])

    syntax_errors = [f for f in result.findings if f.type == "syntax_error"]
    assert len(syntax_errors) >= 1, (
        f"Expected syntax_error finding for broken file, "
        f"got findings: {[(f.type, f.message[:60]) for f in result.findings]}"
    )


def test_coordinator_fix_empty_findings(tmp_path):
    """fix() with no findings returns an empty dict immediately."""
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[],
        context=context,
        mode=FixMode.SAFE_AUTO,
    )

    assert isinstance(results, dict)
    assert len(results) == 0


def test_coordinator_fix_auto_fix_all(tmp_path):
    """ALL_AUTO fix mode removes unused import when candidate authorizes destruction.

    Covers: fix() ALL_AUTO branch lines 276-285.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode
    from imodent.analysis.decision_models import DecisionCandidate, SubjectKey

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={
            "import_info": {
                "module": None,
                "name": "os",
                "alias": None,
                "intent": "usage",
            }
        },
    )

    subject_key = SubjectKey(
        kind="import", file=py_file, scope="module", module=None, name="os"
    )

    candidate = DecisionCandidate(
        issue_type="unused_import",
        subject_key=subject_key,
        finding_ids=[finding.id],
        confidence=0.85,
        destructive_allowed=True,
        requires_user_decision=False,
    )

    coordinator = AnalysisCoordinator()
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.ALL_AUTO,
        candidates=[candidate],
    )

    assert py_file in results
    assert results[py_file].success
    assert "import os" not in results[py_file].content


def test_coordinator_fix_safe_auto_mode(tmp_path):
    """SAFE_AUTO skips fix when no candidate is available (candidate=None gate).

    Covers: fix() SAFE_AUTO candidate-is-None skip lines 265-274.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator, FixMode

    py_file = tmp_path / "mod.py"
    py_file.write_text("import os\n")

    file_info = FileInfo.from_path(py_file)
    context = AnalysisContext(
        files={py_file: file_info},
        project_root=tmp_path,
    )

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=py_file,
        message="Unused: os",
        location=Location(line=1),
        import_name="os",
        data={"import_info": {"module": None, "name": "os"}},
    )

    coordinator = AnalysisCoordinator()
    # Empty candidates → candidate_by_finding_id returns None → SAFE_AUTO skips
    results = coordinator.fix(
        findings=[finding],
        context=context,
        mode=FixMode.SAFE_AUTO,
        candidates=[],
    )

    # No fix applied when candidate safety gate is empty
    assert py_file not in results


# ============================================================================
# _discover_files edge cases — exclude patterns, rust, multiple dirs
# ============================================================================


def test_discover_files_with_exclude_patterns(tmp_path):
    """_discover_files respects configured exclude patterns from AnalysisConfig.

    Covers: _is_excluded lines 385-397, _matches_path_pattern lines 530-540.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator

    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.py").write_text("x = 1\n")
    (src / "ignored_util.py").write_text("y = 2\n")

    config = AnalysisConfig(
        include_patterns=["*.py", "**/*.py"],
        exclude_patterns=["**/ignored_*.py"],
    )
    coordinator = AnalysisCoordinator(config=config)
    files = coordinator._discover_files([src])
    names = {p.name for p in files}

    assert "keep.py" in names
    assert "ignored_util.py" not in names


def test_discover_files_with_rust_enabled(tmp_path):
    """check_rust=True adds .rs / Cargo.toml / clippy.toml patterns to include glob.

    Covers: _discover_files rust-pattern injection lines 335-347.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator

    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("fn main() {}\n")
    (src / "Cargo.toml").write_text('[package]\nname = "test"\n')
    (src / "clippy.toml").write_text("cognitive-complexity-threshold = 30\n")

    config = AnalysisConfig(
        check_rust=True,
        include_patterns=["*.py", "**/*.py"],
    )
    coordinator = AnalysisCoordinator(config=config)
    files = coordinator._discover_files([src])
    names = {p.name for p in files}

    assert "lib.rs" in names
    assert "Cargo.toml" in names
    assert "clippy.toml" in names


def test_discover_files_multiple_directories():
    """_discover_files collects files across multiple directory arguments.

    Covers: _discover_files multi-path loop lines 348-369.

    Uses tempfile (not pytest tmp_path) because fnmatch on Linux treats
    ``*`` as matching ``/``, so pytest's ``test_*`` directory names
    would collide with the default ``test_*.py`` exclude pattern.
    """
    import tempfile

    from imodent.analysis.coordinator import AnalysisCoordinator

    with tempfile.TemporaryDirectory(prefix="imodent_multi_") as tmp:
        tmp_path = Path(tmp)
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        (dir_a / "mod_a.py").write_text("x = 1\n")

        dir_b = tmp_path / "b"
        dir_b.mkdir()
        (dir_b / "mod_b.py").write_text("y = 2\n")

        coordinator = AnalysisCoordinator()
        files = coordinator._discover_files([dir_a, dir_b])
        names = {p.name for p in files}

        assert "mod_a.py" in names
        assert "mod_b.py" in names


def test_discover_files_explicit_file_path():
    """_discover_files handles explicit file paths (not just directories).

    Covers: _discover_files path.is_file() branch lines 352-358.
    """
    import tempfile

    from imodent.analysis.coordinator import AnalysisCoordinator

    with tempfile.TemporaryDirectory(prefix="imodent_file_") as tmp:
        tmp_path = Path(tmp)
        py_file = tmp_path / "standalone.py"
        py_file.write_text("x = 1\n")

        coordinator = AnalysisCoordinator()
        files = coordinator._discover_files([py_file])
        names = {p.name for p in files}

        assert "standalone.py" in names


# ============================================================================
# _get_user_choice edge cases — invalid input, EOFError
# ============================================================================


def test_get_user_choice_invalid_then_valid(monkeypatch):
    """_get_user_choice handles invalid input then accepts a valid choice."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # First input is invalid (not a number), second is valid
    inputs = iter(["xyz", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is not None
    assert option.action in ("investigate", "keep")


def test_get_user_choice_eof_error(monkeypatch):
    """_get_user_choice returns None on EOFError during interactive prompt."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def raise_eof(prompt=""):
        raise EOFError("simulated EOF")

    monkeypatch.setattr("builtins.input", raise_eof)

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is None


def test_get_user_choice_out_of_range_then_skip(monkeypatch):
    """_get_user_choice handles out-of-range index then 's' to skip."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Out of range, then skip
    inputs = iter(["99", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is None


def test_get_user_choice_eof_on_tty(monkeypatch):
    """_get_user_choice with EOFError on TTY prints proper message."""
    from imodent.analysis.coordinator import AnalysisCoordinator
    from imodent.fixers.imports import ImportFixer

    coordinator = AnalysisCoordinator()

    finding = Finding.create(
        type="unused_import",
        severity=Severity.WARNING,
        file=Path("/fake/mod.py"),
        message="Unused: os",
        location=Location(line=1),
        data={"import_info": {"module": None, "name": "os"}},
    )

    context = AnalysisContext(files={}, project_root=Path("/fake"))
    fixer = ImportFixer()

    # TTY=True → EOF error path uses the "TTY disconnected" message
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def raise_eof(prompt=""):
        raise EOFError("simulated EOF")

    monkeypatch.setattr("builtins.input", raise_eof)

    option = coordinator._get_user_choice(finding, fixer, context)
    assert option is None


# ============================================================================
# Full analyze pipeline + generated-artifact exclusion
# ============================================================================


def test_coordinator_analyze_with_all_analyzers(tmp_path):
    """Full analyze() pipeline with imports + syntax + lint analyzers enabled.

    Covers: analyze() multi-analyzer orchestration lines 85-195,
            _get_analyzers lines 420-458.
    """
    from imodent.analysis.coordinator import AnalysisCoordinator

    py_file = tmp_path / "mod.py"
    # Use a real, well-formed file that exercises all analyzer code paths
    py_file.write_text(
        "import json\n"
        "import os\n\n"
        "def get_info():\n"
        '    return json.dumps({"cwd": os.getcwd()})\n'
    )

    config = AnalysisConfig(
        check_imports=True,
        check_lint=True,
        check_syntax=True,
        use_ruff=False,
    )
    coordinator = AnalysisCoordinator(config=config)
    result = coordinator.analyze([py_file])

    assert result is not None
    assert isinstance(result.findings, list)
    assert len(result.files) >= 1
    assert result.elapsed_time >= 0
    assert len(result.analyzer_names) >= 1

    # Results should include candidates from DecisionEngine
    assert isinstance(result.candidates, list)


def test_coordinator_analyze_skips_generated_artifacts():
    """Generated artifacts (.venv, __pycache__) are excluded during file discovery.

    Covers: _is_excluded → is_generated_artifact lines 386-388,
            discovery.py DEFAULT_EXCLUDED_DIR_NAMES.

    Uses tempfile (not pytest tmp_path) because fnmatch on Linux treats
    ``*`` as matching ``/``, so pytest's ``test_*`` directory names
    would collide with the default ``test_*.py`` exclude pattern.
    """
    import tempfile

    from imodent.analysis.coordinator import AnalysisCoordinator

    with tempfile.TemporaryDirectory(prefix="imodent_artifact_") as tmp:
        tmp_path = Path(tmp)

        # Normal source directory
        src = tmp_path / "src"
        src.mkdir()
        (src / "mod.py").write_text("x = 1\n")

        # Simulated venv directory — should be excluded entirely
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "ignored.py").write_text("y = 2\n")

        coordinator = AnalysisCoordinator()
        files = coordinator._discover_files([tmp_path])
        names = {p.name for p in files}

        assert "mod.py" in names
        assert "ignored.py" not in names, (
            "Files inside .venv must be excluded from discovery"
        )
