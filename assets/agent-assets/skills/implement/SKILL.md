---
name: implement
description: >-
  Execute an agreed feature or fix with focused validation and clear integration
  handoffs. Use once design intent and scope are sufficient; scale delegation
  and checkpoints to the accepted plan.
---
# Implement An Agreed Change

Recover the accepted scope, design constraints, current source, and acceptance
criteria. A clear small request can supply all of them; do not demand separate
planning artifacts. Resolve only gaps that affect the work.

Read the closest project instructions and inspect the actual repository/worktree
before editing. Preserve unrelated changes. Use an isolated worktree when
ownership overlaps or editing a canonical source would activate live behavior
outside the requested scope. Keep one writer for overlapping source changes.

For Git isolation, follow [repository placement](references/repository-placement.md)
and use its shared root/area helper. Resolve the owning primary checkout even
inside a linked worktree; create task worktrees in its ignored `.worktrees/`.
Other VCSs use the applicable native project/environment procedure.

Implement a coherent increment using the project's established patterns. Keep
contracts and ownership consistent with the accepted design. Avoid speculative
abstractions, compatibility layers, and cleanup unrelated to the requested
behavior. Record a necessary temporary seam and its exit condition.

Use `qa` for checks relevant to the changed behavior and integration boundary.
Check failures should inform the next edit; do not replace progress with full
suite reruns after every patch. Add tests for meaningful changed behavior or
risk, rather than tests that merely mirror implementation or generated wording.

Delegate only when available, authorized, and useful for independently bounded
work. Give a worker its input/source, owned paths, constraints, acceptance
criteria, test budget, and return contract. Use `coordinate-workstreams` for
sustained fanout. Integrate completed pieces deliberately; do not let several
agents rewrite the same checkout or run competing expensive builds.

Stop the affected path for a material design/scope ambiguity, explain the
concrete choice, and continue independent work. An ordinary implementation
decision or reversible fix within scope does not need renewed permission.

At the increment boundary, inspect the combined diff and required evidence.
Return exact source identity, changes, executed versus supplied checks, and
remaining risks. Use `review` when an independent gate is requested or required.
At the end of a major autonomous implementation/review cycle, follow
[cycle completion](references/cycle-completion.md): reconcile workstreams,
commit completed repository changes through `ship` within existing authority,
and deliver the per-task status review. Uncommitted implementation is not a
completed cycle. Honor explicit requests to leave changes uncommitted and
report that state without calling it complete. Implementation authority does
not silently expand to landing, external messages, or task mutation; reuse any
applicable standing authorization for local commits and task comments.
