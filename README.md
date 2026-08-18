# Soul

Soul is a lightweight project memory and current-state layer for AI agents.

It keeps long memory in ReMe and injects only a compact Current State into the next agent turn.

```text
agent episode -> ReMe evidence -> Soul STATE.md -> next agent turn
```

## Status

Soul is an experimental MVP.

- Python CLI, HTTP API, and MCP entry points are available.
- Codex and TraeX integration are supported through MCP and project hooks.
- npm packaging is prepared as `@soulkit/soul`; registry publishing is still pending.
- ReMe stores ordinary memory and evidence as inspectable local files.
- Soul keeps a small accepted Current State snapshot.

Soul is not a planner, executor, or full chat-history summarizer.

## Install

From a checkout:

```bash
pip install -e .
```

Local npm wrapper:

```bash
npm install -g .
```

After publishing:

```bash
npm install -g @soulkit/soul
```

If Python is not on `PATH`, set `SOUL_PYTHON=/path/to/python`.

## Quick Start

Initialize a project:

```bash
soul init
soul state show
soul context
```

Runtime files stay under the target project:

```text
.soul/
  reme/       ReMe memory and evidence consumed by Soul
  state/      STATE.md, state.json, patch_proposals.jsonl, integration_runs.jsonl
  traces/     evidence-to-state trace files
```

These files should normally stay out of Git.

## How Soul Uses ReMe

ReMe owns durable memory and evidence files. Soul consumes ReMe outputs, stores compact evidence references in `.soul/state/`, and injects only Current State into agents by default.

By default Soul uses `.soul/reme/` as the project-local ReMe workspace. ReMe-backed writes use ReMe CLI capabilities such as `auto_memory`, `read`, `traverse`, `auto_dream`, and `proactive`.

## Integrations

| Use case | Command |
| --- | --- |
| Codex user setup | `soul codex install --scope user --project-dir . --init` |
| Codex MCP command | `soul-mcp --project-dir .` |
| Codex verification | `soul codex doctor --project-dir .` |
| DeepSeek Harness API | `soul-api --project-dir . --port 8765` |
| DeepSeek Harness verification | `soul dsh doctor --project-dir .` |
| TraeX project setup | `./node_modules/.bin/soul traex install --init` |
| TraeX user setup | `soul traex install --scope user --project-dir . --init` |
| TraeX verification | `soul traex doctor --project-dir .` |
| ReMe Web/HTTP service | `soul reme start --project-dir .` |
| Benchmark | `python benchmarks/soulbench_v0/run_soulbench.py` |

For TraeX, install Soul into the target project first:

```bash
# after publishing: npm install @soulkit/soul
npm install /path/to/SoulKit
./node_modules/.bin/soul traex install --init
```

If your TraeX sessions do not load project resources, install the user-level config too:

```bash
soul traex install --scope user --project-dir . --init
```

In TraeX, `/mcp` and `/hooks` show configuration visibility. To verify Soul actually ran, start a new turn and then run:

```bash
soul traex doctor --project-dir .
```

The same rule applies to Codex and DeepSeek Harness: use the relevant `doctor` command to verify recent Soul execution, not just MCP/API visibility. Codex setup writes `[mcp_servers.soul]` to `$CODEX_HOME/config.toml` or the current user's default Codex config.

## More

- Soul/ReMe design: [docs/soul-reme-integration.md](docs/soul-reme-integration.md)
- Benchmark scorecard: [benchmarks/soulbench_v0/SCORECARD.md](benchmarks/soulbench_v0/SCORECARD.md)
- Runtime outputs: `.soul/`
- Generated benchmark results: `benchmarks/**/results/`
