# Soul-ReMe Integration Design

## Purpose

Soul uses ReMe as the durable evidence and long-memory substrate, and keeps only compact Current State in Soul-owned state
files. The target architecture is:

```text
Codex / DeepSeek Harness / TraeX episode
  -> ReMe auto_memory
  -> ReMe session/ + daily/
  -> optional ReMe auto_dream
  -> ReMe digest/ + interests.yaml
  -> Soul State Patch from evidence refs
  -> .soul/state/STATE.md
  -> next agent turn receives only projected Current State
  -> read / traverse ReMe evidence only when causality is needed
```

This design intentionally avoids copying full conversations or raw evidence into Soul state. ReMe owns traceable files,
indexes, links, and long-term memory evolution. Soul owns the reviewed state layer used for default agent injection.

## Source-of-Truth Boundaries

| Layer | Owner | Purpose | Source of truth |
|-------|-------|---------|-----------------|
| Conversation source records | ReMe | Preserve filtered agent/user messages as evidence | `.soul/reme/session/dialog/*.jsonl` |
| Daily memory | ReMe | Human-readable daily cards and source-linked episode summaries | `.soul/reme/daily/**` |
| Long memory | ReMe | Consolidated personal, procedure, and wiki memory nodes | `.soul/reme/digest/**` |
| Search/link metadata | ReMe | Rebuildable BM25/vector/link indexes | `.soul/reme/metadata/**` |
| Current State | Soul | Compact accepted/tentative project cognition for injection | `.soul/state/STATE.md` and `.soul/state/state.json` |
| State patch queue | Soul | Reviewable state changes derived from evidence refs | `.soul/state/patch_proposals.jsonl` |
| State/evidence trace | Soul | Readable mapping from ReMe refs to Soul patch IDs | `.soul/traces/reme_state_trace.md` |

ReMe's default workspace is `.reme/`, but Soul uses `.soul/reme/` by default to keep the project-local memory boundary
under one runtime directory. Inside that workspace, Soul must preserve ReMe's native layout and semantics.

## Adjacent Responsibilities

Soul and ReMe both deal with memory, but they should operate at different layers. The design must keep these adjacent
areas explicit so implementation does not duplicate or bypass ReMe.

### ReMe Digest vs Soul Current State

ReMe `digest/` stores reusable long-term memory nodes:

- `digest/procedure/` for runbooks, workflows, failed attempts, and successful paths.
- `digest/personal/` for user, team, and project preferences.
- `digest/wiki/` for domain concepts, architecture facts, and decision precedents.

Soul Current State stores only the small set of cognition that should affect the next agent turn by default:

- active constraints
- decision gates
- rejected directions
- working hypotheses
- tentative observations
- open questions
- accepted beliefs

Rule: do not copy ReMe digest bodies into Soul state. A Soul state item may point to digest evidence with refs, but its
statement must be compact enough to inject by default.

Example:

```text
ReMe digest/procedure/typescript-build-oom.md
  = full diagnostic runbook, known symptoms, failed paths, sources, related links

Soul state item
  = "When builds stall with memory growth, check type-checking subprocess memory before cache or minifier changes."
  evidence_refs = [digest/procedure/typescript-build-oom.md:...]
```

### ReMe Daily Summary vs Soul Patch Evidence

ReMe daily cards are readable memory artifacts. They can include what happened, conclusions, follow-ups, source links,
and narrative context.

Soul patch evidence is only a review payload for changing Current State. It should contain:

- compact summary
- evidence refs
- source ownership metadata
- review recommendation

Rule: patch evidence must not become a second daily note. If an investigator needs the full event, they should call
`read_evidence` on the ReMe ref.

### ReMe Proactive Topics vs Soul Open Questions

ReMe `proactive` reads `daily/<date>/interests.yaml`, which contains topics worth attention. These are candidates, not
accepted project state.

Soul `open_question` and `decision_gate` items are reviewed state items that affect default agent behavior.

Rule: proactive topics should not be injected automatically and should not automatically become Soul state. They may
create a State Patch only when a host or user explicitly asks Soul to evaluate them as project state.

### ReMe Search Context vs Soul Injection

ReMe search, read, and traverse are retrieval tools. They are used when the agent needs evidence, causality, or background
context.

Soul injection is the default pre-turn context. It must stay small and state-shaped.

Rule: `get_projected_state` must not include ReMe search snippets, daily content, digest bodies, or proactive topics by
default. It may include evidence ref IDs or paths only when they are part of a compact state item.

## Soul Consumption Model

Soul consumes ReMe in four distinct ways:

| Consumption | ReMe operation | Soul behavior | Default injection? |
|-------------|----------------|---------------|--------------------|
| Evidence capture | `auto_memory` | Record episode and propose State Patch from refs | No |
| Evidence retrieval | `search`, `read`, `traverse` | Explain or validate why a state item exists | No |
| Long-memory consolidation | `auto_dream` | Let ReMe update `digest/`; Soul may later cite digest refs | No |
| Proactive candidate topics | `proactive` | Expose optional topics for host policy or explicit review | No |

Only reviewed Soul Current State is injected by default. ReMe output becomes default context only after it is distilled
into a Soul State Patch and that patch is accepted or intentionally surfaced as a tentative/open state item.

## Runtime Lifecycle

### Before Turn

1. Soul loads `.soul/state/STATE.md` or regenerates it from `.soul/state/state.json` if missing.
2. Soul projects task-relevant state items from the Current State model.
3. The agent receives only the projected Soul Current State by default.
4. ReMe `proactive` topics are not injected automatically. They are exposed as an optional side channel because the host
   agent or product policy must decide whether to mention proactive topics.

### After Turn

1. The host calls `observe_evidence` with a stable `session_id`, task/outcome text, and normalized messages/events.
2. Soul preflights ReMe:
   - `reme` CLI exists.
   - a compatible ReMe service is discoverable or startable.
   - `.soul/reme/` exists and has the native ReMe workspace layout.
   - LLM configuration is available when model-powered jobs are requested.
3. Soul records the episode through ReMe:
   - Preferred: `reme auto_memory session_id=... messages=... memory_hint=... date=...`.
   - Fallback only when explicitly configured: `reme write` or the existing `daily_write` job.
4. Soul searches related memory:
   - `reme search query=... limit=...`
   - include direct refs to newly written daily/session paths even when search returns no result yet.
5. Soul converts ReMe search metadata into evidence refs:
   - chunk path, line range, score, chunk ID.
   - link expansion summaries when present.
   - source conversation path when present.
6. Soul proposes a State Patch whose evidence body contains refs and summary only.
7. The patch is not applied automatically. Applying the patch updates `state.json` and `STATE.md`.

### Host Integration Modes

| Host | Before turn | After turn | Manual tools |
|------|-------------|------------|--------------|
| DeepSeek Harness | HTTP before-turn plugin calls `/state` | HTTP after-turn plugin calls `/reme/transition/propose` | HTTP endpoints |
| Codex | MCP `get_projected_state` | MCP `observe_evidence` | MCP tools |
| TraeX | `UserPromptSubmit` project hook injects Soul Current State | `Stop` project hook records ReMe-backed evidence | Project MCP server exposes Soul tools |

TraeX uses project resources under `.trae/`:

- `.trae/.mcp.json` registers `soul-mcp`.
- `.trae/hooks.json` registers `UserPromptSubmit` and `Stop` command hooks.
- `.trae/hooks/soul_user_prompt_submit.py` reads Soul Current State and returns hook `additionalContext`.
- `.trae/hooks/soul_stop.py` records the completed turn through `propose_reme_transition`.

The TraeX hooks must be visible in `/hooks` and trusted before they run. The MCP server should be visible in `/mcp`.

### Consolidation

`auto_dream` is not part of the synchronous `observe_evidence` critical path. It should run on a schedule or by explicit
command:

```text
reme auto_dream date=<YYYY-MM-DD> hint="Prioritize project decisions, constraints, procedures, and user preferences."
```

`auto_dream` consumes ReMe daily inputs and writes reusable digest nodes:

- `digest/procedure/*.md` for workflows, runbooks, failed paths, and successful diagnostic procedures.
- `digest/personal/*.md` for user or team preferences and durable project context.
- `digest/wiki/*.md` for concepts, decisions, architecture facts, and domain knowledge.

Soul should not duplicate digest nodes into Current State. It should search/read digest when a state patch or a future
answer needs background evidence.

## ReMe Adapter Contract

The adapter should move from a low-level job wrapper toward ReMe's public agent-facing surface:

| Method | Preferred ReMe operation | Notes |
|--------|--------------------------|-------|
| `preflight()` | `command -v reme`, `reme find_reme`, `reme health_check` | Distinguish missing CLI, stopped service, unhealthy service, and missing LLM config. |
| `auto_memory()` | `reme auto_memory` | Main conversation persistence path. Requires LLM config. |
| `search()` | `reme search` | Default retrieval uses BM25 and wikilinks; vectors are optional. |
| `read()` | `reme read` | Reads Markdown paths and line ranges from evidence refs. |
| `traverse()` | `reme traverse` | Explores wikilink neighbors for causality and related context. |
| `auto_dream()` | `reme auto_dream` | Manual or scheduled consolidation from daily to digest. Requires LLM config. |
| `proactive()` | `reme proactive` | Reads `interests.yaml`; no LLM call. |
| `reindex()` | `reme reindex` | Repairs derived metadata without rewriting user memory files. |

The existing `daily_write` path remains a legacy fallback, not the primary integration. It can be useful for offline tests
or deployments that intentionally avoid LLM-powered `auto_memory`.

## API and MCP Contract

### `observe_evidence`

Default behavior:

```json
{
  "memory_mode": "soul_reme",
  "reme": {
    "write_mode": "auto_memory",
    "search_limit": 5,
    "auto_dream": false
  }
}
```

Soul writes host evidence to the project-local ReMe workspace at `.soul/reme`.
Standard Codex, TraeX, and DeepSeek Harness integrations should not override
the write workspace.

Inputs:

- `task`: current task or user request.
- `outcome` / `summary`: assistant result.
- `session_id`: stable host session ID. Required for high-quality ReMe memory.
- `messages`: normalized conversation messages when available.
- `events`: optional lifecycle events, tool summaries, or host metadata.
- `reme.date`: optional `YYYY-MM-DD`; otherwise ReMe derives from message timestamps or current date.
- `reme.memory_hint`: optional guidance for `auto_memory`.

Outputs:

- `memory_mode`
- `reme_write`: job result metadata from `auto_memory` or fallback write path.
- `reme_search`: search result metadata.
- `evidence_refs`: structured refs suitable for patch evidence.
- `patch_proposal`
- `trace_path`

Failure behavior:

- Missing ReMe CLI: fail with a clear install/setup error.
- Stopped service: fail with service discovery/start instructions, or start only if the host explicitly enables managed
  service startup.
- Missing LLM config for `auto_memory`: fail clearly unless `write_mode=fallback_daily_write` is explicitly set.
- Search index lag: keep the direct write refs and mark `search_status=index_pending`.

### `read_evidence`

New MCP/API tool for causality:

```json
{
  "path": "daily/2026-08-17/example.md",
  "start_line": 1,
  "end_line": 40
}
```

It calls `reme read`, returns the content, and never modifies Soul state.

### `trace_evidence`

New MCP/API tool for graph context:

```json
{
  "path": "digest/procedure/typescript-build-oom.md",
  "depth": 1,
  "direction": "both"
}
```

It calls `reme traverse`, returns neighboring nodes, and helps answer "why do we believe this?" without loading the whole
workspace.

### `consolidate_memory`

New explicit tool or CLI command:

```json
{
  "date": "2026-08-17",
  "hint": "Prioritize project decisions, constraints, procedures, and user preferences.",
  "scan_days": 2,
  "max_units": 5
}
```

It calls `reme auto_dream`. This should not run silently after every turn because it can require LLM credentials, take
time, and rewrite digest memory.

### `get_proactive_topics`

New optional tool:

```json
{
  "date": "2026-08-17",
  "include_content": false
}
```

It calls `reme proactive`. Missing `interests.yaml` is an empty state, not an error.

## Evidence Ref Shape

Soul should preserve enough ReMe metadata to support audit and follow-up reads:

```json
{
  "type": "reme_file_chunk",
  "path": "daily/2026-08-17/session-summary.md",
  "chunk_id": "chunk-1",
  "start_line": 12,
  "end_line": 28,
  "score": 2.5,
  "source_conversation": "session/dialog/session-1.jsonl",
  "links": {
    "outlinks": ["digest/procedure/build-debugging.md"],
    "inlinks": ["daily/2026-08-17.md"]
  }
}
```

Soul patch evidence may include a compact list of these refs, but not the full matched content.

## Migration Plan

### Phase 1: Adapter and Contract

1. Add adapter methods for `auto_memory`, `traverse`, `auto_dream`, `proactive`, and service discovery.
2. Keep `daily_write` as `write_mode=fallback_daily_write`.
3. Extend tests with fake ReMe adapter results for each operation.
4. Keep existing `observe_evidence` response fields stable where possible.

### Phase 2: Default Write Path

1. Change `propose_reme_transition` to call `auto_memory` by default.
2. Require or synthesize a stable `session_id`.
3. Accept normalized `messages` in MCP/HTTP.
4. Add direct refs from `auto_memory` output to avoid relying only on immediate search results.

### Phase 3: Evidence Reading Tools

1. Add MCP/API `read_evidence`.
2. Add MCP/API `trace_evidence`.
3. Include examples in README.

### Phase 4: Consolidation

1. Add CLI/API/MCP `consolidate_memory`.
2. Add `get_proactive_topics`.
3. Document that proactive topics are optional side-channel context, not default prompt injection.

### Phase 5: Service Management

1. Implement `find_reme` and `health_check` support.
2. Decide whether Soul may start a ReMe service automatically or should only report setup instructions.
3. Add configuration for managed service startup, port, workspace, and LLM-required jobs.

## Test Strategy

Unit tests should not require a real ReMe service. Use fake adapter results to verify Soul contracts:

- `observe_evidence` calls `auto_memory` by default.
- `write_mode=fallback_daily_write` uses the legacy path.
- missing LLM config for `auto_memory` fails with a typed setup error.
- search results plus link expansion become evidence refs.
- direct write refs are retained when search returns no results.
- `read_evidence` maps refs to `reme read path/start_line/end_line`.
- `trace_evidence` maps paths to `reme traverse`.
- `consolidate_memory` calls `auto_dream` and reports failed paths without checkpointing them in Soul.
- `get_projected_state` continues to inject only Soul Current State, not ReMe daily/digest bodies.

Integration tests may run against a real ReMe install only when `REME_INTEGRATION=1` is set.

## Open Decisions

1. Should Soul automatically start a ReMe service, or only use an existing healthy service?
2. Should `auto_dream` be exposed only as explicit command/tool, or also as a scheduled background task managed by Soul?
3. Should `.soul/reme/` remain the default workspace, or should users be encouraged to set a shared absolute ReMe
   workspace when multiple projects should share memory?
4. Should failed `auto_memory` calls block patch proposal creation, or should Soul allow explicit fallback writes in
   low-capability environments?
