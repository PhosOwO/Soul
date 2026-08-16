# Soul Whitepaper

Version: v0.1 executive draft

## Abstract

Soul is a project cognitive state system for AI agents.

It is not a memory database, transcript archive, prompt library, or generic
knowledge base. Soul exists to preserve and evolve the cognitive state of a
project: what the project believes, why those beliefs changed, which evidence
supports them, and how agents should act from that state.

The core unit of Soul is not a message. The core unit is state.

## 1. Why Memory Is Not Enough

Most agent memory systems store prior content: messages, summaries, files,
embeddings, retrieved facts, or user preferences. That helps recall, but recall
alone does not create continuity.

A project does not only need to remember what was said. It needs to know:

- Which ideas became accepted project cognition.
- Which ideas remained tentative.
- Which artifacts prove that a belief entered practice.
- Which decisions are still active.
- Which state changes should influence future behavior.
- Which past details should be ignored because they were not accepted.

Soul treats conversation history as evidence, not as state.

## 2. Project Cognitive State

Project Cognitive State is the evolving model of a project as understood by the
agent system. It includes accepted entities, design beliefs, constraints,
relationships, events, artifacts, open candidates, and the reasoning trail that
connects them.

This state is scoped. A belief may apply to a repository, product, feature,
document, user workflow, research thread, or agent role. Soul therefore models
scope explicitly instead of assuming one global memory space.

## 3. Belief State

Belief State is the accepted cognitive layer of Soul.

A belief is not accepted simply because it appeared in a conversation. A belief
enters state only after it passes through a transition process and has enough
evidence to justify promotion.

Soul distinguishes:

- Episode: an imported interaction slice.
- Candidate: a possible cognitive update.
- Entity: accepted project cognition.
- Artifact: concrete project evidence.
- Cognitive Event: an accepted change to project cognition.
- System Event: operational activity such as import or reflection.

## 4. First Principles

Soul is state-centric.

The system should ask "what changed in the project state?" before asking "what
should be remembered?"

Soul is evidence-driven.

Accepted cognition should be backed by observable evidence, such as committed
artifacts, confirmed decisions, implemented behavior, or stable design
agreements.

Soul is transition-oriented.

The important event is not that text exists. The important event is that a
project moved from one cognitive state to another.

Soul is reversible and inspectable.

State changes should be represented as events or patches so that future agents
can inspect how the current state came to exist.

Soul is agent-facing.

The purpose of state is to improve future agent behavior, not merely to build a
human-readable archive.

## 5. State Transition

A State Transition is the controlled movement from current project state to a
new project state.

The transition pipeline is:

```text
Interaction Episode
    -> Cognitive Diff
    -> Proposal / Candidate
    -> Artifact practice
    -> Confirmation
    -> Entity
    -> Cognitive Event
    -> Updated State
```

This pipeline prevents raw conversation from directly mutating state. Reflection
can propose changes, but promotion requires confirmation and evidence.

## 6. Cognitive Diff

Cognitive Diff is the detected difference between the prior project state and
new evidence.

A diff may identify:

- New project entities.
- Changed beliefs.
- Deprecated assumptions.
- Newly discovered constraints.
- Stronger or weaker confidence in existing beliefs.
- Links between entities and artifacts.
- No meaningful project cognition change.

The default outcome for ordinary task instructions is `NO_CHANGE`. Soul should
not treat every user instruction as durable cognition.

## 7. Evidence

Evidence is the bridge between discussion and accepted project state.

Evidence may include:

- Source files.
- Documentation.
- Tests.
- Decisions explicitly confirmed by the user.
- Imported interaction episodes.
- Concrete project artifacts referenced in practice.

Evidence allows Soul to answer not only "what do we believe?" but also "why do
we believe it?"

## 8. State Patch

A State Patch is a structured representation of a proposed or accepted state
change.

A patch should be small enough to inspect and specific enough to apply. It
should describe the target scope, affected entities, proposed operation,
evidence, and confidence.

In the MVP, State Patch can remain conceptual and be represented through
Candidates and Cognitive Events. A later protocol can formalize the patch
language once real usage reveals stable patterns.

## 9. Soul-Agent Loop

Soul is designed for a closed interaction loop between agents and project
cognitive state:

```text
Agent reads State
Agent performs work
Agent produces artifacts or decisions
Soul imports Episode
Soul reflects Cognitive Diff
Human or workflow confirms Candidates
Soul updates State
Future Agent reads updated State
```

The agent should not treat Soul as passive storage. Soul should shape what the
agent attends to, which assumptions it carries forward, and which decisions it
does not re-litigate.

## 10. Behavioral Evaluation

Soul should be evaluated by agent behavior, not by storage completeness.

Useful evaluation questions include:

- Does the agent preserve project intent across sessions?
- Does it avoid reopening settled decisions?
- Does it distinguish accepted state from tentative discussion?
- Does it cite evidence for important beliefs?
- Does it detect meaningful changes as Cognitive Diff?
- Does it correctly return `NO_CHANGE` for non-durable task chatter?
- Does it improve future work without polluting state?

## 11. MVP Architecture

The current MVP focuses on the smallest useful loop:

- SQLite storage bootstrap.
- Core models for Scope, Episode, Candidate, Entity, Event, Artifact, and links.
- Codex JSONL import into Interaction Episodes.
- Cognitive Diff reflection.
- `NO_CHANGE` as the conservative default.
- Candidate promotion gated by artifact evidence.
- CLI workflows for initializing, importing, reflecting, listing, and confirming.

The MVP intentionally excludes automatic LLM reflection, graph storage, MCP
integration, schema registry, and broad codebase scanning until the state model
has proven itself in practice.

## 12. Research Direction

Soul can evolve toward:

- A formal State Patch language.
- Stronger belief revision semantics.
- Multi-agent shared cognitive state.
- Project-world modeling across repositories and tools.
- Evaluation benchmarks for cognitive continuity.
- Agent protocols that make project state a first-class runtime dependency.

The long-term goal is not better memory. The goal is agents that can participate
in a project as if the project has an evolving mind of its own.
