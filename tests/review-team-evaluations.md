# Review-team behavior evaluations

Use these cases for independent, observed evaluations, not Markdown keyword tests.
The asset unit suite checks installation and the synthetic source regression;
it does not establish that an agent follows the review policy.

Use `fixtures/review-teams/evals.json`. Its `candidate` is small public synthetic
source: before/after bodies, requirements and an unchanged caller. The operator
may materialize it in a fresh owned scratch worktree and bind exact base/head
using the already qualified review workflow. Never apply it to a real product.
Fixture materialization is intentionally an operator-owned preparation step;
these cases do not supply or qualify an automated provider runner.
This fixture samples modifications, deletion and unchanged context; it is not
an exhaustive add/dependency/lock/mode/gitlink matrix. Its matching generated/source
pair checks consistency, but cannot by itself detect skipped verification.
Extend those cases when evaluating those specific obligations.
Give an evaluator only the chosen request, candidate, the case's declared `inputs`
and applicable event inputs, plus the exact candidate skill path. Keep `observe`
rubrics and peer outputs from the evaluator. Do not reveal hidden expected
findings or selection outcomes.
Explicit user model choices in a case's request and prescribed event inputs
are part of the scenario and must still be supplied; they are not observer
rubrics or findings the evaluator is expected to discover independently.

Selection-only cases use explicitly simulated capability evidence. For these
simulated inputs, tell the evaluator that larger `capability_rank` means stronger
capability among selectable entries. Their
`fixture-*` IDs must never be passed to a real provider. Identity-evidence cases
also use ad-hoc synthetic model strings outside this selection catalog. These are
supplied scenario evidence, not provider IDs or observed runtime metadata.

For actual autonomous team cases, replace that simulation with current
operator-qualified capabilities, real selectable IDs, resource/access limits and
launch authority. Before launch,
include that actual context, the authorized round budget and global concurrency
limit in the evaluator's operator-materialized handoff; retain the supplied values
with the execution evidence. These are operator inputs, not facts established by
the synthetic fixture or new controller schema fields. If the required context is
unavailable, preserve that gap rather than claiming a qualified live-team result.
Installed CLI help alone does not prove delegation. If delegation is unavailable,
evaluate the honest blocked behavior; do not mark a live-team case passed.

Start with the interactive/default and explicit-selection cases, then exercise
autonomous coverage, status continuity, adaptive research, incomplete reports,
classification, explicit accepted-risk versus clean readiness, contradictory
identity, recovery and failure. A global concurrency limit of one or two
is useful for observing sequential scheduling; it is an evaluation setting, not
a universal runtime bound or model-context claim. Inject events at the stated
boundary and record their provenance as supplied scenario inputs, not observed
reviewer output. Do not fabricate real run IDs for import.
In a materialized recovery event, replace
`REPLACE_WITH_OPERATOR_SUPPLIED_REAL_RUN_ID` only with the actual observed native
run ID and its provenance. If it is unknown, explicitly exercise the unknown-run
variant and preserve that limitation; do not present the placeholder as an
observed ID. This native ID is distinct from the controller attempt ID used by
`start --retry`. This substitution belongs in the materialized event, not the
checked-in fixture.

`same-session-synthesis-continuation` is a fictional, explanation-only scenario.
Supply its events in order without materializing native IDs or a real ledger.
Its simulated additional-resource decision is not live inference authority, and
success in this exercise does not establish native-session resumability.

Inspect actual launch/tool traces, assignments, candidate-bound coverage maps,
source reads, lead reports and any coordinator decision. Check both teams'
coverage independently and that consequential claims were verified, not merely
echoed. Scope writes to owned review/fixture evidence. No source fixes, installs,
network tests, repository publication or active skill changes are granted by a
scenario. Provider/delegation launches require the operator's separate authority.
Existing QA boundaries apply if an evaluator proposes running a check.

Retain a concise result: candidate skill/source identity, scenario, operating
mode, selected models and capability basis, actual lead/child sessions, relevant
actions/artifacts, observed behavior, effects and limitations. A self-report is
not an execution trace. Compaction itself is not failure; unresolved evidence
or coverage is. A decisive finding may correctly end incomplete rather than
PASS. Preserve unsuccessful and interrupted evaluations.

Record a forward-evaluation outcome only after an independent harness actually
exercises it. Do not turn fixture consistency tests into behavioral results.

## Scenario index

These IDs name the cases in `fixtures/review-teams/evals.json`; the index is
navigation, not execution evidence or an additional required ordering. Continue
to withhold observer rubrics and peer outputs from evaluators.

| Case ID | Focus |
| --- | --- |
| `interactive-default` | Confirm the proposed interactive review configuration. |
| `explicit-selection` | Respect an explicitly selected configuration. |
| `autonomous-full-coverage` | Apply the full floor in an authorized autonomous stage. |
| `status-preserves-autonomy` | Preserve authorization across a status check-in. |
| `adaptive-research` | Investigate risks discovered during review. |
| `incomplete-pass` | Reject PASS with unresolved coverage. |
| `classification-and-disposition` | Separate researcher proposals, lead judgments and coordinator decisions. |
| `compaction-and-truncation` | Recover evidence without inventing missing coverage. |
| `delegation-unavailable` | Report unavailable delegation honestly. |
| `accepted-risk-is-not-clean-pass` | Keep risk acceptance distinct from clean readiness. |
| `contradictory-identity` | Preserve and report contradictory identity evidence. |
| `same-session-synthesis-continuation` | Reconcile v3 authorization, retained researchers and unobserved launch failure. |
| `workset-in-progress` | Keep intermediate implementation checkpoints out of the full-panel gate. |
| `workset-nonblocking-followups` | Release eligibility with tracked follow-ups is not a clean PASS. |
| `workset-delta-review` | Combine qualified prior evidence with focused review and final-head endorsement. |
| `workset-redesign` | Renew full review when new architecture invalidates previous coverage. |
| `lead-failure` | Retain unsuccessful lead execution and recovery state. |
