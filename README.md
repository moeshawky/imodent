# imodent

Smart Python + JSON/JSONL indentation fixer with AST-based validation.

## Features

- **Auto-detection**: Automatically detects Python, JSON, or JSONL format
- **AST-based validation**: Ensures code remains syntactically correct after fixing
- **Python 3.10+ support**: Handles `async/await`, `match/case`, and all modern syntax
- **Smart indentation**: Uses AST structure to determine correct indentation levels
- **CLI interface**: Supports backup, dry-run, check, and recursive modes

## Installation

```bash
pip install .
# or
imodent <file>
```

## Usage

### Fix a single file
```bash
imodent myfile.py
```

### Preview changes (dry-run)
```bash
imodent myfile.py --dry-run
```

### Create backup before fixing
```bash
imodent myfile.py --backup
```

### Check only (don't fix)
```bash
imodent myfile.py --check
```

### Process directory recursively
```bash
imodent ./src --recursive --backup
```

### Custom indent size
```bash
imodent myfile.py --indent 2
```

## Supported Formats

### Python
- Fixes indentation based on AST structure
- Handles decorators, docstrings, and continuation lines
- Supports Python 3.10+ `match/case` syntax
- Validates with `ast.parse()` before and after fixing

### JSON
- Pretty-prints minified JSON
- Configurable indent size
- Validates with `json.loads()`

### JSONL (JSON Lines)
- Fixes each line independently
- Maintains one JSON object per line
- Validates each line

## API Usage

```python
from imodent import IndentationFixer

fixer = IndentationFixer(default_indent=4)

# Fix code
fixed_code = fixer.fix(messy_code)

# Check AST validity
is_valid, error = fixer.check_ast(code)

# Fix file with options
from pathlib import Path
fixer.fix_file(Path("myfile.py"), backup=True, dry_run=False)
```

## License

MIT
