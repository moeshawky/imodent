# imodent Decision Architecture Plan

Date: 2026-05-25

Status: contract-first implementation plan after subagent design review

## Problem

imodent is moving from lint reporting toward code-intelligence triage for messy repositories. The target repositories contain both real operator intent and hallucinated imports/APIs. Therefore, imodent must not pretend it can know intent with certainty.

The correct design is not binary:

- not "this is intended"
- not "this is hallucinated"
- not "always delete unused-looking code"
- not "always preserve weird-looking code"

The correct design is confidence-weighted decision support. imodent gathers evidence, calculates confidence, shows the evidence for and against each interpretation, and leaves final design decisions to the user or the user's agent unless a change passes strict safety gates.

## Requirements

- R-01: One evidence core must support multiple operating modes.
- R-02: Findings must carry uncertainty; intent is inferred with confidence, not declared as fact.
- R-03: Final ambiguous decisions remain with the user or their agent.
- R-04: Destructive actions require both high confidence and an explicit safety gate.
- R-05: CLI modes and help text must make behavior clear.
- R-06: Fragmented repos with hallucinated imports/APIs are first-class targets.
- R-07: Backward-compatible report mode must continue to work.
- R-08: Raw ambiguity must not be suppressed before the evidence ledger sees it.
- R-09: Diagnosis confidence and destructive edit safety must be separate concepts.
- R-10: Huge repos with repeated filenames need relative paths and subject keys in decision-grade output.

## Architecture

```text
Analyzers
  -> Evidence Ledger
  -> Subject Keys
  -> Decision Engine
  -> Policy Flags
  -> Renderer / Fix Eligibility
```

Analyzers observe. They do not own final intent decisions.

The Decision Engine fuses observations into candidates. Policy flags decide which candidates to show, rank, suppress, or make eligible for action.

Critical rule from review: no analyzer should suppress inconvenient ambiguity. Public API, try-block, comment, and registration signals are evidence that lowers destructive confidence or changes suggested actions. They should not erase the raw diagnostic before the ledger records it.

## Core Contracts

### SubjectKey

Stable identity for the thing being judged.

```python
SubjectKey:
    kind: str                  # import, symbol, lint, api
    file: Path
    scope: str                 # module, class, function, unknown
    module: str | None
    name: str | None
    alias: str | None
    bound_name: str | None
    origin: str | None         # fully qualified origin if known
```

Invariants:

- Same semantic subject should map to the same key even when Ruff and AST report different line/column positions.
- Line number is evidence metadata, not identity.
- Subject keys must be serializable for reports.

### Evidence

Observed fact from a source.

```python
Evidence:
    id: int
    kind: str
    source: str                # ast, ruff, graph, grep, config, comment
    subject_key: SubjectKey
    file: Path
    location: Location | None
    claim: str                 # unused_import, undefined_api, public_export, etc.
    polarity: str              # supports, opposes, context
    strength: float            # 0.0 - 1.0
    data: dict
```

Invariants:

- Evidence does not decide action.
- Evidence must say what it supports or contradicts.
- Opaque evidence is not allowed in confidence mode.
- Ruff evidence must be recorded before any public API or re-export suppression.
- Line and column are evidence metadata, never identity.

### DecisionCandidate

Actionable/reviewable interpretation of evidence.

```python
DecisionCandidate:
    issue_type: str            # unused_import, undefined_api, public_api_review
    subject_key: SubjectKey
    finding_ids: list[str]
    evidence_ids: list[int]
    confidence: float          # 0.0 - 1.0
    confidence_label: str      # low, medium, high
    proof_state: str
    evidence_for: list[Evidence]
    evidence_against: list[Evidence]
    suggested_actions: list[ActionOption]
    destructive_allowed: bool
    requires_user_decision: bool
```

Invariants:

- Confidence expresses epistemic certainty, not severity.
- Proof state expresses lifecycle/evidence status, not confidence.
- `destructive_allowed=True` requires high confidence and a safe edit shape.
- Ambiguous candidates must set `requires_user_decision=True`.
- Diagnosis confidence answers "how likely is this interpretation?"
- Destructive safety answers "is this edit allowed?"
- High diagnosis confidence does not imply destructive safety.

### ActionOption

```python
ActionOption:
    id: str
    label: str
    description: str
    destructive: bool
    safe_auto: bool
    requires_decision: bool
```

### Policy Flags

CLI behavior over the same candidate set. Review rejected one large `--mode` enum because scope, presentation, write policy, and CI policy are orthogonal.

```text
analysis scope:       --imports, --lint, --advisory, --api-review
presentation:         --confidence, --salvage-report, --verbose
write policy:         --fix, --interactive, future --fix-mode {safe,all}
CI/filter policy:     --strict
preview/no-write:     --report, --dry-run
```

## CLI Policy Surface

Keep existing commands working. Add orthogonal flags instead of replacing them with one global mode enum.

Examples:

```bash
imodent ./repo --analyze --imports --lint --report
imodent ./repo --analyze --imports --lint --confidence --report
imodent ./repo --analyze --imports --lint --salvage-report --report
imodent ./repo --analyze --api-review --report
imodent ./repo --analyze --imports --lint --strict
imodent ./repo --analyze --imports --fix
```

### report

- Writes: no
- Confidence: optional/verbose
- Intended user: existing users

### --confidence

Ranked decision candidates with evidence for/against.

- Writes: no
- Confidence: required
- Intended user: human or agent triage

### --strict

High-confidence external-tool findings only.

- Writes: no
- Confidence: high only
- Intended user: quick CI-style sanity check

### --salvage-report

Fragmented-repo recovery mode.

- Writes: no by default
- Bias: recover/implement/quarantine choices, not deletion
- Intended user: operator or agent cleaning a broken repo

### --api-review

Public API/export surface review.

- Writes: no
- Focus: `__init__.py`, `__all__`, redundant aliases, package-local exports

### --fix

Only low-risk edits.

- Writes: yes, only explicit safe shapes
- Excludes ambiguous intent cleanup

`ALL_AUTO` is currently unsafe as an expansion point because fixer capability can bypass finding-level safety. Before adding `--fix-mode all`, every fixer must require candidate/finding-level safety, not merely fixer-level capability.

## Confidence Scoring Draft

The first implementation should use transparent weighted rules, not opaque ML.

Example support signals:

- Ruff `F821`: strong support for undefined/hallucinated API.
- Ruff `F841`: strong support for unused local variable.
- Ruff `F401`: strong support for unused import.
- AST no-use: support for unused binding.
- Project graph no definition: support for hallucinated/missing API.
- `__all__`: contradiction against unused-public-export removal.
- Redundant alias: contradiction against removal.
- Package-local `__init__.py` import: context for public API review.
- Side-effect marker: contradiction against removal, medium strength.
- Try-block context alone: weak context, not enough to protect an alias.
- Dynamic imports, monkeypatching, plugin discovery, and framework registration cap confidence and require review.
- `# noqa` and comment markers are policy evidence, not correctness evidence.
- Ruff unavailable or invalid JSON means insufficient evidence, never clean.

Example confidence sketches:

- `F821` + no local definition + no import + no graph hit: `0.90`.
- `F401` normal module + no AST use: `0.85`.
- `F401` in `__init__.py` + package-local + `__all__`: removal confidence `0.10`.
- Try-block import + no use + no marker: unused confidence `0.65`, review required.

## Implementation Tranches

### Tranche 1: Identity and Fusion

- Fix duplicate `Evidence.to_dict()`.
- Add `SubjectKey`.
- Add `subject_key` to `Evidence`.
- Add `id`, `polarity`, `claim`, and `strength` to `Evidence`.
- Parse Ruff `F401` diagnostic messages into import subject keys.
- Replace line-based dedupe with binding-key dedupe.
- Stop suppressing package-local `__init__.py` Ruff F401 before evidence is recorded.

Verification:

- Hive grouped imports no longer produce duplicate Ruff + RAW local import findings.
- `__init__.py` package-local re-export evidence is visible in the ledger.
- Existing test suite passes.

### Tranche 2: Decision Candidates

- Add `DecisionCandidate`.
- Add `DecisionEngine` that emits candidates from findings/evidence.
- Keep existing `Finding` output backward compatible.
- Move confidence to `DecisionCandidate`; mirror compact summaries into `Finding.data["decision"]` only for compatibility.
- Resolve proof state in the decision layer, not analyzer-specific data bags.

Verification:

- Unit tests for `F401`, `F821`, `F841`, public API review, and conflicting evidence.

### Tranche 3: Confidence Presentation

- Add `--confidence`.
- Show relative path, subject key, issue type, confidence, evidence for, evidence against, action options, destructive policy.
- Keep normal report output backward-compatible.

Verification:

- CLI routing tests.
- Help text tests.
- Repeated filenames in different directories are unambiguous in output.

### Tranche 4: Public API Evidence and API Review

- Detect `__all__`.
- Detect redundant aliases.
- Detect package-local exports.
- Use evidence against destructive removal.
- Add `--api-review` as analysis scope/presentation filter.

Verification:

- moedularizer `__init__.py` stdlib/typing imports remain review/actionable.
- real package exports remain protected.
- hallucinated package-local re-export is not auto-protected.

### Tranche 5: Try-Block Intent Split

- Stop treating all try-block imports as protected.
- Emit try-block evidence instead of exonerating the import.
- Treat `ImportError`/`ModuleNotFoundError` optional dependency patterns as stronger context than broad `except Exception`.
- Protect only used imports or explicit side-effect markers from destructive auto-action.

Verification:

- Hive false intent shields decrease.

### Tranche 6: Salvage Report

- Add `--salvage-report`.
- Group candidates by recovery action: implement missing API, import/replace symbol, quarantine fragment, remove unused binding, preserve public API.
- No writes by itself.

Verification:

- Synthetic fragmented-repo fixtures show hallucinated APIs and intentional weirdness in separate candidate buckets.

## Not Building Yet

- No ML confidence model.
- No persistent event store.
- No automatic learning from user decisions.
- No broad auto-fix expansion.
- No full scan-profile system until mode behavior is stable.
- No one-size-fits-all `--mode` enum replacing existing scan flags.

## Blockers Before Implementation

- `Evidence.subject` is stringly typed and cannot support fusion.
- `Evidence.to_dict()` is duplicated in current source.
- `Finding.proof_state` is stringly typed while `ProofState` is an enum.
- `REVIEW_PUBLIC_API` exists in current output but not in `ProofState`.
- `context.evidence` is populated but not consumed by a decision layer.
- Current dedupe is `(file, line)`, which fails grouped and multiline imports.
- `_detect_import_intent()` still exonerates try-block imports.
- Ruff package-local `__init__.py` F401 suppression hides evidence from future confidence scoring.
- CLI output prints only `f.file.name`, unsafe in huge repos with repeated filenames.
- `ALL_AUTO` can rely on fixer capability instead of finding/candidate-level destructive safety.

## Guardrail Tests To Add

- grouped one-line import keeps separate subjects: `from x import a, b, c`
- multiline import uses alias identity/ranges, not statement start line
- alias identity tracks both `Alias` and `pkg.mod.Thing`
- same symbol with `F401` and `F821` remains conflicting evidence, not fused certainty
- `__init__.py` local re-export records evidence instead of suppressing it
- hallucinated package-local re-export is not auto-protected
- try import caps deletion confidence but does not validate intent
- broad `except Exception` lowers optional-dependency confidence
- Ruff unavailable/invalid JSON produces `INSUFFICIENT_EVIDENCE`
- confidence output includes relative path and subject key
- `ALL_AUTO` cannot fix duplicates unless the finding/candidate itself is safe
- synthetic fragmented repo includes both hallucinated APIs and intentional public exports

## Open Design Questions

- Should `strict` include Ruff style findings or only behavioral classes like `F821`, `F841`, and `F401`?
- How should decisions from a user/agent be recorded for future calibration?
- Should `--salvage-report` be implemented before or after `--api-review` once confidence output exists?
