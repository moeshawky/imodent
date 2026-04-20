# imodent

Smart indentation fixer — with extensible plugin architecture.

## Features

- **Auto-detection**: Detects Python, JSON, JSONL, YAML automatically
- **AST-based validation**: Python code validated with `ast.parse()` before and after fixing
- **Python 3.10+ support**: `async/await`, `match/case`, decorators, continuation lines
- **YAML support**: Proper list indentation via PyYAML
- **Extensible**: Add new languages via `@StrategyRegistry.register` — no core changes needed
- **CLI**: `imodent file.py --backup` after `pip install -e .`

## Install

```bash
pip install -e .
```

## Usage

```bash
# Fix a file with backup
imodent myfile.py --backup

# Preview changes
imodent myfile.py --dry-run

# Check only (no fix)
imodent myfile.py --check

# Recursive directory scan
imodent ./src --recursive --indent 2 --backup

# Fix JSON
imodent config.json --backup

# Fix YAML
imodent docker-compose.yml --backup
```

## Python API

```python
from imodent.pipeline import FixPipeline

pipeline = FixPipeline(indent_size=4)

# Fix content (auto-detects language)
result = pipeline.fix(messy_code)

if result.success:
    print(result.content)
else:
    for error in result.errors:
        print(f"Error: {error}")
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
        return ['.toml']

    def detect(self, content: str) -> bool:
        return '[[' in content or '=' in content

    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        # Your fixing logic
        return FixResult(success=True, content=content, errors=[], warnings=[],
                         original_valid=True, fixed_valid=True)

    def validate(self, content: str) -> tuple[bool, str | None]:
        return True, None
```

No core changes needed — the strategy auto-registers and the pipeline picks it up.

## CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `-i` | Indent size (spaces) | `4` |
| `-b` | Create `.bak` backup | off |
| `-n` | Dry run (preview only) | off |
| `-c` | Check only (no fix) | off |
| `-r` | Recursive directory scan | off |

## Supported Formats

| Format | Detection | Fixing | Validation |
|--------|-----------|--------|------------|
| **Python** | Keywords + AST parse | AST-based level detection | `ast.parse()` |
| **JSON** | `{`/`[` + `json.loads()` | Pretty-print with indent | `json.loads()` |
| **JSONL** | Each line valid JSON | Compact each line | `json.loads()` per line |
| **YAML** | `---` or key patterns + `yaml.safe_load()` | Pretty-print with proper list indent | `yaml.safe_load()` |

## License

MIT
