# Requirements: imodent Expansion

## Functional Requirements

### R-01: Multi-File Analysis
**Requirement:** System must analyze multiple files as a cohesive unit, understanding imports between modules.
**Acceptance Criteria:**
- Can scan a directory tree and build an import dependency graph
- Can identify which imports are used by which files
- Can trace usage of a symbol across the codebase
- Must be language-aware (Python imports, JS imports, etc.)

### R-02: Import Analysis as Intent Evidence
**Requirement:** For unused imports, system must treat the import as possible unfinished intent before deletion.
**Acceptance Criteria:**
- Detects unused imports at file level
- Checks if import is used anywhere in the module tree
- Classifies imports by intent: runtime usage, typing, re-export, registration, side effect, or unknown
- Presents recovery-first options: investigate/wire, keep with reason, false-positive, delete as terminal action
- Can auto-resolve only when deducible from context and non-destructive
- Only asks user when truly ambiguous

### R-03: LLM Residue Detection
**Requirement:** Detect LLM-generated residue where code declares behavior but fails to wire execution.
**Acceptance Criteria:**
- Detects declared-but-unwired behavior such as CLI flags with ignored execution paths
- Groups related signals into an intent cluster before recommending action
- Recommends wire, export, register, test, quarantine, or mark-unsupported before delete
- Reports evidence for each cluster: source lines, config fields, missing executor, and destructive policy

### R-04: Optional External Tool Evidence
**Requirement:** External tools such as ruff may provide evidence but must not define the product behavior.
**Acceptance Criteria:**
- Integrates with standard linters where useful
- Treats linter output as one signal among code, imports, exports, config, CLI, tests, and docs
- Never auto-deletes a residue solely because a linter reports it unused
- Falls back gracefully when external tools are unavailable

### R-05: Advisory Mode
**Requirement:** System must be able to advise on issues before fixing.
**Acceptance Criteria:**
- Can run in "report only" mode
- Provides actionable recommendations
- Explains WHY something is an issue
- Suggests specific fixes, not just "there's a problem"

### R-06: Modular Architecture
**Requirement:** System must be modular, not monolithic.
**Acceptance Criteria:**
- Each concern (indentation, imports, linting, multi-file) is a separate module
- Modules can be used independently or together
- Clear interfaces between modules
- Easy to add new analyzers/fixers

### R-07: User Interaction Policy
**Requirement:** Only ask user when information is impossible to deduce.
**Acceptance Criteria:**
- Scan module tree before asking about imports
- Check usages before suggesting deletion
- Provide context with any question
- Default to safe action if user doesn't respond

### R-08: Delete-Last Policy
**Requirement:** Destructive cleanup is allowed only after intent recovery checks fail.
**Acceptance Criteria:**
- Delete is never the first recommendation for unused imports or residue clusters
- Findings carry whether destructive action is allowed
- Non-runtime intent findings are non-fixable evidence unless a later planner proves a safe patch
- Safe auto-fix applies only to mechanically redundant code, such as duplicate import lines

## Non-Functional Requirements

### NFR-01: Performance
- Analysis of 100-file codebase in < 10 seconds
- Incremental analysis (only changed files)
- Lazy loading of heavy dependencies

### NFR-02: Extensibility
- New language support via plugins
- New lint rules via registration
- Custom fixers via interface

### NFR-03: Reliability
- Never corrupt source files
- Always create backups before modification
- Validate fixes before writing

## Constraints

### C-01: Python First
- Primary target is Python codebases
- Other languages are secondary
- Must handle Python-specific constructs (decorators, async, type hints)

### C-02: Backward Compatible
- Existing CLI must continue to work
- New features are additive
- No breaking changes to public API

## Unknowns

### U-01: Which lint tools are acceptable?
- Need to verify: ruff, autoflake, pyflakes, pylint
- Decision: Import vs subprocess execution

### U-02: How to handle multi-language projects?
- Mixed Python/JS/YAML configs
- Language-specific rules

### U-03: Scope of "advise" feature
- How detailed should recommendations be?
- Is this a report or interactive consultation?
