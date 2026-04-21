# Contract Definitions: imodent Expansion

## Core Data Types

### FileInfo
```python
@dataclass
class FileInfo:
    """Information about a single source file."""
    path: Path
    content: str
    language: str  # 'python', 'json', 'yaml', etc.
    encoding: str = 'utf-8'
    line_count: int = 0
    has_syntax_errors: bool = False
    ast: ast.AST | None = None  # Parsed AST for Python files
```

### Finding
```python
class Severity(Enum):
    ERROR = "error"      # Must fix - code won't run
    WARNING = "warning"  # Should fix - potential bug
    INFO = "info"        # Know about - style/optimization
    HINT = "hint"        # Suggestion - optional improvement

@dataclass
class Location:
    """Location in a source file."""
    line: int
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

@dataclass
class Finding:
    """A single issue or observation from analysis."""
    id: str                    # UUID for this finding
    type: str                  # Category identifier
    severity: Severity
    file: Path
    location: Location | None
    message: str               # Human-readable
    fixable: bool              # Can auto-fix?
    auto_fix_safe: bool        # Is auto-fix safe without review?
    data: dict = field(default_factory=dict)
    
    # Import-specific fields
    import_name: str | None = None
    import_module: str | None = None
    usage_count: int = 0
    usage_locations: list[Location] = field(default_factory=list)
    
    # Lint-specific fields
    lint_code: str | None = None  # e.g., "F401" for unused import
    lint_source: str | None = None  # e.g., "ruff", "pyright"
```

### FixOption
```python
@dataclass
class FixOption:
    """An option for fixing a finding."""
    id: str
    label: str              # Short label for UI
    description: str        # Full description
    action: str             # 'delete', 'keep', 'investigate', 'custom'
    is_safe: bool           # Can apply without review?
    preview: str | None     # Preview of change if applicable
    requires_input: bool    # Does this need user input?
```

### FixResult (expanded)
```python
@dataclass
class FixResult:
    """Result of a fix operation."""
    success: bool
    content: str                    # Fixed content
    original_content: str           # For diff
    errors: list[str]
    warnings: list[str]
    changes: list[Change]           # What was changed
    original_valid: bool
    fixed_valid: bool

@dataclass
class Change:
    """A single change made to content."""
    type: str               # 'add', 'remove', 'modify'
    location: Location
    old_text: str | None
    new_text: str | None
    reason: str
```

### Advice
```python
@dataclass
class Advice:
    """Advisory output for a finding or set of findings."""
    finding_ids: list[str]          # Related findings
    category: str                   # 'architecture', 'performance', 'style'
    summary: str                    # One-line summary
    explanation: str                # Detailed explanation
    recommendation: str             # What to do
    example: str | None             # Example fix or improvement
    impact: str                     # What happens if ignored
    priority: int                   # 1-10, higher = more important
```

## Interface Contracts

### Analyzer Interface
```python
class AnalyzerCapability(Enum):
    SYNTAX = "syntax"
    IMPORTS = "imports"
    LINT = "lint"
    TYPES = "types"
    STYLE = "style"

class Analyzer(ABC):
    """Base class for all analyzers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this analyzer."""
        ...
    
    @property
    @abstractmethod
    def capabilities(self) -> set[AnalyzerCapability]:
        """What this analyzer can detect."""
        ...
    
    @property
    def languages(self) -> set[str]:
        """Languages this analyzer handles. Default: all."""
        return set()  # Empty = all languages
    
    @property
    def requires_ast(self) -> bool:
        """Does this analyzer require parsed AST?"""
        return False
    
    @abstractmethod
    def analyze(self, context: AnalysisContext) -> list[Finding]:
        """
        Analyze files in context.
        
        Pre-conditions:
        - context.files is populated
        - If requires_ast, context.files[*].ast is populated
        
        Post-conditions:
        - Returns list of Finding objects
        - Each finding has unique id
        - No side effects on context
        
        Error handling:
        - On error, return finding with severity=ERROR
        - Never raise exceptions for analysis failures
        """
        ...
```

### Fixer Interface
```python
class Fixer(ABC):
    """Base class for all fixers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this fixer."""
        ...
    
    @property
    @abstractmethod
    def handles(self) -> set[str]:
        """Finding types this fixer can handle."""
        ...
    
    @abstractmethod
    def can_auto_fix(self, finding: Finding) -> bool:
        """
        Check if finding can be safely auto-fixed.
        
        Pre-conditions:
        - finding is not None
        
        Post-conditions:
        - Returns True if fix is safe without review
        - Never raises exceptions
        
        Safety criteria:
        - Fix is deterministic
        - Fix preserves semantics
        - Fix has no side effects on other code
        """
        ...
    
    @abstractmethod
    def get_options(self, finding: Finding, context: AnalysisContext) -> list[FixOption]:
        """
        Get fix options for a finding.
        
        Pre-conditions:
        - finding is not None
        - finding is in handles
        
        Post-conditions:
        - Returns at least one option
        - First option is safest/recommended
        - Each option has unique id
        
        Options for imports:
        - 'delete': Remove unused import
        - 'keep': Keep with @staticmethod or similar
        - 'investigate': Needs more analysis
        - 'use': Import is used (false positive)
        """
        ...
    
    @abstractmethod
    def apply_fix(self, finding: Finding, option: FixOption, content: str) -> FixResult:
        """
        Apply fix to content.
        
        Pre-conditions:
        - finding is not None
        - option is from get_options for this finding
        - content is the file content
        
        Post-conditions:
        - Returns FixResult with success status
        - If success, content is valid
        - If failure, errors list explains why
        
        Safety:
        - Never modify content in place
        - Always validate fix before returning
        """
        ...
```

### Advisor Interface
```python
class Advisor(ABC):
    """Base class for advisory modules."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier."""
        ...
    
    @property
    def priority(self) -> int:
        """Advisory priority (higher = more important)."""
        return 5
    
    @abstractmethod
    def should_advise(self, findings: list[Finding], context: AnalysisContext) -> bool:
        """
        Check if advisor has relevant advice.
        
        Pre-conditions:
        - findings may be empty
        - context is populated
        
        Post-conditions:
        - Returns True if advisor can contribute
        """
        ...
    
    @abstractmethod
    def advise(self, findings: list[Finding], context: AnalysisContext) -> list[Advice]:
        """
        Generate advice based on findings.
        
        Pre-conditions:
        - should_advise returned True
        
        Post-conditions:
        - Returns non-empty list
        - Each advice has unique finding_ids
        - Priority is assigned based on impact
        """
        ...
```

### AnalysisCoordinator Interface
```python
class AnalysisCoordinator:
    """Coordinates analysis across multiple files and analyzers."""
    
    def __init__(self, config: AnalysisConfig):
        """
        Initialize coordinator.
        
        Pre-conditions:
        - config is valid
            
        Post-conditions:
        - Analyzers registered
        - Ready to analyze
        """
        ...
    
    def analyze(
        self,
        paths: list[Path],
        analyzers: list[str] | None = None,
    ) -> AnalysisResult:
        """
        Run analysis on paths.
        
        Pre-conditions:
        - paths is non-empty
        - paths exist and are readable
            
        Post-conditions:
        - Returns AnalysisResult with:
          - files: dict of path -> FileInfo
          - graph: DependencyGraph
          - findings: list of Finding
          - errors: list of errors during analysis
            
        Error handling:
        - Missing files: logged, not in result
        - Parse errors: finding with severity=ERROR
        - Analyzer errors: logged, other analyzers continue
        """
        ...
    
    def fix(
        self,
        findings: list[Finding],
        mode: FixMode = FixMode.SAFE_AUTO,
    ) -> dict[Path, FixResult]:
        """
        Fix findings.
        
        Pre-conditions:
        - findings are from analyze()
        - mode is valid
            
        Post-conditions:
        - Returns dict of path -> FixResult
        - Only SAFE_AUTO mode auto-fixes without review
        - All fixes validated before return
            
        FixMode values:
        - SAFE_AUTO: Only fix auto_fix_safe findings
        - ALL_AUTO: Fix all fixable findings
        - INTERACTIVE: Present options for each finding
        - REPORT: Don't fix, just report
        """
        ...
```

## Error Contracts

### AnalysisError
```python
@dataclass
class AnalysisError:
    """Error during analysis."""
    analyzer: str           # Which analyzer failed
    file: Path | None       # File being analyzed, if any
    error_type: str         # Exception type
    message: str            # Error message
    recoverable: bool       # Can analysis continue?
```

### FixError
```python
@dataclass
class FixError:
    """Error during fixing."""
    finding_id: str         # Finding being fixed
    fixer: str              # Which fixer
    error_type: str
    message: str
    content_preserved: bool  # Was original content preserved?
```

## Event Contracts

### AnalysisProgress
```python
@dataclass
class AnalysisProgress:
    """Progress event during analysis."""
    phase: str              # 'discovery', 'parsing', 'analyzing', 'done'
    current: int
    total: int
    current_file: str | None
```

### FixProgress
```python
@dataclass
class FixProgress:
    """Progress event during fixing."""
    finding_id: str
    status: str             # 'started', 'fixed', 'skipped', 'error'
    message: str | None
```
