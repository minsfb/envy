# V3: interrupted lead-synthesis continuation

Use this only when the same native lead can finish an interrupted review from
unchanged researcher reports. It is not a fresh independent review, a replacement
lead, a permission-denial workaround, or a way to reroll a rejection. Read the
[shared bundle procedure](review-bundle.md) and [team protocol](team-review.md)
first; their source, evidence, coverage and readiness requirements still apply.

## Opt in before the review

Use `version: 3` in otherwise-v2 preparation inputs. Reviewer and `team_review`
fields are unchanged. The candidate binds the same complete source/checklist,
exact model policy and evidence. V3 requests/results carry `version: 3`; disposition
records also carry `version: 3` with the same fields and semantics as v2.
Old v1/v2 candidates retain their original retry, uniqueness and readiness rules.
They cannot be continued in place by upgrading the controller
(`continuation_requires_v3`). A successor can
opt into v3, but gains no old approval or cross-candidate researcher ownership.
Ledger version follows the highest candidate version, and prospective schema
downgrades remain refused. Older controllers reject v3 rather than reinterpret it.

Distinguish three identities: the frozen candidate; each unique controller
attempt; and the actual native lead session. A continuation creates a new
controller attempt while retaining that one native session. Do not fabricate
an aggregate session, rename native IDs or attach old children to a new fork.

## Reconcile and authorize

Run ordinary `resume` and inspect the exact candidate and latest attempt. Check
the original invocation's real termination and saved reports before deciding
whether continuation is appropriate. Record its honest `interrupted` result
first, preserving partial findings and unresolved coverage. An invocation with a
completed rejection is not an interruption merely because more work would help.

Continuation requires the latest recorded **interrupted** result for the same
active candidate and slot, a non-null observed lead session and admissible lead
identity/launch evidence under the frozen policy. Pending, pass, changes-required,
failed and cancelled results are not eligible; an ineligible or nonlatest origin
is refused with `invalid_continuation`. A stopped invocation must not be
resumed concurrently with an already-running one. Native session recoverability
and actual termination are operator-verified facts, not inferred from CLI help
or the ledger's status. If the harness cannot resume that session, stop; this
mode does not support a replacement lead.

Establish explicit remaining or additional time/resources before another call.
Carry forward all consumed resources and the original researcher-run count;
no new researchers or resumed child work are allowed in this mode. The actual
lead must have enough evidence to reconcile every required obligation and
proposal. If more substantive research is needed, report that limit instead.
Autonomous authority can cover an in-scope continuation within its agreed
bounds; it does not silently override a fixed limit, access refusal or policy.

Prepare an audited authorization JSON with exactly:

```json
{
  "actor": "coordinator",
  "authority": "Specific existing or newly granted continuation authority",
  "limits": "Explicit remaining/additional resource bounds; no new research",
  "source": "Durable authorization and prior-invocation reconciliation evidence",
  "source_sha256": "SHA256_OF_REFERENCED_EVIDENCE"
}
```

These are nonempty bounded strings and a SHA-256 reference, not credentials or
an authenticated permission token. The operator verifies the cited evidence and
enforces the actual limits. The controller validates structure, not provider
availability, elapsed time, spending, live concurrency or permission grants.
Changed candidate scope/model policy still requires a successor, not an
authorization file claiming an exception.

## Checkpoint the new segment

```sh
python3 -I -B /path/to/review/scripts/review_bundle.py start \
  --store /private/reviews/round --expect CANDIDATE_DIGEST \
  --slot REVIEWER_SLOT --continue-from LATEST_INTERRUPTED_ATTEMPT \
  --authorization /private/review-inputs/continuation-authority.json \
  --reason "Authorized completion of interrupted lead synthesis" --sanitized \
  --output /private/reviewer-inputs/continuation-request.json
```

`--continue-from` and `--retry` are mutually exclusive. `--authorization` is
required with `--continue-from` (`continuation_authorization_required` if absent);
supplying it without `--continue-from` is refused (`continuation_not_requested`).
`--sanitized` acknowledges the added authorization's privacy audit; omitting it
yields `sanitization_acknowledgment_required`. V3 requests add one field:

```text
continuation: null | {attempt, result_digest, authorization}
```

Initial attempts and ordinary retries use null. A continuation binds the exact
predecessor result digest and copied authorization in its new request digest.
A mismatched bound predecessor digest is refused with `invalid_continuation`.
The attempt's `retry` link is null for continuation; the request's explicit
continuation field names the predecessor and selects this distinct transition.

V3 exported envelopes add `continuation_history`: an empty list for an initial
attempt or ordinary retry, otherwise the chronological full predecessor attempt
objects along this continuation chain. Each has the existing
`id/request/retry/reason/result/result_digest` fields. This includes original
findings and report references, never another team's results or the complete
ledger. Operators still provide the actual authorized report bodies and verify
their fidelity; an exported reference is not proof its content was read.

Relevant `invalid_continuation` checks include an ineligible or nonlatest
predecessor, a mismatched predecessor result digest, and a continuation combined
with a non-null retry link. Export-chain checks also reject a predecessor missing
from this candidate, a repeated/cyclic predecessor, a different slot, or a
mismatched result digest.

Checkpoint-before-export, exclusive output creation and existing size limits
remain in force. If export fails, inspect `resume` and re-export that same saved
attempt rather than create another segment. No provider is launched by `start`.
Use only the actual supported native resume mechanism, with unchanged access and
model restrictions. Keep original and resumed invocation evidence separately.

## Record and gate

The resumed result uses the normal team-result fields with `version: 3`. It must
retain the same lead adapter/session, identity basis and reviewer, and must not
lose or contradict a previously known served model. New admissible launch and
lead-report evidence can describe the resumed invocation; an unknown served
model may gain a matching actual observation. If a previously unknown served
model instead yields a contradictory observation, preserve it in the nonpassing
result: it blocks completion and further continuation. Do not replace it with
null to make the evidence appear consistent.

Linked-result checks use `continuation_result_mismatch` when an observed
continuation changes the lead adapter/session, full researcher list, identity
basis/reviewer, or a previously known served model—including losing that
observation—or loses or changes the predecessor's launch-model selection. New
launch-evidence references may describe the resumed invocation; they need not
equal the predecessor's references. The diagnostic also covers an unobserved
closure that violates the null-run form described below.

These descriptions are not an exhaustive malformed-input or diagnostic-precedence
catalog. Argument, schema and ordinary result validation may refuse input before
these checks.

If a new observation contradicts a previously **known** served model, the
unchanged-identity rule refuses that result with `continuation_result_mismatch`.
Preserve the original observation/report outside the store and stop for
coordinator adjudication; the attempt remains unresolved. Do not fabricate an
unobserved launch, erase either observation, or treat this as authorization for
a retry or cosmetic supersession.

Copy the **entire predecessor researcher list unchanged**, including IDs,
parentage, status, identity, launch evidence and report references/hashes. Those
are carried original observations, not newly executed or independently
corroborating research. No added, omitted, reparented or revised child record is
accepted. Known identity contradictions cannot be dropped to pass the gate.

Only the actual continuing lead can finish its judgments and coverage map.
Old rows may be unresolved even when their researcher reports are complete;
the new result can reconcile them into reviewed rows with the required evidence.
Their existence does not auto-complete coverage. Preserve every original
proposal, explain reclassifications, and disclose representation gaps without
inventing missing classifications. The coordinator cannot synthesize a lead
approval. Earlier actionable findings keep blocking clean readiness even if a
later result omits or downgrades them.
Findings are keyed by `(attempt, finding)`: repeating a finding ID in another
segment does not combine the records or their disposition requirements. Each
actionable record needs its own disposition; accepting risks, where explicitly
enabled, still yields non-clean `accepted-with-findings`, not clean readiness.

If continuation fails before a resumed runtime is observed, record an honest
nonpassing failed/cancelled/interrupted result with null run/launch, identity
basis `unknown` with null reviewer and model, no researcher records and no reviewed
rows. Preserve the origin via
the request/history; do not invent a resumed invocation to close the attempt.
That unobserved result cannot itself be continued. An actually observed
continuation must instead preserve the same session and full researcher list.
Resolve whether reattachment is still possible before closing an unobserved
attempt. Once recorded, that latest segment prevents further continuation of
the earlier native session in this store; its identity also remains unavailable
to an ordinary retry or successor candidate. A separately authorized fresh-team
retry can proceed with new identities, not by reopening the closed record.

A further continuation may link only the latest eligible interrupted segment,
with no branching to an older ancestor. The operator must verify new explicit
authorization for each segment; the controller checks its structure, not its
novelty or the authority it describes. Runtime
identity reuse is permitted only on this checked same-candidate/same-slot chain;
ordinary retries and unrelated teams/candidates retain global uniqueness.
Original attempts/results/digests stay immutable. V3 status identifies the latest
slot's `continuation_of`; it does not claim that a live process is running.

Finish with `status --require-ready` and inspect the exact candidate/head and
complete team evidence. An interruption, missing coverage or identity, and
accepted-with-findings remain non-clean. Continuation never authorizes a push,
merge, deployment or managed-environment activation.
