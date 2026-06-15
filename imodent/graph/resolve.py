"""Resolve undefined names to importable module paths.

When Ruff reports F821 "Undefined name ``SomeName``", this module
searches the project's AST for where ``SomeName`` is defined and
computes the import statement needed to bring it into scope.

.. note::

   ``_file_to_module`` delegates to :func:`imodent.graph.imports.resolve_module_name`
   (Pair 1 / C28 deduplication).  ``_common_ancestor`` has a near-identical sibling
   ``_find_common_root`` in :mod:`imodent.graph.dependency` (Pair 2 / C28) —
   consolidate if either implementation changes.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .imports import resolve_module_name

if TYPE_CHECKING:
    from ..analysis.context import FileInfo


@dataclass
class ImportSuggestion:
    """A resolved import for an undefined name.

    Attributes:
        name: The undefined symbol (e.g., ``"main"``).
        module: The module path to import from
            (e.g., ``"imodent.cli"``).
        import_stmt: The Python import statement to insert
            (e.g., ``"from imodent.cli import main"``).
        definition_file: Path to the file where the name is defined.
        definition_line: Line number of the definition (1-indexed).
        is_stdlib: True if the module is a standard library.
    """

    name: str
    module: str
    import_stmt: str
    definition_file: Path
    definition_line: int
    is_stdlib: bool = False


def resolve_undefined_name(
    # C27 CWD fallback comment: `# NOTE: Path.cwd() fallback is last-resort when files dict is empty. # Preferred project root discovery is via project_context.py.`
    name: str,
    files: dict[Path, FileInfo],
    project_root: Path | None = None,
) -> list[ImportSuggestion]:
    """Search project files for where *name* is defined.

    Walks the AST of every Python file in *files*, looking for
    top-level definitions (functions, classes, assignments) whose
    target name matches *name*.  Computes the import path for each
    match and returns a structured ``ImportSuggestion``.

    Args:
        name: The undefined symbol to resolve (e.g., ``"MyClass"``).
        files: A mapping of ``Path → FileInfo`` for all project files.
        project_root: Root directory for computing module paths.
            Defaults to the common ancestor of all file paths.

    Returns:
        A list of ``ImportSuggestion`` objects, one per definition
        site found.  Returns an empty list if *name* is not defined
        in any project file, or if no AST trees are available.

        When multiple files define a symbol with the same name,
        all matches are returned.  The caller should prefer matches
        in the same package or closest to the calling file.

    Note:
        Only definitions at module (top) level are considered.
        Nested functions, inner classes, and local variables are
        skipped because they cannot be imported from another module.
    """
    if project_root is None:
        # NOTE: Path.cwd() fallback is last-resort when files dict is empty.
        # Preferred project root discovery is via project_context.py.
        project_root = _common_ancestor(files.keys()) if files else Path.cwd()

    suggestions: list[ImportSuggestion] = []
    for file_path, file_info in files.items():
        if file_info.language != "python" or file_info.ast_tree is None:
            continue

        tree = file_info.ast_tree
        for node in tree.body:
            match node:
                case ast.FunctionDef() if node.name == name:
                    mod = _file_to_module(file_path, project_root)
                    stmt = _format_import(mod, name, as_name=None)
                    suggestions.append(
                        ImportSuggestion(
                            name=name,
                            module=mod,
                            import_stmt=stmt,
                            definition_file=file_path,
                            definition_line=node.lineno,
                        )
                    )
                case ast.ClassDef() if node.name == name:
                    mod = _file_to_module(file_path, project_root)
                    stmt = _format_import(mod, name, as_name=None)
                    suggestions.append(
                        ImportSuggestion(
                            name=name,
                            module=mod,
                            import_stmt=stmt,
                            definition_file=file_path,
                            definition_line=node.lineno,
                        )
                    )
                case ast.Assign() if _assigns_name(node, name):
                    mod = _file_to_module(file_path, project_root)
                    stmt = _format_import(mod, name, as_name=None)
                    suggestions.append(
                        ImportSuggestion(
                            name=name,
                            module=mod,
                            import_stmt=stmt,
                            definition_file=file_path,
                            definition_line=node.lineno,
                        )
                    )

    # If not found in project files, check if it's a known stdlib module
    if not suggestions and _is_stdlib_module(name):
        suggestions.append(
            ImportSuggestion(
                name=name,
                module=name,
                import_stmt=f"import {name}",
                definition_file=Path("(stdlib)"),
                definition_line=0,
                is_stdlib=True,
            )
        )

    return suggestions


def _file_to_module(file_path: Path, project_root: Path) -> str:
    """Convert a file path to a Python module name.

    Delegates to :func:`imodent.graph.imports.resolve_module_name`
    (C28 deduplication — Pair 1).  The fallback to ``file_path.stem``
    is preserved for the empty-path edge case.

    Args:
        file_path: Path to a ``.py`` file.
        project_root: Root directory of the project.

    Returns:
        Dotted module name (e.g., ``"imodent.graph.resolve"``).

        Falls back to the file stem if the path is not under
        *project_root*.
    """
    name = resolve_module_name(file_path, project_root)
    return name if name else file_path.stem


def _format_import(module: str, name: str, as_name: str | None = None) -> str:
    """Format an import statement for a specific name from a module.

    Args:
        module: The module path (e.g., ``"imodent.cli"``).
        name: The symbol to import.
        as_name: Optional alias for the imported name.

    Returns:
        A Python import statement string, either ``from <module> import <name>``
        or ``import <module>`` if *name* matches the module's top-level
        package and no from-import is meaningful.
    """
    top_level = module.split(".")[0] if "." in module else module
    if name == top_level and "." in module:
        # Prefer 'from pkg.sub import name' over 'import pkg.sub'
        return f"from {module.rsplit('.', 1)[0]} import {module.rsplit('.', 1)[1]}"
    if name == module:
        # Bare import: import os
        base = f"import {module}"
        if as_name:
            base += f" as {as_name}"
        return base
    base = f"from {module} import {name}"
    if as_name:
        base += f" as {as_name}"
    return base


def _assigns_name(node: ast.Assign, name: str) -> bool:
    """Check whether an assignment targets *name*.

    Handles both simple targets (``x = 1``) and tuple unpacking
    (``x, y = 1, 2``), but does not handle starred targets
    (``*x = ...``) or subscript/attribute targets.
    """
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == name:
            return True
        if isinstance(target, ast.Tuple):
            for elt in target.elts:
                if isinstance(elt, ast.Name) and elt.id == name:
                    return True
    return False


def _common_ancestor(paths: list[Path]) -> Path:
    """Find the deepest common parent directory of *paths*.

    If *paths* is empty, returns the current working directory.

    NOTE: Near-identical sibling ``_find_common_root`` exists in
    ``imodent.graph.dependency`` (C28 / Pair 2).  Consolidate into one
    canonical implementation if either logic changes.
    """
    if not paths:
        return Path.cwd()
    abs_paths = [p.resolve() for p in paths]
    common = abs_paths[0]
    for p in abs_paths[1:]:
        while common not in p.parents and common != p:
            if common.parent == common:
                return common
            common = common.parent
    return common


def _is_stdlib_module(name: str) -> bool:
    """Return True if *name* is a Python standard library module.

    Uses a best-effort allowlist of common stdlib names.  Does not
    import the module — import-time side effects are avoided.
    """
    _STDLIB = frozenset(
        {
            "abc",
            "aifc",
            "argparse",
            "array",
            "ast",
            "asynchat",
            "asyncio",
            "asyncore",
            "atexit",
            "audioop",
            "base64",
            "bdb",
            "binascii",
            "binhex",
            "bisect",
            "builtins",
            "bz2",
            "calendar",
            "cgi",
            "cgitb",
            "chunk",
            "cmath",
            "cmd",
            "code",
            "codecs",
            "codeop",
            "collections",
            "colorsys",
            "compileall",
            "concurrent",
            "configparser",
            "contextlib",
            "contextvars",
            "copy",
            "copyreg",
            "cProfile",
            "crypt",
            "csv",
            "ctypes",
            "curses",
            "dataclasses",
            "datetime",
            "dbm",
            "decimal",
            "difflib",
            "dis",
            "distutils",
            "doctest",
            "email",
            "encodings",
            "enum",
            "errno",
            "faulthandler",
            "fcntl",
            "filecmp",
            "fileinput",
            "fnmatch",
            "formatter",
            "fractions",
            "ftplib",
            "functools",
            "gc",
            "getopt",
            "getpass",
            "gettext",
            "glob",
            "grp",
            "gzip",
            "hashlib",
            "heapq",
            "hmac",
            "html",
            "http",
            "idlelib",
            "imaplib",
            "imghdr",
            "imp",
            "importlib",
            "inspect",
            "io",
            "ipaddress",
            "itertools",
            "json",
            "keyword",
            "lib2to3",
            "linecache",
            "locale",
            "logging",
            "lzma",
            "mailbox",
            "mailcap",
            "marshal",
            "math",
            "mimetypes",
            "mmap",
            "modulefinder",
            "multiprocessing",
            "netrc",
            "nis",
            "nntplib",
            "numbers",
            "operator",
            "optparse",
            "os",
            "ossaudiodev",
            "parser",
            "pathlib",
            "pdb",
            "pickle",
            "pickletools",
            "pipes",
            "pkgutil",
            "platform",
            "plistlib",
            "poplib",
            "posix",
            "posixpath",
            "pprint",
            "profile",
            "pstats",
            "pty",
            "pwd",
            "py_compile",
            "pyclbr",
            "pydoc",
            "queue",
            "quopri",
            "random",
            "re",
            "readline",
            "reprlib",
            "resource",
            "rlcompleter",
            "runpy",
            "sched",
            "secrets",
            "select",
            "selectors",
            "shelve",
            "shlex",
            "shutil",
            "signal",
            "site",
            "smtpd",
            "smtplib",
            "sndhdr",
            "socket",
            "socketserver",
            "sqlite3",
            "ssl",
            "stat",
            "statistics",
            "string",
            "stringprep",
            "struct",
            "subprocess",
            "sunau",
            "symtable",
            "sys",
            "sysconfig",
            "syslog",
            "tabnanny",
            "tarfile",
            "telnetlib",
            "tempfile",
            "termios",
            "test",
            "textwrap",
            "threading",
            "time",
            "timeit",
            "tkinter",
            "token",
            "tokenize",
            "trace",
            "traceback",
            "tracemalloc",
            "tty",
            "turtle",
            "turtledemo",
            "types",
            "typing",
            "unicodedata",
            "unittest",
            "urllib",
            "uu",
            "uuid",
            "venv",
            "warnings",
            "wave",
            "weakref",
            "webbrowser",
            "winreg",
            "winsound",
            "wsgiref",
            "xdrlib",
            "xml",
            "xmlrpc",
            "zipapp",
            "zipfile",
            "zipimport",
            "zlib",
        }
    )
    return name in _STDLIB
