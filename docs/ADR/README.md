# Soul ADR Index

Architecture Decision Records capture durable design decisions for Soul.

ADRs should be used when a decision changes the project's cognitive state, sets
a long-term constraint, or explains why an architectural path was accepted over
reasonable alternatives.

## Current Decisions

No numbered ADRs have been written yet.

## Proposed Initial ADRs

- ADR0001: Soul is a Project Cognitive State system, not a memory system.
- ADR0002: Raw conversations are Interaction Episodes, not accepted state.
- ADR0003: State Patch proposals do not directly mutate accepted state.
- ADR0004: Entity/Candidate promotion is outside the active Soul Core loop.
- ADR0005: SQLite is the MVP storage layer.

## ADR Template

```text
# ADR000X: Title

Status: Proposed | Accepted | Superseded
Date: YYYY-MM-DD

## Context

What problem or design pressure led to this decision?

## Decision

What is the accepted decision?

## Consequences

What becomes easier, harder, required, or ruled out?

## Evidence

Which discussions, files, tests, or artifacts support this decision?
```

## Writing Guidelines

- Keep each ADR focused on one decision.
- Link to evidence whenever possible.
- Prefer project-state language over implementation trivia.
- Update status instead of rewriting history when decisions change.
