---
name: ship
description: >-
  Finalize completed changes through the repository's native commit and
  publication workflow. Use for requested local commits, publishing for review,
  or landing; preserve the distinction and the user's authorized destination.
---
# Finalize And Ship Changes

Resolve what the user authorized: local commits, publication for review, or
merge/landing. Reuse existing session or standing authority and the agreed
destination. Local commits required by an authorized cycle-completion workflow
do not require a second permission request. A passing
review does not add publication authority, and a request to publish a feature
branch does not authorize direct updates to a protected branch.

Inspect the actual target repository and its closest instructions. Select only
its concrete native procedure from the available catalog:

- Git repositories: `ship-git`, with repository/provider rules for review and
  merging. Use available environment access guidance only if that host needs it.
- Other VCS: the matching project/environment shipping procedure. Do not use
  Git's publication guard or assume staging semantics in another VCS.

If no native procedure is available, prepare the changes and evidence that can
be verified safely. Stop only the operation whose source, destination, or
receipt cannot be established; report that specific gap.

Use `qa` to complete the relevant final workstream checks and required gates.
Stop before committing if required checks fail, unless the user explicitly
accepts the failure.
Reuse valid evidence, retaining its exact source and limitations. Inspect the
combined diff for intended scope and group changes into logical commits using
the project's conventions. Preserve unrelated user work.

When independent review is requested or required, use `review` and its selected
native procedure on the final candidate. Require fresh readiness before the
operation that gate protects. Do not impose a review round on ordinary
commit-only work or count stale evidence after an amendment as current approval.
Review at the completed feature-workset boundary. Batch blocker fixes for
impact-scoped re-review with exact final-candidate endorsement, and preserve
the bound blocking-only or historical clean-only release policy. Do not treat
a non-clean eligible result as a clean PASS or fix unrelated nits in a frozen
candidate. Renew full review when changed design or risk invalidates prior
analysis; the selected native procedure owns its exact readiness checks.

Perform only the authorized action. Bind it to exact source and destination,
check current remote state, and verify the actual result through the native
procedure. Reconcile a timeout or ambiguous result before retrying a write.
A queued operation remains pending until observed complete.

Report exact commits/revisions, validation and review results, publication or
landing status, verified destination and receipt, and remaining limitations.
Do not update tasks, recipients, or unrelated metadata simply because a CLI
makes those operations available.
For a major autonomous cycle, complete the
[status review and task-comment checkpoint](../implement/references/cycle-completion.md)
after obtaining the final commit identities. Reuse applicable task-comment
authority; do not equate cycle completion with shipment or task closure.
