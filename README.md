# Soul

Soul is a lightweight project memory and current-state layer for AI agents.

It keeps durable evidence in ReMe, injects compact Soul state into agent turns, and separates confirmed state from short-lived working assumptions.

```text
agent turn -> ReMe evidence -> Working State / Accepted State -> next agent turn
```

## Status

Soul is an experimental MVP.

- Python CLI, HTTP API, and MCP entry points.
- Codex, TraeX, and DeepSeek Harness integration paths.
- ReMe stores ordinary memory and evidence as inspectable local files.
- Soul injects confirmed Accepted State plus unconfirmed Working State.

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
soul state review-card
soul context
soul --review
```

Runtime files stay under the target project:

```text
.soul/
  reme/       ReMe memory and evidence consumed by Soul
  state/      STATE.md, state.json, working_state.json, patch_proposals.jsonl, integration_runs.jsonl
  traces/     evidence-to-state trace files
```

These files should normally stay out of Git.

## State Model

- ReMe keeps durable evidence and long memory under `.soul/reme/`.
- Accepted State is confirmed project cognition in `STATE.md` and `state.json`.
- Working State is unconfirmed, short-lived context in `working_state.json`.
- State Patches require explicit review before changing Accepted State.
- Review Cards expose only the small set of high-value state decisions that are ready to confirm or need review.

## ReMe Config

Soul uses `.soul/reme/` as the project-local ReMe workspace. `auto_memory` needs model config visible to the Soul process.

Create a global env template:

```bash
soul reme init-config --scope global
```

Fill it with:

```dotenv
LLM_API_KEY=sk-your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat
```

Use `SOUL_HOME=/path/to/soul-home` for a custom location. Project `.env` files can override global values.

Check setup:

```bash
soul reme doctor --project-dir . --create-workspace
```

## Integrations

| Use case | Command |
| --- | --- |
| Codex user setup | `soul codex install --scope user --project-dir . --init` |
| Codex MCP command | `soul-mcp --project-dir .` |
| Codex verification | `soul codex doctor --project-dir .` |
| DeepSeek Harness API | `soul-api --project-dir . --port 8765` |
| DeepSeek Harness setup | `soul dsh install --project-dir .` |
| DeepSeek Harness launch | `dsh web --patch .soul/dsh/soul.patch.yml` |
| DeepSeek Harness verification | `soul dsh doctor --project-dir .` |
| TraeX project setup | `./node_modules/.bin/soul traex install --init` |
| TraeX user setup | `soul traex install --scope user --project-dir . --init` |
| TraeX verification | `soul traex doctor --project-dir .` |
| ReMe Web/HTTP service | `soul reme start --project-dir .` |
| Soul Review page | `soul --review` |
| Benchmark | `python benchmarks/soulbench_v0/run_soulbench.py` |

If the local Review server is already running with old code or a different project, restart it:

```bash
soul --review --review-restart
```

TraeX local install:

```bash
# after publishing: npm install @soulkit/soul
npm install /path/to/SoulKit
./node_modules/.bin/soul traex install --init
```

If TraeX does not load project resources, install user-level config:

```bash
soul traex install --scope user --project-dir . --init
```

DeepSeek Harness:

```bash
soul-api --project-dir . --port 8765
soul dsh install --project-dir .
dsh web --patch .soul/dsh/soul.patch.yml
```

Verify runtime execution:

```bash
soul traex doctor --project-dir .
soul codex doctor --project-dir .
soul dsh doctor --project-dir .
```

On Windows, `soul reme start --project-dir .` starts ReMe with the service window hidden by default. Use `--foreground` for console logs.

## More

- Soul/ReMe design: [docs/soul-reme-integration.md](docs/soul-reme-integration.md)
- Benchmark scorecard: [benchmarks/soulbench_v0/SCORECARD.md](benchmarks/soulbench_v0/SCORECARD.md)
- Runtime outputs: `.soul/`
- Generated benchmark results: `benchmarks/**/results/`
