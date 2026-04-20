# API Reference

**Source:** `imodent/`  
**Last Verified:** 2026-04-20  
**Tests:** 18 passing

---

## `FixPipeline`

Main entry point. Orchestrates detection → fixing → validation.

### Constructor

```python
FixPipeline(indent_size: int = 4)
```

**Source:** `imodent/pipeline.py`

### `fix(content: str) -> FixResult`

Auto-detect language and fix indentation.

```python
from imodent.pipeline import FixPipeline

pipeline = FixPipeline(indent_size=4)
result = pipeline.fix('def f():\nif True:\npass')

if result.success:
    print(result.content)
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

## `FixResult`

Typed result from all fix/validate operations.

```python
@dataclass
class FixResult:
    success: bool          # Did the operation succeed?
    content: str           # The (possibly fixed) content
    errors: List[str]      # Error messages
    warnings: List[str]    # Warning messages
    original_valid: bool   # Was the input valid?
    fixed_valid: bool      # Is the output valid?
```

**Source:** `imodent/interfaces.py`

---

## `LanguageStrategy` (Abstract Base)

Implement this to add a new language. Three required methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `detect` | `(content: str) -> bool` | Can this strategy handle the content? |
| `fix` | `(content: str, indent_size: int) -> FixResult` | Fix indentation |
| `validate` | `(content: str) -> Tuple[bool, str \| None]` | Validate syntax |

Plus two properties:

| Property | Type | Purpose |
|----------|------|---------|
| `name` | `str` | Unique identifier (e.g. `"python"`) |
| `extensions` | `List[str]` | File extensions (e.g. `[".py"]`) |

**Source:** `imodent/interfaces.py`

---

## `StrategyRegistry`

Centralized registry for language strategies. Strategies auto-register via decorator.

### `@StrategyRegistry.register`

```python
from imodent.interfaces import LanguageStrategy
from imodent.registry import StrategyRegistry

@StrategyRegistry.register
class MyStrategy(LanguageStrategy):
    ...
```

### `StrategyRegistry.get(name: str)`

Get a strategy class by name.

### `StrategyRegistry.get_by_extension(ext: str)`

Get a strategy class by file extension.

### `StrategyRegistry.all()`

List all registered strategy classes.

**Source:** `imodent/registry.py`

---

## `Processor` (Abstract Base)

For future functionality beyond indentation (linting, formatting, etc.).

```python
from imodent.interfaces import Processor, FixResult

class LintProcessor(Processor):
    @property
    def name(self) -> str:
        return "lint"

    def process(self, content: str, strategy: LanguageStrategy) -> FixResult:
        # Your logic here
        ...
```

**Source:** `imodent/interfaces.py`

---

## Built-in Strategies

### `PythonStrategy`

- **Name:** `"python"`
- **Extensions:** `.py`, `.pyw`, `.pyi`
- **Detection:** Python keywords + AST parse fallback
- **Fixing:** AST-based level detection, handles `def`, `class`, `if`, `for`, `while`, `with`, `try`, `match`, `async def`, `async for`, `async with`, decorators, continuation lines
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

---

## CLI

```bash
imodent <path> [options]
```

| Flag | Argument | Description | Default |
|------|----------|-------------|---------|
| `-i` | `INDENT` | Indent size (spaces) | `4` |
| `-b` | | Create `.bak` backup | off |
| `-n` | | Dry run (preview only) | off |
| `-c` | | Check only (no fix) | off |
| `-r` | | Recursive directory scan | off |

### Examples

```bash
imodent myfile.py --backup
imodent myfile.py --dry-run
imodent myfile.py --check
imodent ./src --recursive --indent 2 --backup
imodent config.json --backup
```

---

## Error Handling

| Error | Condition | Result |
|-------|-----------|--------|
| `FixResult.success=False` | Fix fails validation | Content still returned, errors populated |
| `FixResult.errors` | Non-empty list | Check for specific failure messages |
| `FixResult.warnings` | Non-empty list | Heuristic fallback used, etc. |

---

## Verification

```bash
# Run tests
pytest tests/test_modular.py -v

# Verify CLI
imodent -h

# Test idempotency
imodent /tmp/test.py --dry-run
```

**Last Verified:** 2026-04-20
