# API Reference

**Source:** `imodent/`
**Last Verified:** 2026-04-21
**Tests:** 161+ passing

---

## `FixPipeline`

Main entry point. Orchestrates detection → fixing → validation.

### Constructor

```python
FixPipeline(indent_size: int = 4)
```

**Source:** `imodent/pipeline.py`

### `fix(content: str, strategy=None, force: bool = False) -> FixResult`

Auto-detect language and fix indentation.

- `force=False`: If file has non-indentation syntax errors, abort immediately with diagnostic
- `force=True`: Attempt heuristic fix even on structurally broken code

```python
from imodent import FixPipeline

pipeline = FixPipeline(indent_size=4)
result = pipeline.fix('def f():\nif True:\npass')
if result.success:
    print(result.content)
else:
    for err in result.errors:
        print(f"Error: {err}")

# Force fix on broken code
result = pipeline.fix(broken_code, force=True)
```

### `validate(content: str) -> FixResult`

Validate without fixing.

```python
result = pipeline.validate('def f():\n    pass')
print("✓" if result.success else "✗")
```

### `detect(content: str) -> LanguageStrategy | None`

Detect the language strategy for the content.

```python
strategy = pipeline.detect('{"a":1}')
print(strategy.name)  # "json"
```

---

## `AnalysisCoordinator`

Multi-file analysis orchestrator. Coordinates analyzers, fixers, and advisors.

### `analyze(paths: list[Path]) -> AnalysisResult`

Scan files for import issues, lint violations, architectural drift.

```python
from imodent import AnalysisCoordinator

coordinator = AnalysisCoordinator()
result = coordinator.analyze([Path("src/")])
for finding in result.findings:
    print(f"{finding.severity.value}: {finding.message}")
```

### `fix(findings, context, mode=FixMode.SAFE_AUTO) -> dict[Path, FixResult]`

Apply fixes to findings. Modes: `SAFE_AUTO`, `INTERACTIVE`, `REPORT`.

**Source:** `imodent/analysis/coordinator.py`

---

## `FixResult`

Typed result from all fix/validate operations.

```python
@dataclass
class FixResult:
    success: bool         # Did the operation succeed?
    content: str          # The (possibly fixed) content
    errors: List[str]     # Error messages
    warnings: List[str]   # Warning messages
    original_valid: bool  # Was the input valid?
    fixed_valid: bool     # Is the output valid?
```

**Source:** `imodent/interfaces.py`

---

## Analysis Types

### `Finding`

```python
Finding.create(
    type="unused_import",
    severity=Severity.WARNING,
    file=Path("src/mod.py"),
    message="'os' imported but never used",
    location=Location(line=3),
    fixable=True,
    auto_fix_safe=False,  # needs review
)
```

### `Severity`

Enum: `ERROR`, `WARNING`, `INFO`, `HINT`

### `FixOption`

```python
FixOption(id="delete", label="Delete import", description="Remove unused import",
          action="delete", is_safe=True)
```

**Source:** `imodent/analysis/findings.py`

---

## `LanguageStrategy` (Abstract Base)

Implement this to add a new language. Three required methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `detect` | `(content: str) -> bool` | Can this strategy handle the content? |
| `fix` | `(content: str, indent_size: int, force: bool = False) -> FixResult` | Fix indentation |
| `validate` | `(content: str) -> Tuple[bool, str \| None]` | Validate syntax |

Plus two properties:

| Property | Type | Purpose |
|----------|------|---------|
| `name` | `str` | Unique identifier (e.g. `"python"`) |
| `extensions` | `List[str]` | File extensions (e.g. `[".py"]`) |

**Source:** `imodent/interfaces.py`

---

## `StrategyRegistry`

Centralized registry for language strategies. Strategies auto-register via decorator. Lazy-loads builtins on first access.

### `@StrategyRegistry.register`

```python
from imodent.interfaces import LanguageStrategy
from imodent.registry import StrategyRegistry


@StrategyRegistry.register
class MyStrategy(LanguageStrategy):
    ...
```

### `StrategyRegistry.get(name: str)`

Get a strategy class by name. Triggers lazy load of builtins on first call.

### `StrategyRegistry.get_by_extension(ext: str)`

Get a strategy class by file extension.

### `StrategyRegistry.all()`

List all registered strategy classes.

### `StrategyRegistry.clear()`

Remove all strategies. Used for testing isolation.

**Source:** `imodent/registry.py`

---

## Analyzers

### `ImportAnalyzer`

Detects unused, duplicate, and misused imports in Python files.

- Unused imports: imported but never referenced in code
- Duplicate imports: same module imported twice

**Source:** `imodent/analyzers/imports.py`

### `Analyzer` (Abstract Base)

```python
class Analyzer(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> set[AnalyzerCapability]: ...

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> list[Finding]: ...
```

**Source:** `imodent/analyzers/base.py`

---

## Fixers

### `ImportFixer`

Applies fixes for import findings. 5 options per unused import:

| Option | Action | Safe? |
|--------|--------|-------|
| Delete | Remove the import | Yes (for genuinely unused) |
| Keep (typing) | Preserve for type hints | Yes |
| Keep (reason) | Preserve with comment | Yes |
| Investigate | Defer decision | Yes |
| False positive | Mark as used | Yes |

**Source:** `imodent/fixers/imports.py`

---

## Advisors

### `ArchitectureAdvisor`

Flags architectural issues: circular imports, import clustering, module coupling.

**Source:** `imodent/advisors/architecture.py`

---

## Built-in Strategies

### `PythonStrategy`

- **Name:** `"python"`
- **Extensions:** `.py`, `.pyw`, `.pyi`
- **Detection:** Python keywords + AST parse fallback
- **Fixing:** Black → AST heuristic fallback
- **Pre-flight:** Refuses to fix structurally broken code unless `force=True`
- **Validation:** `ast.parse()`

**Source:** `imodent/strategies/python.py`

### `JSONStrategy`

- **Name:** `"json"`
- **Extensions:** `.json`
- **Detection:** Starts with `{` or `[`, parses with `json.loads()`
- **Fixing:** Pretty-print with configurable indent
- **Validation:** `json.loads()`

**Source:** `imodent/strategies/json.py`

### `JSONLStrategy`

- **Name:** `"jsonl"`
- **Extensions:** `.jsonl`, `.ndjson`
- **Detection:** Multiple lines, each valid JSON
- **Fixing:** Compact each line independently
- **Validation:** `json.loads()` per line

**Source:** `imodent/strategies/jsonl.py`

### `YAMLStrategy`

- **Name:** `"yaml"`
- **Extensions:** `.yaml`, `.yml`
- **Detection:** `---` or key patterns + `yaml.safe_load()`
- **Fixing:** Pretty-print with proper list indentation
- **Validation:** `yaml.safe_load()`

**Source:** `imodent/strategies/yaml.py`

---

## CLI

```bash
imodent <path> [options]
```

### FIX mode

| Flag | Argument | Description | Default |
|------|----------|-------------|---------|
| `-i` | `N` | Indent size (spaces) | `4` |
| `-b` | | Create `.bak` backup | off |
| `-n` | | Dry run (preview only) | off |
| `-c` | | Check only (no fix) | off |
| `-r` | | Recursive directory scan | off |
| `--force` | | Attempt fix on broken code | off |

### SCAN mode

| Flag | Description | Default |
|------|-------------|---------|
| `--analyze` | Enable project-wide analysis | off |
| `--imports` | Detect import issues | off |
| `--lint` | Run lint checks | off |
| `--advisory` | Flag architectural issues | off |
| `--fix` | Auto-fix safe findings | off |
| `--interactive` | Prompt before each fix | off |
| `-v` | Show all findings | off |

---

## Error Handling

| Error | Condition | Result |
|-------|-----------|--------|
| `success=False` | Fix fails validation | Content still returned, errors populated |
| "structural syntax error" | Non-indentation SyntaxError without `--force` | Abort immediately, suggest `--force` |
| `errors` list | Non-empty | Check for specific failure messages |
| `warnings` list | Non-empty | Heuristic fallback used, etc. |

---

## Verification

```bash
# Run full test suite
pytest tests/ -v
# Expected: 161+ passed

# Verify CLI
imodent -h

# Test pre-flight guard
echo 'def f():\n  x =' > /tmp/broken.py
imodent /tmp/broken.py
# Expected: "File has structural syntax error — cannot auto-fix."
```

**Last Verified:** 2026-04-21
