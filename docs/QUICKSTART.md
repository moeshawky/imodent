# Quick Start

**Last Verified:** 2026-04-21

---

## Install

```bash
cd /srv/imodent
pip install -e .
# or system-wide:
pipx install .
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
# ↳ backup → /tmp/messy.py.bak
# ✓ /tmp/messy.py
```

### Step 4: Verify

```bash
imodent /tmp/messy.py --check
# ✓ /tmp/messy.py
```

---

## Scan Your Project

```bash
# Find unused & duplicate imports
imodent ./src --analyze --imports

# Flag architectural issues
imodent ./src --analyze --advisory

# Auto-fix safe findings
imodent ./src --analyze --imports --fix
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

# Fix structurally broken code
imodent broken.py --force

# Fix JSON
imodent config.json --backup

# Preview JSON fix
imodent config.json --dry-run
```

---

## Python API

```python
from imodent import FixPipeline, AnalysisCoordinator

# Fix code (auto-detects language)
pipeline = FixPipeline(indent_size=4)
result = pipeline.fix(messy_code)
if result.success:
    print(result.content)
else:
    for err in result.errors:
        print(f"Error: {err}")

# Force fix on structurally broken code
result = pipeline.fix(broken_code, force=True)

# Analyze project
coordinator = AnalysisCoordinator()
result = coordinator.analyze([Path("src/")])
for finding in result.findings:
    print(f"{finding.severity.value}: {finding.message}")
```

---

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

No core changes needed — the pipeline auto-discovers registered strategies.

---

## Troubleshooting

### "File has structural syntax error — cannot auto-fix"

The input has syntax errors beyond indentation (e.g., missing colons, invalid expressions). imodent refuses to run heuristic indentation on structurally broken code.

**Fix:** Repair the syntax error manually, then rerun. Or use `--force` to attempt heuristic fix anyway.

### "Could not parse AST" warning (with --force)

The heuristic indentation engine ran on a file where AST parsing failed. Output may not be correct.

**Fix:** Repair syntax manually, then re-run without `--force`.

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
pytest tests/ -v
# Expected: 161+ passed
```

**Last Verified:** 2026-04-21
