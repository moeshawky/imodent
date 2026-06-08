from __future__ import annotations

from pathlib import Path

from .decision_models import SubjectKey


def subject_key_for_import(
    file: Path,
    module: str | None,
    name: str | None,
    alias: str | None,
    scope: str = "module",
) -> SubjectKey:
    """Convenience factory for import-binding subject keys.

    Normalizes bare imports: when only *name* is known (e.g. from a Ruff
    F401 message) and it has no dot, treat it as a bare ``import X`` so that
    it matches the AST analyzer's module-centric representation.
    """
    # Normalize: bare import when name is known but module is not
    if module is None and name is not None and "." not in name:
        module, name = name, None
    bound_name = alias or name or (module.split(".")[0] if module else None)
    return SubjectKey(
        kind="import",
        file=file,
        scope=scope,
        module=module,
        name=name,
        alias=alias,
        bound_name=bound_name,
        origin=f"{module}.{name}" if module and name else module or None,
    )


def subject_key_for_lint(
    file: Path,
    code: str,
    module: str | None = None,
    name: str | None = None,
    scope: str = "module",
) -> SubjectKey:
    """Convenience factory for lint-diagnostic subject keys."""
    return SubjectKey(
        kind="lint",
        file=file,
        scope=scope,
        module=module,
        name=name or code,
        alias=None,
        bound_name=name,
        origin=None,
    )


def _subject_key_from_finding(finding) -> SubjectKey | None:
    """Extract a SubjectKey from a Finding using its data fields.

    For Ruff F401 diagnostics, normalizes to import-style key so that
    the subject identity matches what the local analyzer uses.
    """
    file = getattr(finding, "file", Path())
    f_type = getattr(finding, "type", "unknown")
    lint_code = getattr(finding, "lint_code", None)
    lint_source = getattr(finding, "lint_source", None)
    import_module = getattr(finding, "import_module", None)
    import_name = getattr(finding, "import_name", None)
    data = getattr(finding, "data", {}) or {}
    import_info = data.get("import_info") or {}

    if lint_source == "ruff" and lint_code == "F401":
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=None,
        )

    if lint_source == "ruff" and lint_code:
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        return subject_key_for_lint(
            file=file,
            code=lint_code,
            module=module,
            name=name,
        )

    if f_type in ("unused_import", "unused_import_file", "import_intent"):
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        alias = import_info.get("alias")
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=alias,
        )

    if f_type == "duplicate_import":
        module = import_info.get("module") or import_module
        name = import_info.get("name") or import_name
        alias = import_info.get("alias")
        return subject_key_for_import(
            file=file,
            module=module,
            name=name,
            alias=alias,
        )

    # Fallback
    return SubjectKey(
        kind="unknown",
        file=file,
        scope="unknown",
        name=getattr(finding, "message", None),
    )
