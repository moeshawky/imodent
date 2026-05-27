# Hive-Mind Compounded Bug Protocol v2

**Adapted from:** COMPOUNDED_BUG_PROTOCOL_v2.md  
**Target:** hive_mind_classic (Python/FastAPI/MCP backend)  
**Predecessor findings:** 1A (persist dual-decision), 1B (grounding dual-decision), 2A (silent except:pass), 2C (TOCTOU steward)

---

## 0. Architecture Map

```
                               ┌─────────────────────────┐
                               │   MCP Clients (Pi, OC)  │
                               └──────┬────────┬─────────┘
                                      │ HTTP   │ MCP Streamable HTTP
                                      ▼        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        FastAPI Server (:3000)                       │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ BearerAuth   │  │ /hooks/*     │  │ /mcp (FastMCP)           │  │
│  │ Middleware   │  │ (9 endpoints)│  │ 63→61 tools, 13 domains  │  │
│  └──────────────┘  └──────┬───────┘  └───────────┬──────────────┘  │
│                           │                       │                  │
│              ┌────────────┼───────────────────────┼──────────┐      │
│              │            ▼                       ▼          │      │
│              │  ┌──────────────┐    ┌──────────────────────┐ │      │
│              │  │ Hook Handlers│    │  Tool Wrappers       │ │      │
│              │  │ (session,    │    │  (reasoning.py,      │ │      │
│              │  │  pre-tool,   │    │   memory.py,         │ │      │
│              │  │  post-tool,  │    │   inbox.py, etc.)    │ │      │
│              │  │  stop, etc.) │    └──────────┬───────────┘ │      │
│              │  └──────┬───────┘               │              │      │
│              │         │                       │              │      │
│              │         └───────────┬───────────┘              │      │
│              │                     ▼                          │      │
│              │  ┌──────────────────────────────────────┐     │      │
│              │  │           Backend Modules             │     │      │
│              │  │                                      │     │      │
│              │  │  reasoning/   memory.py   mailbox    │     │      │
│              │  │  chain.py     steward.py  identity/  │     │      │
│              │  │  gates.py     conductor   janitor    │     │      │
│              │  │  reward.py    evidence_   plm.py     │     │      │
│              │  └───┬──────┬──────┬───────┬───────────┘     │      │
│              │      │      │      │       │                  │      │
└──────────────┼──────┼──────┼──────┼───────┼──────────────────┘
               │      │      │      │       │
               ▼      ▼      ▼      ▼       ▼
          ┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
          │Postgres│ │ Zvec   │ │ SQLite   │ │  Valkey  │
          │(asyncpg│ │(file)  │ │(aiosqlite│ │ (Redis)  │
          │pool)   │ │        │ │)         │ │          │
          └────────┘ └────────┘ └──────────┘ └──────────┘
```

---

## 1. Boundary Map (Phase 0)

### 1.1 Function Call Boundaries (hive-mind's primary boundary type)

| # | Source | Sink | Data | Contract |
|---|--------|------|------|----------|
| B1 | MCP tool wrapper | Backend module | Tool params → function args | Tool signature must match backend signature |
| B2 | HTTP hook handler | Backend module | Request JSON → domain objects | Hook schema must match backend expectation |
| B3 | productive_reason tool | reasoning/chain.py:process_thought | Phase, thought, params | 13-param tool → internal chain state |
| B4 | reasoning/chain.py | reasoning/gates.py | Chain state, thought text | Gate validators parse thought for keywords |
| B5 | reasoning/gates.py | reasoning/reward.py | Gate status, chain outcome | Reward computes from gate outcome |
| B6 | reasoning/gates.py | reasoning/grounding.py | Session + counter | Grounding checks EvidenceStore |
| B7 | Any tool | EvidenceStore.record() | tool_name, content, metadata | Evidence has counter, git_head, session_id |
| B8 | Hook post-tool | steward.py | Tool name, result | Steward tracks counters per session |
| B9 | steward.py | reasoning/chain.py | Steward hints injected as context | Hints must match chain phase expectations |
| B10 | identity/resolver.py | All tools | session_id, project_id, designation | Every tool resolves identity the same way |
| B11 | memory_store | memory.py:store_semantic | content, category, project | Routes to Zvec collection via _collection_for() |
| B12 | memory_search | memory.py:search_all | query, project | Searches SQLite blocks + Zvec |
| B13 | mailbox.send | Valkey pub/sub | message JSON | Serialization must match subscriber expectation |
| B14 | Conductor loop | StewardState | Session metrics | Background thread reads foreground state |
| B15 | Janitor loop | Postgres | DELETE queries | Cleanup must not race with active writes |

### 1.2 Persistence Boundaries

| # | Python | Storage | Serialization | Contract risk |
|---|--------|---------|---------------|---------------|
| P1 | asyncpg | Postgres | asyncpg params → SQL | JSONB codec must be registered |
| P2 | zvec_client | Zvec files | Python dict → Zvec document | Schema fields must match query filters |
| P3 | aiosqlite | SQLite | Key-value blocks | Concurrent access to same block |
| P4 | reasoning/_persist | reasoning_chains table | Chain state → JSONB phases | Column types must match Python types |

### 1.3 Time Boundaries (Concurrency)

| # | Validate | Use | Window | Risk |
|---|----------|-----|--------|------|
| T1 | steward checks session stale | steward evicts session | Between re-check and _evict_session() | TOCTOU — fixed in 2C |
| T2 | gate reads chain state | gate writes chain state | Between read and write | Concurrent tool calls |
| T3 | Zvec write buffer append | Zvec write buffer flush | Between append and flush | Buffer size / ordering |
| T4 | Conductor reads StewardState | StewardState updated by hook | Between read and update | Stale metrics |

### 1.4 Privilege Boundaries

| # | Lower | Higher | Transfer |
|---|-------|--------|----------|
| PR1 | MCP client (Bearer token) | MCP server | Token validated by BearerAuthMiddleware |
| PR2 | Agent (no token) | create_collection | Was gated by HIVE_MIND_TOKEN — NOW REMOVED |
| PR3 | MCP tool call (no ctx) | Identity resolution | _resolve_sid() resolves from transport context |

---

## 2. Hive-Mind Specific Attack Categories

### Category 1: Contract Mismatch

**The #1 bug class in hive-mind.** Python's dynamic typing makes contract violations silent.

#### Detection Procedure:

```
For each @mcp.tool() decorated function (tool wrapper):
  1. Identify the backend function it calls (e.g., memory.search_all)
  2. Diff the parameter names AND types:
     - Tool param annotation vs backend param expectation
     - Default values match?
     - Optional params handled correctly?
  3. Diff the return type:
     - Tool returns str, but what does backend return?
     - Is json.dumps() called when needed? (schedule_list bug)
  4. Check: does the backend function still exist? (docker_ctl.container_status bug)
```

#### Concrete Witness Template:
```python
# Given tool T with params P1..Pn calling backend B
# Invoke T with edge-case params and verify B receives correct values
# Check: default value propagation, None handling, type coercion
```

#### Known instances:
- `docker_ctl.container_status()` — called but never defined (B1, system.py→docker_ctl.py)
- `evidence_read`/_add — `_resolve_sid()` called without `await` (B10, evidence.py)
- `schedule_list` returns `list[dict]`, not `str` (B1, scheduler.py return type)
- `_get_instructions()` lists tools that don't exist / misses tools that do (B1, core.py)
- Tool count mismatch: __init__.py says 61, actual 63 (B1, docstring drift)

### Category 2: Authority Confusion

**Rare in hive-mind due to flat Python architecture, but present at MCP boundary.**

#### Detection Procedure:

```
For each MCP tool:
  1. Does it access data scoped to another session? (session isolation)
  2. Does it perform writes without evidence recording? (grounding gate bypass)
  3. Does it access Postgres without session-scoping WHERE clause?
```

#### Concrete Witness Template:
```python
# Session A calls tool → check if data from Session B is accessible
# Session A calls productive_reason → check if chain is isolated
```

#### Known instances:
- EvidenceStore accessed without session scoping (evidence_read with broken SID resolution)
- Reasoning chain persistence: chain_id vs session_id scoping (P4)

### Category 3: Temporal Vulnerability (TOCTOU)

**Present at every shared-state boundary.**

#### Detection Procedure:

```
For each shared state object (_chains dict, _steward_states dict, _gate_status dict):
  1. Find all readers of the state
  2. Find all writers of the state
  3. For each read→write sequence, check:
     - Is there a lock held between read and write?
     - Can a concurrent tool call or hook mutate state between?
     - Is the window bounded (timeout, retry)?
```

#### Known instances:
- Steward session eviction: check → evict without lock (fixed in 2C)
- Chain state: _chains dict modified by multiple tool calls
- Zvec write buffer: append→flush window
- StewardState counters: hook writes while conductor reads

### Category 4: Side Channel

**Low priority for hive-mind (no crypto, no multi-tenant secrets).**

#### Detection Procedure:
```
For each error path:
  - Does the error message leak internal state?
  - Does timing differ based on data validity?
  - Are stack traces exposed in MCP responses?
```

### Category 5: Capability Downgrade

**Relevant at serialization boundaries.**

#### Detection Procedure:
```
For each persistence boundary (P1-P4):
  1. What properties does data have in Python? (types, constraints, relationships)
  2. What properties survive serialization? (JSON has no datetime, sets, or tuples)
  3. Does the reader know which properties were lost?
```

#### Known instances:
- asyncpg JSONB codec: if not registered, JSONB comes back as string (P1)
- Zvec schema fields: old collections lack project/source_session fields (P2)
- datetime serialization: Postgres timestamptz vs Python datetime vs JSON string

### Category 6: Protocol Downgrade

**Relevant at MCP transport and API version boundaries.**

#### Detection Procedure:
```
For each version-negotiated boundary:
  1. MCP protocol version (currently 2024-11-05)
  2. Tool parameter compatibility (V3 compat, ctx parameter)
  3. Hook response format compatibility
```

---

## 3. Hive-Mind Dual-Decision Map

**The most common compound bug pattern in this codebase.** Two modules independently re-implement the same decision logic with different rules.

| Decision | Module A | Module B | Divergence | Fixed? |
|----------|----------|----------|------------|--------|
| Is this tool a persist operation? | reasoning/chain.py (_PERSIST_TOOLS) | reasoning/gates.py (PERSIST_STEMS) | Different sets, different matching logic | Yes (1A) |
| Has grounding evidence been recorded? | reasoning/gates/grounding.py (EvidenceStore) | reasoning/reward.py (regex on thought text) | EvidenceStore vs text-pattern proxy | Yes (1B) |
| Is the session stale? | hooks/steward.py (last_touched) | steward.py (StewardState) | Same data, separate access | Partial |
| What is the session ID? | hooks (HTTP headers) | MCP tools (transport Context) | Different resolution paths, same target | Ongoing |

### Dual-Decision Detection (systematic):

```bash
# 1. Find all functions that implement the same semantic check
grep -rn "def.*check\|def.*is_\|def.*has_\|def.*validate" hive_mind/ --include="*.py"

# 2. For each pair of functions with overlapping semantics, verify:
#    a. Same input types
#    b. Same output type  
#    c. Same edge cases handled
#    d. Same authority to make the decision
#    e. Both called from the same callers, or one is dead code

# 3. Flag any pair where (a-d) differ
```

---

## 4. Silent Error Drop Map

**Errors caught and silently discarded at boundaries.**

```bash
# Find every silent except block
grep -rn "except.*:" hive_mind/ --include="*.py" | grep -v "log\.\|raise\|#\|pass  #" | grep "pass"
```

| Location | Exception caught | Impact | Fixed? |
|----------|-----------------|--------|--------|
| evidence_store.py:115 | Exception | Evidence persist failure silently dropped | Yes (2A) |
| reasoning.py:88-92 | Exception in evidence recording | Evidence recording failure silently dropped | No |
| inbox.py:70-71 | Exception | Inbox check failure returns error string (acceptable) | N/A |
| memory.py:178 | Exception | Dedup check failure silently passed | No |

### Detection:
```bash
# Find every try/except that doesn't log
grep -B5 "except" hive_mind/**/*.py | grep -v "log\."
```

---

## 5. Session Identity Drift Map

**Session ID resolves differently depending on call path.**

| Call path | Resolution method | SID source |
|-----------|-------------------|------------|
| HTTP hook → handler | `_resolve_sid(None, req.session_id)` | Request JSON field |
| MCP tool → tool wrapper | `_resolve_sid(ctx, session_id or "")` | Transport Context or explicit param |
| Background conductor | `_chains.keys()` | In-memory dict |
| Evidence store | `EvidenceStore.get(sid)` | Whatever SID was resolved above |
| Reasoning chain | `_chains[sid]` | Must match EvidenceStore key |

### Detection:
```python
# For a single logical session, trace SID through every boundary
# Verify: hook SID == MCP tool SID == chain SID == evidence SID
```

---

## 6. Concrete Witness Templates

### Template 1: Contract Mismatch Witness

```python
"""
WITNESS: Tool T calls backend B with mismatched contract.

STEPS:
1. Identify T's @mcp.tool() parameter annotations
2. Identify B's function signature
3. Find param that exists in T but not in B, or vice versa
4. Find param where type annotation differs
5. Invoke T with edge case: None, empty string, very long string, special chars
6. Verify B receives correct value OR appropriate error

EXPECTED: If contract matches, all params pass through correctly
ACTUAL: [fill in]
"""
```

### Template 2: Dual-Decision Witness

```python
"""
WITNESS: Module A and Module B decide X differently.

STEPS:
1. Identify decision X (e.g., "has grounding evidence?")
2. Show Module A's implementation of X-decider
3. Show Module B's implementation of X-decider
4. Construct input I where A.decide(I) != B.decide(I)
5. Trace what happens when A's decision is used vs B's decision
6. Show divergent behavior

EXPECTED: A.decide(I) == B.decide(I) for all I
ACTUAL: For I = [fill in], A says [X] but B says [Y]
"""
```

### Template 3: TOCTOU Witness

```python
"""
WITNESS: State S is validated at T1, used at T2, and changed between.

STEPS:
1. Identify validate(V) and use(U) of shared state S
2. Identify concurrent writer W that can modify S
3. Construct timeline:
   T1: V checks S → passes
   T1.5: W modifies S
   T2: U uses S → operates on stale/wrong state
4. Show that lock/protection is absent or insufficient

EXPECTED: Lock covers check-then-act atomically
ACTUAL: Window between V and U is [N]ms, lock is [absent/insufficient]
"""
```

### Template 4: Silent Error Drop Witness

```python
"""
WITNESS: Error at boundary B is caught and silently discarded.

STEPS:
1. Identify boundary B where source calls sink
2. Show try/except that catches Exception
3. Show that except block does not log, raise, or record
4. Force an error at the boundary (e.g., DB down, schema mismatch)
5. Verify the error is invisible to caller

EXPECTED: Error is logged, propagated, or recorded as dead-letter
ACTUAL: Error is silently discarded at [file:line]
"""
```

---

## 7. Fix Priority (hive-mind specific)

1. **P0 — Contract violation causing crash**: Called function doesn't exist (`docker_ctl.container_status()`), called with wrong signature (`await` missing)

2. **P1 — Dual-decision divergence**: Two modules independently decide same thing differently. Fix by UNIFICATION into single source of truth, not patching both.

3. **P2 — Silent error drop at persistence boundary**: `except: pass` at Postgres/Zvec/Valkey boundaries. Fix by dead-letter queue + logging + metric increment.

4. **P3 — TOCTOU at shared state**: validate→use window without lock. Fix by lock covering check-then-act.

5. **P4 — Session identity drift**: Different SID resolution paths produce different IDs for the same session. Fix by canonical resolver.

6. **P5 — Contract drift (docs/code)**: Tool signatures change, docs/instructions don't. Fix by generating docs from code.

7. **P6 — Capability downgrade (serialization)**: JSONB codec not registered, datetime not timezone-aware, old Zvec collections lack fields. Fix at init time.

---

## 8. Automated Detection Scripts

### 8.1 Find all dual-decision candidates

```bash
# Find functions with overlapping semantics by name pattern
grep -rn "def.*is_\|def.*has_\|def.*check_\|def.*validate_\|def.*can_" \
  hive_mind/ --include="*.py" | grep -v __pycache__ | sort -t: -k3
```

### 8.2 Find all silent except blocks

```bash
# Find try/except blocks without logging, raising, or recording
python3 -c "
import ast, os

for root, dirs, files in os.walk('hive_mind'):
    for f in files:
        if not f.endswith('.py'): continue
        path = os.path.join(root, f)
        try:
            with open(path) as fh:
                tree = ast.parse(fh.read(), path)
        except: continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if isinstance(handler.type, ast.Name) and handler.type.id in ('Exception', 'BaseException'):
                        body_text = ast.unparse(handler.body) if hasattr(ast, 'unparse') else str(handler.body)
                        if 'log.' not in body_text and 'raise' not in body_text:
                            print(f'{path}:{handler.lineno}: silent except {handler.type.id}')
"
```

### 8.3 Find all MCP tool → backend contract mismatches

```bash
# For each @mcp.tool(), find the backend function it calls, then diff signatures
# This requires semi-automated extraction — see Phase 4 templates above
```

### 8.4 Find all time-of-check-to-time-of-use candidates

```bash
# Find shared state dicts and trace read→write sequences
grep -rn "^\w*_\w*\[.*\]\s*=" hive_mind/ --include="*.py"  # state writes
grep -rn "^\w*_\w*\[.*\]" hive_mind/ --include="*.py"        # state reads (same dict)
```

---

## 9. Current Finding State

| ID | Category | Description | Fixed | Commit |
|----|----------|-------------|-------|--------|
| 1A | Dual-decision | Persist tool detection: chain.py vs gates.py different sets/logic | Yes | `5dcdd72` |
| 1B | Dual-decision | Grounding detection: EvidenceStore vs text-pattern regex | Yes | `63545be` |
| 2A | Silent drop | EvidenceStore except:pass at persist boundary | Yes | `b780136` |
| 2C | TOCTOU | Steward eviction: check→evict without lock | Yes | `04de186` |
| 3A | Contract | docker_ctl.container_status() called but never defined | No | — |
| 3B | Contract | evidence_read/_add: _resolve_sid() not awaited | No | — |
| 3C | Contract | schedule_list returns list[dict] not str | No | — |
| 3D | Contract | Tool count mismatch in __init__.py (partially fixed) | Partial | — |
| 3E | Contract | _get_instructions() stale | Partial | — |
| 3F | Contract | create_collection/list_collections vestigial after collection auto-management | Yes | Previous edit |
| 4A | Silent drop | reasoning.py evidence recording except:pass | No | — |
| 4B | Silent drop | memory_store dedup check except:pass | No | — |
| 5A | Identity drift | Session ID resolves differently in hooks vs MCP tools | Ongoing | — |

---

## 10. Quick Audit Checklist

Run these before any deployment of hive-mind_classic:

```
[ ] All @mcp.tool() → backend function contracts verified (grep -A5 '@mcp.tool' tools/*.py)
[ ] No silent except:pass at persistence boundaries (script 8.2)
[ ] No dual-decisions (script 8.1, manual review)
[ ] All TOCTOU windows closed (script 8.4, manual review)
[ ] All async functions awaited (grep 'def.*async\|_resolve_sid\|_resolve_identity')
[ ] All MCP tools return str (grep 'list\[dict\]' tools/*.py)
[ ] Tool count in __init__.py == actual @mcp.tool() count
[ ] _get_instructions() lists all current tools
[ ] docs/hive-mind-api-docs.md lists all current tools
[ ] AGENTS.md tool catalog matches reality
[ ] No unregistered JSONB codec paths (standalone scripts)
[ ] BearerAuthMiddleware covers all non-public paths
[ ] Steward lock covers all check-then-act sequences
```

---

*Adapted 2026-05-26 from COMPOUNDED_BUG_PROTOCOL_v2.md. Hive-mind specific categories, boundaries, and detection procedures added. All known findings cataloged.*
