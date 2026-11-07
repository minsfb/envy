# Workflow decision probes

These synthetic cases complement installer/controller tests. They are not a
live publication or cross-harness certification suite. Run with the candidate
skill catalog and minimum raw project artifacts; do not give the operator the
expectations or implementation diff before its initial response.

For `small-fix`, provide a disposable Python project with `summarize_count`
currently returning `str(value)` and tests for None, zero, positive and negative
integers. Permit changes only to that fixture and run its standard-library tests.
The other cases request a decision/procedure response; no real repository or
provider mutations are permitted. Capture responses and observable tool actions
outside the tested fixture. Do not ask the real user the synthetic questions.

Record exact skill-source hashes, case, actual harness/reviewer identity,
response/tool evidence, modified files, executed checks, and limitations.
Assess decisions against the held-back expectations after the response; do not
assert a pass from text/heading matching. Repeat any case affected by a
substantive correction. Native procedure probes do not prove live command/API
execution or an actual publication receipt.
