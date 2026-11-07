---
name: coordinate-workstreams
description: >-
  Coordinate sustained implementation across dependent workstreams or agents
  using explicit ownership, integration checkpoints, and recoverable state.
  Use for an agreed multi-owner plan, not as a default for small tasks.
---
# Coordinate Workstreams

Execute the accepted plan at its agreed scale. Preserve design goals, binding
contracts, success criteria, and current authority. If they are missing, resolve
those gaps through `design` or `plan`; do not infer a mandate from old notes.

## Assign Work And Integration Ownership

Keep a current map of deliverables, dependencies, exact source versions, owners,
and acceptance evidence. Use the project's existing roadmap or task system when
authorized. Distinguish implemented, integrated, validated, reviewed, committed,
and published; agent activity is not evidence of completion.

Assign bounded work with [the work packet](references/work-packet.md), adapting
its fields to the task. Select one writer per mutable checkout or overlapping
surface and one accountable integration owner. Serialize coupled work; fan out
independent research, implementation, QA, or architecture review only when their
outputs can be combined without ownership conflict.

Choose reviewers with complementary questions, not a fixed team size or model
roster. Keep initial independent reviews free of peer verdicts. Reconcile
findings against code, contracts, and evidence; majority agreement does not
resolve a correctness defect. Use `review` for required source-bound rounds.

## Manage Progress And Resources

At planned checkpoints, inspect outputs and current source, not only worker
status. Check interface drift, dependency movement, missing failure paths,
validation coverage, and temporary seams. Use `review-architecture` when a
cross-boundary decision warrants independent assessment.

Separate agent slots from build/device capacity. Give each constrained resource
an owner and serialize expensive builds or isolation daemons on a shared host.
Reuse compatible check evidence and reserve full relevant suites/device
regression for stable review gates. An agent waiting for a build can still do
independent source review or documentation work.

Keep other teams unblocked where the agreed interfaces permit it. The designated
integration owner owns reconciliation, adapters, and conflict resolution;
do not silently require another team to finish unrelated future work. Pin a
tested checkpoint while independent later work continues.

## Recover And Adjust

Use the available native agent/remote tools; do not assume a particular daemon,
terminal multiplexer, provider, or model. Record actual worker identities and
recoverable source/artifact locations. A scheduled checker needs a real durable
scheduler and observed runs; a sleeping conversation is not reliable automation.

After interruption or a moved host, inspect current artifacts and worker state
before resuming or replacing work. Reconcile in-flight operations and preserve
completed outputs; avoid duplicate writers, duplicate publications, or repeated
expensive tests. Reassign routine blocked work within existing authority.
Request direction for changes to scope, architecture, material resource
commitments, or completion criteria; continue unaffected work meanwhile.

For a continuation, use `handoff` with the current map, next actions, outstanding
decisions, source identity, and evidence links. At completion, reconcile each
workstream against acceptance criteria and follow the
[cycle completion checkpoint](../implement/references/cycle-completion.md).
Require local commits for completed repository work and provide the overall
and per-task status review. Publish associated task comments when authorized,
including through standing instructions, and verify their saved contents.
