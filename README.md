# Soul

Soul is a lightweight project memory and current-state layer for AI agents.

It helps an agent keep project continuity without injecting full chat history into every turn.

```text
files as memory + compact current state = Soul
```

## What It Does

Soul separates long memory from active cognition:

- ReMe stores ordinary memory and evidence as inspectable local files.
- Soul keeps a small accepted Current State snapshot.
- Agents receive the projected state, plus references back to full evidence when needed.

Soul is not a planner, executor, or chat-history summarizer. It is a local-first continuity layer for agents such as DeepSeek Harness and Codex.

## Current Status

Soul is an experimental MVP.

- Python CLI/API/MCP entry points are available.
- ReMe-backed memory is in active integration.
- DeepSeek Harness support is experimental.
- Public package publishing is not planned yet; the npm package currently provides local command wrappers only.

## Quick Start

Install from this repository:

```bash
pip install -e .
```

Initialize Soul in a project:

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

Run the local HTTP API:

```bash
soul-api --project-dir . --port 8765
```

Run the MCP server:

```bash
soul-mcp --project-dir .
```

Optional local npm wrappers:

```bash
npm install -g .
```

If Python is not on `PATH`, set:

```bash
SOUL_PYTHON=/path/to/python
```

## Runtime Layout

Soul writes runtime state into the target project:

```text
.soul/
  reme/       ordinary memory and evidence
  state/      compact Current State snapshot
  traces/     readable links between evidence and state patches
```

The state directory contains:

```text
.soul/state/
  state.json
  patch_proposals.jsonl
  soul.db
```

These runtime files are local project memory and should normally stay out of Git.

## How It Works

Soul's loop is intentionally small:

```text
before turn:
  inject only projected Current State

after turn:
  write episode evidence to ReMe
  propose a Soul State Patch with evidence references
  review/apply accepted patches into Current State
```

State stores typed project cognition:

- active constraints
- working hypotheses
- rejected directions
- decision gates
- open questions

Soul keeps references to memory instead of copying full evidence into state, for example:

```text
reme://daily/2026-08-16/example.md:11-21#chunk-id
```

## Integrations

### ReMe

ReMe is the file-memory substrate. Soul uses it for ordinary memory and evidence, then stores only accepted state and evidence references in `.soul/state/`.

### DeepSeek Harness

DeepSeek Harness can use Soul through the local HTTP API:

```bash
soul-api --project-dir . --port 8765
```

Example context plugin configuration:

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

### Codex

Codex can use Soul through the MCP server:

```bash
soul-mcp --project-dir .
```

MCP tools:

- `get_projected_state`
- `observe_evidence`
- `propose_patch`
- `apply_patch`

## Benchmarks

SoulBench v0 is a small AMBench-style benchmark for comparing:

- `baseline`: current task only
- `memory_summary`: current task plus natural-language memory summary
- `soul`: current task plus projected Current State

Run the deterministic local benchmark:

```bash
python benchmarks/soulbench_v0/run_soulbench.py
```

Read the latest public scorecard:

```text
benchmarks/soulbench_v0/SCORECARD.md
```

Generated benchmark outputs stay local under `benchmarks/**/results/`.

## Roadmap

- Harden the ReMe-backed memory loop.
- Reduce projected-state token cost.
- Publish clean integration examples.
- Add clearer state patch review workflows.
- Keep package publishing disabled until package naming and license are decided.
