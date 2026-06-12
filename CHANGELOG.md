# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Native yapf-like line wrapping + blank line management in PythonStrategy (deferred to next release)

### Fixed
- Hanging test: `test_coordinator_fix_interactive_mode` infinite loop when moedularizer absent
- PythonStrategy: skip black when `indent_size != 4`, fall through to AST-based reindent
- JSONLStrategy: pass `indent_size` through to `json.dumps` for per-line formatting
- 7 pre-existing test assertion failures (moedularizer mock, json-repair mock, indent assertions)

### Changed
- Added `moedularizer>=0.1.1` as optional dependency (`[refactor]` extra)
- Test suite: 631 tests, 91.91% coverage

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
