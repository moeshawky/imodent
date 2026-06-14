# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Aggregate fix-summary tracking: `coordinator.fix()` prints files modified, unchanged,
  missing-context, and without-fixer counts. `coordinator.analyze()` reports load failures.
- Error logging at silent failure paths in `fixers/imports.py` (3 sites)
- pyright stderr now captured and reported as INFO findings in type analyzer

### Fixed
- Mechanism deduplication: `_file_to_module` in resolve.py now delegates to
  `resolve_module_name` in imports.py (C28). Cross-reference annotations on 3 sibling pairs.
- Type boundary fix: `residue.py` annotation `dict[str,list[str]]` → `dict[Path,list[str]]` (C38)
- 7 pre-existing test assertion failures (moedularizer mock, json-repair mock, indent assertions)

### Changed
- Test suite: 676 tests across 18 files, 88.22% coverage (+61 gap-closure tests)
- 9 RNA annotation YAMLs refreshed post-Maat healing; 2 DNA docstrings added to coordinator
- WD-40 cleanup: stale audit artifacts removed
- Bare-except sites debt-commented (4 locations in coordinator.py and cli.py)
- CWD-dependent resolution paths annotated as last-resort in 5 files

## [1.0.0a4] - 2026-06-12

### Added
- Rust advisory scanning (alpha) with Cargo/Clippy oracle support
- `--rust`, `--cargo`, `--cargo-clippy` CLI flags
- Decision engine with confidence scoring and delete-bias correction
- Import intent detection (re_export, typing, registration, side_effect, try_block, usage)
- Fixer safety guards (destructive_allowed, suppression markers)
- Architecture advisor for circular dependency detection
- Residue analyzer for declared-but-unwired behavior detection
- Type analyzer for mypy/pyright subprocess diagnostics

### Fixed
- Import deduplication flow (Ruff F401 evidence wins)
- SubjectKey normalization (alias excluded from identity)
- Evidence auto-ID counter

## [1.0.0a3] - 2026-05-26

### Added
- Analysis coordinator with multi-analyzer orchestration
- Decision models (DecisionCandidate, Action dataclasses)
- Confidence scoring pipeline
- Evidence dataclass with auto-ID

## [1.0.0a2] - 2026-05-16

### Added
- FIX mode with Python, JSON, JSONL, YAML strategies
- SCAN mode with import analyzer and lint analyzer
- CLI entry point with argument parsing
- Strategy registry with lazy loading

## [1.0.0a1] - 2026-04-22

### Added
- Initial release
- Project structure and core interfaces
