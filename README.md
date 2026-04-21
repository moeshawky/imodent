# imodent

Code intelligence tool — fix, scan, and advise on Python, JSON, JSONL, and YAML files.

## Features

- **Auto-detection**: Detects Python, JSON, JSONL, YAML automatically
- **AST-based fixing**: Python code fixed with Black, validated with `ast.parse()`
- **Import analysis**: Detect unused, duplicate, and misused imports — with 5 fix options (delete, keep, investigate, false-positive, keep-typing)
- **Architecture advisor**: Circular dependency detection, import clustering warnings
- **Pre-flight checks**: Refuses to "fix" structurally broken code (use `--force` to override)
- **Extensible**: Add new languages via `@StrategyRegistry.register` — no core changes needed
- **CLI**: Two modes — FIX (default) and SCAN (`--analyze`)

## Install

```bash
pip install -e .
# or system-wide:
pipx install .
```

## Usage

### FIX mode — reformat & repair files

```bash
# Fix a file with backup
imodent myfile.py --backup

# Preview changes without writing
imodent myfile.py --dry-run

# Syntax check only
imodent myfile.py --check

# Recursive directory scan
imodent ./src --recursive --indent 2 --backup

# Fix structurally broken code (skips pre-flight check)
imodent broken.py --force
```

### SCAN mode — project-wide analysis

```bash
# Find unused & duplicate imports
imodent ./src --analyze --imports

# Flag architectural issues
imodent ./src --analyze --advisory

# Auto-fix safe findings
imodent ./src --analyze --imports --fix

# Full audit + fix
imodent ./src --analyze --imports --advisory --fix
```

### Import analysis options

When `--imports` finds an unused import, you get 5 choices:

| Option | When to use |
|--------|-------------|
| **delete** | Import is genuinely unused |
| **keep** | Type hints, re-exports, `__all__` entries |
| **investigate** | Search codebase before deciding |
| **false-positive** | Analysis missed a usage (dynamic import, string reference) |

## Python API

```python
from imodent import FixPipeline

pipeline = FixPipeline(indent_size=4)

# Fix content (auto-detects language)
result = pipeline.fix(messy_code)
if result.success:
    print(result.content)
else:
    for error in result.errors:
        print(f"Error: {error}")

# Force fix on structurally broken code
result = pipeline.fix(broken_code, force=True)
```

### Analysis API

```python
from imodent import AnalysisCoordinator

coordinator = AnalysisCoordinator()
result = coordinator.analyze([Path("src/")])

for finding in result.findings:
    print(f"{finding.severity.value}: {finding.message}")
```

## Add a New Language

```python
from imodent.interfaces import LanguageStrategy, FixResult
from imodent.registry import StrategyRegistry


@StrategyRegistry.register
class TOMLStrategy(LanguageStrategy):
    @property
    def name(self) -> str:
        return "toml"

    @property
    def extensions(self) -> list[str]:
        return [".toml"]

    def detect(self, content: str) -> bool:
        return "[[" in content or "=" in content

    def fix(self, content: str, indent_size: int = 4, force: bool = False) -> FixResult:
        # Your fixing logic
        return FixResult(
            success=True,
            content=content,
            errors=[],
            warnings=[],
            original_valid=True,
            fixed_valid=True,
        )

    def validate(self, content: str) -> tuple[bool, str | None]:
        return True, None
```

No core changes needed — the strategy auto-registers and the pipeline picks it up.

## CLI Reference

| Flag | Mode | Description | Default |
|------|------|-------------|---------|
| `-i N` | FIX | Indent size (spaces) | `4` |
| `-b` | FIX | Create `.bak` backup | off |
| `-n` | FIX | Dry run (preview only) | off |
| `-c` | FIX | Check only (no fix) | off |
| `-r` | FIX | Recursive directory scan | off |
| `--force` | FIX | Attempt fix on structurally broken code | off |
| `--analyze` | SCAN | Enable project-wide analysis | off |
| `--imports` | SCAN | Detect import issues | off |
| `--lint` | SCAN | Run lint checks | off |
| `--advisory` | SCAN | Flag architectural issues | off |
| `--fix` | SCAN | Auto-fix safe findings | off |
| `--interactive` | SCAN | Prompt before each fix | off |
| `-v` | SCAN | Show all findings | off |

## Supported Formats

| Format | Detection | Fixing | Validation |
|--------|-----------|--------|------------|
| **Python** | Keywords + AST parse | Black → AST heuristic | `ast.parse()` |
| **JSON** | `{`/`[` + `json.loads()` | Pretty-print with indent | `json.loads()` |
| **JSONL** | Each line valid JSON | Compact each line | `json.loads()` per line |
| **YAML** | `---` or key patterns + `yaml.safe_load()` | Pretty-print with proper list indent | `yaml.safe_load()` |

## License

MIT
