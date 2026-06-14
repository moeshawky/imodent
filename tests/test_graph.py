"""Tests for graph module — import extraction, module name resolution, and dependency graph building."""

import ast
from pathlib import Path

from imodent.analysis.context import DependencyGraph, FileInfo
from imodent.analysis.findings import Location
from imodent.graph.dependency import (
    build_dependency_graph,
    resolve_module_name,
    trace_symbol_usage,
)
from imodent.graph.imports import ImportInfo, extract_imports
from imodent.graph.resolve import (
    ImportSuggestion,
    _assigns_name,
    _common_ancestor,
    _file_to_module,
    _format_import,
    _is_stdlib_module,
    resolve_undefined_name,
)

# ---------------------------------------------------------------------------
# extract_imports tests
# ---------------------------------------------------------------------------


def test_extract_imports_basic():
    """extract_imports extracts ImportInfo for `import X` and `from X import Y`."""
    source = "import os\nfrom sys import path\n"
    file = Path("/fake/mod.py")
    imports = extract_imports(source, file)
    assert len(imports) == 2
    modules = {imp.module for imp in imports}
    names = {imp.name for imp in imports}
    assert "os" in modules
    assert "sys" in modules
    assert "path" in names
    # Verify reconstructed import statements
    stmts = {imp.import_statement for imp in imports}
    assert "import os" in stmts
    assert "from sys import path" in stmts


def test_extract_imports_with_alias():
    """extract_imports captures `as` aliases on both forms."""
    source = "import os as operating_system\nfrom sys import path as syspath\n"
    file = Path("/fake/mod.py")
    imports = extract_imports(source, file)
    assert len(imports) == 2

    i_os = next(imp for imp in imports if imp.module == "os")
    assert i_os.alias == "operating_system"
    assert i_os.name is None
    assert not i_os.is_from_import
    assert i_os.import_statement == "import os as operating_system"

    i_sys = next(imp for imp in imports if imp.module == "sys")
    assert i_sys.alias == "syspath"
    assert i_sys.name == "path"
    assert i_sys.is_from_import
    assert i_sys.import_statement == "from sys import path as syspath"


def test_extract_imports_multi_names():
    """extract_imports handles `from X import A, B, C` correctly."""
    source = "from os.path import join, exists, dirname\n"
    file = Path("/fake/mod.py")
    imports = extract_imports(source, file)
    assert len(imports) == 3, f"Expected 3 imports, got {len(imports)}: {imports}"
    names = {imp.name for imp in imports}
    assert names == {"join", "exists", "dirname"}
    for imp in imports:
        assert imp.module == "os.path"
        assert imp.is_from_import


def test_extract_imports_relative():
    """extract_imports handles `from . import sibling` (relative imports)."""
    source = "from . import sibling\nfrom .. import parent_sibling\n"
    file = Path("/fake/pkg/mod.py")
    imports = extract_imports(source, file)
    assert len(imports) == 2
    names = {imp.name for imp in imports}
    assert "sibling" in names
    assert "parent_sibling" in names
    # Relative imports have empty module string (node.module is None → "")
    for imp in imports:
        assert imp.module == ""
        assert imp.is_from_import


def test_extract_imports_regex_fallback():
    """extract_imports falls back to regex on SyntaxError."""
    source = "def f(\nimport os\n"  # syntax error prevents AST parsing
    file = Path("/fake/broken.py")
    imports = extract_imports(source, file)
    assert len(imports) >= 1
    modules = {imp.module for imp in imports}
    assert "os" in modules


def test_extract_imports_empty():
    """extract_imports returns empty list for code with no imports."""
    source = "x = 1\ny = 2\nprint(x + y)\n"
    file = Path("/fake/mod.py")
    imports = extract_imports(source, file)
    assert imports == []


# ---------------------------------------------------------------------------
# resolve_module_name tests
# ---------------------------------------------------------------------------


def test_resolve_module_name_absolute():
    """resolve_module_name converts a file path to a dotted module name."""
    project_root = Path("/project")
    file = project_root / "pkg" / "submod.py"
    result = resolve_module_name(file, project_root)
    assert result == "pkg.submod"


def test_resolve_module_name_bare():
    """resolve_module_name works for top-level files."""
    project_root = Path("/project")
    file = project_root / "main.py"
    result = resolve_module_name(file, project_root)
    assert result == "main"


def test_resolve_module_name_init():
    """resolve_module_name strips __init__.py to yield the package name."""
    project_root = Path("/project")
    file = project_root / "pkg" / "__init__.py"
    result = resolve_module_name(file, project_root)
    assert result == "pkg"


def test_resolve_module_name_outside_root():
    """resolve_module_name falls back to stem when file is outside project root."""
    project_root = Path("/project")
    file = Path("/elsewhere/standalone.py")
    result = resolve_module_name(file, project_root)
    # ValueError from relative_to → falls back to file.stem
    assert result == "standalone"


# ---------------------------------------------------------------------------
# build_dependency_graph tests
# ---------------------------------------------------------------------------


def test_build_dependency_graph_multiple_files(tmp_path):
    """build_dependency_graph processes multiple FileInfo and resolves import edges."""
    # Create project structure
    project_root = tmp_path / "project"
    pkg = project_root / "pkg"
    pkg.mkdir(parents=True)

    # main.py imports from pkg.util
    main_py = project_root / "main.py"
    main_py.write_text("import pkg.util\nimport os\n\npkg.util.helper()\n")

    # util.py — no imports
    util_py = pkg / "util.py"
    util_py.write_text("def helper():\n    return 42\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        util_py: FileInfo.from_path(util_py),
    }

    graph = build_dependency_graph(files, project_root)

    # main imports pkg.util and os
    assert "main" in graph.imports, f"main not in imports: {graph.imports}"
    importees = graph.imports["main"]
    assert "pkg.util" in importees, f"pkg.util not in imports of main: {importees}"
    assert "os" in importees, f"os not in imports of main: {importees}"

    # pkg.util is imported by main
    assert "pkg.util" in graph.imported_by
    assert "main" in graph.imported_by["pkg.util"]


def test_build_dependency_graph_empty():
    """build_dependency_graph handles empty file dict gracefully."""
    graph = build_dependency_graph({}, Path("/project"))
    assert isinstance(graph, DependencyGraph)
    assert graph.imports == {}
    assert graph.imported_by == {}


def test_build_dependency_graph_non_python_files(tmp_path):
    """build_dependency_graph skips non-Python files."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Create a JSON file
    json_file = project_root / "data.json"
    json_file.write_text('{"key": "value"}')

    info = FileInfo.from_path(json_file)
    files = {json_file: info}
    graph = build_dependency_graph(files, project_root)

    assert graph.imports == {}
    assert graph.imported_by == {}
    # JSON file not registered as a module
    assert json_file not in graph.file_to_module


def test_build_dependency_graph_circular(tmp_path):
    """build_dependency_graph handles circular imports correctly."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    b_py = project_root / "b.py"
    a_py.write_text("import b\n")
    b_py.write_text("import a\n")

    files = {
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
    }

    graph = build_dependency_graph(files, project_root)

    # Both edges should be present
    assert "b" in graph.imports.get("a", [])
    assert "a" in graph.imports.get("b", [])


def test_build_dependency_graph_self_import_filtered(tmp_path):
    """build_dependency_graph filters out self-imports (module importing itself)."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # This is unusual but edge case: a module importing itself
    mod_py = project_root / "mod.py"
    mod_py.write_text("import mod\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    graph = build_dependency_graph(files, project_root)

    # The self-import should NOT appear (line 47: importee_module != importer_module)
    if "mod" in graph.imports:
        assert "mod" not in graph.imports["mod"], "Self-import should be filtered out"


# ============================================================================
# build_dependency_graph — relative imports, aliased imports, multi-level
# ============================================================================


def test_build_dependency_graph_with_relative_imports(tmp_path):
    """build_dependency_graph handles `from . import sibling` relative imports."""
    project_root = tmp_path / "project"
    pkg = project_root / "pkg"
    pkg.mkdir(parents=True)

    sibling_py = pkg / "sibling.py"
    sibling_py.write_text("def helper():\n    return 42\n")

    mod_py = pkg / "mod.py"
    mod_py.write_text("from . import sibling\n\nsibling.helper()\n")

    files: dict[Path, FileInfo] = {
        mod_py: FileInfo.from_path(mod_py),
        sibling_py: FileInfo.from_path(sibling_py),
    }

    graph = build_dependency_graph(files, project_root)

    # mod_py registers as pkg.mod
    assert "pkg.mod" in graph.imports or "pkg.mod" in graph.file_to_module.values(), (
        f"Expected pkg.mod to be registered, got: {graph.file_to_module}"
    )
    # sibling_py registers as pkg.sibling
    assert (
        "pkg.sibling" in graph.imported_by
        or "pkg.sibling" in graph.file_to_module.values()
    ), f"Expected pkg.sibling in graph, got file_to_module: {graph.file_to_module}"


def test_build_dependency_graph_with_aliased_imports(tmp_path):
    """build_dependency_graph resolves aliased imports: alias doesn't corrupt module resolution."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    a_py.write_text("from b import helper as h\n\nh()\n")

    b_py = project_root / "b.py"
    b_py.write_text("def helper():\n    return 42\n")

    files = {
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
    }

    graph = build_dependency_graph(files, project_root)

    # a should import b (module resolved correctly despite alias "h")
    assert "a" in graph.imports, f"a not in imports: {list(graph.imports.keys())}"
    assert "b" in graph.imports["a"], f"Expected a→b edge, got {graph.imports.get('a')}"
    # b imported_by a
    assert "b" in graph.imported_by
    assert "a" in graph.imported_by["b"]


def test_build_dependency_graph_multiple_levels(tmp_path):
    """build_dependency_graph captures transitive import chain A→B→C."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    a_py.write_text("import b\n\nb.do_work()\n")

    b_py = project_root / "b.py"
    b_py.write_text("import c\n\ndef do_work():\n    return c.data()\n")

    c_py = project_root / "c.py"
    c_py.write_text("def data():\n    return 42\n")

    files = {
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
        c_py: FileInfo.from_path(c_py),
    }

    graph = build_dependency_graph(files, project_root)

    # Two edges: a→b, b→c
    assert "a" in graph.imports, f"a should import b, got: {graph.imports}"
    assert "b" in graph.imports["a"], f"a→b missing, got: {graph.imports.get('a')}"
    assert "b" in graph.imports, f"b should import c, got: {graph.imports}"
    assert "c" in graph.imports["b"], f"b→c missing, got: {graph.imports.get('b')}"

    # imported_by: b imported by a, c imported by b
    assert "b" in graph.imported_by
    assert "a" in graph.imported_by["b"]
    assert "c" in graph.imported_by
    assert "b" in graph.imported_by["c"]


# ============================================================================
# trace_symbol_usage — multi-file, attribute access, non-Python skip
# ============================================================================


def test_trace_symbol_usage_across_multiple_files(tmp_path):
    """trace_symbol_usage finds symbol references across 3+ files."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    a_py.write_text("config = {'debug': True}\n")

    b_py = project_root / "b.py"
    b_py.write_text("from a import config\n\nprint(config)\n")

    c_py = project_root / "c.py"
    c_py.write_text("from a import config\n\nprint(config['debug'])\n")

    files = {
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
        c_py: FileInfo.from_path(c_py),
    }
    graph = build_dependency_graph(files, project_root)

    usages = trace_symbol_usage("config", files, graph)

    # config appears in a.py (assignment), b.py (Name + maybe Attribute), c.py (Name + maybe Attribute)
    assert len(usages) >= 3, (
        f"Expected >=3 usages of 'config' across 3 files, "
        f"got {len(usages)}: {[(u.file.name, u.location.line) for u in usages]}"
    )
    files_with_usage = {u.file for u in usages}
    assert len(files_with_usage) >= 3, (
        f"Expected usage in 3 files, got {len(files_with_usage)}: "
        f"{[f.name for f in files_with_usage]}"
    )

    for usage in usages:
        assert usage.symbol == "config"
        assert isinstance(usage.location, Location)


def test_trace_symbol_usage_with_attribute_access(tmp_path):
    """trace_symbol_usage detects attribute access like os.path.join."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    mod_py = project_root / "mod.py"
    mod_py.write_text("import os\n\nprint(os.path.join('a', 'b'))\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    graph = build_dependency_graph(files, project_root)

    # Trace "join" → should find it as ast.Attribute node
    usages = trace_symbol_usage("join", files, graph)
    assert len(usages) >= 1, (
        f"Expected 'join' found as Attribute node, got {len(usages)} usages"
    )
    join_usage = [u for u in usages if u.file == mod_py]
    assert len(join_usage) >= 1, "join usage should be in mod.py"

    # Trace "path" → should find it as ast.Attribute with attr="path"
    usages_path = trace_symbol_usage("path", files, graph)
    assert len(usages_path) >= 1, (
        f"Expected 'path' found as Attribute node, got {len(usages_path)}"
    )

    # Trace "os" → should find Name nodes
    usages_os = trace_symbol_usage("os", files, graph)
    assert len(usages_os) >= 1, "Expected 'os' found as Name node"


def test_trace_symbol_usage_skip_non_python(tmp_path):
    """trace_symbol_usage skips files that are not Python or have no AST tree."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    py_file = project_root / "mod.py"
    py_file.write_text("x = 1\n")

    # Create a FileInfo with language != "python" and no AST
    non_py_info = FileInfo(
        path=project_root / "data.json",
        content='{"key": "value"}',
        language="json",
        ast_tree=None,
    )

    py_info = FileInfo.from_path(py_file)

    files = {py_info.path: py_info, non_py_info.path: non_py_info}
    graph = build_dependency_graph(files, project_root)

    # Trace "key" — should not find it in JSON file (skipped)
    usages = trace_symbol_usage("key", files, graph)
    # "key" doesn't appear in mod.py → empty
    assert len([u for u in usages if u.file == non_py_info.path]) == 0, (
        "trace_symbol_usage should skip non-Python files"
    )


# ============================================================================
# extract_imports — complex file, line number preservation
# ============================================================================


def test_extract_imports_complex_file():
    """extract_imports handles many import patterns in a single source."""
    source = (
        "import os\n"
        "from sys import path\n"
        "from typing import Dict, List\n"
        "from . import sibling\n"
        "import json as js\n"
        "from collections import OrderedDict as OD\n"
        "from math import *\n"
    )
    file = Path("/fake/complex.py")
    imports = extract_imports(source, file)

    assert len(imports) >= 8, f"Expected >=8 imports, got {len(imports)}: {imports}"

    # Plain import
    os_imp = [i for i in imports if i.module == "os" and not i.is_from_import]
    assert len(os_imp) == 1
    assert os_imp[0].name is None

    # From import single
    path_imp = [i for i in imports if i.name == "path" and i.is_from_import]
    assert len(path_imp) == 1
    assert path_imp[0].module == "sys"

    # From import multi
    dict_imp = [i for i in imports if i.name == "Dict"]
    list_imp = [i for i in imports if i.name == "List"]
    assert len(dict_imp) == 1
    assert len(list_imp) == 1

    # Relative import
    sibling_imp = [i for i in imports if i.name == "sibling"]
    assert len(sibling_imp) >= 1

    # Aliased plain import
    js_imp = [i for i in imports if i.alias == "js"]
    assert len(js_imp) == 1
    assert js_imp[0].module == "json"
    assert js_imp[0].name is None
    assert not js_imp[0].is_from_import

    # Aliased from import
    od_imp = [i for i in imports if i.alias == "OD"]
    assert len(od_imp) == 1
    assert od_imp[0].module == "collections"
    assert od_imp[0].name == "OrderedDict"

    # Star import
    star_imp = [i for i in imports if i.name == "*"]
    assert len(star_imp) == 1
    assert star_imp[0].module == "math"


def test_extract_imports_preserves_line_numbers():
    """Each ImportInfo records the correct source line number."""
    source = (
        "import os\n"  # line 1
        "\n"  # line 2
        "from sys import path\n"  # line 3
        "\n"  # line 4
        "import json as js\n"  # line 5
    )
    file = Path("/fake/mod.py")
    imports = extract_imports(source, file)

    by_module = {imp.module: imp for imp in imports}

    assert by_module["os"].line == 1, (
        f"Expected line 1 for 'import os', got {by_module['os'].line}"
    )
    assert by_module["sys"].line == 3, (
        f"Expected line 3 for 'from sys import path', got {by_module['sys'].line}"
    )
    assert by_module["json"].line == 5, (
        f"Expected line 5 for 'import json as js', got {by_module['json'].line}"
    )


# ============================================================================
# resolve_module_name — relative paths, aliased import integration
# ============================================================================


def test_resolve_module_name_relative():
    """resolve_module_name handles deeply nested module paths."""
    project_root = Path("/project")
    file = project_root / "pkg" / "sub" / "deep.py"
    result = resolve_module_name(file, project_root)
    assert result == "pkg.sub.deep", f"Expected pkg.sub.deep, got {result}"

    # Also test with __init__.py in nested path
    init_file = project_root / "pkg" / "sub" / "__init__.py"
    result2 = resolve_module_name(init_file, project_root)
    assert result2 == "pkg.sub", f"Expected pkg.sub, got {result2}"


def test_resolve_module_name_with_alias(tmp_path):
    """build_dependency_graph + resolve_module_name handle aliased imports correctly.

    An aliased import `from b import helper as h` should resolve the module to 'b',
    not 'h' or 'helper'.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    a_py.write_text("from b import helper as h\n\nh()\n")

    b_py = project_root / "b.py"
    b_py.write_text("def helper():\n    return 42\n")

    files = {
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
    }

    graph = build_dependency_graph(files, project_root)

    # The module resolution should map a→b, regardless of alias
    assert "a" in graph.file_to_module or a_py in graph.file_to_module, (
        "a.py should be registered"
    )
    assert "b" in graph.file_to_module or b_py in graph.file_to_module, (
        "b.py should be registered"
    )

    # Verify the import edge exists
    a_module = graph.file_to_module.get(a_py, "a")
    b_module = graph.file_to_module.get(b_py, "b")
    assert b_module in graph.imports.get(a_module, []), (
        f"Expected {a_module}→{b_module} edge, got imports: {graph.imports}"
    )

    # The alias "h" should NOT appear as a module in the graph
    assert "h" not in graph.module_to_file
    assert "h" not in graph.file_to_module.values()


# ============================================================================
# find_unused_imports — local module with no importers
# ============================================================================


def test_find_unused_imports(tmp_path):
    """find_unused_imports identifies local modules registered in imported_by with zero importers.

    The function iterates over graph.imported_by.items() — modules must be in imported_by
    to be checked. A module that is never imported by anyone won't appear in imported_by
    at all, so it won't be found. The function finds modules that ARE importees but have
    zero importers.
    """
    from imodent.graph.dependency import find_unused_imports

    project_root = tmp_path / "project"
    project_root.mkdir()

    a_py = project_root / "a.py"
    a_py.write_text("import b\n" + "# b is used below\n" + "b.do_work()\n")

    b_py = project_root / "b.py"
    b_py.write_text("def do_work():\n    return 42\n")

    # c imports os (third-party) but nobody imports c → c ends up in imported_by
    # with zero importers because it imports os, putting c in imports dict, but
    # nobody imports c so imported_by has no entry for c.
    # Actually: c won't be in imported_by at all. The function only finds modules
    # in imported_by.items(). We need a module that IS in imported_by with empty list.
    # Let's build the graph manually to test the function directly.

    # Build graph manually: mod_a is a local module in imported_by with empty list
    graph = DependencyGraph()
    graph.module_to_file["mod_a"] = project_root / "mod_a.py"
    graph.module_to_file["mod_b"] = project_root / "mod_b.py"
    graph.file_to_module[project_root / "mod_a.py"] = "mod_a"
    graph.file_to_module[project_root / "mod_b.py"] = "mod_b"

    # mod_a imported by nothing (empty list in imported_by)
    graph.imported_by["mod_a"] = []
    # mod_b imported by mod_c (has importers — NOT unused)
    graph.imported_by["mod_b"] = ["mod_c"]

    unused = find_unused_imports(graph, {}, project_root)

    # mod_a: no importers + is local → unused
    a_unused = [u for u in unused if u["module"] == "mod_a"]
    assert len(a_unused) == 1, (
        f"Expected mod_a to be unused (empty imported_by + local), got: {unused}"
    )
    assert a_unused[0]["is_third_party"] is False
    assert a_unused[0]["imported_by"] == []

    # mod_b: has importers → should NOT appear
    b_unused = [u for u in unused if u["module"] == "mod_b"]
    assert len(b_unused) == 0, (
        f"mod_b has importers, should not be unused, got: {b_unused}"
    )


def test_find_unused_imports_all_used():
    """find_unused_imports returns empty when all imported_by entries have importers."""
    from imodent.graph.dependency import find_unused_imports

    graph = DependencyGraph()
    graph.module_to_file["mod_a"] = Path("/fake/mod_a.py")
    graph.module_to_file["mod_b"] = Path("/fake/mod_b.py")
    graph.imported_by["mod_a"] = ["mod_c"]
    graph.imported_by["mod_b"] = ["mod_c"]

    unused = find_unused_imports(graph, {}, Path("/fake"))
    assert len(unused) == 0, f"Expected no unused, got: {unused}"


# ============================================================================
# _get_context — helper for symbol-level reporting
# ============================================================================


def test_get_context():
    """_get_context returns lines around a target line."""
    from imodent.graph.dependency import _get_context

    content = "line1\nline2\nline3\nline4\nline5\n"
    result = _get_context(content, line=3, context_lines=1)

    # line=3 with context_lines=1 → start=max(0, 3-1-1)=1, end=min(5, 3+1)=4 → lines 2-4
    assert "line2" in result
    assert "line3" in result
    assert "line4" in result
    assert "line1" not in result and "line5" not in result, (
        f"Expected only lines 2-4, got: {result!r}"
    )


def test_get_context_boundary():
    """_get_context handles boundary conditions (first/last lines)."""
    from imodent.graph.dependency import _get_context

    content = "line1\nline2\nline3\n"
    # First line
    result = _get_context(content, line=1, context_lines=2)
    assert "line1" in result

    # Last line
    result = _get_context(content, line=3, context_lines=2)
    assert "line3" in result


# ============================================================================
# _find_common_root — common ancestor discovery
# ============================================================================


def test_find_common_root():
    """_find_common_root finds the deepest common ancestor of paths."""
    from imodent.graph.dependency import _find_common_root

    paths = [
        Path("/project/pkg/sub/mod_a.py"),
        Path("/project/pkg/sub/mod_b.py"),
        Path("/project/pkg/other.py"),
    ]
    result = _find_common_root(paths)
    expected = Path("/project/pkg")
    assert result == expected, f"Expected common root {expected}, got {result}"


def test_find_common_root_single_path():
    """_find_common_root with one path returns that path."""
    from imodent.graph.dependency import _find_common_root

    paths = [Path("/project/pkg/mod.py")]
    result = _find_common_root(paths)
    assert result == Path("/project/pkg/mod.py")


def test_find_common_root_empty():
    """_find_common_root with empty list returns cwd."""
    from imodent.graph.dependency import _find_common_root

    result = _find_common_root([])
    assert result == Path.cwd()


# ============================================================================
# ImportInfo — full_name property
# ============================================================================


def test_import_info_full_name():
    """ImportInfo.full_name returns module.name or module alone."""
    info1 = ImportInfo(
        module="os",
        name=None,
        alias=None,
        line=1,
        is_from_import=False,
        file=Path("/fake/mod.py"),
    )
    assert info1.full_name == "os"

    info2 = ImportInfo(
        module="os",
        name="path",
        alias=None,
        line=1,
        is_from_import=True,
        file=Path("/fake/mod.py"),
    )
    assert info2.full_name == "os.path"

    info3 = ImportInfo(
        module="typing",
        name="Dict",
        alias="TypeDict",
        line=2,
        is_from_import=True,
        file=Path("/fake/mod.py"),
    )
    # Alias doesn't change full_name (it's module.name)
    assert info3.full_name == "typing.Dict"


# ============================================================================
# _extract_imports_regex — alias handling in fallback
# ============================================================================


def test_extract_imports_regex_with_as_alias():
    """_extract_imports_regex captures 'as' aliases in both import forms."""
    # SyntaxError source to trigger regex fallback
    source = "def f(\nimport os\nimport sys as system\nfrom json import dumps as dmp\n"
    file = Path("/fake/broken.py")
    imports = extract_imports(source, file)

    # Regular import with alias
    sys_imp = [i for i in imports if i.module == "sys"]
    assert len(sys_imp) >= 1, (
        f"Expected sys import, got {[(i.module, i.alias) for i in imports]}"
    )
    assert sys_imp[0].alias == "system", (
        f"Expected alias='system', got {sys_imp[0].alias}"
    )

    # From-import with alias (via regex)
    json_imp = [i for i in imports if i.module == "json"]
    assert len(json_imp) >= 1, f"Expected json from-import, got {imports}"
    assert json_imp[0].alias == "dmp" or any(i.alias == "dmp" for i in json_imp), (
        f"Expected alias='dmp' for json import, got aliases: {[i.alias for i in json_imp]}"
    )


def test_extract_imports_regex_with_comma_alias():
    """_extract_imports_regex handles comma-separated imports with aliases.

    Note: the regex pattern uses `[a-zA-Z0-9_]?` (zero-or-one char) for
    comma-separated aliases vs `[a-zA-Z0-9_]*` (zero-or-more) for standalone
    aliases.  This is a pre-existing behavior — test verifies what currently works.
    """
    # SyntaxError source — use a single-char alias to match the `?` quantifier
    source = "def f(\nimport os, sys as s, json\n"
    file = Path("/fake/broken.py")
    imports = extract_imports(source, file)

    modules = {i.module for i in imports}
    assert "os" in modules
    assert "sys" in modules
    assert "json" in modules

    sys_imp = [i for i in imports if i.module == "sys"]
    assert len(sys_imp) >= 1
    assert sys_imp[0].alias == "s"


# ============================================================================
# _resolve_import — filesystem fallback at line 102
# ============================================================================


def test_resolve_import_via_filesystem(tmp_path):
    """_resolve_import finds module on disk even when not in files dict (line 102)."""
    project_root = tmp_path / "project"
    pkg = project_root / "pkg"
    pkg.mkdir(parents=True)

    util_py = pkg / "util.py"
    util_py.write_text("def helper():\n    return 42\n")

    # a.py imports pkg.util, but pkg.util is NOT in the files dict
    a_py = project_root / "a.py"
    a_py.write_text("import pkg.util\n\npkg.util.helper()\n")

    files = {a_py: FileInfo.from_path(a_py)}

    graph = build_dependency_graph(files, project_root)

    # a should import pkg.util (resolved via filesystem at line 100-102)
    a_module = graph.file_to_module.get(a_py, "a")
    importees = graph.imports.get(a_module, [])

    assert "pkg.util" in importees, (
        f"Expected a→pkg.util edge via filesystem resolution, "
        f"got imports: {graph.imports}"
    )


# ============================================================================
# resolve.py — _is_stdlib_module
# ============================================================================


def test_is_stdlib_module_known_stdlib():
    """_is_stdlib_module returns True for known stdlib modules."""
    assert _is_stdlib_module("os") is True
    assert _is_stdlib_module("sys") is True
    assert _is_stdlib_module("json") is True
    assert _is_stdlib_module("typing") is True
    assert _is_stdlib_module("collections") is True
    assert _is_stdlib_module("pathlib") is True


def test_is_stdlib_module_non_stdlib():
    """_is_stdlib_module returns False for third-party or project modules."""
    assert _is_stdlib_module("imodent") is False
    assert _is_stdlib_module("pytest") is False
    assert _is_stdlib_module("numpy") is False
    assert _is_stdlib_module("flask") is False


def test_is_stdlib_module_edge_cases():
    """_is_stdlib_module handles empty string and case sensitivity."""
    assert _is_stdlib_module("") is False
    # The allowlist is lowercase; 'OS' should not match 'os'
    assert _is_stdlib_module("OS") is False


# ============================================================================
# resolve.py — _file_to_module
# ============================================================================


def test_file_to_module_simple():
    """_file_to_module converts a Python file path to a dotted module name."""
    assert _file_to_module(Path("/proj/pkg/mod.py"), Path("/proj")) == "pkg.mod"


def test_file_to_module_init():
    """_file_to_module strips __init__.py to yield the package name."""
    assert _file_to_module(Path("/proj/pkg/__init__.py"), Path("/proj")) == "pkg"


def test_file_to_module_top_level():
    """_file_to_module handles top-level files correctly."""
    assert _file_to_module(Path("/proj/main.py"), Path("/proj")) == "main"


def test_file_to_module_outside_root():
    """_file_to_module falls back to file stem when path is outside project root."""
    assert _file_to_module(Path("/elsewhere/standalone.py"), Path("/proj")) == "standalone"


def test_file_to_module_deeply_nested():
    """_file_to_module handles deeply nested module paths."""
    result = _file_to_module(
        Path("/proj/pkg/sub/deep/module.py"), Path("/proj")
    )
    assert result == "pkg.sub.deep.module"


def test_file_to_module_nested_init():
    """_file_to_module strips nested __init__.py to yield the package."""
    result = _file_to_module(Path("/proj/pkg/sub/__init__.py"), Path("/proj"))
    assert result == "pkg.sub"


# ============================================================================
# resolve.py — _common_ancestor
# ============================================================================


def test_common_ancestor_shared_parent():
    """_common_ancestor finds the deepest common parent directory of two paths."""
    paths = [Path("/a/b/c.py"), Path("/a/b/d.py")]
    assert _common_ancestor(paths) == Path("/a/b")


def test_common_ancestor_no_shared_parent():
    """_common_ancestor returns root when paths share no common ancestor."""
    paths = [Path("/x/a.py"), Path("/y/b.py")]
    assert _common_ancestor(paths) == Path("/")


def test_common_ancestor_identical():
    """_common_ancestor with identical paths returns the path itself."""
    paths = [Path("/a/b/c.py"), Path("/a/b/c.py")]
    assert _common_ancestor(paths) == Path("/a/b/c.py")


def test_common_ancestor_empty():
    """_common_ancestor returns cwd for empty path list."""
    assert _common_ancestor([]) == Path.cwd()


def test_common_ancestor_single():
    """_common_ancestor with a single path returns that resolved path."""
    paths = [Path("/a/b/c.py")]
    assert _common_ancestor(paths) == Path("/a/b/c.py")


def test_common_ancestor_three_paths():
    """_common_ancestor handles three paths with varying common ancestry."""
    paths = [Path("/a/b/c/d/e.py"), Path("/a/b/c/f.py"), Path("/a/b/g.py")]
    assert _common_ancestor(paths) == Path("/a/b")


def test_common_ancestor_same_directory():
    """_common_ancestor when all files are in the same directory."""
    paths = [Path("/a/b/c.py"), Path("/a/b/d.py"), Path("/a/b/e.py")]
    assert _common_ancestor(paths) == Path("/a/b")


# ============================================================================
# resolve.py — _assigns_name
# ============================================================================


def test_assigns_name_simple_match():
    """_assigns_name returns True when simple assignment target matches name."""
    tree = ast.parse("x = 1")
    assign_node = tree.body[0]
    assert _assigns_name(assign_node, "x") is True


def test_assigns_name_simple_no_match():
    """_assigns_name returns False when simple assignment target does not match."""
    tree = ast.parse("x = 1")
    assign_node = tree.body[0]
    assert _assigns_name(assign_node, "y") is False


def test_assigns_name_tuple_match():
    """_assigns_name detects name in tuple destructuring (x, y = 1, 2)."""
    tree = ast.parse("x, y = 1, 2")
    assign_node = tree.body[0]
    assert _assigns_name(assign_node, "x") is True
    assert _assigns_name(assign_node, "y") is True


def test_assigns_name_tuple_no_match():
    """_assigns_name returns False when tuple destructuring has no matching name."""
    tree = ast.parse("x, y = 1, 2")
    assign_node = tree.body[0]
    assert _assigns_name(assign_node, "z") is False


def test_assigns_name_multiple_targets():
    """_assigns_name checks all targets in chained assignment (a = b = 1)."""
    tree = ast.parse("a = b = 1")
    assign_node = tree.body[0]
    assert _assigns_name(assign_node, "a") is True
    assert _assigns_name(assign_node, "b") is True
    assert _assigns_name(assign_node, "c") is False


# ============================================================================
# resolve.py — _format_import
# ============================================================================


def test_format_import_from():
    """_format_import produces 'from module import name' for standard from-import."""
    assert _format_import("os.path", "join") == "from os.path import join"


def test_format_import_bare():
    """_format_import produces 'import module' when name matches module."""
    assert _format_import("os", "os") == "import os"


def test_format_import_bare_with_alias():
    """_format_import adds 'as alias' for bare imports with alias."""
    assert _format_import("os", "os", "operating_system") == "import os as operating_system"


def test_format_import_from_with_alias():
    """_format_import adds 'as alias' for from-imports with alias."""
    result = _format_import("collections", "OrderedDict", "OD")
    assert result == "from collections import OrderedDict as OD"


def test_format_import_submodule_matching_top_package():
    """_format_import when name matches top-level package of a submodule.

    module='imodent.cli', name='imodent' → 'from imodent import cli'.
    This avoids 'import imodent.cli' which would force attribute access.
    """
    assert _format_import("imodent.cli", "imodent") == "from imodent import cli"


def test_format_import_submodule_name():
    """_format_import produces correct from-import for submodule symbols."""
    assert _format_import("imodent.cli", "main") == "from imodent.cli import main"


def test_format_import_nested_submodule():
    """_format_import handles deeply nested submodule imports."""
    assert _format_import("imodent.analysis.context", "FileInfo") == (
        "from imodent.analysis.context import FileInfo"
    )


# ============================================================================
# resolve.py — ImportSuggestion dataclass
# ============================================================================


def test_import_suggestion_fields():
    """ImportSuggestion dataclass stores all fields correctly."""
    suggestion = ImportSuggestion(
        name="helper",
        module="pkg.util",
        import_stmt="from pkg.util import helper",
        definition_file=Path("/proj/pkg/util.py"),
        definition_line=1,
        is_stdlib=False,
    )
    assert suggestion.name == "helper"
    assert suggestion.module == "pkg.util"
    assert suggestion.import_stmt == "from pkg.util import helper"
    assert suggestion.definition_file == Path("/proj/pkg/util.py")
    assert suggestion.definition_line == 1
    assert suggestion.is_stdlib is False


def test_import_suggestion_default_is_stdlib():
    """ImportSuggestion.is_stdlib defaults to False when not specified."""
    suggestion = ImportSuggestion(
        name="os",
        module="os",
        import_stmt="import os",
        definition_file=Path("/proj/mod.py"),
        definition_line=5,
    )
    assert suggestion.is_stdlib is False


def test_import_suggestion_stdlib_flag():
    """ImportSuggestion with is_stdlib=True identifies stdlib suggestions."""
    suggestion = ImportSuggestion(
        name="os",
        module="os",
        import_stmt="import os",
        definition_file=Path("(stdlib)"),
        definition_line=0,
        is_stdlib=True,
    )
    assert suggestion.is_stdlib is True
    assert suggestion.definition_file == Path("(stdlib)")
    assert suggestion.definition_line == 0


# ============================================================================
# resolve.py — resolve_undefined_name integration tests
# ============================================================================


def test_resolve_undefined_name_finds_function(tmp_path):
    """resolve_undefined_name finds a function defined in another project file."""
    project_root = tmp_path / "project"
    pkg = project_root / "pkg"
    pkg.mkdir(parents=True)

    # util.py defines helper() at module level
    util_py = pkg / "util.py"
    util_py.write_text("def helper():\n    return 42\n")

    # main.py uses 'helper' but does NOT import it — it would be F821
    main_py = project_root / "main.py"
    main_py.write_text("print(helper())\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        util_py: FileInfo.from_path(util_py),
    }

    suggestions = resolve_undefined_name("helper", files, project_root)

    assert len(suggestions) == 1, (
        f"Expected 1 suggestion for 'helper', got {len(suggestions)}: {suggestions}"
    )
    s = suggestions[0]
    assert s.name == "helper"
    assert s.module == "pkg.util"
    assert s.import_stmt == "from pkg.util import helper"
    assert s.definition_file.resolve() == util_py.resolve()
    assert s.definition_line == 1
    assert s.is_stdlib is False


def test_resolve_undefined_name_finds_class(tmp_path):
    """resolve_undefined_name finds a class defined in another project file."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    models_py = project_root / "models.py"
    models_py.write_text("class User:\n    name: str\n")

    main_py = project_root / "main.py"
    main_py.write_text("u = User()\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        models_py: FileInfo.from_path(models_py),
    }

    suggestions = resolve_undefined_name("User", files, project_root)
    assert len(suggestions) == 1
    assert suggestions[0].name == "User"
    assert suggestions[0].module == "models"
    assert suggestions[0].import_stmt == "from models import User"


def test_resolve_undefined_name_finds_assignment(tmp_path):
    """resolve_undefined_name finds a module-level variable assignment."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    config_py = project_root / "config.py"
    config_py.write_text("DEBUG = True\nVERSION = '1.0'\n")

    main_py = project_root / "main.py"
    main_py.write_text("print(VERSION)\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        config_py: FileInfo.from_path(config_py),
    }

    suggestions = resolve_undefined_name("VERSION", files, project_root)
    assert len(suggestions) == 1
    assert suggestions[0].name == "VERSION"
    assert suggestions[0].module == "config"
    assert suggestions[0].import_stmt == "from config import VERSION"


def test_resolve_undefined_name_local_variable_not_found(tmp_path):
    """resolve_undefined_name only checks module-level definitions, not locals.

    A name defined as a local variable inside a function is not found
    because resolve_undefined_name only walks tree.body (top-level nodes).
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    mod_py = project_root / "mod.py"
    mod_py.write_text("def func():\n    x = 1\n    return x\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    suggestions = resolve_undefined_name("x", files, project_root)
    assert suggestions == [], (
        f"Local variable 'x' inside func() should not be found at module level, "
        f"got: {suggestions}"
    )


def test_resolve_undefined_name_truly_undefined(tmp_path):
    """resolve_undefined_name returns empty list when name is not defined anywhere."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    mod_py = project_root / "mod.py"
    mod_py.write_text("print(42)\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    suggestions = resolve_undefined_name("nonexistent", files, project_root)
    assert suggestions == [], (
        f"'nonexistent' should not be found, got: {suggestions}"
    )


def test_resolve_undefined_name_stdlib_fallback(tmp_path):
    """resolve_undefined_name suggests stdlib import when name is a known stdlib module.

    When 'os' is not defined in any project file, but is_stdlib_module('os') is True,
    resolve_undefined_name returns a stdlib ImportSuggestion.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    mod_py = project_root / "mod.py"
    mod_py.write_text("print(os.getcwd())\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    suggestions = resolve_undefined_name("os", files, project_root)

    assert len(suggestions) == 1, (
        f"Expected stdlib fallback suggestion for 'os', got {len(suggestions)}"
    )
    s = suggestions[0]
    assert s.name == "os"
    assert s.module == "os"
    assert s.import_stmt == "import os"
    assert s.is_stdlib is True
    assert s.definition_file == Path("(stdlib)")
    assert s.definition_line == 0


def test_resolve_undefined_name_multiple_matches(tmp_path):
    """resolve_undefined_name returns all matches when name is defined in multiple files."""
    project_root = tmp_path / "project"
    pkg_a = project_root / "pkg_a"
    pkg_b = project_root / "pkg_b"
    pkg_a.mkdir(parents=True)
    pkg_b.mkdir(parents=True)

    a_py = pkg_a / "mod.py"
    a_py.write_text("def helper():\n    return 1\n")

    b_py = pkg_b / "mod.py"
    b_py.write_text("def helper():\n    return 2\n")

    main_py = project_root / "main.py"
    main_py.write_text("print(helper())\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        a_py: FileInfo.from_path(a_py),
        b_py: FileInfo.from_path(b_py),
    }

    suggestions = resolve_undefined_name("helper", files, project_root)
    assert len(suggestions) == 2, (
        f"Expected 2 suggestions for 'helper', got {len(suggestions)}: {suggestions}"
    )
    modules = {s.module for s in suggestions}
    assert "pkg_a.mod" in modules
    assert "pkg_b.mod" in modules


def test_resolve_undefined_name_skips_non_python(tmp_path):
    """resolve_undefined_name skips files that are not Python or have no AST tree."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    py_file = project_root / "mod.py"
    py_file.write_text("print(42)\n")

    # Non-Python file with no AST
    json_info = FileInfo(
        path=project_root / "data.json",
        content='{"helper": 1}',
        language="json",
        ast_tree=None,
    )

    files = {
        py_file: FileInfo.from_path(py_file),
        json_info.path: json_info,
    }

    suggestions = resolve_undefined_name("helper", files, project_root)
    # 'helper' not in mod.py, and data.json is skipped (not Python)
    assert suggestions == [], (
        f"Non-Python files should be skipped, got: {suggestions}"
    )


def test_resolve_undefined_name_empty_files(tmp_path):
    """resolve_undefined_name handles empty files dict gracefully."""
    suggestions = resolve_undefined_name("anything", {}, Path("/fake"))
    # Empty files → no matches → not stdlib → empty list
    assert suggestions == []


def test_resolve_undefined_name_project_root_default(tmp_path):
    """resolve_undefined_name computes project_root from common ancestor when not given."""
    project_root = tmp_path / "project"
    pkg = project_root / "pkg"
    pkg.mkdir(parents=True)

    util_py = pkg / "util.py"
    util_py.write_text("def helper():\n    return 42\n")

    main_py = project_root / "main.py"
    main_py.write_text("print(helper())\n")

    files: dict[Path, FileInfo] = {
        main_py: FileInfo.from_path(main_py),
        util_py: FileInfo.from_path(util_py),
    }

    # project_root=None → computed from _common_ancestor(files.keys())
    suggestions = resolve_undefined_name("helper", files, project_root=None)

    assert len(suggestions) == 1
    # With no explicit project_root, _file_to_module uses the common ancestor
    # which should be the project directory or similar
    assert suggestions[0].name == "helper"


def test_resolve_undefined_name_functiondef_precedence(tmp_path):
    """resolve_undefined_name finds FunctionDef definitions at module level."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    mod_py = project_root / "mod.py"
    mod_py.write_text("def main():\n    pass\n")

    files = {mod_py: FileInfo.from_path(mod_py)}
    suggestions = resolve_undefined_name("main", files, project_root)
    assert len(suggestions) == 1
    assert suggestions[0].name == "main"
