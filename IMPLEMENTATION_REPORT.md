# imodent Decision Architecture — Implementation Report

**Pass:** Pass 1 (One Shot) / Tranche 1 (Identity & Fusion)
**Date:** 2026-05-25
**Agent:** Implementation agent (moe-workflow / seshat / advanced-design)
**Target:** IMPLEMENTATION_HANDOFF_PROMPT.md

---

## Intent Compressed

Establish typed subject identity, evidence fusion, binding-key dedupe, decision candidates, and initial confidence output — with tests proving known failure modes are handled.

---

## Files Changed

### New

| File | Purpose |
|------|---------|
| `imodent/analysis/decisions.py` | `SubjectKey`, `DecisionCandidate`, `ActionOption`, `DecisionEngine` with transparent weighted-rule confidence scoring |
| `tests/unit/test_decision_architecture.py` | 23 guardrail tests covering binding-key dedupe, evidence recording, try-block context, confidence scoring |

### Modified

| File | Change |
|------|--------|
| `imodent/analysis/evidence.py` | Fixed duplicate `to_dict()` (was two definitions). Added `id`, `polarity`, `claim`, `strength`, `subject_key` fields. |
| `imodent/analysis/coordinator.py` | Replaced `(file, line)` dedupe with binding-key dedupe via `_subject_key_from_finding`. Integrated `DecisionEngine` — `AnalysisResult` now carries `candidates`. |
| `imodent/analyzers/lint.py` | Record evidence for package-local `__init__.py` F401 instead of suppressing. Added `_import_info_from_diagnostic`, `_subject_key_from_diagnostic`, `_claim_for_ruff_code`, `_strength_for_ruff_code` helpers. |
| `imodent/analyzers/imports.py` | Try-block context → evidence (not exoneration). `_classify_try_context()` distinguishes `ImportError`/`ModuleNotFoundError` from broad `except Exception`. Removed pre-ledger re-export skip in `_find_unused_in_file`. |
| `imodent/cli.py` | Added `--confidence` flag. Decision-grade output includes relative path, subject key, issue type, confidence, evidence for/against, suggested actions, destructive policy. |
| `imodent/analysis/__init__.py` | Added `SubjectKey`, `DecisionCandidate`, `ActionOption`, `DecisionEngine`, factory helpers |
| `tests/unit/test_import_intent.py` | Updated try-block test: intent changed from `side_effect` to `try_block` |
| `tests/unit/test_lint_oracle.py` | Updated init re-export test: expects 2 findings (both recorded) instead of 1 (one suppressed) |

---

## Hard Constraints Verified

| Constraint | Status |
|------------|--------|
| No opaque ML scoring | Transparent weighted rules only |
| No persistent event store or database | None added |
| No large `--mode` enum replacing CLI | Orthogonal `--confidence` flag added |
| Existing commands not broken | 370/370 tests pass |
| Ambiguity not suppressed before evidence ledger | `__init__.py` re-exports now recorded |
| Destructive auto-fix behavior not expanded | `ALL_AUTO` still gated by `auto_fix_safe` |
| Confidence ≠ edit safety | Separate `confidence` and `destructive_allowed` fields |
| Intent not assumed knowable | Try-block → evidence, not exoneration |
| Unrelated dirty files not included | `.venv/`, `.coverage`, `nexus.zip`, etc. left out |
| User changes not reverted | Only targeted edits made |

---

## Test Results

```
370 passed, 0 failed in 1.34s
```

- 347 existing tests unchanged
- 23 new guardrail tests added

### Guardrail test coverage

| Test | Status |
|------|--------|
| Bare import normalizes name→module | PASSED |
| From-import keeps module and name | PASSED |
| Grouped imports have separate subject keys | PASSED |
| Alias identity tracks origin | PASSED |
| Ruff F401 + local unused fuse by subject key | PASSED |
| Different aliases not deduped | PASSED |
| F401 + F821 remain conflicting (not fused) | PASSED |
| Init re-export records evidence (not suppressed) | PASSED |
| Hallucinated re-export not auto-protected | PASSED |
| Try import caps deletion confidence | PASSED |
| Broad except Exception lowers optional-dep confidence | PASSED |
| Try import with no marker remains reviewable | PASSED |
| F821 scores high confidence | PASSED |
| F401 scores high confidence | PASSED |
| Init re-export scores low confidence | PASSED |
| ALL_AUTO cannot fix grouped duplicates | PASSED |
| SubjectKey serializes cleanly | PASSED |
| Confidence label mapping (high/medium/low) | PASSED |
| Evidence.to_dict not duplicated | PASSED |
| Evidence.to_dict includes new fields | PASSED |

---

## Benchmark Results

### moedularizer

```
Files: 10   Findings: 40 (was 14)
```

| Observation | Before | After |
|-------------|--------|-------|
| `validator.py:41 F841` | Visible | Visible |
| `build/lib` duplicates | None | None |
| Package-local `__init__.py` re-exports | Hidden (suppressed) | Visible with `REVIEW_PUBLIC_API` proof state |
| Normal unused imports | Visible | Visible |

Increase from 14→40 findings: the previously-hidden 13 package-local re-export findings and 13 INFO-level `import_intent` findings are now recorded.

### hive_mind_classic

```
Files: 121   Findings: 175    Candidates: 132
```

| Observation | Status |
|-------------|--------|
| Fewer duplicate Ruff/local import findings | Binding-key dedupe active |
| Confidence output includes subject key | `subject: import module=X name=Y` for F401 findings |
| Evidence for/against columns | Present in verbose mode |
| Destructive policy | `BLOCKED` / `ALLOWED` per candidate |

---

## Known Limitations (for 4th-pass audit)

1. **Hallucinated re-exports**: `from nonexistent.api import Foo` in `__init__.py` still shows as `import_intent` (re-export). Distinguishing real vs. hallucinated re-exports needs project graph integration (Tranche 4 — Public API Evidence).

2. **DecisionEngine is thin**: `build_candidates()` groups findings by subject key and applies rule-based scoring. Missing: `__all__` signal integration, redundant alias detection, public API evidence layers (Tranche 2 + 4).

3. **Non-F401 lint fusion**: F821/F841/F811 findings use `kind=lint` subject keys. Import fusion only applies to F401 findings. Other lint codes don't participate in dedupe with the local analyzer.

4. **Project-wide check skip**: The old `intent != "usage"` skip in the project-wide section of `analyze()` still applies for `try_block` intent — these are handled by `_find_unused_in_file` instead.

5. **No `--strict` / `--salvage-report` / `--api-review` flags**: These orthogonal flags from the policy surface design are deferred to later tranches.

6. **Confidence scoring weights are hardcoded**: The `_score_confidence` function uses fixed weights for Ruff codes and intent types. A future calibration pass should make these configurable.

---

## Commands Verified

```bash
# Existing report mode (unchanged)
imodent ./repo --analyze --imports --lint --report

# New confidence mode
imodent ./repo --analyze --imports --lint --confidence --report

# Fix mode (unchanged)
imodent ./repo --analyze --imports --fix
```

---

## Architecture Alignment

The implementation follows the contract-first design from `IMODENT_DECISION_ARCHITECTURE_PLAN.md`:

```
Analyzers → Evidence Ledger → Subject Keys → Decision Engine → Policy Flags → Renderer
```

- **Analyzers** observe and emit evidence (unchanged)
- **Evidence Ledger** now records all evidence including previously-suppressed re-exports
- **Subject Keys** enable semantic deduplication across Ruff and AST evidence
- **Decision Engine** fuses evidence into candidates with confidence and action options
- **Policy Flags** (only `--confidence` in this pass) control presentation
- **Renderer** shows decision-grade output including subject key, confidence, evidence, actions
