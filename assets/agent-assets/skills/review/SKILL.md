---
name: review
description: >-
  Review a completed feature workset with independent reviewer-led teams, or
  perform impact-scoped re-review of blocker fixes using preserved evidence.
  Use for requested independent reviews or an established release gate, not
  intermediate implementation commits or routine diff reading.
---
# Independent Review

This skill does not replace a repository's review policy. Keep the user's
requested reviewer policy; do not add mandatory independent reviews to an
ordinary commit-only request.

## Operating mode

For an interactive one-off review, briefly present the proposed scope/checklist,
exact reviewers/models, delegation and material limits; ask for confirmation or
adjustments before launch. Explicitly autonomous work containing review stages
uses the defaults below and additions within the authorized scope and resource
budget without repeated approval.
Determine mode from the task's authorization: a status question does not cancel
autonomy; explicit newer instructions can. Reuse an agreed in-scope configuration.

Default to independent leads using the strongest actually available GPT and
Claude models, with researchers using their own lead's model. Resolve and record
exact IDs through [model selection](references/model-selection.md), not guessed
names. Explicit user choices override defaults. A narrowed review must disclose
its limits and does not satisfy a separately required broader gate. Do not
collapse required reviewer families without explicit task authorization.

## Review at feature completion

Read [workset cadence and delta review](references/worksets.md) when planning
an autonomous review stage or reviewing fixes. Define one coherent, independently
shippable capability, including required cross-repository integration. Do not
make unrelated features wait for a backlog-wide review batch.

During development, use implementer/coordinator code inspection, focused tests
and relevant integration checks. Intermediate commits do not trigger the full
panel. Targeted early architectural or security advice is appropriate when useful.
Run the full independent review once the workset's implementation, tests,
documentation and offline integration are ready, against a frozen candidate.
Batch blocking fixes for impact-scoped re-review with final-head endorsement;
renew full review only when changed design or risk invalidates prior analysis.
Leave nonblocking fixes to later worksets rather than repeatedly changing the
release candidate.

For new autonomous feature-workset reviews, bind
`team_review.release_policy: "blocking-only"` by default unless the user or
repository requires a stricter gate. Include this policy in an interactive
configuration proposal. The controller requires explicit opt-in so existing
candidate histories keep their original rules.

## Prepare the review

Read repository requirements and derive the [minimum checklist](references/checklist.md)
from the actual diff and supporting contracts, not only the author's plan.
Each team independently covers every required change and checklist obligation.
Read [team review](references/team-review.md) before assigning lead/research work.
Risk determines additional depth and overlap, not whether a change is reviewed.

Identify the actual repository and select only its native evidence procedure:

- Git: load `review-git`. The existing controller and
  [versioned schema](references/review-bundle.md) remain at their current paths;
  only the Git procedure invokes them.
- Other VCS: use the matching project/environment review skill. Do not run Git
  commands or its controller in another VCS. If that procedure cannot represent
  a required obligation, report the gap rather than claim a controller pass.

The model/team/checklist policy is shared. Controller fields and examples in
supporting references describe Git evidence; other native procedures must bind
those obligations through their own established evidence format. Read only the
references needed for that native procedure. Preserve user work and use an
isolated candidate checkout when needed. Audit authorized source and evidence
before capture; keep durable records outside source repositories.

## Conduct independent reviews

Give each lead the candidate, full checklist and authorized evidence, never the
other team's findings or the complete ledger. Leads can add bounded, one-level
research to investigate discoveries without approval for each in-scope task.
Use only actual supported delegation; missing capability is a reported limit,
not permission to invent a CLI, bypass managed policy, copy an environment-specific
launcher or silently substitute models. An explicitly authorized external/manual
reviewer can use the same evidence contract. Lead-only work cannot satisfy a
requested delegated-team gate; narrowing that requirement needs
explicit task authorization. Set resource and access limits before launch.
This skill grants no new provider, network, install, mutation or publication
authority; the offline controller launches none of them.

Leads inspect critical code themselves, verify consequential findings and close
coverage gaps. Researchers propose severity, confidence and disposition; leads
adjudicate and explain material reclassifications. Keep coordinator/user decisions
separate from the original review. Use the available QA workflow before checks;
distinguish supplied logs, reviewer-reported execution and observed tool evidence.

## Reconcile and report

Recover compaction/truncation with durable reports and complete targeted rereads;
do not infer unread material or require a universal source-echo ceremony.
After interruption, reconcile the original run through the native procedure
before any new attempt. For a Git v3 candidate, the
[synthesis continuation procedure](references/continuation.md) preserves the
original attempt and researcher records; it is not a fresh team or extra
research allowance. V1/v2 do not gain continuation semantics implicitly.
Preserve incomplete reports and original findings. Candidate/input/policy
changes require a successor and current endorsement, not automatic approval
carryover. An authorized delta review can combine qualified prior per-team
analysis with new impact-scoped research; do not reroll a rejection until it passes.

Incomplete substantive coverage cannot pass. Distinguish a clean review from
release eligibility: under an explicitly bound blocking-only policy, completed
reviews with tracked nonblocking findings can qualify for release. Severity alone
does not determine blocking; require a concrete serious correctness, safety or
architectural consequence. Preserve original reviewer verdicts and separate
coordinator decisions. Record the exact candidate, real lead/child identities,
coverage, findings, check provenance and limitations using the versioned schema.
Never invent a team-wide runtime identity or fill missing evidence by inference.

Read original researcher proposals and the lead's reclassification reasons;
a status summary is insufficient. Before a required gate, obtain fresh native
readiness for the exact source and bound policy. `review-git` owns the Git
controller's release-ready versus clean-ready checks; another native workflow
must establish equivalent evidence without claiming a Git-controller result.
Follow [the shared handoff](references/review-report.md) and the native schema.
Report source, store, actual identities, findings, coverage, check provenance,
remaining blockers and next authorized action. Link durable records from a
handoff instead of copying transcripts or calling a pending round done.
A non-clean eligible result is not a clean PASS or publication permission.
Do not reinterpret historical clean-only policies. Ship only when authorized,
reusing applicable session or standing authority. When this review closes a
major autonomous implementation cycle, follow the
[cycle completion checkpoint](../implement/references/cycle-completion.md):
local commits, overall and per-task status review, and authorized task comments
with read-back receipts. Do not label uncommitted implementation cycle-complete.
