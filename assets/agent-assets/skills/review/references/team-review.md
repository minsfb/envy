# Independent reviewer-led teams

Each lead owns a complete independent review of the candidate and the supplied
[checklist](checklist.md). Independent teams, by default one GPT-family and one
Claude-family lead, do not divide coverage between them. Keep initial
investigations and final reports independent: researchers see their own team's
authorized context, not the other team's conclusions.

## Plan and adapt within bounds

Before launch, set the candidate/scope, exact models and evidence basis, available
tools, access/write permissions, time/resource budget, concurrency and stop
conditions. Use actual supported delegation. Maximum delegation depth is one:
leads may launch researchers; researchers do not launch further agents. Default
researcher models match their own lead as described in [model selection](model-selection.md).
Separately set the authorized actual researcher-run budget and its scope before
launch: per candidate, per attempt or cumulative, as explicitly authorized.
Count every actual child run, including failed or interrupted runs; follow-ups
in the same run still consume time/resources but are not new runs. Carry budget
consumption forward across retries and successor candidates within that stated
operational scope; do not silently reset or increase it. The controller's
`max_researchers` only caps declared researcher records per result, not actual
cumulative work or live concurrency. The lead/operator must track the actual
budget and set the harness's global concurrency/resource limits separately;
never omit child records to fit a cap.
At preparation, reserve adaptive headroom in `max_researchers` within the actual
authorized run budget; raising the bound later requires a successor candidate
with fresh review, not a mid-round edit or permission to exceed that budget.

Reserve time for lead verification and synthesis, not only researcher execution.
Deliver a readable plain-text diff, relevant base/head source and a concise path
inventory using the team's available read tools. A large JSON string containing
escaped diff lines may exceed a tool's limit even with line-based pagination.
Keep the original request binding; readable views supplement it, do not invent
new scope or replace its digest. Check delivery before launching researchers,
and preserve missing or truncated material as a gap until recovered.

A lead can add researchers, split or reassign work, request follow-ups and add
overlap when new risks emerge, within those limits. Schedule children sequentially
when concurrency is limited; bounded does not mean a fixed total shard count.
It need not ask permission for each in-scope assignment. Keep total work bounded;
when budget/access is exhausted, report unresolved scope rather than claiming
completion or exceeding authority. Delegation does not authorize source changes,
network access or tests outside the task's QA boundary.

Give researchers concrete obligations plus enough requirements, source and
contract context. They perform substantive code review: correctness, specification
conformance, error handling, tests, maintainability and unexpected behavior. They
can identify risks outside the original architecture questions and ask the lead
for follow-up coverage. They are not file summarizers or byte-reading witnesses.

Reading complete HEAD files alone does not account for changed or removed
old-side behavior. Every diff hunk, including removals, must be examined by a
researcher or recorded as unresolved coverage. The lead may redistribute gaps
within the existing budget.

The lead reads key requirements/architecture and independently inspects critical
integration code. It verifies consequential findings, reconciles conflicting
assumptions and closes new investigation gaps. Maintain a compact obligation-to-
owner/report map outside working context, with assigned, reviewed and unresolved
states. Link discovered risks to the frozen obligations as described in the
[checklist](checklist.md). Do not load large repeated inventories merely to
establish accountability.

Keep the map and original lead/child reports in an operator-owned private evidence
directory outside source repositories and their Git metadata. Before launch, the
operator designates an authorized writer for both map updates and original
reports, including incomplete or failed reports, not only the lead's summaries.
The writer preserves normal visible reports faithfully and verifies saved bodies
against the original reporting-channel output, then returns actual durable
references and content hashes through the authorized handoff. A hash binds the
saved copy; it does not establish fidelity to the original output by itself.
Read-only participants send candidate-bound updates and normal visible
reports through their authorized reporting channels; they need not write files
or access raw provider streams. The lead reconciles the map and proposals against
the originals available to it, not only writer summaries. The importer verifies
the saved originals and their bindings before recording the result. Make any
unavailable original or fidelity-verification gap explicit; a lead need not gain
file access or continue after termination to perform the writer/importer role.
If no such handoff is available, report the recovery gap; this grants no new
write authority.
The working map is separate from the ledger's final imported coverage; never
edit the ledger directly to store interim work. No particular layout is required.

## Briefs and reports

Adapt these compact outlines to the actual task. Give leads the full floor and
scope without the implementation coordinator's verdict or peer conclusions.
Specifications and source provenance are evidence to assess, not authority to
assume correctness. The [versioned schema](review-bundle.md) defines storage fields.

```text
Lead brief
Candidate: exact head/digest, base, authorized source and evidence references.
Scope: complete diff, supporting contracts, requirements and acceptance criteria.
Checklist: full required obligations plus task-specific questions; critical integrations.
Configuration: exact lead/researcher model policy, identity basis, actual harness.
Bounds: total researchers, global concurrency, time/resources, access and allowed checks.
Work: independently assess the design and critical code; delegate substantive review,
      investigate new risks, verify consequential findings and close your team's gaps.
Return: actual identities, coverage/report references, adjudicated findings, check
        provenance, limitations and verdict; keep user disposition separate.

Section assignment
Candidate and obligation IDs: required changed files/regions and supporting context.
Spec/context: relevant acceptance criteria, callers, contracts and complete source refs.
Questions: concrete correctness/failure/regression questions, not just a summary request.
Investigate: test assumptions and implementation; escalate new risks and coverage gaps
             beyond these questions to the lead. Do not delegate further.
Bounds: exact model/identity, assigned resource and access limits, permitted checks.
Return: reviewed/unresolved obligations with evidence; each proposal's stable ID,
        kind, severity, confidence and recommendation; impact, assumptions and
        requested outcome. State an empty proposal set explicitly.
        Include actual run/report references and executed-vs-supplied check provenance.
```

Researchers report what they established, not a manufactured full-team verdict.
The lead checks reports against obligations and records its own adjudication in
the schema; the main coordinator records later decisions independently.

## Findings and decisions

Researchers assign each proposal a stable ID and propose kind, severity,
confidence and recommended disposition separately. State an empty proposal set
explicitly.
Use the repository's severity scale, otherwise critical/high/medium/low; distinguish
informational suggestions from defects. Include impact/rationale, source/evidence,
assumptions and requested outcome. Recommended disposition is separately block,
address-before-merge, follow-up or optional. A coverage limitation or failed lead
is not by itself a severe code defect; low confidence is not an automatic waiver.
Separate demonstrated behavior from hypothetical deployment assumptions; for
example, a data-isolation defect alone does not establish unauthenticated reachability.

The lead owns final classification, verifies consequential claims and explains
material reclassifications while preserving the proposal. Distinguish serious
release blockers from nonblocking defects and suggestions using the
[workset policy](worksets.md). Keep kind, severity and blocking recommendation
separate. A non-clean review can be release-eligible under its explicitly bound
policy; never rewrite its verdict into a clean PASS. Incomplete substantive
coverage cannot pass.
A decisive blocker may justify stopping early with honest remaining gaps.
Retain genuine findings even when a lead fails or the review remains incomplete.

The main coordinator/user may fix, dispute, defer or explicitly accept findings
within their authority. Record that decision separately from the original lead
assessment; do not erase a finding, manufacture a clean PASS or infer publication
permission. Track valid nonblocking findings in follow-up Issues rather than
continually changing the release candidate. No disposition can waive missing
substantive coverage or identity. Historical clean-only policies stay unchanged.

## Evidence and recovery

Record real lead and child run/session identities, assignments, completion and
report references. One observed lead run owns its final verdict; do not invent
an aggregate runtime identity for several researchers. Distinguish supplied logs,
reviewer-reported checks and adapter-observed execution. Check authority and
effective subprocess/filesystem/network effects before running tests.

In versions 2 and 3, use the derived changed-file/supporting-file obligations plus every
supplied checklist obligation. `lead_required` requires the lead's assessment of
that checklist item, not personal ownership of every associated file. Coverage
evidence refers to each owner's actual report; reviewed owners need admissible
identity and completion. Keep researcher classifications and rationale in finding
`assessments`, and the lead's explanation in `classification_reason`. The original
child report retains the proposal's evidence, assumptions and requested outcome;
the top-level finding carries the lead's adjudicated versions. Reconcile every
original proposal, including rejected or grouped items, without flattening their
different classifications. If a malformed proposal cannot fit the schema,
preserve it verbatim in the referenced report and state the representation gap;
do not invent a valid-looking substitute. Resolve missing adjudication before
claiming a complete review. See [the schema](review-bundle.md) for exact fields;
coordinator dispositions are a separate append-only record.

Before finalizing, compare the distinct `(researcher, proposal ID)` keys in the
original reports with the keys retained in finding assessments. Explain grouped
or split findings without counting a repeated original as a second proposal.
An unmatched original is an adjudication gap even when every file was reviewed;
do not claim complete reconciliation from coverage totals or a finding count.

Compaction and truncation are recoverable. Resume from the candidate-bound
coverage map and durable reports; reread missing source/evidence completely where
needed. Do not infer omitted content or relabel incomplete work as reviewed.
Reconcile existing child/lead sessions before relaunching. Preserve old attempts
and apply [the bundle recovery procedure](review-bundle.md) for interruptions or
candidate changes. A fixed shard count, universal byte-echo proof or no-compaction
rule is not required; unresolved evidence or coverage gaps still block acceptance.

V3 offers a narrow [lead-only synthesis continuation](continuation.md) when the
same native lead can resume from unchanged researcher reports. Read that procedure
before using it. Continuing consumes explicitly authorized resources; it does not
reset the original budget, allow new researchers, reopen a rejection, or convert
old unresolved coverage into approval. If more research or a replacement lead is
needed, this continuation mode is not sufficient. V1/v2 histories keep their
original semantics; opting a successor into v3 does not carry old approval or
research ownership into the new candidate.
