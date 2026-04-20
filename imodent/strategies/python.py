"""
Python Language Strategy.

Handles Python indentation fixing using AST-based validation.
"""
import ast
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..interfaces import LanguageStrategy, FixResult
from ..registry import StrategyRegistry


class ASTStructureVisitor:
    """Extracts structural indentation levels from the AST."""

    def __init__(self):
        self.line_to_level: Dict[int, int] = {}
        self.block_starts: set[int] = set()

    def visit_node(self, node: ast.AST, level: int):
        """Recursively visit AST nodes and record indentation level."""
        if hasattr(node, 'lineno'):
            self.line_to_level[node.lineno] = level

        # Compound statements that add a level
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.block_starts.add(node.lineno)
            for item in getattr(node, 'decorator_list', []):
                self.visit_node(item, level)
            for item in node.body:
                self.visit_node(item, level + 1)

        elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            self.block_starts.add(node.lineno)
            for item in node.body:
                self.visit_node(item, level + 1)
            for item in node.orelse:
                self.visit_node(item, level)
            for handler in getattr(node, 'handlers', []):
                for item in handler.body:
                    self.visit_node(item, level + 1)
            for item in getattr(node, 'finalbody', []):
                self.visit_node(item, level)

        elif isinstance(node, ast.Match):
            self.block_starts.add(node.lineno)
            for case in node.cases:
                if hasattr(case.pattern, 'lineno'):
                    self.line_to_level[case.pattern.lineno] = level + 1
                for item in case.body:
                    self.visit_node(item, level + 2)

        elif isinstance(node, ast.AsyncFor):
            self.block_starts.add(node.lineno)
            for item in node.body:
                self.visit_node(item, level + 1)
            for item in getattr(node, 'orelse', []):
                self.visit_node(item, level)
            for handler in getattr(node, 'handlers', []):
                for item in handler.body:
                    self.visit_node(item, level + 1)
            for item in getattr(node, 'finalbody', []):
                self.visit_node(item, level)

        elif isinstance(node, ast.AsyncWith):
            self.block_starts.add(node.lineno)
            for item in node.body:
                self.visit_node(item, level + 1)

        else:
            for child in ast.iter_child_nodes(node):
                self.visit_node(child, level)

    def run(self, tree: ast.AST):
        """Start the visitor on the AST tree."""
        self.visit_node(tree, 0)


@StrategyRegistry.register
class PythonStrategy(LanguageStrategy):
    """Strategy for fixing Python indentation."""

    @property
    def name(self) -> str:
        return "python"

    @property
    def extensions(self) -> List[str]:
        return ['.py', '.pyw', '.pyi']

    def detect(self, content: str) -> bool:
        """Detect if content is Python code."""
        stripped = content.strip()
        if not stripped:
            return False
        
        # Check for Python keywords at the start of lines
        python_indicators = ['def ', 'class ', 'import ', 'from ', 'if __name__', 'async def ', '@', 'return ', 'pass', 'break', 'continue']
        lines = stripped.split('\n')
        
        # Check first non-empty line
        for line in lines[:5]:  # Check first 5 lines
            line_stripped = line.strip()
            if any(line_stripped.startswith(kw) for kw in python_indicators):
                return True
            # Check for assignment or function call patterns
            if re.match(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*=', line_stripped):
                return True
            if re.match(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\(', line_stripped):
                return True
        
        # Try parsing as Python (most reliable)
        try:
            ast.parse(content)
            return True
        except SyntaxError:
            # Check if it looks like Python with indentation issues
            return bool(re.search(r'^\s*(def|class|if|for|while|with|try|async|elif|else|except|finally)\s', content, re.MULTILINE))

    def fix(self, content: str, indent_size: int = 4) -> FixResult:
        """Fix Python indentation using black first, then our AST logic as fallback."""
        errors = []
        warnings = []
        
        # Check original validity
        original_valid, original_error = self.validate(content)
        if not original_valid:
            if "indent" not in original_error.lower():
                warnings.append(f"Original code has syntax error: {original_error}")
        
        # STAGE 1: Try black first (it handles complex formatting)
        try:
            import black
            fixed = black.format_str(content, mode=black.Mode())
            
            # Validate black's output
            is_valid, error = self.validate(fixed)
            if is_valid:
                return FixResult(
                    success=True,
                    content=fixed,
                    errors=errors,
                    warnings=warnings,
                    original_valid=original_valid,
                    fixed_valid=True
                )
            else:
                warnings.append(f"Black output invalid: {error}, falling back to internal logic")
        except ImportError:
            warnings.append("black not installed, using internal AST logic")
        except Exception as e:
            warnings.append(f"Black failed: {e}, falling back to internal logic")
        
        # STAGE 2: Fallback to our AST-based fixer
        try:
            tree = ast.parse(content)
            visitor = ASTStructureVisitor()
            visitor.run(tree)
            ast_info = {'levels': visitor.line_to_level, 'block_starts': visitor.block_starts}
        except SyntaxError:
            ast_info = {'levels': {}, 'block_starts': set()}
            warnings.append("Could not parse AST, using heuristic indentation")
        
        fixed = self._reindent(content, ast_info, indent_size)
        
        # Validate fixed
        fixed_valid, fixed_error = self.validate(fixed)
        if not fixed_valid:
            errors.append(f"Fixed code still has error: {fixed_error}")
        
        return FixResult(
            success=fixed_valid,
            content=fixed,
            errors=errors,
            warnings=warnings,
            original_valid=original_valid,
            fixed_valid=fixed_valid
        )

    def validate(self, content: str) -> Tuple[bool, Optional[str]]:
        """Validate Python syntax."""
        try:
            ast.parse(content)
            return True, None
        except SyntaxError as e:
            return False, f"{type(e).__name__}: {e} (line {getattr(e, 'lineno', '?')})"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _reindent(self, content: str, ast_info: dict, indent_size: int) -> str:
        """Reindent content using AST levels."""
        lines = content.splitlines()
        fixed_lines: List[str] = []
        
        level_stack = [0]
        continuation_indent_stack: List[int] = []
        last_block_line = 0

        for lineno, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()

            if not stripped:
                if len(fixed_lines) > 0 and (lineno - last_block_line) <= 3:
                    fixed_lines.append("")
                continue

            if stripped.startswith('#'):
                indent = level_stack[-1] * indent_size if not fixed_lines or fixed_lines[-1].strip() else 0
                fixed_lines.append(" " * indent + stripped)
                continue

            if stripped.startswith('@'):
                fixed_lines.append(" " * (level_stack[-1] * indent_size) + stripped)
                last_block_line = lineno
                continue

            if continuation_indent_stack:
                indent = continuation_indent_stack[-1]
                fixed_lines.append(" " * indent + stripped)
                if any(c in stripped for c in ')]}'):
                    continuation_indent_stack.pop()
                continue

            level = ast_info['levels'].get(lineno, level_stack[-1])
            new_indent = level * indent_size
            fixed_lines.append(" " * new_indent + stripped)
            last_block_line = lineno

            if stripped.rstrip().endswith(':'):
                level_stack.append(level + 1)

            open_count = stripped.count('(') + stripped.count('[') + stripped.count('{')
            close_count = stripped.count(')') + stripped.count(']') + stripped.count('}')
            if open_count > close_count:
                open_pos = stripped.find('(') if '(' in stripped else \
                           stripped.find('[') if '[' in stripped else stripped.find('{')
                continuation_indent_stack.append(open_pos + 1)

        result = "\n".join(fixed_lines)
        result = re.sub(r'\n{4,}', '\n\n\n', result)
        return result + "\n"
