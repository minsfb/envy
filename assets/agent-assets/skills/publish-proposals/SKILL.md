---
name: publish-proposals
description: >-
  Validate a selected proposal set and prepare an exact private publication plan
  when the user asks to prepare filing. This offline first slice cannot file
  Issues or other artifacts; it is not code shipping or automatic reflection.
---
# Prepare Proposal Publication

This skill's workflow remains offline-only. It does not invoke a real provider
or CLI writer; Gist support, private adapters and automatic reflection handoff
remain unavailable through this skill. Do not invent
a missing provider or substitute ad hoc forge commands. A prepared plan is not a
published artifact, tested backend integration or completed publishing feature.

The fixed GitHub HTTPS implementation has a narrow source-qualified Linux
profile; unsupported client/runtime/configuration contexts still refuse.
That capability does not expand this skill's offline authority or grant filing.
Do not enable unsupported contexts through input data, environment changes,
monkeypatches or ad hoc commands. Read [the HTTPS boundary](references/https-transport-candidate.md)
when assessing that capability, or [the legacy unavailable protocol](references/github-issues-v1.md)
when interpreting its explicitly versioned fixture state.

## Establish The Requested Action

Discussion, splitting, selecting proposals and positive feedback do not authorize
filing or even creation of a local publication store. Keep initial reflection
proposal-only. Only a request to prepare filing permits private plan preparation;
normal source implementation, Git publication, installation and live testing
remain separate actions.

Read [the v1 boundary and records](references/publication-v1.md) before preparing
a store or interpreting recovery state. Accept a workflow-neutral selected set;
do not require a reflection session or include unselected proposals.

Use `scripts/publication.py` relative to this skill. This workflow's commands are
`validate`, `prepare` and `status`; none contacts a provider. `validate` does not
create a store. `prepare` requires an explicit preparation-authority reference
and a new owned private location outside a repository. Never put credentials in
that reference, paths or input data. The reference records authority supplied by
the user/operator; it cannot establish that consent independently.

## Preview And Report Honestly

Inspect the complete saved plan: selected content, exact destination/account,
audience, immutable IDs, inline bodies, declared dependency URL slots, provider
limitation and digest. Full detail is inline by default. Oversized or unsupported
content blocks; secret Gists are not private storage and are unavailable here.

Human content/privacy review is still required. Audience checks cannot prove
arbitrary prose, titles or embedded links are safe to share. Unknown audiences
block; a repository's visibility label is not evidence of internet permission.

Future writes require one explicit confirmation covering the exact plan digest
plus an authority reference. Computing a digest, reusing an old approval or
passing a review is not filing consent. This slice cannot perform those writes.
Never claim that preparing its local store filed the proposals.

Report the private store path, plan digest, pending/verified/uncertain states and
unsupported capabilities. Keep receipts and indexes private. After an uncertain
operation, preserve the original evidence; no blind retry, deletion, duplicate
batch, visibility change or credential repair. Source/provider changes invalidate
the plan binding instead of silently migrating the store.

A `blocked` row is terminal in P1: `blocked_plan` cannot be cleared by
reconciliation, even if the original preflight problem disappears. Preserve and
report the incomplete plan as abandoned, including any already verified artifacts;
this is not a new receipt state or permission to delete anything. Do not use a
fresh store or batch ID to finish it: adoption is unsupported and duplicate filing
is not a recovery path. In the test API, reconciliation of attempted operations
permits continuation only when every row is `planned` or `verified`. The CLI has
no offline apply or reconciliation command; the separate GitHub commands are
outside this skill's authority.
