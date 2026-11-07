---
name: design
description: >-
  Establish product intent, architectural direction, and success criteria when
  consequential design choices remain unresolved. Research and compare options
  before implementation planning; reuse design decisions already agreed.
---
# Align On Design

Start with what the user and project have already established. Inspect the
relevant product, interfaces, constraints, prior decisions, and examples before
asking the user to repeat them. Keep this stage in conversation unless durable
design artifacts are requested or part of the authorized work.

Build enough shared understanding to decide:

- The user outcome and observable behavior, including failure and recovery.
- Scope and exclusions, compatibility commitments, and success criteria.
- Architectural boundaries, ownership, dependencies, and established patterns.
- Constraints that materially affect the solution: performance, operability,
  security, platforms, migration, or external teams.
- Which decisions are binding, which are assumptions, and where implementers
  have freedom to choose.

Investigate uncertainty that could change those decisions. Prefer concrete
existing code, experiments, and authoritative documentation to speculative
architecture. Label inspected evidence, supplied claims, and unknowns. A small
prototype can answer a question when implementation research is authorized;
it is not automatically a production commitment.

Present a recommendation and its meaningful tradeoffs. Ask a few focused
questions only where intent cannot be inferred or alternatives materially
change outcomes, architecture, cost, or scope. Do not impose a question quota,
mandatory PRD, or exhaustive design document. Small requests may supply all the
necessary design already.

For broad work, make the proposed boundaries and unresolved decisions concise
enough for the user to approve. Reuse existing approval. Do not interpret
agreement with an architectural idea as permission to implement, create tasks,
or publish it when the current request remains discussion-only.

Hand `plan` the agreed outcome, boundaries, success criteria, design references,
constraints, and open questions with their owners. An open question need only
block work that depends on its answer. Do not prescribe a detailed commit or
agent breakdown here before the execution dependencies are understood.
