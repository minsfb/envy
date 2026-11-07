# Captured Evidence And Acceptance

Use this procedure for delegated or multi-wave investigations. Resolve
`scripts/research_bundle.py` relative to the skill root. It uses Python 3.9+
and the standard library. Keep the bundle outside source repositories in an
authorized durable location; captured source content inherits its restrictions.
The evidence companion is still the reader's usable record, not just a link to
machine files.
Use the [shared graph loop](graph-workflow.md) for ownership, automatic ingestion,
generated inventories, recovery and reuse. This reference explains the original
recorder and artifact checks that the graph continues to use.

The helper records execution and checks declared structure. It cannot prove
exhaustive discovery, truthful manual extraction, semantic citation support,
layout, or source-access authority. It does not grant execution permission or
sandbox commands. Use only authorized operations; never put credentials in
specifications or argv. Use an existing credential mechanism. Check the output
route before capturing anything that could emit credentials.

## 1. Preserve The Task And Provider Constraints

Create a contract from the actual conversation. Preserve quoted requirements in
`requirements_source`; include purpose-specific requirements beyond the checked
fields there or in additional fields. A check cannot recover a requirement that
was omitted at intake. Example for fictional local research:

```json
{
  "version": 1,
  "purpose": "Help the director decide whether to retry Harbor streaming",
  "scope": "Supplied 2021–2024 records; include its former Quay name",
  "requirements_source": "User: two linked documents and a candidate table; brief at most 600 words",
  "requirements": {
    "destination": "Owned output directory; two linked documents",
    "candidate_table": true,
    "brief_max_words": 600,
    "research_plan": true,
    "search_routes": true,
    "discovery_followups": true,
    "research_budget": true,
    "protected_followups": true
  },
  "providers": {
    "local": {
      "read_formats": ["text"],
      "basis": "Supplied plain-text exports; unavailable referenced boards are not included"
    }
  },
  "questions": [
    {"id": "rationale", "text": "Why was it deferred?", "central": true}
  ]
}
```

Provider names/formats are task-specific. Resolve actual provider guidance and
record required faithful formats plus limits before delegating. Do not label a
lossy export acceptable merely to pass. Use null for an unspecified word limit.
For a one-page brief, explicitly define whether appendices count and check the
render too.

```sh
python3 /skill/scripts/research_bundle.py init /owned/run --contract /owned/contract.json
```

For new investigations, enable `research_plan` and follow
[the plan procedure](research-plan.md) before the first search. It adds a saved
coverage/worklist document, operation bindings, inspection/analysis states, and
reconciliation checks to the compact examples below. Older bundles without the
capability remain readable; do not claim the new checks ran on them.
For new delegated or multi-wave work also enable `search_routes`,
`discovery_followups`, `research_budget` and `protected_followups`. Use the
[budget and scheduling policy](research-budgets.md). The protected 330/600-minute defaults apply
when `requirements.protected_followups` is true; older unprotected budgets keep
their original defaults; optional top-level `research_budget`
overrides retain any user-specified limits in the contract. Do not apply new
accounting retroactively to an old run that did not capture it.
Use the [exact supported budget keys](research-budgets.md#set-the-allowance-before-dispatch);
delivery, concurrency and stage-share settings belong outside that object.

Give every researcher the actual contract, provider constraints, bundle path,
and a distinct operation-ID prefix and actor label. Actor labels are supplied by
the caller, not authenticated runtime identities; check them against assignments
and actual readers. A successful capture does not establish who interpreted it.
One coordinator owns shared decisions, saved plans and authored artifacts.
Researchers submit independent immutable returns to the same graph; its generated
ledger is read-only. Researchers can capture operations concurrently
under unique IDs. Do not amend the contract concurrently with capture. When scope
or requirements change, `amend ROOT --contract FILE` retains the prior contract
snapshots and receipts. Old reads are evaluated against the current read policy;
re-read affected sources if fidelity requirements changed.
Before final synthesis under an amended contract, save an updated plan using the
preceding generated ledger, settle any reopened questions or work, and export
and review the resulting inventory again. An old plan or semantic review cannot
establish completion under new requirements. Historical plan views retain the
questions and requirements from their saved contract.

Contract and plan publication retain a transaction under `state-publications/`
before changing the current file or `bundle.json`. If interrupted, use
`recover-current ROOT` to finish that exact retained publication; `amend` and
`plan` also resume it before accepting another change. The helper verifies both
old and proposed bytes, preserves immutable snapshots, and refuses unrelated
current-file edits. It never reruns research. A pre-existing mismatch without a
retained transaction still needs inspection of its original evidence.
Budget enablement resumes both journal and return-directory initialization;
an existing journal is validated and preserved before publication completes.

### Research Admission And Time Accounting

For protected budgeted runs, start each actor's research interval with
`budget-activity ROOT --actor NAME --action start --allocation baseline
--purpose research --branch BRANCH` before its research work. For legacy budgeted
runs without `protected_followups`, use
`budget-activity ROOT --actor NAME --action start` without allocation, purpose,
or branch flags. Stop either interval with
`budget-activity ROOT --actor NAME --action stop` when the actor leaves research.
Keep intervals open
through substantive reading and assessment, not only network calls. Stop for a
genuine user/infrastructure wait and record the reason in the work log. The
recorder sums overlapping actor intervals for effort and their union for elapsed
research time. These are declared activity intervals, not authenticated runtime
telemetry; the coordinator reconciles them against assignments and actual work.
For delegated work, the supervising coordinator owns these start/stop calls,
from before dispatch through observed completion; identify that owner in the
assignment. Children must not independently stop their clocks or substitute a
short local-script timer for the entire reading, assessment and return interval.
Report missing or partial timing without rewriting the original journal.
In protected runs, switch allocation/purpose/branch by closing the old interval
and starting a new one. Use `followup-1`, `followup-2` (or further configured
reserves) for their actual generations; use `extension` for later expansion or
coverage review. Coordination and repairs are charged to the cycle they support.
See [protected follow-ups](protected-followups.md) for binding and release rules.

Use `budget-status ROOT` at checkpoints. Record an advisory scheduling transition
with `budget-stage ROOT --stage baseline|priority|deep --reason TEXT`. In graph
mode, claim the fair next work; the advisory budget stage does not bypass protected
earlier work or establish dispatch authority. Stop research
intervals after the coverage review, before synthesis, revision and publication.
Budget exhaustion prevents further discovery admission; it does not prevent
validation or delivery from existing evidence.

Each `capture`/`begin` spec adds a `research` object, for example:

```json
{
  "stage": "priority",
  "branch": "earlier-evaluations",
  "parent_operation": "read-planning-index",
  "allocation": "followup-1",
  "purpose": "research"
}
```

A baseline root uses `parent_operation: null` and a concrete `root_basis`
explaining the scope/coverage need. Source-derived work keeps its parent rather
than claiming a new root. Generation is derived through the operation chain:
read→new query or different-source read advances it; search→result read does not.
Same-source formats/extents and pagination retain generation. Retries add
`retry_of` naming the failed operation; retain the exact request and recorded
failure cause. Classify an unsuccessful operation with
`budget-classify ROOT --id OP --classification access-blocked|unreadable|transient|unknown --reason TEXT`.
`budget-reopen ROOT --source-id ID --reason TEXT` records a material changed
access/capability basis before retrying a terminal source. Do not evade caps with
renamed retries or new actor budgets.
For a term discovered in search metadata/snippets, use the search parent plus
`origin_source_id`, `origin_locator` and `derivation_basis`; the new query advances
generation without requiring a body read merely to use its title. A justified
repeat of a successful request uses `refresh_of` and `refresh_basis`, such as a
changed source revision or an explicit repeatability experiment. This is separate
from automatic retry of a failed request and still consumes the shared cap.
In graph mode, a request-only admission left by a local interruption first needs
original-worker cancellation and `graph work` action `recover-retry`; see
[request-only recovery](graph-workflow.md). Do not fabricate a failure receipt
or use a fresh refresh epoch to bypass the original retry limit.

Admission reserves shared attempts and distinct source assessment identities
under a lock before dispatch. The effective timeout is limited by the request
cap and remaining research time. Source identity must be canonical: alternate
URLs for one file must not become separate assessment slots. Every actual
provider call, including additional formats and pages, needs its own operation.
Metadata-only reads set `research.assess: false`; they consume a read attempt but
no assessment slot. A subsequent content read defaults to assessment admission.
For already captured content, use `budget-assess ROOT --read-id ID --actor NAME`
before substantive inspection. Do not mark content reads as metadata to bypass
the distinct-artifact cap.
For imported MCP calls, honor the returned allowance in the actual tool timeout
where supported and report enforcement limitations; a recorder cannot cancel an
independently invoked tool or prevent unreported work.

## 2. Capture Before Interpreting

A CLI operation specification contains an ID, actor, kind, provider, exact request,
argv, absolute working directory and optional timeout (seconds; default 120).
With `research_plan` enabled, also supply `plan_item` and `plan_sha256` from the
saved assignment. The actor, provider, kind, and request must match it.
In graph mode also supply `graph_assignment_id` from a claimed, started assignment
and its original research ancestry. The compact request below shows capture
fields; add those actual plan/graph/budget bindings before execution.

```json
{
  "id": "history-search-01",
  "actor": "history-researcher",
  "kind": "search",
  "provider": "local",
  "request": {"query": "Quay", "filters": {}, "cap": null, "cursor": null},
  "command": ["rg", "-n", "Quay", "/owned/records.txt"],
  "cwd": "/owned",
  "timeout": 30
}
```

```sh
python3 /skill/scripts/research_bundle.py capture /owned/run --spec /owned/search.json
```

The request is written first. Output streams go directly to retained files,
including partial output on timeout; completion records hashes and exit status.
A killed recorder leaves an incomplete operation that fails acceptance. A
subprocess's nonzero status is an error receipt, including tools whose nonzero
exit means no matches; explain that provider convention in the indexed note.
Do not overwrite or delete failed operations to make a wave pass. Index the
failure and record its effect on coverage. On POSIX, timeout/interruption kills
the owned subprocess group, including ordinary wrapper children. Deliberately
detached processes, remote work and imported tool calls remain outside that
cancellation boundary; do not claim the recorder is a sandbox.

For a read, use `"kind": "read"` and a request such as:

```json
{"source_id": "record-7", "format": "text", "extent": "entire supplied body; linked board unavailable"}
```

New graph read plans also require a structured `graph.extent`, for example
`{"body":"all"}` for the whole captured body. This is separate from the provider
request's prose. The [graph workflow](graph-workflow.md#capture-index-and-actually-inspect)
shows the numeric complete-capture attestation and actual inspected bounds needed
to satisfy that request. Plan saving preflights graph scope and references before
retaining immutable plan files; fix a rejected draft before execution.

An MCP/native tool call cannot run through the CLI wrapper. Use `begin ROOT
--spec FILE` before calling the tool, save its exact permitted output, then use
`finish ROOT --id ID --output FILE --outcome ok` (or error/timeout/interrupted;
optional `--error FILE`). The receipt labels this **agent-attested tool output**,
not subprocess-observed execution. Do not upgrade recollections to captured
receipts. If storage restrictions prohibit retention, record that limitation and
use the provider-approved evidence route; report the affected checks unavailable.

After each operation, extract returned stable source IDs and pagination into a
result file, then `index ROOT --id ID --result FILE`:

```json
{
  "source_ids": ["record-7"],
  "pagination": "complete",
  "next_cursor": null,
  "note": "IDs extracted from retained stdout; all supplied records searched"
}
```

Allowed pagination values: `complete`, `capped`, `more`, `unknown`,
`not-applicable`. Record actual caps/cursors in requests, including each page.
An empty list represents no returned IDs, including a failed operation. Read
results in legacy bundles can use an empty list. Native graph reads include the
source ID and a `captures` declaration binding the actual format, extent and
receipt-bound retained bytes; see the [graph result example](graph-workflow.md).
Keep supplied seeds and link-followed candidates' distinct original provenance;
an observed reference is not a newly supplied seed.
Result extraction is agent-authored; spot-check it against raw output. Reruns get
new IDs and timestamps. They do not repair missing original receipts retroactively.
For captured source reads, also retain the reference scan and reconcile every
linked artifact into the common inventory as described in
[reading and reference expansion](reading-and-expansion.md). The scan records
references exposed by the captured format, separately from substantive inspection.

## 3. Accept Research Returns

For budgeted runs, retain each structured reader extraction with
`reader-return ROOT --return FILE` before consolidation. For example:

```json
{
  "id": "reader-index-return",
  "read_id": "read-planning-index",
  "actor": "history-researcher",
  "coverage": "Full supplied body; no comments exposed",
  "inspection": "full",
  "relevance": "relevant",
  "expansion_review": "The earlier pilot label can locate its evaluation",
  "leads": [{
    "id": "earlier-pilot",
    "kind": "vocabulary",
    "value": "Quay pilot",
    "locator": "Prior work, paragraph 2",
    "questions": ["rationale"],
    "reason": "Find the evaluation under its original name"
  }]
}
```

In graph mode, `reader-return` automatically preflights before retaining the
original or adding its budget event. Use `graph check-return ROOT --file FILE`
for an optional early diagnostic. A draft rejected by preflight can be fixed
before submission; the check does not perform or attest the reading.

The lead list may be empty with a source-specific explanation. This preserves
what the reader returned; it does not authenticate that the reader extracted
every useful phrase. Native graph returns add `graph_assignment_id` and structured
inspections, locators, scans and assertions as shown in the graph workflow. A base
return without those bindings is historical attested evidence, not sufficient
native coverage. The generated source inspection retains `return_id` and each
consolidated lead maps back with
`return_refs: [{"return_id": "reader-index-return", "lead_id": "earlier-pilot"}]`.
Preserve the original kind, wording, locator and question coverage. An explicit
bounded disposition still retains this mapping. New observations get a new
return ID. For an already retained invalid return, follow the
[reject-and-correct procedure](graph-workflow.md#recover-originals-without-repeating-research):
`graph reject-return` preserves a proven-invalid, wholly unaccepted atomic return,
then a new return's `corrects` links the original ID and file hash with a reason.
Never overwrite an original to match a later summary. Structural correction
preserves its inspection and disclosed leads, does not upgrade scope or refresh
inspection time, and grants no new read/assessment credit. A substantive new
inspection or interpretation follows ordinary return/reconciliation and budget
rules. Rejected or unsupported originals remain visible and incomplete.
Synthesis validation checks reconciliation of returned leads as well as the
existing lead→assignment→execution checks. Review the original content for
omitted vocabulary and reference edges separately.

An accepted native return that declares no inspection can be accounted for
without reading credit. The same applies when every inspection in a return was
formally withdrawn. Verified frozen graph evidence supplies these distinct
dispositions; all original leads and unfinished source work remain obligations.
Do not invent a successor correction link or restore withdrawn inspections just
to make synthesis validation pass.

Export the current graph ledger for record checks. This compact fragment
illustrates compatibility fields; it is not a template to edit over graph state.
The companion still needs dates, excerpts, contradictions and other context
relevant to the question. Record claim/question decisions through graph
reconciliation before regenerating the view.

```json
{
  "sources": [{
    "id": "record-7", "title": "Release review", "url": "https://example.invalid/record-7",
    "discovered_by": ["history-search-01"], "reads": ["history-read-01"],
    "coverage": "Full supplied body; linked board unread",
    "finding": "Defers release 1 pending reconnect correctness",
    "contributor_basis": "Named reviewer in supplied record"
  }],
  "claims": [{
    "id": "C1", "text": "Release 1 was deferred pending reconnect correctness",
    "basis": "documented decision", "reads": ["history-read-01"],
    "locator": "record-7, Release decision; excerpt retained in companion C1"
  }],
  "questions": [{
    "id": "rationale", "status": "answered", "claims": ["C1"],
    "reason": "Contemporaneous decision record; later summary conflicts"
  }]
}
```

Include every returned candidate, even exclusions and unread sources. Use empty
`reads` for unread candidates. A `discovered_by` entry may be `seed:` followed by
exact supplied provenance; graph records retain observed reference paths separately.
Successful reads must
match the source and current provider format policy. Claims reference successful
reads and precise evidence locations. These fields are assertions checked by a
human or agent against the source, not automatic semantic proof.

```sh
python3 /skill/scripts/research_bundle.py graph export /owned/run --id final-evidence
python3 /skill/scripts/research_bundle.py validate /owned/run --ledger /owned/run/graph/exports/final-evidence/ledger.json --phase research
python3 /skill/scripts/research_bundle.py validate /owned/run --ledger /owned/run/graph/exports/final-evidence/ledger.json --phase synthesis --semantic-review /owned/run/reviews/semantic-review.json
```

**Consume the exit status:** 0 passes the stated checks; 1 fails; argparse usage
errors return 2. Preserve JSON diagnostics. Do not accept a research return until
record checks pass, or call synthesis complete while its checks fail. Repair the
underlying records/retrievals, not the failure label. Keep meaningful limits visible.
Acceptance also compares reference scans against available source link metadata,
checks target dispositions, and verifies actual delegated assignments and returns.
These are agent/source checks outside the helper's structural validator; preserve
their evidence and do not report a helper pass as proof they occurred.
Keep semantic review separate from the immutable export. After actual passage
and claim review, retain a JSON file inside the bundle with `reviewer`, `note`,
`ledger_sha256` and `graph_sequence`. Obtain the canonical ledger hash and
`represented_sequence` from `graph validate-ledger ROOT --ledger FILE`; put that
represented sequence in the review's `graph_sequence`. Validation reports the
review file hash and rejects a mismatched or stale review. Actor identity and
the review itself remain explicit attestations, not inferred from a graph pass.

The synthesis phase also requires all declared questions to be `answered` with
claims or `bounded` with reasons. A bounded central question additionally needs
`followups` listing executed operation IDs. Assess whether those follow-ups really
address the gap—IDs alone cannot establish adequacy. Use older aliases, adjacent
reviews and cross-links, and request known source leads if useful. Record assisted
discovery honestly. No score or count establishes exhaustive coverage.

## 4. Check Delivered Artifacts

Export the intended content and freshly retrieved actual documents as separate
HTML files. Multiple files can represent ordered tabs. Create a manifest:

```json
{"candidate_tables": [0], "artifacts": [
  {"id": "brief", "url": "https://example.invalid/brief",
   "expected": ["/owned/expected-brief.html"], "actual": ["/owned/readback-brief.html"]},
  {"id": "companion", "url": "https://example.invalid/evidence",
   "expected": ["/owned/expected-evidence.html"], "actual": ["/owned/readback-evidence.html"]}
]}
```

Run `publication ROOT --ledger FILE --manifest FILE --semantic-review REVIEW_FILE`
with the same frozen graph ledger and retained review. It first requires synthesis
validation, then compares normalized text, link multiplicity and placement,
ordered table cells/spans and export-file boundaries. It checks mutual links and
any word limit, and reports exact file, contract, ledger and checker hashes.
If a candidate table was required, `candidate_tables` selects zero-based table
indices across the companion's ordered export files. Select only the candidate
inventory tables; read logs and claim tables can repeat the same source IDs.
Each inventory ID must have exactly one distinct row in the selected tables,
with its ID in a distinct cell, source link, title, read coverage, finding and
contributor basis. For readable compact IDs, add an explicit `candidate_rows`
mapping to the manifest, for example
`[{"source_id": "record-7", "row_id": "S0001"}]`. Map every raw ledger source ID
exactly once to a unique visible row ID. This preserves raw identities, including
aliases that would otherwise collide after whitespace normalization; do not
rewrite the ledger or combine records to satisfy publication checks. The receipt
records the mapping hash and number of distinct rows checked.

For a source explicitly excluded by an identity in the frozen contract's
`prior_research.excluded_artifact_keys` or graph `excluded_artifact_ids`, a row
mapping may add `navigation_exclusion_reason`. Put that exact nonempty,
normalized reason in the visible row and include no hyperlinks in the row.
The checker verifies the registered frozen identity, including known aliases,
against the current contract and reports omitted navigation separately. A broad
reuse exclusion, a source-status label, or a self-declared manifest reason alone
does not authorize this exception. Keep the row and all required source fields;
do not fetch a protected source to satisfy publication lint.

Navigation links may strip surrounding whitespace from a raw source URL. The
receipt records each such normalization without changing the retained source.
Encoding that whitespace as `%20` is a different destination and fails the check.
No other URL equivalence is inferred. Harmless style/wrapper differences are
ignored. Nested tables
require a provider-specific check. The helper is not a general HTML validator.

Use the appropriate provider/artifact tools to retrieve and render. Never create
the actual export by copying expected files. For other formats, use an equivalent
artifact check with explicit failures and retain its evidence; do not claim this
HTML check ran. Verify layout and substantive citation support separately.
When the research plan is required, refresh its readable view, place its final
coverage/worklist in the companion, and supply the `plan_review` attestation
described in [the plan procedure](research-plan.md). This attests to a review of
placement; it does not mechanically prove coverage or readable presentation.
After user edits, read current content and update expected material only within
authorized editing scope. Review-only never authorizes repairing the artifact.
