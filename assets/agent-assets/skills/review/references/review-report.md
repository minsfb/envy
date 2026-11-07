# Independent review handoff

The selected native procedure owns candidate binding and readiness. For Git,
use `review-git` and its existing controller/result schema. For another native
workflow, retain the same evidence distinctions without claiming that a manual
record was verified by that controller.

A review request identifies:

- Repository, exact candidate/base/ordered commits and relevant artifact versions.
- Scope, accepted constraints, review questions, and dependency changes.
- Required reviewer slots and any actual identity/model policy.
- Supplied verification evidence and allowed execution/resource boundaries.

A result records:

- The request/candidate identity and actual reviewer/run identity; unknown model
  or provenance remains unknown rather than inferred from a requested flag.
- Findings by severity, with file/line, trigger, impact, and suggested remedy.
- Executed checks versus supplied evidence, skips, and coverage limitations.
- Pass, changes required, failed, cancelled, or interrupted outcome.

Preserve original reports, including rejected or interrupted attempts. Reconcile
findings and record disposition; do not erase disagreement or retry reviewers
until a desired verdict appears. Relevant source, input, or policy changes need
a fresh required gate. Routine inspection does not require creating a ledger.
