---
name: develop
description: >-
  Route requests to explore, propose, build, or change a project through the
  necessary design, planning, and execution work. Use natural project intent;
  users need not name phases. Resume established work at its current phase.
---
# Develop A Project Or Feature

Recover the user's intent, accepted decisions, constraints, current artifacts,
and authority before choosing the next step. Discussion and investigation can
stay in conversation; they do not authorize implementation or publication.
A request to implement does authorize ordinary implementation and appropriate
validation within its scope. Do not require the user to name a skill sequence.

Load only the next relevant skill from the available catalog:

| What is missing or requested? | Next skill |
|---|---|
| Outcome, product behavior, architectural direction, consequential research | `design` |
| Deliverable scope, dependency order, execution and validation strategy | `plan` |
| Authorized changes with sufficient intent and scope | `implement` |
| Validation or a specific coverage question | `qa` |
| Independent review required or requested | `review` |
| Cross-boundary architectural assessment | `review-architecture` |
| Local commits or publication requested | `ship` |
| Sustained coordination across dependent workstreams | `coordinate-workstreams` |

An adequate prompt or accepted artifact may already satisfy design and planning.
For a small fix, state the relevant assumption and proceed; do not create a PRD,
roadmap, agent team, or approval loop. An ordinary factual question needs none
of this process. A phase-specific request does not require loading this whole
family first. If a skill is unavailable, use the established project workflow
and report only a material capability gap; do not invent a tool invocation.

For substantial new work, align on the important design choices and a
proportionate execution plan before spreading implementation across agents or
systems. Ask only questions whose answers change the work. Reuse agreement
already present in the session; continue independent authorized work while a
material decision is pending.

Identify the target repository and native workflow from its actual metadata and
instructions. The session cwd may be elsewhere. Provider names alone do not
establish publication authority or visibility. Select the concrete native
review/shipping procedure only when needed, without loading incompatible VCS
procedures together.

Return to design or planning when new evidence changes a binding decision,
scope, dependency, or completion criterion. Routine implementation choices stay
within the approved plan. For a requested retrospective or continuation, use
`reflect` or `handoff` rather than restarting project design.
