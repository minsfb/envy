---
name: ship-git
description: >-
  Commit and publish Git changes with explicit source, remote, destination, and
  verified receipts. Select for Git repositories, including hosted PR workflows,
  after the requested action and authority are established.
---
# Git Commit And Publication

Read repository instructions and inspect status/diffs, including staged and
untracked content. Preserve unrelated user changes and the user's chosen
feature-branch/PR workflow. Follow `qa` for required validation, completing
workstream gates rather than blindly rerunning every available test.

Group only intended files into logically atomic commits. Use the repository's
message conventions; conventional prefixes are not universal. Use an isolated
worktree when needed and non-interactive commands. Do not reset or clean a
checkout to satisfy a gate. A commit-only request ends after the local commit.

If independent review is required, use `review-git` on the exact committed
candidate. For an explicitly bound blocking-only policy, require fresh
`status --require-release-ready`, `release_ready: true`, no release blockers,
and exit 0. Historical clean-only policies retain `status --require-ready`
and its `ready`/exit-0 requirement. Inspect the complete readiness JSON,
including expected head and candidate digest; a per-slot PASS or isolated
boolean is insufficient. Do not reinterpret an old accepted-risk result under
a different flag. Superseded, stale or incomplete rounds cannot qualify.
Use impact-scoped re-review with preserved evidence and final-head endorsement
for batched blocker fixes. Review grants no publication authority.

## Publish Git Refs

Read [the publication procedure](../ship/references/git-publication.md) and use
`../ship/scripts/git_publish.py`, resolving both against this skill directory.
The helper and reference remain at their existing `ship` paths for callers.

Bind a plan to the exact repository, reviewed source commit, effective remote
endpoint, full destination ref, and remote pre-state. Inspect it against the
user's authority before applying it. Use only a server with a known ordinary-ref/
no-alias contract, as described in the procedure; the client cannot enforce
behavior on an unknown or contract-violating server.

Never let an upstream, `push.default`, or implicit refspec select the destination.
Require a verified receipt read from the actual destination; dry-run is not
publication. A stale plan stops the write and does not authorize automatic
replanning or a force push. Reconcile uncertain publication before retrying.
Preserve configured access routes and credentials when access fails.

## Hosted Reviews And Merges

Use the repository's provider tooling and current help for the authorized
PR/review action. Resolve the actual host and repository from configured source,
not the session cwd alone. Do not infer public visibility from Git hosting.

Before creating or updating a PR, verify its source/base and requested audience.
Preserve explicitly selected reviewers and subscribers. Read back the review
artifact and its actual source revision; a successful branch push alone does
not prove the PR was created or updated as intended.

Merge/auto-merge is a separate action from publishing for review. Check current
head/base, required approvals, CI and merge policy. Use the provider's expected
head protection where supported; otherwise establish an equivalent race-safe
native operation before merging. Do not use admin bypasses or remove review
requirements. An auto-merge queue entry is not a completed merge.

After merging, verify provider state and the resulting revision on the intended
base. Avoid pulling into an unrelated or dirty worktree merely to report success.
Report exact source, destination, checks, receipts, and any pending/uncertain
result. Issue/task closure requires its own applicable authority and evidence
that the requested work is complete, not merely that a PR is closed.
