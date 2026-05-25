# imodent Decision Architecture Implementation Prompt

Use this prompt with the implementation model. The implementation model is not a fallback or lesser reviewer; it is the planned implementation pass. The fourth pass will be performed afterward by the primary audit agent in the original session.

## Role

You are the implementation agent for imodent. You are executing a planned pass in a multi-agent workflow, not improvising a new architecture.

You must follow:

- `moe-workflow`
- `seshat`
- `advanced-design`
- Charlie prompt-engineering principles:
  - compress intent before acting;
  - state assumptions;
  - prevent hallucinated APIs;
  - leave clear progress markers;
  - document gaps and decisions for the next audit pass.

## Repository

```text
/home/ubuntu/imodent
```

Primary design document:

```text
/home/ubuntu/imodent/IMODENT_DECISION_ARCHITECTURE_PLAN.md
```

Read that file before editing anything.

## Core Intent

Implement the first contract-first tranche of imodent's decision architecture.

Do not build the whole temple in one pass. The goal is to establish the foundation:

1. typed subject identity;
2. evidence that can be fused;
3. binding-key dedupe;
4. decision candidates;
5. initial confidence output;
6. tests proving the known failure modes are handled.

The next audit pass will check your implementation. Make the work easy to inspect.

## Workflow Requirements

### Pass 1: One Shot

Load applicable skills and state them in your working notes:

- `moe-workflow`
- `seshat`
- `advanced-design`
- use Charlie prompt-engineering principles naturally

Then:

1. Read the current files:
   - `imodent/analysis/evidence.py`
   - `imodent/analysis/findings.py`
   - `imodent/analysis/context.py`
   - `imodent/analysis/coordinator.py`
   - `imodent/analyzers/imports.py`
   - `imodent/analyzers/lint.py`
   - `imodent/analyzers/residue.py`
   - `imodent/cli.py`
   - `imodent/fixers/imports.py`
   - relevant tests under `tests/unit/` and `tests/smoke/`
2. Measure current state with tests before major edits if practical.
3. Implement the smallest coherent vertical slice.
4. Record gaps you intentionally leave.

### Pass 2: Consolidate / Debug

Fix test failures one at a time. Do not paper over failures by weakening tests.

If the same boundary fails three times, stop and redesign that boundary instead of stacking patches.

### Pass 3: Quality Control

Before final response:

1. Run the full test suite:

   ```bash
   PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest
   ```

2. Run imodent on the two current benchmark repos:

   ```bash
   PATH="/home/ubuntu/imodent/.venv/bin:$PATH" \
     imodent /home/ubuntu/moedularizer --analyze --imports --lint --report -v

   PATH="/home/ubuntu/imodent/.venv/bin:$PATH" \
     imodent /home/ubuntu/hive_mind_classic --analyze --imports --lint --report -v
   ```

3. If you add `--confidence`, also run:

   ```bash
   PATH="/home/ubuntu/imodent/.venv/bin:$PATH" \
     imodent /home/ubuntu/hive_mind_classic --analyze --imports --lint --confidence --report
   ```

4. Leave a concise implementation summary and known limitations.

## Hard Constraints

- Do not implement opaque ML scoring.
- Do not add a persistent event store or database.
- Do not replace the CLI with one large `--mode` enum.
- Do not break existing commands.
- Do not suppress ambiguity before the evidence ledger records it.
- Do not expand destructive auto-fix behavior.
- Do not treat confidence as edit safety.
- Do not assume intent is knowable.
- Do not remove unrelated dirty files.
- Do not revert user changes.

## Required Architecture

### 1. SubjectKey

Add a typed subject identity, probably in a new module:

```text
imodent/analysis/decisions.py
```

Minimal contract:

```python
SubjectKey:
    kind: str                  # import, symbol, lint, api
    file: Path
    scope: str                 # module, class, function, unknown
    module: str | None
    name: str | None
    alias: str | None
    bound_name: str | None
    origin: str | None
```

Rules:

- Line number is evidence metadata, not identity.
- Same semantic subject must map to the same key across Ruff and AST evidence.
- Subject keys must serialize cleanly for reports.

### 2. Evidence Upgrade

Refine `Evidence`:

```python
Evidence:
    id: int
    kind: str
    source: str
    subject_key: SubjectKey
    file: Path
    location: Location | None
    claim: str
    polarity: str              # supports, opposes, context
    strength: float
    data: dict
```

Required fixes:

- Remove duplicate `Evidence.to_dict()`.
- Keep backward compatibility where reasonable, but the new path must use `subject_key`.
- Evidence must not decide action.
- Ruff evidence must be recorded before any public API or re-export suppression.

### 3. DecisionCandidate

Add a candidate model:

```python
DecisionCandidate:
    issue_type: str
    subject_key: SubjectKey
    finding_ids: list[str]
    evidence_ids: list[int]
    confidence: float
    confidence_label: str
    proof_state: str
    evidence_for: list[Evidence]
    evidence_against: list[Evidence]
    suggested_actions: list[ActionOption]
    destructive_allowed: bool
    requires_user_decision: bool
```

Also add:

```python
ActionOption:
    id: str
    label: str
    description: str
    destructive: bool
    safe_auto: bool
    requires_decision: bool
```

Rules:

- Confidence means "how likely is this diagnosis?"
- Destructive safety means "is this edit allowed?"
- High confidence does not imply destructive safety.
- Ambiguous candidates require user/agent decision.

### 4. Binding-Key Fusion

Replace line-based Ruff/local import dedupe.

Current known issue:

```text
_deduplicate_findings() joins by (file, line)
```

Required behavior:

- Ruff `F401` and local unused-import findings fuse by subject/binding key.
- Grouped imports remain separate subjects:

  ```python
  from x import a, b, c
  ```

- Multiline imports must not collapse into one subject by statement start line.
- Alias identity must track both the local alias and the origin symbol.

### 5. No Pre-Ledger Suppression

Current known issue:

- package-local `__init__.py` Ruff `F401` can be suppressed before evidence is recorded.
- local import analyzer can skip likely re-exports before evidence is useful.

Required behavior:

- record evidence first;
- then use public API signals to lower destructive confidence or change action options.

### 6. Try-Block Intent

Current known issue:

`_detect_import_intent()` treats import-in-try as protected side effect.

Required behavior:

- try-block context is evidence, not exoneration;
- `ImportError` / `ModuleNotFoundError` optional dependency patterns are stronger context;
- broad `except Exception` is weaker context;
- unused import in try block with no marker remains reviewable/actionable;
- no destructive auto-action unless safe gates pass.

### 7. CLI Presentation

Do not replace everything with `--mode`.

Use orthogonal flags:

```text
scope:          --imports, --lint, --advisory, --api-review
presentation:   --confidence, --salvage-report, --verbose
policy:         --strict, --fix, --interactive
preview:        --report, --dry-run
```

Implement only what fits cleanly in this pass. Prefer `--confidence` first.

Decision-grade output must include:

- relative path, not just filename;
- subject key;
- issue type;
- confidence;
- evidence for;
- evidence against;
- suggested actions;
- destructive policy.

## Required Tests

Add focused tests before or during implementation.

Must cover:

- grouped one-line import keeps separate subjects: `from x import a, b, c`
- multiline import uses alias identity/ranges, not statement start line
- alias identity tracks both local alias and origin symbol
- Ruff `F401` + local unused import fuse by subject key
- same symbol with `F401` and `F821` remains conflicting evidence, not fused certainty
- `__init__.py` local re-export records evidence instead of suppressing it
- hallucinated package-local re-export is not auto-protected
- try import caps deletion confidence but does not validate intent
- broad `except Exception` lowers optional-dependency confidence
- Ruff unavailable/invalid JSON means `INSUFFICIENT_EVIDENCE`, never clean
- `--confidence` output includes relative path and subject key
- `ALL_AUTO` or equivalent cannot fix duplicates unless the finding/candidate itself is safe

## Benchmarks

Use these repos as regression gates:

```text
/home/ubuntu/moedularizer
/home/ubuntu/hive_mind_classic
```

Expected qualitative results:

- moedularizer remains cleanly scoped:
  - no `build/lib` duplicates;
  - normal unused imports remain visible;
  - `validator.py:41 F841` remains visible.
- hive_mind_classic improves:
  - fewer duplicate Ruff/local grouped import findings;
  - fewer false try-block intent shields;
  - public API ambiguity appears as evidence/confidence, not hidden suppression.

## Known Dirty Working Tree

Before starting, check:

```bash
git status --short
```

Known dirty/untracked items may include:

- `.gitignore`
- `.ix/shard.ix`
- `.coverage`
- `.venv/`
- `IMODENT_DECISION_ARCHITECTURE_PLAN.md`
- `nexus.zip`
- `ses.txt`

Do not include unrelated artifacts in any commit.

## Final Response Requirements

At the end, report:

1. files changed;
2. tests run and results;
3. benchmark runs and key differences;
4. known limitations left for the fourth-pass audit;
5. whether any dirty files were intentionally left out.

The fourth pass will audit your work. Optimize for correctness, transparency, and reviewability.
