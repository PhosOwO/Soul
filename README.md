# Soul

Soul is a lightweight project memory layer for AI agents.

Its core idea is simple:

```text
files as memory + current state snapshot = Soul
```

Soul is not a planner, executor, or chat-history summarizer. It keeps project memory inspectable, keeps current cognition small, and lets agents such as DeepSeek Harness or Codex use that state without stuffing every past conversation into context.

## 1. Files As Memory

Soul uses file-based memory because files are inspectable, portable, and easy to version, search, copy, delete, or review.

That is why Soul uses ReMe for ordinary memory/evidence.

ReMe writes agent episodes into a local workspace:

```text
.soul/reme/
  daily/
  session/
  resource/
  metadata/
```

The important point is that memory remains evidence. Soul does not copy the full memory body into state. It keeps references such as:

```text
reme://daily/2026-08-16/example.md:11-21#chunk-id
```

If an agent needs more background, it can follow the reference and read the original ReMe evidence.

## 2. State Snapshot

Long-term memory is too large to inject every turn, so Soul keeps a small Current State snapshot:

```text
.soul/state/
  state.json
  patch_proposals.jsonl
  soul.db
```

Current State is the accepted project cognition:

- active constraints
- working hypotheses
- rejected directions
- decision gates
- open questions

State changes are proposed as patches first. They are not automatically accepted.

```text
Evidence
  -> State Patch Proposal
  -> Review / Apply
  -> New Current State
```

## 3. ReMe + State = Soul

Soul combines the two layers:

```text
.soul/
  reme/       ordinary memory and evidence
  state/      compact Current State snapshot
  traces/     readable links between ReMe evidence and State patches
```

The runtime loop is:

```text
before turn:
  inject only Soul Current State

after turn:
  write episode to ReMe
  search ReMe for evidence refs
  propose a Soul State Patch with refs only
```

This keeps context small while preserving traceability back to full evidence.

## 4. Use With Harness And Codex

Soul currently exposes three command entry points:

```text
soul
soul-api
soul-mcp
```

DeepSeek Harness can use Soul through the local HTTP API:

```bash
soul-api --project-dir . --port 8765
```

Use the ReMe-backed memory mode in the dsh Soul context plugin:

```yaml
- id: soul-context
  name: '@deepseek-ai/dsh-soul-context'
  config:
    baseUrl: http://127.0.0.1:8765
    scope: project
    stateLimit: 8
    memoryMode: soul_reme
    remeSearchLimit: 5
```

Codex can use Soul through the MCP server:

```bash
soul-mcp --project-dir .
```

The MCP tools are:

- `get_projected_state`
- `observe_evidence`
- `propose_patch`
- `apply_patch`

## 5. Quick Start

Install from this repository with Python:

```bash
pip install -e .
```

Or install the local npm wrapper:

```bash
npm install -g .
```

The npm wrapper is thin: it launches the Python Soul package. If Python is not on `PATH`, set:

```bash
SOUL_PYTHON=/path/to/python
```

Initialize a project:

```bash
soul init
```

Show the current state:

```bash
soul state show
```

Get compact context for an agent:

```bash
soul context
```

Run the local API for DeepSeek Harness:

```bash
soul-api --project-dir . --port 8765
```

Run the MCP server for Codex:

```bash
soul-mcp --project-dir .
```

Run the ReMe integration dry-run:

```bash
python integrations/dsh_reme_v0_1/run_dryrun.py
```

Expected runtime layout:

```text
.soul/
  state/
    state.json
    patch_proposals.jsonl
    soul.db
  reme/
    daily/
    metadata/
    session/
    resource/
  traces/
    reme_state_trace.md
```

Public `npm install soul` is not available yet. The current npm wrapper works for local/private installs; publishing to npm still requires confirming the package name and removing `private: true` from `package.json`.
