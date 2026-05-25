# imodent Delegation Prompt — Pass 2 Consolidate/Debug

## Role

You are the Pass 2 implementation agent for `imodent`, a Python CLI code-intelligence tool. Your job is to consolidate and debug the recently added decision architecture. Do not redesign the whole system. Fix the audited integration bugs, add guardrail tests that fail before your fixes, and verify with fresh commands.

## Required Workflow

Use the operator's moe workflow:

1. Load applicable skills before action:
   - `moe-workflow`
   - `advanced-debugging`
   - `llm-guardrails`
   - `code-audit-mindset`
   - `charlie-prompt-engineering` if you need to clarify or update prompts/docs
2. Use structured reasoning in the SEE -> EXPLORE -> CONVERGE -> REFLECT shape.
3. Keep edits scoped to the decision-layer bugs below.
4. Do not touch unrelated dirty files such as `.ix/shard.ix`, `.gitignore`, `.venv/`, `.coverage`, `nexus.zip`, or `ses.txt` unless the operator explicitly asks.

## Current State

The previous pass introduced:

- `imodent/analysis/decisions.py`
- typed `SubjectKey`
- evidence IDs and richer evidence fields
- decision candidates
- `--confidence` output
- tests in `tests/unit/test_decision_architecture.py`

Fresh verification from the fourth-pass audit:

```bash
PATH=/home/ubuntu/imodent/.venv/bin:$PATH .venv/bin/pytest -q
# 370 passed
```

But the same audit found real contract bugs. Passing tests are not enough.

## Hard Bugs To Fix

### Bug 1: `--confidence` alone routes to FIX mode and can write files

Location:

- `imodent/cli.py`

Trigger:

```bash
tmp=$(mktemp -d)
printf '{"a":1}' > "$tmp/config.json"
before=$(sha256sum "$tmp/config.json")
.venv/bin/imodent "$tmp/config.json" --confidence
after=$(sha256sum "$tmp/config.json")
echo "$before"
echo "$after"
```

Observed: the checksum changes. `--confidence` is a scan/report presentation flag and must not cause file writes.

Expected:

- `--confidence` must route to scan mode.
- If used without explicit `--analyze`, it should behave like a report-style analysis view.
- No files are modified unless an explicit write policy is selected (`--fix` in scan mode, or default FIX mode without scan flags).
- Add a CLI routing test proving this.

### Bug 2: decision candidates lose evidence objects

Location:

- `imodent/analysis/decisions.py`

Symptom:

- `DecisionEngine.build_candidates()` creates `evidence_index` but never uses it.
- Candidates receive `evidence_ids`, but `evidence_for` and `evidence_against` are empty.
- `--confidence --verbose` therefore cannot show the actual evidence ledger.

Expected:

- Rehydrate evidence dict IDs into `Evidence` objects using the supplied evidence list.
- Attach evidence by matching `subject_key` when IDs are missing or stale.
- Preserve polarity:
  - `supports` -> `evidence_for`
  - `opposes` -> `evidence_against`
  - `context` should be carried in a way the renderer can display; if you do not add a third list yet, include it in `evidence_for` only with clear polarity preserved.
- Add tests that assert `evidence_for` is non-empty for a simple Ruff `F401`.

### Bug 3: Ruff `F401` candidates are classified as generic `LINT`

Location:

- `imodent/analysis/decisions.py`

Trigger:

```bash
tmp=$(mktemp -d)
printf 'import os\n' > "$tmp/sample.py"
.venv/bin/imodent "$tmp/sample.py" --analyze --imports --lint --confidence --report --verbose
```

Observed:

- Candidate displays as `LINT`.
- Actions are only `Review manually`.

Expected:

- Ruff `F401` should become an `UNUSED_IMPORT` decision candidate.
- It should use import-specific actions: remove, keep, investigate.
- Destructive safety still remains blocked unless the candidate/fixer safety contract explicitly allows it.

### Bug 4: subject-key fusion fails for common import forms

Location:

- `imodent/analyzers/lint.py`
- `imodent/analysis/decisions.py`
- `imodent/analysis/coordinator.py`
- possibly `imodent/analyzers/imports.py`

Trigger:

```bash
tmp=$(mktemp -d)
printf 'import os.path\nimport numpy as np\nfrom typing import Dict as D, List\n' > "$tmp/sample.py"
.venv/bin/imodent "$tmp/sample.py" --analyze --imports --lint --confidence --report --verbose
```

Observed:

- `import os.path` appears as separate local and Ruff candidates because AST uses `module=os.path, name=None` while Ruff message parsing uses `module=os, name=path`.
- `from typing import Dict as D` appears as separate local and Ruff candidates because Ruff does not know the alias from the message.
- `import numpy as np` has no local match because the current file-local unused pass misses bare aliased imports.

Expected:

- Normalize import binding identity around the bound runtime name and origin.
- Do not rely only on Ruff message parsing when location and AST import statements are available.
- Use line/column only as evidence to find the import alias, not as final subject identity.
- Grouped imports must stay separate per alias.
- Aliased imports must fuse when Ruff and AST refer to the same binding.
- Add tests for:
  - `import os.path`
  - `import numpy as np`
  - `from typing import Dict as D, List`
  - grouped imports where one alias is used and one alias is unused

### Bug 5: Ruff fails on the changed code

Command:

```bash
PATH=/home/ubuntu/imodent/.venv/bin:$PATH .venv/bin/ruff check imodent tests/unit/test_decision_architecture.py tests/unit/test_import_intent.py tests/unit/test_lint_oracle.py
```

Observed examples:

- unused imports in `imodent/analysis/coordinator.py`
- redefined local imports in `imodent/analysis/coordinator.py`
- undefined `SubjectKey` in quoted annotations for Ruff's rules
- unused test imports
- unused `evidence_index` in `imodent/analysis/decisions.py`

Expected:

- Ruff check passes for touched source and tests.
- Keep type annotations valid under Ruff.

## Design Constraints

- Do not add ML scoring.
- Do not add a database or persistent event store.
- Do not replace the CLI with a large `--mode` enum.
- Do not expand destructive auto-fix behavior.
- Keep diagnosis confidence separate from destructive edit safety.
- Do not suppress ambiguity before it is recorded in the evidence ledger.
- Do not treat intent as binary. Public API, try-block, typing, side-effect, and comment markers are evidence that lower deletion confidence, not magic exoneration.

## Required Verification

Run all of these and record the results in your implementation report:

```bash
PATH=/home/ubuntu/imodent/.venv/bin:$PATH .venv/bin/pytest -q
PATH=/home/ubuntu/imodent/.venv/bin:$PATH .venv/bin/ruff check imodent tests/unit/test_decision_architecture.py tests/unit/test_import_intent.py tests/unit/test_lint_oracle.py
tmp=$(mktemp -d); printf '{"a":1}' > "$tmp/config.json"; before=$(sha256sum "$tmp/config.json"); .venv/bin/imodent "$tmp/config.json" --confidence >/tmp/imodent-confidence.out 2>&1; after=$(sha256sum "$tmp/config.json"); echo "$before"; echo "$after"; cat /tmp/imodent-confidence.out; rm -rf "$tmp" /tmp/imodent-confidence.out
tmp=$(mktemp -d); printf 'import os\n' > "$tmp/sample.py"; .venv/bin/imodent "$tmp/sample.py" --analyze --imports --lint --confidence --report --verbose; rm -rf "$tmp"
tmp=$(mktemp -d); printf 'import os.path\nimport numpy as np\nfrom typing import Dict as D, List\n' > "$tmp/sample.py"; .venv/bin/imodent "$tmp/sample.py" --analyze --imports --lint --confidence --report --verbose; rm -rf "$tmp"
```

## Deliverables

1. Code fixes and focused tests.
2. Updated implementation report, or a new `IMPLEMENTATION_REPORT_PASS_2.md`.
3. A short note listing any remaining known limitations.

## Stop Conditions

Stop and report instead of patching around symptoms if:

- the same identity/fusion boundary fails three different ways after your changes;
- fixing one import form breaks another import form;
- destructive safety becomes coupled to confidence score alone;
- the CLI can still write files from any presentation/report flag.
