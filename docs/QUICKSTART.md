# Quick Start Guide

**Last Verified:** 2026-04-20  
**Test Coverage:** 31 tests passing

---

## Installation

No installation required! The tool is a single Python file.

```bash
# Clone or copy the repository
cd /srv/imodent

# Verify Python 3.10+ (required for match/case support)
python3 --version
# Expected: Python 3.10+
```

---

## Your First Fix (2 Minutes)

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

### Step 2: Preview the fix

```bash
imodent /tmp/messy.py --dry-run
```

**Expected Output:**
```
--- /tmp/messy.py ---
def hello():
    if True:
        print("world")
class Foo:
    def bar(self):
        pass
```

### Step 3: Apply the fix with backup

```bash
imodent /tmp/messy.py --backup
```

**Expected Output:**
```
Backup created: /tmp/messy.py.bak
Fixed: /tmp/messy.py
```

### Step 4: Verify the result

```bash
python3 -m py_compile /tmp/messy.py && echo "✓ Syntax OK"
```

**Expected Output:**
```
✓ Syntax OK
```

---

## Common Use Cases

### Fix an entire project

```bash
imodent ./src --recursive --backup
```

**What it does:**
- Scans `./src` and all subdirectories
- Fixes all `.py`, `.json`, `.jsonl` files
- Creates `.bak` backups before modifying

### Fix with custom indent size (2 spaces)

```bash
imodent myfile.py --indent 2
```

### Check syntax without modifying

```bash
imodent myfile.py --check
```

**Expected Output:**
```
✓ myfile.py
```
or
```
✗ myfile.py
```

### Fix JSON configuration files

```bash
imodent config.json --backup
```

**Input:**
```json
{"name":"myapp","version":"1.0.0","dependencies":{"express":"^4.0.0"}}
```

**Output:**
```json
{
    "name": "myapp",
    "version": "1.0.0",
    "dependencies": {
        "express": "^4.0.0"
    }
}
```

---

## Python 3.10+ Features

The tool fully supports modern Python syntax:

### Async/Await

```python
async def fetch():
    async with aiohttp.ClientSession() as session:
        async for item in session.iter_chunks():
            match item.type:
                case 'data':
                    print(item.data)
                case 'error':
                    raise Exception(item.error)
```

### Match/Case

See above example - the tool correctly handles Python 3.10+ `match` statements.

---

## Using as a Library

```python
from imodent import IndentationFixer
from pathlib import Path

# Create fixer instance
fixer = IndentationFixer(default_indent=4)

# Fix code string
messy_code = '''def f():
if True:
print("hello")'''
fixed_code = fixer.fix(messy_code)

# Validate syntax
is_valid, error = fixer.check_ast(fixed_code)
if not is_valid:
    print(f"Syntax error: {error}")

# Fix file with options
fixer.fix_file(
    Path("myfile.py"),
    backup=True,      # Create .bak file
    dry_run=False,    # Actually write changes
    check_only=False  # Modify the file
)
```

---

## Troubleshooting

### "AST ERROR AFTER FIX" appears in output

**Cause:** The original code has syntax errors that can't be fixed by indentation alone.

**Solution:**
1. Check the error message for the specific line
2. Fix the syntax error manually
3. Re-run the tool

```bash
# Example error
# AST ERROR AFTER FIX: IndentationError: expected an indented block (line 3)
def f():
if True:
print("x")  # ← Missing indentation
```

### File is not being detected as Python

**Cause:** The file doesn't start with Python keywords (`def`, `class`, etc.).

**Solution:**
- Add a Python shebang or import at the top
- Or rename the file with `.py` extension

### Backup file not created

**Cause:** `--backup` flag not used.

**Solution:**
```bash
imodent myfile.py --backup
```

---

## Next Steps

- **API Reference**: See `docs/API.md` for complete method documentation
- **Tests**: Run `pytest tests/` to verify your installation
- **Contributing**: See `CONTRIBUTING.md` (if applicable)

---

## Verification

Run this command to verify the tool is working correctly:

```bash
pytest tests/test_imodent.py -v
```

**Expected:** 31 passed

**Last Verified:** 2026-04-20
