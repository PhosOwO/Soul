# Soul

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/status-MVP-orange" alt="MVP">
  <img src="https://img.shields.io/badge/memory-local--first-brightgreen" alt="Local-first memory">
  <img src="https://img.shields.io/badge/review-low--noise-purple" alt="Low-noise review">
</p>

Soul is a lightweight evidence and state companion for coding agents. It captures agent turns as local evidence, keeps confirmed project memory separate from short-lived working assumptions, and exposes a small Review Card queue for low-cost confirmation.

```text
agent turn -> local API -> ReMe-backed evidence -> Working State / Accepted State -> Review Card
```

Soul is not a planner, executor, or full chat-history summarizer.

Use Soul when you want to capture project decisions, constraints, and useful evidence from agent sessions without silently rewriting accepted project state.

## ✨ What It Does

| Layer | Purpose | Files |
| --- | --- | --- |
| ReMe evidence | Durable, inspectable memory and source evidence | `.soul/reme/` |
| Accepted State | Confirmed project cognition, updated only through explicit review | `.soul/state/STATE.md`, `.soul/state/state.json` |
| Working State | Temporary assumptions that may need confirmation | `.soul/state/working_state.json` |
| Review Card | Low-noise confirm / needs-review decisions | `.soul/state/patch_proposals.jsonl`, working state |

## Quick Start: DeepSeek Harness

Use this path if you want to install Soul as a DSH plugin.

Install from GitHub and start DSH:

```bash
dsh plugin --profile web add github:PhosOwO/SoulKit
dsh web
```

The package contains a standard `dsh.bundle` manifest. When `dsh web` starts, the plugin checks `http://127.0.0.1:8765/health` and starts the package-local `soul-api` automatically if it is not already running.

Requirements: Python 3.11+, Node.js 22+ for DSH, and `dsh` on your `PATH`.

This is enough for DSH to:

- inject reviewed project Accepted State before model requests, once there is any
- enqueue after-turn evidence
- keep Working State and Review Card data local

Accepted State is added through DSH's native system prompt assembly path. A fresh project only has Soul's built-in seed state, which is not injected. Working State is not injected by default because it is unconfirmed.

The same accepted-only rule is used by the Codex and TraeX before-turn hooks.

`@soulkit/soul/dsh` is the DSH bundle entrypoint. Codex and TraeX use the `soul codex install` and `soul traex install` commands below.

## Optional: Full Local Setup

Use this path if you want the `soul` CLI, ReMe memory indexing, diagnostics, or the Review Card UI.

```bash
git clone https://github.com/PhosOwO/SoulKit.git
cd SoulKit
pip install -e .

soul reme init-config --scope global
# Fill LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL_NAME in the generated file.
soul reme doctor --project-dir . --create-workspace
```

After one or more DSH turns, check the integration with:

```bash
soul dsh doctor --project-dir .
```

## Quick Start: Local CLI

Use this path if you want to run Soul commands directly from this repository.

```bash
pip install -e .

soul init
soul state show
soul state audit
soul --review
```

`soul --review` opens the local Review Card UI. If an old local Review server is already running:

```bash
soul --review --review-restart
```

Try Review Card interactions without touching the current project state:

```bash
soul review mock --reset
soul --review --review-project-dir .soul/sandboxes/review-mock --review-port 8766 --review-restart
```

For a screenshot/recording walkthrough, see [Review Card Demo](docs/review-card-demo.md).

## What Gets Stored

Soul uses project-local ReMe storage at `.soul/reme/`. Package installation does not create this directory; it is created when ReMe preflight/start or evidence writing runs.

```bash
soul reme init-config --scope global
# Fill LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL_NAME in the generated file.

soul reme doctor --project-dir . --create-workspace
soul reme start --project-dir .
```

On Windows, `soul reme start --project-dir .` starts ReMe hidden by default. Add `--foreground` when you want console logs.

State updates stay explicit: evidence may produce Working State, but Accepted State changes require review and confirmation.

## Other Agent Integrations

```bash
# Codex
soul codex install --scope user --project-dir . --init
soul codex doctor --project-dir .

# TraeX
soul traex install --scope user --project-dir . --init
soul traex doctor --project-dir .
```

The Codex and TraeX installers write user-level integration config and can initialize `.soul/state` in the target project with `--init`.

## Runtime Files

```text
.soul/
  reme/       ReMe workspace: memory, evidence, indexes
  state/      Accepted State, Working State, Review Card queue, integration runs
  traces/     evidence-to-state trace files
  dsh/        generated DeepSeek Harness patch
  sandboxes/  mock/demo state that should not pollute real project state
```

These files are runtime data and should normally stay out of Git.

## Useful Commands

| Command | Use |
| --- | --- |
| `soul context` | Print compact state for an agent turn |
| `soul state show` | Inspect Accepted State |
| `soul state audit` | Check `.soul/state` consistency |
| `soul state review-card` | Print current Review Card candidates |
| `soul --review` | Open the Review Card web UI |
| `soul queue status` / `soul queue drain` | Inspect or process queued evidence jobs |

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `dsh` fails before opening | Run `node -v`; use Node.js 22+ for the DSH process. |
| Soul plugin loads but evidence is not written | Run `soul dsh doctor --project-dir .`. |
| `soul-api` cannot start from DSH | Check Python 3.11+ is available, or set `SOUL_PYTHON=/path/to/python3.11`. |
| ReMe memory is missing or not searchable | Run `soul reme doctor --project-dir . --create-workspace`. |

To run the API yourself:

```bash
soul-api --project-dir . --port 8765
```

To disable plugin auto-start in your DSH profile patch:

```yaml
- id: soul
  config:
    autoStart: false
```

To disable before-turn Accepted State injection:

```yaml
- id: soul
  config:
    injectAcceptedState: false
```

## More

- Design notes: [docs/soul-reme-integration.md](docs/soul-reme-integration.md)
- Benchmark scorecard: [benchmarks/soulbench_v0/SCORECARD.md](benchmarks/soulbench_v0/SCORECARD.md)
- ReMe project: <https://github.com/agentscope-ai/ReMe>
