---
name: review-architecture
description: >-
  Independently assess architectural boundaries, ownership, and integration
  against agreed design goals. Use for consequential cross-surface changes or
  a planned architecture checkpoint, not every routine code review.
---
# Review Architecture Against Intent

Establish the accepted goals, constraints, exact candidate, and questions.
Review actual interfaces and consumer paths alongside the design; distinguish
observed implementation from intended future work. Do not redesign the project
around a reviewer's preferred style.

Focus on boundaries that could create expensive drift:

- Authority and state ownership, mutation versus observation, and lifecycle.
- Contract semantics, dependency direction, encapsulation, and compatibility.
- Failure behavior, atomicity, teardown, and cross-instance isolation where
  relevant to the changed design.
- Integration/migration sequencing, independently deliverable increments, and
  temporary seams with clear owners and removal conditions.
- Testability, diagnostics, and performance claims supported by evidence.

Use only relevant dimensions; do not demand every subsystem change. Identify a
concrete trigger, violated goal/invariant, impact, and smallest plausible remedy
for each material finding. Separate correctness blockers, design decisions
requiring direction, optional follow-ups, and stylistic preferences.

An independent reviewer receives accepted constraints and raw artifacts before
peer conclusions. Use `review` and its selected native procedure when this is a
required source-bound review round. Otherwise return an architectural assessment
with exact source, inspected scope, evidence, limitations, and open decisions.
The coordinator reconciles findings and obtains direction for architectural
changes outside the agreed scope; review itself grants no mutation authority.
