# Implementation Plan: imodent Residue Recovery

This plan supersedes the linter-wrapper framing. External lint output is useful
evidence, but the first product slice is recovery-first analysis for LLM residue.

## Current Vertical Slice

### Task V1.1: Preserve Import Intent
**Input:** Import findings with typing/re-export/registration/side-effect evidence
**Output:** `import_intent` findings that are not directly fixable
**Verification:** Side-effect import produces `import_intent`, not `unused_import_file`
**Files:**
- `imodent/analyzers/imports.py`
- `tests/unit/test_import_intent.py`

### Task V1.2: Recovery-First Import Options
**Input:** `unused_import` / `unused_import_file` findings
**Output:** Options ordered as investigate/keep/use/delete, with delete unsafe and terminal
**Verification:** First option is `investigate`; delete remains available but not safe
**Files:**
- `imodent/fixers/imports.py`
- `tests/unit/test_edge_cases.py`

### Task V1.3: Declared-But-Unwired Lint Behavior
**Input:** `--lint`, `analyze_lint`, `AnalysisConfig.check_lint`, and no executor
**Output:** Non-fixable `declared_behavior_unwired` finding with cluster evidence
**Verification:** ResidueAnalyzer emits one lint cluster when `check_lint=True`
**Files:**
- `imodent/analyzers/base.py`
- `imodent/analyzers/residue.py`
- `imodent/analysis/coordinator.py`
- `imodent/cli.py`
- `tests/unit/test_import_intent.py`

### Task V1.4: Documentation Realignment
**Input:** Existing design docs that frame imodent as lint/import cleanup
**Output:** Requirements, architecture, and contracts describe residue recovery
**Verification:** Docs state delete-last policy and external lint as evidence
**Files:**
- `design/requirements.md`
- `design/architecture.md`
- `design/contracts.md`
- `design/implementation-plan.md`

## Task Breakdown

### Phase 1: Core Infrastructure (Day 1)

#### Task 1.1: Expand interfaces.py
**Input:** Current interfaces.py
**Output:** New interfaces with Analyzer, Fixer, Advisor, Finding, etc.
**Verification:** All interfaces compile, existing tests still pass
**Dependencies:** None
**Files:**
- `imodent/interfaces.py` (modify)

#### Task 1.2: Create analysis context
**Input:** None
**Output:** AnalysisContext, FileInfo, AnalysisConfig classes
**Verification:** Unit tests for context operations
**Dependencies:** Task 1.1
**Files:**
- `imodent/analysis/context.py` (new)

#### Task 1.3: Create finding types
**Input:** None
**Output:** Finding, Severity, Location, FixOption dataclasses
**Verification:** Unit tests for finding creation
**Dependencies:** Task 1.1
**Files:**
- `imodent/analysis/findings.py` (new)

#### Task 1.4: Create registry expansion
**Input:** Current registry.py
**Output:** AnalyzerRegistry, FixerRegistry, AdvisorRegistry
**Verification:** Registration works, lazy loading preserved
**Dependencies:** Task 1.1
**Files:**
- `imodent/registry.py` (modify)

### Phase 2: Dependency Graph (Day 1-2)

#### Task 2.1: Import extraction
**Input:** Python source files
**Output:** List of imports per file (module, name, alias)
**Verification:** Extract imports from test files correctly
**Dependencies:** Task 1.2
**Files:**
- `imodent/graph/imports.py` (new)

#### Task 2.2: Dependency graph builder
**Input:** Import data from all files
**Output:** DependencyGraph with edges (importer -> importee)
**Verification:** Graph built correctly for multi-file test case
**Dependencies:** Task 2.1
**Files:**
- `imodent/graph/dependency.py` (new)

#### Task 2.3: Symbol usage tracer
**Input:** Dependency graph + symbol name
**Output:** List of locations where symbol is used
**Verification:** Trace symbol across files
**Dependencies:** Task 2.2
**Files:**
- `imodent/graph/usage.py` (new)

### Phase 3: Analyzers (Day 2-3)

#### Task 3.1: Base analyzer class
**Input:** Analyzer interface
**Output:** Abstract base class with common utilities
**Verification:** Can be subclassed
**Dependencies:** Task 1.1
**Files:**
- `imodent/analyzers/base.py` (new)

#### Task 3.2: Indentation analyzer
**Input:** Current indentation logic
**Output:** IndentationAnalyzer implementing Analyzer
**Verification:** Same behavior as current, finding-based output
**Dependencies:** Task 3.1
**Files:**
- `imodent/analyzers/indentation.py` (new)

#### Task 3.3: Import analyzer
**Input:** Dependency graph
**Output:** Findings for unused/duplicate imports
**Verification:** Detects unused imports in test files
**Dependencies:** Task 2.3, Task 3.1
**Files:**
- `imodent/analyzers/imports.py` (new)

#### Task 3.4: Lint analyzer (ruff)
**Input:** Source files
**Output:** Findings from ruff output
**Verification:** ruff integration works
**Dependencies:** Task 3.1
**Files:**
- `imodent/analyzers/lint.py` (new)

### Phase 4: Fixers (Day 3-4)

#### Task 4.1: Base fixer class
**Input:** Fixer interface
**Output:** Abstract base class with common utilities
**Verification:** Can be subclassed
**Dependencies:** Task 1.1
**Files:**
- `imodent/fixers/base.py` (new)

#### Task 4.2: Import fixer
**Input:** Import findings
**Output:** Options (delete, keep, investigate) + fix application
**Verification:** Can fix unused imports
**Dependencies:** Task 4.1, Task 3.3
**Files:**
- `imodent/fixers/imports.py` (new)

#### Task 4.3: Lint fixer
**Input:** Lint findings
**Output:** Auto-fix where safe
**Verification:** ruff --fix integration
**Dependencies:** Task 4.1, Task 3.4
**Files:**
- `imodent/fixers/lint.py` (new)

### Phase 5: Coordinator (Day 4)

#### Task 5.1: Analysis coordinator
**Input:** All analyzers, fixers
**Output:** Coordinator that runs analysis pipeline
**Verification:** End-to-end analysis works
**Dependencies:** Task 3.4, Task 4.3
**Files:**
- `imodent/analysis/coordinator.py` (new)

### Phase 6: Advisory (Day 5)

#### Task 6.1: Base advisor class
**Input:** Advisor interface
**Output:** Abstract base class
**Verification:** Can be subclassed
**Dependencies:** Task 1.1
**Files:**
- `imodent/advisors/base.py` (new)

#### Task 6.2: Architecture advisor
**Input:** Findings + dependency graph
**Output:** Architectural recommendations
**Verification:** Produces sensible advice
**Dependencies:** Task 6.1
**Files:**
- `imodent/advisors/architecture.py` (new)

### Phase 7: CLI Integration (Day 5)

#### Task 7.1: Expand CLI
**Input:** Current CLI
**Output:** New options for multi-file, imports, lint, advisory
**Verification:** All options work
**Dependencies:** Task 5.1
**Files:**
- `imodent/cli.py` (modify)

#### Task 7.2: Update pyproject.toml
**Input:** Current pyproject.toml
**Output:** Add ruff, pyright dependencies
**Verification:** Install works
**Dependencies:** None
**Files:**
- `pyproject.toml` (modify)

## Task Dependency Graph

```
Phase 1 (Core)
    1.1 ─┬─> 1.2 ─> 2.1 ─> 2.2 ─> 2.3 ─┐
         ├─> 1.3                       │
         └─> 1.4                       │
                                      ├─> 3.3 ─> 4.2 ─┐
Phase 3 (Analyzers)                   │               │
    3.1 ─┬─> 3.2                      │               │
         ├─> 3.4 ─────────────────────┼───────────────┼─> 4.3 ─┐
         └─────────────────────────────┘               │        │
                                                        │        │
Phase 4 (Fixers)                                        │        │
    4.1 ─┬──────────────────────────────────────────────┘        │
         └───────────────────────────────────────────────────────┼─> 5.1 ─> 7.1
                                                                    │
Phase 6 (Advisory)                                                  │
    6.1 ─> 6.2 ─────────────────────────────────────────────────────┘
```

## Verification Strategy

### Per-Task Verification
Each task has:
1. **Unit tests** - Test the specific functionality
2. **Integration checkpoint** - Verify it works with previous tasks
3. **No regression** - Existing tests still pass

### Phase Gates
- **Phase 1 Gate:** All interfaces compile, registry works
- **Phase 2 Gate:** Dependency graph builds for test project
- **Phase 3 Gate:** All analyzers produce findings
- **Phase 4 Gate:** Fixers can fix test cases
- **Phase 5 Gate:** End-to-end analysis works
- **Phase 6 Gate:** Advisory produces output
- **Phase 7 Gate:** CLI fully functional

### Final Verification
- All existing tests pass
- New tests cover new functionality
- Manual testing with real project
- Performance benchmark (<10s for 100 files)

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| ruff integration issues | Fallback to manual parsing |
| Circular imports in graph | Detect and report, don't crash |
| Large project performance | Implement incremental analysis |
| Breaking existing API | Keep old API, add new as separate |

## File Creation Order

1. `imodent/analysis/__init__.py`
2. `imodent/analysis/context.py`
3. `imodent/analysis/findings.py`
4. `imodent/analysis/coordinator.py`
5. `imodent/graph/__init__.py`
6. `imodent/graph/imports.py`
7. `imodent/graph/dependency.py`
8. `imodent/graph/usage.py`
9. `imodent/analyzers/__init__.py`
10. `imodent/analyzers/base.py`
11. `imodent/analyzers/indentation.py`
12. `imodent/analyzers/imports.py`
13. `imodent/analyzers/lint.py`
14. `imodent/fixers/__init__.py`
15. `imodent/fixers/base.py`
16. `imodent/fixers/imports.py`
17. `imodent/fixers/lint.py`
18. `imodent/advisors/__init__.py`
19. `imodent/advisors/base.py`
20. `imodent/advisors/architecture.py`
21. Update `imodent/interfaces.py`
22. Update `imodent/registry.py`
23. Update `imodent/cli.py`
24. Update `pyproject.toml`
