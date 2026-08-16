# Soul Core

Soul Core is a project cognitive continuity layer for AI agents.

It keeps a small, inspectable Current State for a project and provides a controlled path for changing that state:

```text
Current State
  -> Evidence
  -> State Patch Proposal
  -> Review / Apply
  -> New State
```

Soul is not a planner, executor, transcript archive, or generic memory summary. It observes evidence, proposes state changes, and projects accepted state back into an agent context.

## Current Status

Implemented:

- Python package `soul-core`
- Local JSON Current State in `.brain/state.json`
- SQLite event and episode storage in `.brain/soul.db`
- State Patch proposal and explicit apply
- Agent adapter for before-task context injection and after-task evidence submission
- Local HTTP API for harness/plugin adapters
- Stdio MCP server
- DeepSeek Harness thin context plugin integration
- SoulBench v0 AMBench-style benchmark

Latest benchmark scorecard:

```text
benchmarks/soulbench_v0/results/deepseek_latest_rescored/ambench_scorecard.md
```

DeepSeek benchmark summary:

| Adapter | Judge Score | Quick Score | Total Tokens |
|---|---:|---:|---:|
| baseline | 0.4167 | 0.5417 | 3532 |
| memory_summary | 0.7979 | 0.8542 | 2984 |
| soul | 0.8833 | 0.9583 | 4640 |

## Install

From this repository:

```bash
pip install -e .
```

This installs three commands:

```bash
soul
soul-api
soul-mcp
```

## Start The HTTP API

From the repository root:

```bash
soul api serve --project-dir . --port 8765
```

Equivalent direct command:

```bash
soul-api --project-dir . --port 8765
```

HTTP endpoints:

- `GET /health`
- `GET /state?task=...&scope=project&limit=8`
- `POST /transition/propose`
- `POST /patch/propose`
- `POST /patch/apply`

## Use With DeepSeek Harness

DeepSeek Harness connects through the `@deepseek-ai/dsh-soul-context` plugin.

Example configuration:

```yaml
- id: soul-context
  name: '@deepseek-ai/dsh-soul-context'
  config:
    baseUrl: http://127.0.0.1:8765
    scope: demo
    stateLimit: 8
    proposeTransitions: true
    requireState: true
```

The plugin does two things only:

- before a model step, fetch Soul Current State and append it as durable context;
- after a turn ends, submit durable session evidence to Soul for a patch proposal.

It does not plan, execute tools, rewrite model output, or apply patches.

## Use As MCP

Run the stdio MCP server:

```bash
soul-mcp --project-dir .
```

MCP tools:

- `get_projected_state`
- `observe_evidence`
- `propose_patch`
- `apply_patch`

## CLI

Common commands:

```bash
soul init
soul status
soul context
soul state show
soul state diff --summary "..."
soul state apply <proposal-id>
soul agent before-task "..."
soul agent after-task "..." "..."
soul codex ingest <path-to-codex-jsonl>
soul import codex <path-to-codex-jsonl>
soul episode list
soul episode show <id>
soul reflect episode <id>
```

## SoulBench

Run the deterministic local benchmark:

```bash
python benchmarks/soulbench_v0/run_soulbench.py
```

Run the real DeepSeek benchmark:

```bash
python benchmarks/soulbench_v0/run_soulbench.py --backend deepseek --output-dir benchmarks/soulbench_v0/results/deepseek_latest
```

Rescore an existing run without calling a model:

```bash
python benchmarks/soulbench_v0/run_soulbench.py --rescore-from benchmarks/soulbench_v0/results/deepseek_latest/results.json --output-dir benchmarks/soulbench_v0/results/deepseek_latest_rescored
```

## Repository Layout

```text
soul/                         Python package
tests/                        Core, API, MCP, and benchmark tests
benchmarks/soulbench_v0/      AMBench-style benchmark
docs/                         Design notes and historical product/technical docs
experiments/                  Local historical experiments, ignored by default
```

## Design Principles

- Current State is accepted project cognition, not raw memory.
- Evidence and Episodes can suggest a change, but they are not accepted state.
- State Patch proposals require review before apply.
- Soul should improve agent continuity and constraints without controlling the agent.
- Adapters should stay thin: observe, inject context, and submit evidence.
