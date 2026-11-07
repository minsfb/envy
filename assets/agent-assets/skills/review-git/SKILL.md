---
name: review-git
description: >-
  Bind independent Git reviews to exact commits using the offline review-bundle
  controller, including reviewer-led teams, continuation, and impact-scoped
  re-review. Select after the repository's native VCS is known.
---
# Independent Git Review

Follow `review` for scope, operating mode, reviewer/model policy, workset cadence,
independence and adjudication. This skill owns only the Git evidence procedure.
Do not impose a review gate on ordinary implementation or local commits.

## Bind the candidate

Use `../review/scripts/review_bundle.py`. Read
[the procedure and schema](../review/references/review-bundle.md) before its
commands. Resolve paths against this skill directory; the controller and its
references remain at their existing `review` paths. It launches no reviewers,
tests or providers and grants no publication authority.

Use version 3 for new team reviews that may need
[interrupted-synthesis continuation](../review/references/continuation.md),
reading that procedure before preparation. V3 opt-in does not establish native
session resumability; preserve v1/v2 histories. The usual sequence is `prepare`
→ `start` → `record` → `status`; `disposition` records coordinator decisions
separately. Bind exact models and identity requirements before launch using
the shared model-selection policy; the controller itself has no model default.

Capture the exact committed candidate and audited allowlisted inputs in an
access-controlled durable store outside source repositories and Git metadata.
Use a dedicated clean worktree pinned at the candidate when other work is in
progress. Preserve user content; never reset or clean to pass capture checks.
Determine gitlink scope and check the procedure's path, text, size and platform
limits before capture. Secret-pattern checks do not replace a privacy audit.

## Record and recover

`start` checkpoints an attempt before exporting its request. Give each lead only
its own request and authorized evidence, never peer findings or the full ledger.
Keep actual execution and identity provenance distinct from requested settings,
reviewer reports and supplied logs. Record sanitized results without inventing
missing facts or replacing completed reports.

After interruption, `resume` first and reconcile the original run. An explicitly
authorized v3 continuation preserves original attempts and researcher records;
it is not a fresh review or new research budget. Preserve reports externally if
source changed before import rather than restoring user work to force an import.
Candidate/input/policy changes require a successor. For impact-scoped re-review,
follow the shared workset procedure, preserving original analysis and obtaining
each required team's explicit endorsement of the final candidate.

## Verify the bound gate

Inspect a fresh complete readiness result on the expected head and candidate
digest. An ordinary status exit 0 only means the query succeeded.

- Explicit blocking-only policy: `status --require-release-ready`,
  `release_ready: true`, no release blockers, and exit 0. Valid tracked
  nonblocking findings may remain; report them without claiming a clean PASS.
- Historical clean-only policy: retain `status --require-ready`, `ready`, and
  exit 0. Do not reinterpret accepted risk by choosing another flag.

Superseded, stale or substantively incomplete rounds never qualify. Inspect
original researcher proposals and material reclassifications, not just a status
summary. Report the store, source/digest, state, outstanding obligations, evidence
provenance and scope limits. Hand readiness to `ship` only for authorized actions.
