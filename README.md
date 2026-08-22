# Soul

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/status-MVP-orange" alt="MVP">
  <img src="https://img.shields.io/badge/memory-local--first-brightgreen" alt="Local-first memory">
  <img src="https://img.shields.io/badge/review-low--noise-purple" alt="Low-noise review">
</p>

Soul is a lightweight current-state layer for coding agents. It keeps durable evidence in ReMe, separates accepted state from short-lived working assumptions, and exposes a small Review Card queue for low-cost confirmation.

```text
agent turn -> ReMe evidence -> Working State / Accepted State -> next agent turn
```

Soul is not a planner, executor, or full chat-history summarizer.

## ✨ What It Does

| Layer | Purpose | Files |
| --- | --- | --- |
| ReMe evidence | Durable, inspectable memory and source evidence | `.soul/reme/` |
| Accepted State | Confirmed project cognition injected into future turns | `.soul/state/STATE.md`, `.soul/state/state.json` |
| Working State | Temporary assumptions that may need confirmation | `.soul/state/working_state.json` |
| Review Card | Low-noise confirm / needs-review decisions | `.soul/state/patch_proposals.jsonl`, working state |

## 🚀 Quick Start

From a checkout:

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

## 🧠 ReMe Setup

Soul uses project-local ReMe storage at `.soul/reme/`. Package installation does not create this directory; it is created when ReMe preflight/start or evidence writing runs.

```bash
soul reme init-config --scope global
# Fill LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL_NAME in the generated file.

soul reme doctor --project-dir . --create-workspace
soul reme start --project-dir .
```

On Windows, `soul reme start --project-dir .` starts ReMe hidden by default. Add `--foreground` when you want console logs.

## 🔌 Agent Integrations

```bash
# Codex
soul codex install --scope user --project-dir . --init
soul codex doctor --project-dir .

# DeepSeek Harness
soul-api --project-dir . --port 8765
soul dsh install --project-dir .
dsh web --patch .soul/dsh/soul.patch.yml
soul dsh doctor --project-dir .

# TraeX
soul traex install --scope user --project-dir . --init
soul traex doctor --project-dir .
```

DeepSeek Harness uses the local Soul API plus a generated patch file. The expected flow is: start `soul-api`, install the DSH patch, launch `dsh web --patch ...`, then use `soul dsh doctor` to verify real after-turn evidence heartbeats.

Current DSH support covers after-turn evidence enqueue through `/evidence/enqueue`. Before-turn Current State injection is still a follow-up item unless DSH exposes a stable prompt/context hook for the plugin.

## 📁 Runtime Files

```text
.soul/
  reme/       ReMe workspace: memory, evidence, indexes
  state/      Accepted State, Working State, Review Card queue, integration runs
  traces/     evidence-to-state trace files
  dsh/        generated DeepSeek Harness patch
  sandboxes/  mock/demo state that should not pollute real project state
```

These files are runtime data and should normally stay out of Git.

## 🧩 Commands

| Command | Use |
| --- | --- |
| `soul context` | Print compact state for an agent turn |
| `soul state show` | Inspect Accepted State |
| `soul state audit` | Check `.soul/state` consistency |
| `soul state review-card` | Print current Review Card candidates |
| `soul --review` | Open the Review Card web UI |
| `soul queue status` / `soul queue drain` | Inspect or process queued evidence jobs |

## 📚 More

- Design notes: [docs/soul-reme-integration.md](docs/soul-reme-integration.md)
- Benchmark scorecard: [benchmarks/soulbench_v0/SCORECARD.md](benchmarks/soulbench_v0/SCORECARD.md)
- ReMe project: <https://github.com/agentscope-ai/ReMe>
