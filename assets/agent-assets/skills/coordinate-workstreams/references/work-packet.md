# Bounded work packet

Use the fields that help the worker execute independently. A short task needs
a short packet; this is not a mandatory schema or a new task database.

- **Outcome:** accepted behavior and the question this increment resolves.
- **Source:** repository/worktree, exact base/head or supplied artifact version.
- **Ownership:** editable paths/surfaces, integration owner, concurrent writers
  to avoid, and dependencies that must be available first.
- **Contract:** binding design decisions, interfaces, compatibility constraints,
  scope exclusions, and implementation freedom.
- **Acceptance:** observable criteria, focused checks, required final gates,
  resource ownership and budget, and checks another owner will run.
- **Authority:** permitted changes and external actions, with material decisions
  that must return to the coordinator.
- **Return:** changed source/artifacts, exact revisions, actual check evidence,
  unresolved findings, integration instructions, and temporary-seam cleanup.

Research and review packets specify the question and evidence sources rather
than implementation conclusions. Give independent reviewers the same accepted
constraints and raw evidence, without peers' findings before their initial pass.

The coordinator's current map can be a small table:

| Deliverable | Owner / source | Dependency / interface | State and evidence | Next action |
|---|---|---|---|---|

Keep historical logs separate. An ETA should state the remaining critical path
and uncertainty; partial implementation alone does not establish ship readiness.
