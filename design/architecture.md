# Architecture Decision Record: imodent Residue Recovery

## ADR-001: Modular Analyzer/Fixer Pipeline With Intent Preservation

### Context

imodent is not primarily an import remover or a wrapper around ruff. In
LLM-authored codebases, unused imports, orphan helpers, ignored CLI flags, and
dead-looking branches are often unfinished capabilities. The system must recover
intent before deleting code.

### Decision

Use a modular analysis pipeline, but make **intent-preserving findings** the
central contract.

```text
CLI / API
  -> project context + config
  -> file discovery + dependency graph
  -> analyzers
       imports: usage plus intent classification
       residue: declared behavior without execution path
       optional lint: external evidence only
  -> findings
       unused_import_file
       import_intent
       declared_behavior_unwired
  -> fix/advice
       wire / export / register / test / quarantine / mark unsupported
       delete only after intent review
```

### Rationale

- Plugin analyzers remain the right shape because import analysis, residue
  detection, and advisory checks are separable.
- The previous design collapsed too quickly into lint/fix behavior. That is
  wrong for LLM residue: the same signal can mean either garbage or an unwired
  feature.
- Fixers must not decide from one signal. They operate only after analyzers have
  preserved enough evidence for a recovery-first choice.

### Core Components

```text
imodent/
├── analysis/
│   ├── context.py        # FileInfo, AnalysisConfig, AnalysisContext
│   ├── findings.py       # Finding, FixOption, Location, Advice
│   └── coordinator.py    # Selects analyzers from config
├── analyzers/
│   ├── imports.py        # unused imports + import intent evidence
│   └── residue.py        # declared-but-unwired behavior
├── fixers/
│   └── imports.py        # duplicate removal, recovery-first import options
├── graph/
│   ├── imports.py        # ImportInfo extraction
│   └── dependency.py     # module graph + symbol tracing
└── project/
    ├── config.py         # project config loading
    └── project_context.py
```

### Finding Policy

| Finding | Meaning | Fixable | Auto-fix |
|---|---|---:|---:|
| `duplicate_import` | Mechanically redundant import line | yes | yes |
| `unused_import_file` | Normal import appears unused in file | yes | no |
| `import_intent` | Import is not directly used but carries typing/re-export/registration/side-effect intent | no | no |
| `declared_behavior_unwired` | CLI/config/API declares behavior without executor | no | no |

### Fix Policy

Unused imports must present options in recovery order:

1. `investigate`: search/wire intended usage
2. `keep`: preserve with reason or typing/public API evidence
3. `false_positive`: record analyzer miss
4. `delete`: terminal cleanup after intent evidence is exhausted

Delete is never the first recommendation for residue-like findings.

### Current Vertical Slice

The first residue slice detects lint behavior that is declared but not wired:

- `--lint` exists in the CLI
- `analyze_lint` plumbing exists
- `AnalysisConfig.check_lint` exists
- no `LintAnalyzer` or lint executor exists

The output is a non-fixable `declared_behavior_unwired` finding with cluster
evidence and recommended non-destructive actions.

### Guardrails

| Rule | Status | Evidence |
|---|---|---|
| G-SCOPE-1 | pass | Components trace to LLM residue recovery and existing scan CLI |
| G-PATTERN-1 | pass | Plugin pipeline is justified by independent analyzer/fixer concerns |
| G-CONTRACT-1 | pass | Findings carry type, severity, fixability, safety, location, and structured evidence |
| G-SIMPLE-1 | pass | No new graph engine or external service is required for the first slice |

### Negative Space

- Do not import CodeGraph or Moedularizer into imodent.
- Do not make ruff the core authority.
- Do not auto-delete unused imports by default.
- Do not build a full semantic patch engine before residue findings are stable.
