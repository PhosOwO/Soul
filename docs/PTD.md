# Soul PTD

## Technical Purpose

Soul implements a local Project Cognitive State layer for AI agents.

The technical architecture must protect the distinction between raw evidence,
proposed cognition, and accepted state. All implementation choices should serve
that separation.

## Architecture Principles

- State is the primary abstraction.
- Raw conversations are Episodes, not state.
- Reflection proposes Cognitive Diff, but does not directly mutate accepted
  cognition.
- Entity/Candidate promotion is outside the current active loop.
- Accepted changes are recorded as Cognitive Events.
- Storage should remain inspectable and simple in the MVP.
- Schema flexibility is preferred until real usage reveals stable contracts.

## Current Components

### CLI

The CLI provides the user-facing workflow:

- Initialize storage.
- Check status.
- Import Codex sessions.
- List and inspect Episodes.
- Reflect Episodes.
- Inspect Current State.
- Propose and apply State Patches.
- Produce agent-facing Current State context.
- Start the local HTTP API with `soul api serve`.

### HTTP API

The HTTP API is the stable adapter surface for harness plugins. It exposes
Current State projection, transition proposal, patch proposal, and explicit
patch apply.

### MCP Server

The stdio MCP server exposes the same core loop to MCP clients through tools:

- `get_projected_state`
- `observe_evidence`
- `propose_patch`
- `apply_patch`

### Storage

SQLite is the MVP storage layer. It provides local durability without requiring
external infrastructure.

The default database path is:

```text
.brain/soul.db
```

Accepted project cognition is stored separately as inspectable JSON:

```text
.brain/state.json
```

### Core Models

Core models represent the minimum cognitive-state vocabulary:

- Current State
- State Patch Proposal
- Scope
- Interaction Episode
- System Event
- Cognitive Event

### Adapters

Adapters convert external work records into Soul Episodes. The current adapter
supports Codex JSONL import.

### Reflection

Reflection reads an Episode and identifies Cognitive Diff. In the MVP,
reflection is intentionally conservative. It should produce `NO_CHANGE` when an
episode contains normal task instructions without durable project cognition.

## State Transition Model

Soul's state transition model is:

```text
Current State
    -> Evidence / Episode
    -> Cognitive Diff
    -> State Patch Proposal
    -> Confirmation
    -> New State
```

This pipeline is the key technical invariant. No implementation should bypass it
unless the operation is explicitly administrative and records an equivalent
event trail.

## Evidence Model

Evidence links proposed cognition to concrete project practice.

The MVP evidence model can be simple:

- File references discovered during import.
- Artifacts explicitly linked to Candidates or Entities.
- User confirmation events.
- Existing project documents.

Future versions may add stronger evidence types, confidence scoring, and
multi-source verification.

## State Patch Model

State Patch is the future formal language for state changes.

For the MVP, the practical patch representation is:

- Patch proposal JSONL in `.brain/patch_proposals.jsonl`.
- Explicit operations such as `upsert_belief` and `add_constraint`.
- Supporting evidence.
- Confirmation metadata.
- Resulting `.brain/state.json` version update.

A formal patch schema should be introduced only when repeated real patches show
stable structure.

## Soul-Agent Protocol

Agents should interact with Soul through a closed loop:

1. Read accepted project state before acting.
2. Perform work and produce artifacts.
3. Submit the work result as Episode/Evidence.
4. Produce a State Patch Proposal.
5. Review and confirm the patch.
6. Use updated state in future sessions.

The protocol should make it hard for an agent to confuse retrieved text with
accepted project belief.

## Behavioral Evaluation

Technical quality should be measured through agent behavior:

- Does context output help the agent make better decisions?
- Does reflection avoid false positives?
- Does confirmation preserve human control?
- Can accepted beliefs be traced to evidence?
- Does the system reduce repeated explanation?
- Does it avoid stale or contradictory project assumptions?

## MVP Boundaries

The MVP should not add the following prematurely:

- Full graph database.
- Automatic LLM reflection.
- MCP integration.
- Global schema registry.
- Continuous codebase scanning.
- Autonomous state mutation.

These features may become valuable later, but the first technical priority is a
trustworthy state transition loop.

## Implementation Notes

- Keep model fields compact and explicit.
- Use JSON payload fields where the domain is still changing.
- Prefer clear event records over hidden mutation.
- Treat imported sessions as immutable evidence.
- Keep `NO_CHANGE` cheap and common.
- Make confirmation a deliberate action.
