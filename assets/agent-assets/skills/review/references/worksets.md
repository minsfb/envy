# Feature worksets and focused re-review

## Choose the review boundary

A workset is one coherent, independently shippable capability with bounded
acceptance criteria and exclusions. It can include multiple PRs or repositories
when their contracts and version pins must work together. List those exact
components; do not make the entire backlog one batch or hide unrelated changes.

During implementation, inspect changes and run focused tests continuously, with
broader checks at meaningful integration points. Use targeted early advice for
consequential architectural/security decisions. Neither a checkpoint commit nor
an ordinary correction requires another complete independent panel.

Freeze a release candidate when implementation, required tests, documentation
and offline integration are ready. Both required reviewer teams independently
review the complete workset and checklist. Production activation remains a
separate authorized step; a review batch does not expand testing permissions.

## Decide which findings block

A blocker needs evidence or a credible unresolved risk of serious consequences:

- materially wrong, error-prone or missing required functionality;
- significant security, privacy, data-loss, isolation or recovery failures;
- a critical design/architecture miss that undermines the intended system or
  creates severe, expensive-to-reverse technical debt.

Explain the affected requirement, impact and minimum necessary correction.
Severity labels, preferred abstractions, speculative extensibility, or merely
deviating from an initial implementation sketch do not establish a blocker.
Do not dismiss a credible serious risk merely because its confidence is low.

Researchers propose classifications; leads adjudicate them. The coordinator
records the release decision under the applicable authority, without rewriting
the reviewer's original verdict. Ordinary nonblocking findings can be deferred
under an agreed standing policy rather than requiring permission for every nit.
Preserve honest severity and reasons for disagreements.

Each valid deferred finding needs a follow-up Issue or a matching existing Issue,
including evidence, impact and acceptance criteria. Deduplicate related proposals;
explain false positives rather than filing unnecessary work. Use an authorized
publisher for the intended repository/audience; the controller never contacts
an issue tracker. Keep private evidence out of public follow-ups.

Do not opportunistically fix nonblocking findings in the frozen candidate.
Schedule them as later bounded worksets. Release readiness is completion of the
required substantive reviews with no unresolved blockers, not a zero-findings
target. A non-clean release result must remain distinguishable from a clean PASS.

## Batch fixes and review the impact

After a completed whole-workset review, batch the genuine blocker fixes and run
their regression tests plus required project checks. For focused re-review:

1. Bind the original workset base, previously reviewed head, new head, policy and
   exact per-team prior report references/hashes in a neutral manifest. Provide
   the complete cumulative diff alongside the smaller correction diff and an
   impact map of affected contracts/tests. Deliver original report bodies through
   each team's authorized evidence channel, not as common attachments visible
   to both teams.
2. Each required team receives its own qualified prior analysis, not the other
   team's conclusions. Determine which coverage remains valid and why. Prior
   missing coverage, unqualified identity or a provider refusal is not reusable
   approval; preserve the original gap or restriction.
3. Review every new change and the affected contracts and regressions, adding
   research when newly discovered risk requires it. Both leads explicitly assess
   the final composition and endorse the exact final candidate.
4. Attribute prior research to its actual original reports. Current participants
   may assess its continued applicability without pretending to reread every
   unchanged line. Record that distinction in current coverage/report evidence;
   never relabel old researchers as new runs or silently transfer a stale PASS.

Use the existing successor and artifact mechanisms to carry the evidence.
Keep the complete workset base/checklist bound on the successor, not merely the
last correction diff. Current result owners attest to the combined analysis in
their new reports, referencing prior evidence where used; prior runtime records
remain historical, not newly declared children. This is not the same as v3's
same-candidate interrupted-synthesis continuation.

Renew full review when redesign, changed trust/ownership/persistence boundaries,
new acceptance scope or accumulated corrections invalidate the prior analysis.
Use impact rather than a fixed file-count threshold. If the available workflow
cannot represent essential coverage or endorsement honestly, report that specific
limit; do not invent approval or build unrelated machinery automatically.

## Substantive evidence, not perfect formatting

Missing required review, unestablished source/identity, incomplete substantive
coverage and failed essential validation remain not-ready. A formatting or schema
imperfection is not itself a code defect. Normalize only facts established by
the preserved original evidence and disclose nonessential representation gaps;
never manufacture missing judgments or hide contradictions. Where a controller
cannot represent a valid result, retain the originals and identify the precise
compatibility limit rather than declaring the review passed or redoing its code
research unnecessarily.
