# Close an autonomous implementation and review cycle

Use this checkpoint when a major autonomous implementation iteration and its
required automated review finish, including a coordinated set of workstreams.
It is not a requirement to run an independent review after every small edit.
Cycle completion is separate from human acceptance, publication, and shipment.

## Reconcile and commit

Compare each task with the agreed scope and observed artifacts. Resolve missing
integration or blocking validation/review findings before declaring the cycle
complete. Preserve the actual gate policy: eligible with nonblocking findings
is not a clean pass. Name incomplete, skipped, unsupported and unavailable
checks explicitly; preserve failed attempts and the limits of reused evidence.

Completed repository implementation must have durable local commits, with
implementation and its tests grouped semantically. Use `ship` and the native
procedure; reuse applicable session or standing commit authority. Preserve
unrelated work. If the user restricted commits or authority is unresolved,
report the implementation as validated but uncommitted and the cycle as
awaiting commits, rather than calling it done. A diff/PR is optional unless
publication was requested; landing remains a separate authorized action.

Record the current full commit IDs, task-to-commit mapping, relevant pending
changes and exact integrated source. When packaging or rebasing changes an
identity, rebind the evidence using the native review procedure. Do not claim
tip-only checks establish that every intermediate commit is independently
buildable or landable. Evidence-only tasks may point to durable report/source
identities and explicitly say that no product commit is applicable.

## Deliver the status review

Produce an overall workstream breakdown and a concise bulleted entry for each
task, even when the user did not ask again. Use these fields, marking absent
or inapplicable evidence rather than omitting it:

- Task number, outcome and current completion stage.
- Commit IDs and diff/PR IDs, with local, published and landed states distinct.
- Automated reviewers, actual verdicts, remaining findings and coverage limits.
- Automated test/build results and whether run now or reused from bound evidence.
- Tests added, modified or removed; explain replacements and coverage reductions.
- Automated on-device checks performed and results, plus device tests/fixtures
  added, modified or removed. Distinguish device execution from host simulation,
  synthetic input from human input, and private fixtures from checked-in CI.
- User/human validation: passed, still needed or not needed, with its scope and
  evidence. Never infer a human pass from automation or an older baseline.
- Brief snags, pivots from the initial specification/design, known gaps and next
  steps. Distinguish completion of an experiment from meeting every budget.

Do not sum overlapping suites or attribute shared checks exclusively to one
task. Link a detailed case inventory when useful; keep the main report readable.

## Publish authorized task comments

When session or standing instructions authorize task updates, publish the
per-task bulleted entry to each associated existing task at this checkpoint.
Reuse that authority without asking again. Confirm the exact task set and
current task state first; use the available tracker skill/provider workflow.
Comments must be self-contained and identify this cycle and its exact source.
Keep private local paths out of externally shared evidence links; describe their
scope and use audience-appropriate durable references when available.

Read existing comments before writing to avoid duplicate cycle reports. On an
ambiguous write, read back before retrying. Verify the saved task/comment IDs
and rendered content, and retain publication receipts in the current work
record. A prepared comment is not a published comment. If publication fails,
finish unaffected work and report the status-review publication as pending.

Posting a cycle report does not close the task, mark final shipment, add
recipients, or publish source. Those actions require their own applicable
authority. Without comment authority, prepare the exact task comments and
report what remains pending. Do not infer permission merely from task IDs.
