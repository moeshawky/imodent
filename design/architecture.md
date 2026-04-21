# Architecture Decision Record: imodent Expansion

## ADR-001: Modular Analyzer/Fixer Pipeline

### Context
imodent currently handles indentation only. We need to expand to:
- Import analysis (unused, duplicates, missing)
- Lint detection and fixing
- Multi-file/module analysis
- Advisory capabilities

### Decision
**Pattern: Plugin Pipeline with Analysis Graph**

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI / API Entry                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Analysis Coordinator                      │
│  - Builds dependency graph                                   │
│  - Coordinates analyzer passes                               │
│  - Aggregates findings                                       │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   Analyzers   │     │   Analyzers   │     │   Analyzers   │
│  (per-file)   │     │  (cross-file) │     │  (advisory)   │
├───────────────┤     ├───────────────┤     ├───────────────┤
│ Indentation   │     │ Import Graph  │     │ Architectural │
│ Syntax        │     │ Usage Trace   │     │ Patterns      │
│ Lint (ruff)   │     │ Dep Cycles    │     │ Suggestions   │
│ Type (pyright)│     │               │     │               │
└───────────────┘     └───────────────┘     └───────────────┘
        │                     │                     │
        └─────────────────────┴─────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Findings Aggregator                     │
│  - Deduplicates issues                                       │
│  - Prioritizes by severity                                   │
│  - Groups by file/module                                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       Fix Pipeline                           │
│  - Auto-fixes safe issues                                    │
│  - Presents options for ambiguous                            │
│  - Validates fixes before writing                            │
└─────────────────────────────────────────────────────────────┘
```

### Rationale

**Why this pattern:**

1. **Plugin-based**: Each analyzer is independent, implementing a common interface
2. **Pipeline**: Analyzers run in sequence, findings aggregated
3. **Separation of concerns**: Analysis ≠ Fixing ≠ Advisory
4. **Extensible**: New analyzers added via registration (like current strategies)

**Why NOT monolithic:**
- G-SINK violation: Would mix unrelated concerns
- Harder to test, harder to extend
- Violates single responsibility

**Why NOT microservices:**
- Overkill for a CLI tool
- No distribution requirement
- Adds unnecessary complexity

### Module Structure

```
imodent/
├── __init__.py              # Public API exports
├── cli.py                   # CLI entry point (expanded)
├── pipeline.py              # Analysis coordination (NEW)
├── interfaces.py            # ABCs for all components
├── registry.py              # Plugin registration (expanded)
│
├── analyzers/               # Analysis plugins
│   ├── __init__.py
│   ├── base.py              # Analyzer ABC
│   ├── indentation.py       # Current logic (migrated)
│   ├── syntax.py            # AST-based syntax checks
│   ├── imports.py           # Import analysis (NEW)
│   ├── lint.py              # External linter wrapper (NEW)
│   └── types.py             # Type checking wrapper (NEW)
│
├── fixers/                  # Fixing plugins
│   ├── __init__.py
│   ├── base.py              # Fixer ABC
│   ├── indentation.py       # Current logic (migrated)
│   ├── imports.py           # Import fixing (NEW)
│   └── lint.py              # Auto-fix from linters (NEW)
│
├── advisors/                # Advisory plugins
│   ├── __init__.py
│   ├── base.py              # Advisor ABC
│   └── architecture.py      # Architectural advice (NEW)
│
├── graph/                   # Multi-file analysis
│   ├── __init__.py
│   ├── dependency.py        # Import dependency graph (NEW)
│   └── usage.py             # Symbol usage tracing (NEW)
│
├── strategies/              # Current language strategies
│   └── ... (unchanged)
│
└── utils/                   # Shared utilities
    ├── __init__.py
    ├── backup.py            # Backup management
    └── validation.py        # Pre-write validation
```

### Guardrail Verification

| Rule | Status | Evidence |
|------|--------|----------|
| G-SCOPE-1 | ✓ | Each module traces to a requirement |
| G-PATTERN-1 | ✓ | Plugin pattern justified by extensibility requirement |
| G-SIMPLE-1 | ✓ | No simpler design provides extensibility |
| G-CONTRACT-1 | ✓ | Each module has defined interface |

### Failure Mode Scan

| Code | Check | Result |
|------|-------|--------|
| F-ABS | Premature abstraction? | No - each module has 2+ implementations planned |
| F-RESUME | Resume-driven? | No - pattern justified by R-05 |
| F-DIAGRAM | Diagram-only? | No - interfaces defined below |
| F-COPY | Copy architecture? | No - original design for this problem |
| F-SINK | Kitchen sink? | No - clear separation of concerns |
| F-SPEC | Speculative generality? | No - all modules trace to requirements |

### Component Interfaces

```python
# interfaces.py (expanded)

class Analyzer(ABC):
    """Base class for all analyzers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Analyzer name for registration."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> set[AnalyzerCapability]:
        """What this analyzer can detect."""
        pass
    
    @abstractmethod
    def analyze(self, context: AnalysisContext) -> list[Finding]:
        """Run analysis on the context.
        
        Args:
            context: Contains files, graph, previous findings
            
        Returns:
            List of findings (issues, suggestions, info)
        """
        pass


class Fixer(ABC):
    """Base class for all fixers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Fixer name for registration."""
        pass
    
    @property
    @abstractmethod
    def handles(self) -> set[str]:
        """Finding types this fixer can handle."""
        pass
    
    @abstractmethod
    def can_auto_fix(self, finding: Finding) -> bool:
        """Check if this finding can be safely auto-fixed."""
        pass
    
    @abstractmethod
    def fix(self, finding: Finding, content: str) -> FixResult:
        """Apply the fix to content."""
        pass
    
    @abstractmethod
    def get_options(self, finding: Finding) -> list[FixOption]:
        """Get options for ambiguous findings."""
        pass


class Advisor(ABC):
    """Base class for advisory modules."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Advisor name."""
        pass
    
    @abstractmethod
    def advise(self, findings: list[Finding], context: AnalysisContext) -> list[Advice]:
        """Generate advice based on findings."""
        pass


class AnalysisContext:
    """Shared context for all analyzers."""
    
    files: dict[str, FileInfo]          # Path -> file info
    graph: DependencyGraph              # Import relationships
    findings: list[Finding]             # Accumulated findings
    config: AnalysisConfig              # User preferences
    
    def get_file(self, path: str) -> FileInfo | None: ...
    def get_importers(self, module: str) -> list[str]: ...
    def get_importees(self, module: str) -> list[str]: ...
    def trace_symbol(self, symbol: str) -> list[SymbolUsage]: ...


@dataclass
class Finding:
    """A single issue or observation from analysis."""
    
    id: str                    # Unique identifier
    type: str                  # Category (unused_import, syntax_error, etc.)
    severity: Severity         # ERROR, WARNING, INFO, HINT
    file: str                  # File path
    location: Location | None  # Line/column if applicable
    message: str               # Human-readable description
    fixable: bool              # Can be auto-fixed?
    data: dict                 # Additional structured data
    
    # For import findings
    import_name: str | None = None
    usage_count: int = 0
    usage_locations: list[str] | None = None

`
```

### Dependencies to Add

```toml
# pyproject.toml additions
dependencies = [
    # Existing
    "black>=23.0",
    "ruamel.yaml>=0.18",
    "json-repair>=0.20",
    
    # New - Analysis tools
    "ruff>=0.1.0",           # Fast linter (replaces flake8, isort, autoflake)
    "pyright>=1.1.0",        # Type checking
]

# Note: ruff handles:
# - Linting (flake8, pycodestyle, pyflakes)
# - Import sorting (isort)
# - Unused imports (autoflake feature)
# - Auto-fixing
```

### Implementation Order

**Phase 1: Core Infrastructure**
1. Define expanded interfaces.py
2. Create AnalysisContext and Finding types
3. Create AnalysisCoordinator
4. Migrate current indentation to analyzer pattern

**Phase 2: Import Analysis**
1. Build dependency graph module
2. Create ImportAnalyzer
3. Create ImportFixer with options
4. Integrate with coordinator

**Phase 3: Lint Integration**
1. Create LintAnalyzer (ruff wrapper)
2. Create LintFixer (auto-fix where safe)
3. Add to pipeline

**Phase 4: Advisory**
1. Create ArchitectureAdvisor
2. Create advisory output format
3. Integrate with CLI

### Open Questions

1. **Q: Should advisors be interactive or report-only?**
   - A: Report-only initially, interactive as future enhancement
   
2. **Q: How to handle multi-language projects?**
   - A: Language-specific analyzers, unified finding format
   
3. **Q: How detailed should recommendations be?**
   - A: Actionable + explanation + example fix
