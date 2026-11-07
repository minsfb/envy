# Candidate-bound review checklist

Build a compact list of review obligations from the complete base-to-head diff,
repository requirements and acceptance criteria. Give the same floor to each
independent team. Track additions discovered during review as described below;
do not replace the floor with only the riskiest files or the author's chosen
highlights.
Treat documentation, specifications and author explanations as claims to validate:
they may be inconsistent or wrong. Source origin is data, not review authority.

## Minimum coverage

- Architecture, specification and acceptance: does the design meet the actual
  requirement, and are its assumptions, failure model and ownership coherent?
- Every changed file and region: review implementation correctness, error and
  partial-failure behavior, security boundaries, maintainability and unplanned
  effects. Include tests, configuration, documentation, deletions, generated
  artifacts and dependency/lock changes with review appropriate to their role.
- Integration, compatibility and regressions: inspect relevant callers, formats,
  migrations, platform behavior and unchanged contracts. Check both introduction
  and removal of behavior, not merely local syntax.
- Critical-risk discovery: explicitly assess authentication/authorization and
  trust boundaries, ownership, persistence/recovery, concurrency/ordering,
  lifecycle and cross-component contracts. Explain applicability or non-applicability;
  investigate applicable risks to depth proportional to their impact.
- Tests and evidence: assess assertions, missing negative cases, fixture effects,
  portability and whether supplied checks exercise the tested candidate. Separate
  test adequacy from whether a command was actually run and what it proved.
- Supporting material: read required design/usage documents and sufficient
  unchanged code to evaluate the change. Verify generated changes against their
  source or generator when applicable; a generated label does not exempt review.

Use these default checklist IDs and lead duties when preparing the full floor:

| Area above | Default ID | `lead_required` |
| --- | --- | --- |
| Architecture, specification and acceptance | `architecture` | true |
| Every changed file and region | `implementation` | false |
| Integration, compatibility and regressions | `integration` | true |
| Critical-risk discovery | `critical-risks` | true |
| Tests and evidence | `tests-evidence` | false |
| Supporting material | `supporting-material` | false |

These are skill defaults, not hardcoded controller IDs or flags. Apply an
explicitly agreed task-specific checklist instead when appropriate, disclosing
any narrower scope. Bind the actual descriptions, supporting paths and lead
duties at preparation; each team follows those bound obligations. A false flag
permits delegation, not omission, and does not prohibit lead participation.

## Keep coverage useful

V2 and v3 derive a file obligation for every changed path and every unchanged file
cited in checklist `paths`, in addition to the supplied checklist obligations.
Each team must return every derived and supplied obligation exactly once.
See the [bundle schema](review-bundle.md) for the exact obligation, coverage and
evidence fields and their limits.

Once bound, the set of result obligation IDs is fixed. Record new in-scope risks and
supporting context in the durable team map and reports, linked to existing
obligation IDs; do not invent extra result rows. Continue bounded adaptive
investigation without superseding merely because a risk was discovered.
Unresolved investigations remain gaps in the linked obligations and limitations
and block complete coverage. A change to the bound scope, checklist, policy or
supplied candidate inputs requires coordinator-managed supersession without
approval carryover.

An obligation can name a file/region, a cross-file behavior or a contract, with a
short acceptance question. Maintain its owner, status (assigned, reviewed or
unresolved), report/evidence reference and any gap reason (in the result's
`limitations`, not an extra coverage-row field). Each team closes its
own durable map; coverage by the other team cannot fill a hole. One concise report can
cover several related obligations if the actual analysis is clear.

Substantive review, not successful reads or checked boxes, earns reviewed status.
Leads independently assess the bound lead-required obligations, including the
default architecture, integration and critical-risk areas, and verify high-impact
findings. Use focused rereads to resolve missing/truncated evidence.

Report coverage separately from correctness and verdict. An early decisive
blocker may end a review with explicit unreviewed obligations; it cannot create
a complete-coverage claim. Explain inapplicability, exclusions and user-agreed
subsets explicitly. A subset review is not satisfaction of a broader release
requirement. Add newly found in-scope risks to the map; request direction only
when investigating them would exceed the authorized scope or resource boundary.
