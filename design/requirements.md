# Requirements: imodent Expansion

## Functional Requirements

### R-01: Multi-File Analysis
**Requirement:** System must analyze multiple files as a cohesive unit, understanding imports between modules.
**Acceptance Criteria:**
- Can scan a directory tree and build an import dependency graph
- Can identify which imports are used by which files
- Can trace usage of a symbol across the codebase
- Must be language-aware (Python imports, JS imports, etc.)

### R-02: Import Analysis (Not Just Deletion)
**Requirement:** For unused imports, system must present options: delete OR use properly.
**Acceptance Criteria:**
- Detects unused imports at file level
- Checks if import is used anywhere in the module tree
- Presents three options: (1) delete, (2) keep (with reason), (3) investigate further
- Can auto-resolve if deducible from context
- Only asks user when truly ambiguous

### R-03: Lint Detection and Fixing
**Requirement:** Detect and fix lint issues using best available tools.
**Acceptance Criteria:**
- Integrates with standard linters (ruff, pyflakes, pylint)
- Provides unified interface for all lint types
- Can auto-fix where safe, presents options where risky
- Reports what was fixed and what needs attention

### R-04: Advisory Mode
**Requirement:** System must be able to advise on issues before fixing.
**Acceptance Criteria:**
- Can run in "report only" mode
- Provides actionable recommendations
- Explains WHY something is an issue
- Suggests specific fixes, not just "there's a problem"

### R-05: Modular Architecture
**Requirement:** System must be modular, not monolithic.
**Acceptance Criteria:**
- Each concern (indentation, imports, linting, multi-file) is a separate module
- Modules can be used independently or together
- Clear interfaces between modules
- Easy to add new analyzers/fixers

### R-06: Dependency on Existing Tools
**Requirement:** Use existing tools (ruff, etc.) rather than reimplementing.
**Acceptance Criteria:**
- External tools listed in requirements.txt
- Fallback if tool not available (with warning)
- Version pinning for reproducibility
- Import-based integration (not shell commands)

### R-07: User Interaction Policy
**Requirement:** Only ask user when information is impossible to deduce.
**Acceptance Criteria:**
- Scan module tree before asking about imports
- Check usages before suggesting deletion
- Provide context with any question
- Default to safe action if user doesn't respond

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
