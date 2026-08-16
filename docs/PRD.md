# Soul PRD

## Product Definition

Soul is a Project Cognitive State system for AI-assisted software work. It gives agents a durable, inspectable Current State, plus a controlled path for changing that state from evidence.

Soul is not generic chat memory and not an Entity/Candidate promotion system.

## Goals

- Preserve accepted project cognition across sessions.
- Separate raw interaction history from accepted Current State.
- Detect meaningful Cognitive Diff from imported evidence.
- Represent proposed state changes as State Patch Proposals.
- Require explicit confirmation before Current State changes.
- Maintain a clear event trail for accepted state transitions.
- Make state useful to future agent behavior.

## Non-Goals

- Store every message as durable memory.
- Automatically accept every reflected idea.
- Promote Candidates into Entities.
- Build a full knowledge graph in the current core.
- Let Soul plan or execute tasks for the agent.
- Scan the whole codebase without explicit workflow support.

## Core Concepts

### Current State

The accepted project cognition that agents read before work.

### Evidence Episode

An imported conversation or work session. It is evidence, not accepted state.

### Cognitive Diff

The detected project-cognition change between Current State and new evidence.

### State Patch Proposal

A proposed mutation to Current State. It remains proposed until confirmed.

### Cognitive Event

An accepted state transition record created when a patch is applied.

## MVP Requirements

- Initialize local Soul storage.
- Report Current State and recent events.
- Import Codex JSONL sessions as Evidence Episodes.
- Reflect imported Episodes into State Patch Proposals or `NO_CHANGE`.
- Default task-only interactions to `NO_CHANGE`.
- Apply State Patch Proposals only after confirmation.
- Expose Current State context useful to future agent work.
- Expose a local HTTP API for harness plugins.
- Expose a stdio MCP server for MCP clients.

## User Workflows

### Bootstrap Project State

The user initializes Soul in a repository. Soul creates local storage and makes the project ready for state tracking.

### Import Work

The user imports a Codex conversation or session log. Soul stores it as an Evidence Episode.

### Reflect Change

Soul analyzes the Episode and produces a State Patch Proposal. If the episode does not contain durable project cognition, the result is `NO_CHANGE`.

### Confirm State

The user or workflow reviews a State Patch Proposal. Only applying the patch changes Current State.

### Reuse State

Future agents read Current State before acting. They treat Episodes and Patch Proposals as evidence, not accepted belief.

## Success Metrics

- Agents preserve accepted project direction across sessions.
- Repeated re-explanation decreases.
- `NO_CHANGE` prevents task chatter from polluting state.
- Important decisions have visible evidence.
- Agent output aligns with accepted project beliefs.

## MVP Acceptance Criteria

- A fresh repository can run `soul init`.
- Codex session logs can be imported without mutating accepted state.
- Reflection produces State Patch Proposals or `NO_CHANGE`.
- Applying a patch updates Current State and records a Cognitive Event.
- Current State can be inspected through CLI commands.
