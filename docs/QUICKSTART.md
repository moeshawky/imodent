# Quick Start

**Last Verified:** 2026-04-20

---

## Install

```bash
cd /srv/imodent
pip install -e .
```

## Your First Fix

### Step 1: Create a messy file

```bash
cat > /tmp/messy.py << 'EOF'
def hello():
if True:
print("world")
class Foo:
def bar(self):
pass
EOF
```

### Step 2: Preview

```bash
imodent /tmp/messy.py --dry-run
```

### Step 3: Fix with backup

```bash
imodent /tmp/messy.py --backup
# Output:
# Backup created: /tmp/messy.py.bak
# Fixed: /tmp/messy.py
```

### Step 4: Verify

```bash
imodent /tmp/messy.py --check
# Output: ✓ /tmp/messy.py
```

---

## Common Commands

```bash
# Fix entire project
imodent ./src --recursive --backup

# Custom indent (2 spaces)
imodent myfile.py --indent 2

# Check syntax only
imodent myfile.py --check

# Fix JSON
imodent config.json --backup

# Preview JSON fix
imodent config.json --dry-run
```

---

## Python API

```python
from imodent.pipeline import FixPipeline

pipeline = FixPipeline(indent_size=4)

# Fix code (auto-detects language)
result = pipeline.fix(messy_code)

if result.success:
    print(result.content)
else:
    for err in result.errors:
        print(f"Error: {err}")
```

---

## Add a New Language

```python
from imodent.interfaces import LanguageStrategy, FixResult
from imodent.registry import StrategyRegistry

@StrategyRegistry.register
class RustStrategy(LanguageStrategy):
    @property
    def name(self) -> str:
        return "rust"

    @property
    def extensions(self) -> list[str]:
        return ['.rs']

    def detect(self, content: str) -> bool:
        return 'fn ' in content

    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        # Your fixing logic
        return FixResult(success=True, content=content, errors=[], warnings=[],
                         original_valid=True, fixed_valid=True)

    def validate(self, content: str) -> tuple[bool, str | None]:
        return True, None
```

No core changes needed — the pipeline auto-discovers registered strategies.

---

## Troubleshooting

### "Could not parse AST" warning

The input has syntax errors beyond indentation. The tool falls back to heuristic indentation.

**Fix:** Repair syntax manually, then re-run.

### Wrong format detected

The pipeline checks JSON → JSONL → Python (most specific first).

**Fix:** Use the strategy directly:

```python
from imodent.strategies.json import JSONStrategy
strategy = JSONStrategy()
result = strategy.fix(content, indent_size=4)
```

---

## Verification

```bash
pytest tests/test_modular.py -v
# Expected: 18 passed
```

**Last Verified:** 2026-04-20
