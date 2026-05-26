# imodent

Code intelligence tool — fix, scan, and advise on Python, JSON, JSONL, and YAML files.

imodent is a fast first-look mapper and conservative fixer. It is useful for
finding syntax blockers, import hygiene issues, lint-backed risks, and areas
that need review. It does not replace deeper codegraph or model-assisted code
understanding.

## Features

- **Auto-detection**: Detects Python, JSON, JSONL, YAML automatically
- **AST-based fixing**: Python code fixed with Black, validated with `ast.parse()`
- **Import analysis**: Detect unused, duplicate, and misused imports — with 5 fix options (delete, keep, investigate, false-positive, keep-typing)
- **Ruff-backed lint evidence**: Attach external lint diagnostics to scan findings
- **Confidence output**: Show decision candidates with evidence, confidence, and destructive-action policy
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

# Review without modifying files
imodent ./src --report

# Add Ruff-backed lint evidence
imodent ./src --analyze --imports --lint --report

# Show confidence-weighted decision candidates
imodent ./src --analyze --imports --lint --confidence --report

# Flag architectural issues
imodent ./src --analyze --advisory

# Auto-fix safe findings (analysis implied)
imodent ./src --fix

# Auto-fix safe findings with explicit import analysis
imodent ./src --analyze --imports --fix

# Full audit + fix
imodent ./src --analyze --imports --advisory --fix
```

SCAN mode always recurses into subdirectories. `--report`, `--confidence`,
`--imports`, `--lint`, `--advisory`, `--interactive`, and `--fix` all route to
SCAN mode. `-b`, `-n`, and `-c` also work with scan fixes. `-r` and `--force`
are FIX-mode controls; when used with scan flags, imodent prints a note.

### Import analysis options

When `--imports` finds an unused import, interactive mode can offer review
choices such as:

| Option | When to use |
|--------|-------------|
| **delete** | Import is genuinely unused |
| **keep** | Type hints, re-exports, `__all__` entries |
| **keep-typing** | Import is kept specifically for type annotations |
| **investigate** | Search codebase before deciding |
| **false-positive** | Analysis missed a usage (dynamic import, string reference) |

Non-interactive `--fix` only applies findings marked safe by the fixer. Most
unused-import candidates remain review-only unless the tool has enough evidence
to avoid deleting intent.

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
from pathlib import Path

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
| `--report` | SCAN | Review findings without modifying files | off |
| `--confidence` | SCAN | Show evidence-backed decision candidates | off |
| `-v` | SCAN | Show all findings | off |

`-b`, `-n`, and `-c` are shared controls when scan fixes are enabled. `--check`
prevents `--fix` from applying changes and prints a note explaining the conflict.

## Supported Formats

| Format | Detection | Fixing | Validation |
|--------|-----------|--------|------------|
| **Python** | Keywords + AST parse | Black → AST heuristic | `ast.parse()` |
| **JSON** | `{`/`[` + `json.loads()` | Pretty-print with indent | `json.loads()` |
| **JSONL** | Each line valid JSON | Compact each line | `json.loads()` per line |
| **YAML** | `---` or key patterns + `yaml.safe_load()` | Pretty-print with proper list indent | `yaml.safe_load()` |

## Known Limitations

- Confidence is rule-based, not proof of developer intent.
- Fragmented workspaces with multiple import roots can produce ambiguous import findings.
- Type-only or quoted annotation issues may need human review even when Ruff reports them as `F821`.
- Default scan discovery skips common generated artifacts and test files; run targeted paths when you want to inspect those.
- Use codegraph or manual review for deep impact analysis. imodent is the fast mapper and conservative hygiene layer.

## License

MIT
