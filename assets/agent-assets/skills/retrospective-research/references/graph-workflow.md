# Run A Shared Research Graph

Use this loop for new delegated or multi-wave investigations. Resolve `/skill`
to this skill directory and `/owned/run` to the authorized evidence bundle.
Examples use fictional IDs; copy real work IDs, hashes, revisions and scope
requirements from command results. Do not paste placeholder hashes into a run.
For record shapes or normalized reconciliation packets, read the
[graph contract](graph-contract.md). The helper uses Python 3.9+ and SQLite 3.38+.

The graph owns consolidated research state. Original requests, outputs, receipts,
reader returns, saved plans/contracts and the shared budget journal keep their
authority. Inventories and worklists are generated views; the concise brief and
the companion's explanation remain authored work. A graph pass proves neither
source meaning nor complete discovery.

Sections: [initialize](#initialize-and-save-the-plan), [query](#query-before-assigning-work),
[workers](#launch-real-workers-and-claim-their-work), [capture and inspect](#capture-index-and-actually-inspect),
[follow-ups](#reconcile-obligations-and-continue-fairly), [recovery](#recover-originals-without-repeating-research),
[delivery](#freeze-a-view-review-it-and-deliver), [reuse and migration](#reuse-a-prior-investigation-or-migrate-a-legacy-bundle).

## Initialize And Save The Plan

Preserve the actual task in the [contract](bundle-procedure.md), enabling
`research_plan`, `search_routes`, `discovery_followups`, `research_budget` and
`protected_followups` for
new multi-wave work. Establish provider read requirements and any prior-research
policy before use. Prepare the [plan](research-plan.md), including concrete
routes, seed reads and a bounded wave strategy.

```sh
python3 /skill/scripts/research_bundle.py init /owned/run --contract /owned/contract.json
python3 /skill/scripts/research_bundle.py graph init /owned/run --actor coordinator --run-id harbor-2026
python3 /skill/scripts/research_bundle.py plan /owned/run --plan /owned/plan-draft.json --ledger /owned/initial-seeds.json
```

The first ledger is only intake: `{"sources":[]}` or supplied seed records with
exact `seed:` provenance; use a stable key without spaces, such as `seed:intake-1`.
For a supplied record, `initial-seeds.json` can contain:

```json
{
  "sources": [{
    "id": "record-7",
    "title": "Supplied release review",
    "url": "https://example.invalid/record-7",
    "discovered_by": ["seed:intake-1"],
    "reads": [],
    "coverage": "Supplied identity and title only; body unread",
    "finding": "Pending inspection",
    "contributor_basis": "Unknown; title metadata does not establish contribution",
    "inspections": [],
    "analysis": {"status": "pending", "reason": "Source body not yet inspected"},
    "disposition": {"status": "pending", "reason": "Supplied candidate awaits assessment"},
    "leads": []
  }]
}
```

Metadata is not an inspection. Keep those empty read/inspection arrays and pending
states until actual evidence is recorded. This seed can be the target of the
`read-review` item in the [planned read example](research-plan.md).

`plan` preflights the graph projection before saving immutable files, then
registers those seeds and the plan's question-specific work. A rejected draft
does not change the saved plan. After this bootstrap, use a fresh generated
`graph/exports/ID/ledger.json` for each plan save. Change research facts through
returns or reconciliation, never by editing the exported ledger. Save contract
amendments and a matching plan to register new or changed questions; old plan
bindings remain historical.

### Contract amendments

If a contract or plan update stops between publishing its current file and
metadata, run `recover-current ROOT`. This completes the retained transaction,
including pending graph plan registration, without rerunning research or
rewriting earlier snapshots. `amend` and `plan` also resume pending publication
before accepting another update. Do not manually fix current hashes.

Pause dispatch and reconcile active provider calls before changing the contract.
`amend` retains the new contract and updates the recorder; it does not register
that contract in SQLite. A matching new plan normally creates the graph binding.
When continuing existing assignments after a time-only amendment, register the
retained contract first through a coordinator `reconcile` packet and the public
`graph ingest` command:

```json
{
  "schema_version": 1,
  "batch_id": "bind-budget-amendment-2",
  "actor_id": "coordinator",
  "assignment_id": null,
  "packet_kind": "reconcile",
  "contract_sha256": "NEW_CONTRACT_SHA256",
  "submitted_at": "ACTUAL_UTC_TIMESTAMP",
  "mutations": [{
    "op": "insert",
    "table": "contract_snapshots",
    "record": {
      "contract_sha256": "NEW_CONTRACT_SHA256",
      "contract_revision": 2,
      "relative_path": "contracts/NEW_CONTRACT_SHA256.json"
    }
  }]
}
```

```sh
python3 /skill/scripts/research_bundle.py graph ingest /owned/run --file /owned/bind-amendment.json
```

Replace the placeholders with the exact retained contract hash and current UTC
timestamp. Use the registered coordinator, a fresh batch ID and the next unused
contract revision; `2` is only the first-amendment example. Ingesting this packet
validates the retained file and appends a binding; it does not replace earlier
contracts, plans or spending. If questions or work scope changed, also save a
matching plan before assigning changed work. Existing assignments retain their
original plan and inspection scope; a contract binding does not broaden them.

An unregistered amendment can block new exports or operations with
`Packet contract has no retained graph binding`. Register the retained contract
instead of repeatedly retrying the provider. Preserve any failed admissions:
they consume the journal's attempt allowance even if no provider call ran.
Report those admissions separately from actual provider dispatches.

### Planned reads

Each assignment is limited to the saved item's coverage questions, optionally
narrowed by `graph.questions`. An otherwise identical request for another
question needs its own permitted plan binding. Claim, admission, and native
ingestion enforce the same limit; historical accepted records are preserved.

Every new graph read item needs structured `graph.extent`. Request a known
numeric boundary, or use `{"extent":{"body":"all"}}` inside the item's `graph`
object when the task needs the whole captured body. The provider request's prose
`extent` still describes the operation; it cannot establish numeric coverage.
Copy matching scope and format-policy keys from the registered work into the
reader return. See the [planned read example](research-plan.md).

Choose the content binding before assigning a read. The default
`captured_after_assignment` requires a new capture. Use `graph.version_id` for
an exact known provider version, or `graph.capture_ids` to reread specific
retained native captures, including captures whose provider revision is unknown:

```json
{
  "graph": {
    "extent": {"body":"all"},
    "formats": ["ghtml"],
    "capture_ids": ["COPY_NORMALIZED_CAPTURE_ID"]
  }
}
```

Use one binding policy; do not combine `capture_ids` with `version_id` or
`freshness_basis`. Obtain capture IDs from the artifact query. Every pin must
match the target, requested format/extent and verified original receipt/bytes.
Retained-only assignments admit no provider call. A new actual reader return
must cover the selected captures; an old inspection or a reference scan cannot
complete the assignment. Unknown provider revisions remain unknown.

After the graph work completes, export a current ledger and mark that retained
read item `done` in a new saved plan. The plan checker binds it to the original
item, latest completed assignment and effective native inspection; it does not
require a new retrieval. Keep the original item definition and graph scope. If
the content policy was explicitly corrected on completion, replace its old
freshness/version annotation with the verified capture IDs in this new snapshot.
An uncompleted or withdrawn reading cannot back a current `done` status. Older
saved plans still render against their own frozen evidence.

`plan` reports `plan_sha256`. If its immutable files were saved but graph
registration failed, repair the dependency and replay that exact saved plan:

```sh
python3 /skill/scripts/research_bundle.py graph register-plan /owned/run --sha256 SAVED_PLAN_HASH
```

## Query Before Assigning Work

For protected runs, also use `graph followup-status ROOT`. Its graph-derived
view includes unscheduled leads, external references grouped by target/question,
uninspected source surfaces, full-cycle evidence and allocation spending. Check
it after each reader wave; the ordinary task frontier cannot show work that has
never been assigned. See [checkpoint and stop commands](protected-followups.md).

```sh
python3 /skill/scripts/research_bundle.py graph frontier /owned/run --question-id rationale --limit 50
python3 /skill/scripts/research_bundle.py graph coverage /owned/run --question-id rationale --limit 50
python3 /skill/scripts/research_bundle.py graph leads /owned/run --unresolved-only --limit 50
python3 /skill/scripts/research_bundle.py graph next-work /owned/run --actor history-researcher --limit 50
```

`frontier` exposes research holes; `next-work` selects claimable work in fair
waves. Neither dispatches a call. Check unread plausible roots, unresolved
references, disputed role/relevance assessments, inspection scope and explicit
access or effort bounds. Coverage is broader than citations supporting a draft.

Use `graph artifact ROOT --artifact-id ID`, `graph neighbors ROOT --artifact-id ID
--direction both --relation both`, `graph evidence ROOT --claim-id ID` and
`graph changes ROOT --after-sequence N` for source detail, observed and semantic
relationships, claim evidence and audit history. Optional question/disposition
filters narrow a view; they do not erase other scopes or disagreements.
Queries return 1–200 items per page. Continue with the returned cursor and the
same filters; a changed graph sequence rejects the cursor. Restart the query
instead of mixing pages from different snapshots. An empty page is not a proof
of domain coverage.

## Launch Real Workers And Claim Their Work

Register actual harness children, preserve their runtime identities and return
locations, then launch them with the shared contract, saved plan, bundle root,
scope and allowance. A registered actor alone is not delegation. Local processes
may submit to the same graph; remote workers return packets to its host rather
than opening SQLite across a network filesystem. Never hold a graph writer lock
across provider execution, reading or analysis.

Ordinary saved-plan actors are registered as direct reader/searcher children.
Register specialized or nested actors **before** saving their plan items so
their role and parent are correct. A real research lead uses this request to
register a child inside its active parent assignment:

```json
{
  "action": "register-actor",
  "actor_id": "review-reader",
  "role": "reader",
  "parent_actor_id": "history-lead",
  "branch_scope": "history/reviews",
  "stage_scope": "baseline",
  "by_actor_id": "history-lead",
  "parent_assignment_id": "ACTIVE_LEAD_ASSIGNMENT",
  "declared_runtime_identity": "ACTUAL_CHILD_ID"
}
```

Submit work requests with `graph work ROOT --file REQUEST.json`. Claim an item
selected by `next-work`, using its current work revision and saved plan binding:

```json
{
  "action": "claim",
  "work_id": "WORK_FROM_NEXT_WORK",
  "actor_id": "review-reader",
  "plan_sha256": "SAVED_PLAN_HASH",
  "plan_item_id": "read-review",
  "expected_revision": 1,
  "by_actor_id": "history-lead",
  "parent_assignment_id": "ACTIVE_LEAD_ASSIGNMENT"
}
```

For a direct coordinator child, omit the last two fields. The claim returns an
assignment ID; start that new assignment with
`{"action":"start","assignment_id":"RETURNED_ASSIGNMENT","expected_revision":1}`.
Later transitions use its then-current revision. Conflicting claims fail without
creating another lease. Renew an active lease before expiry; expiry is a reason
to reconcile the original worker, never proof it stopped.

Readers submit their own observations, inspections and proposed assessments.
They may expose leads for other known questions, but cannot execute outside
their assignment or accept their own shared conclusions. Leads can schedule
within delegated branch/stage/question limits; global merges, final bounds,
cross-branch priorities and shared claim revisions stay with the coordinator.
Independent coverage/evidence reviewers get distinct work purposes and consume
the same research allowance. Actor labels and runtime IDs remain attestations
unless the harness separately verifies them.

A fresh review of an already retrieved source keeps the original request's
parent and generation. Review importance belongs in `priority` and `purpose`;
it does not make a root request a `priority` research stage. Roots use `baseline`,
and generation two or later uses `deep`. Supply the recorder's explicit refresh
basis for a repeated successful request. If an older helper accepted an invalid
review stage, retain that saved plan and close its unadmitted intent before
adding a corrected replacement. Historical replay or closure never permits a
new admission through the invalid stage.

## Capture, Index And Actually Inspect

Start the actor's classified `budget-activity` interval as described in
[protected follow-ups](protected-followups.md). Every `capture` or `begin` spec
names `graph_assignment_id`, the exact saved `plan_sha256` and `plan_item`, and
the original `research` stage, branch, parent operation and allocation.
The saved `graph.allocation` and optional `graph.activity_purpose` (default
`research`) must match the operation and active clock. Coverage-review uses
`extension` and requires a registered `coverage_reviewer`. Its provider request
must match the saved plan. Each format, page and retry is a separate admitted
operation; graph deduplication cannot erase spending or reset ancestry.

Use the [recorder commands](bundle-procedure.md) to retain execution before
interpreting it. Graph hooks validate before admission, mirror the real admission
and receipt, and record subprocess dispatch intent immediately before launch.
For a native tool, call `begin`, then submit
`{"action":"record-dispatch","operation_id":"read-planning-index"}` immediately
before the actual call. Retain its output with `finish`. If dispatch was not
observed, reconcile its real receipt; do not manufacture an earlier launch event.

In graph mode, a read result explicitly includes its source and captured bytes:

```json
{
  "source_ids": ["record-7"],
  "sources": [{"source_id":"record-7", "namespace":"fictional-archive",
               "external_id":"index-7", "title":"Earlier evaluations", "type":"document"}],
  "pagination": "not-applicable",
  "next_cursor": null,
  "note": "Both supplied body lines retained; comments unavailable",
  "captures": [{"id":"body-text", "source_id":"record-7", "format":"text",
    "relative_path":"operations/read-planning-index/stdout",
    "extent":{"body":{"unit":"line","ranges":[{"start":1,"end":2}],
      "complete":true, "attestation":"Both lines of the supplied body were retained",
      "limitations":"Comments and linked records are not part of this body"}},
    "extraction_status":"complete", "limitations":"Only the supplied body is exposed"}]
}
```

Index every returned occurrence, including duplicates by route and unread or
excluded candidates. A provider namespace identifies the original record, not
the route used to fetch it. Same-title/different-ID records stay distinct. A
capture references receipt-bound bytes; copying another file into the bundle
does not make it verified evidence. Missing bodies stay limitations.

`index ROOT --id OPERATION --result FILE` validates the exact prepared index on
a disposable graph snapshot before retaining `result.json`. It holds the shared
writer through validation, retention and normalization. For an early draft check,
use `graph check-index ROOT --id OPERATION --file FILE`; that check grants no
lease. Failed preflight leaves the index unretained, so correct the draft without
repeating the provider call. Optional `version` must be an object; `revision` is
a string and `modified_at` is a UTC RFC 3339 timestamp. Omit unknown version
metadata from the draft rather than coercing provider timezone text into UTC.
Keep the raw provider output intact.

For requested `body: "all"`, the captured body must declare `complete: true`,
nonempty numeric ranges, a completeness `attestation` and `limitations`.
`extraction_status: "complete"` alone does not establish this boundary. Attest
only the component actually retained by the provider/adapter; body completeness
says nothing about comments, revisions, visuals or inaccessible content. Reading
must cover all those captured numeric bounds with the same units. Record the
actual numeric inspected extent, even when the requested extent was `"all"`.

Before submitting a full-coverage return, check the source-specific assessment
for every assigned section using the [reading checkpoints](reading-and-expansion.md).
Successful displays and method-only placeholders are insufficient. A structural
preflight cannot replace this coordinator check or establish semantic reading.

After actual reading, `reader-return ROOT --return FILE` preflights the complete
draft against current graph state before retaining it in the budget recorder and
normalizing it. Failed preflight leaves the original return and budget unchanged;
fix the unsaved draft. `graph check-return ROOT --file FILE` runs the same check
early when useful. Final ingestion rechecks the bindings. Add `graph_assignment_id` and structured
`graph` observations to the [base return](bundle-procedure.md). For example:

```json
{
  "locators": [{"id":"prior-work", "capture_id":"body-text", "kind":"line",
                "position":{"unit":"line","start":2,"end":2}}],
  "inspections": [{"id":"body", "source_id":"record-7",
    "extent":"Supplied body lines 1–2", "method":"Read retained body text",
    "limitations":"No comments or underlying linked records inspected",
    "captures":[{"capture_id":"body-text","extent":{"body":{"unit":"line","ranges":[{"start":1,"end":2}]}}}],
    "scopes":[{"question_id":"rationale", "required_scope_key":"COPY_FROM_WORK",
                "format_policy_sha256":"COPY_FROM_WORK", "coverage":"sufficient",
                "basis":"Every requested body line and format inspected"}]}],
  "scans": [{"id":"references", "capture_id":"body-text",
    "method":"Enumerated mentions in both supplied lines",
    "extent":{"body":{"unit":"line","ranges":[{"start":1,"end":2}]}},
    "limitations":"Comments unavailable",
    "references":[{"id":"pilot-mention", "kind":"mention", "value":"Quay pilot",
                   "label":"Earlier evaluation named without a URL", "locator_id":"prior-work"}]}],
  "assertions": [{"id":"organizes-prior-work", "source_id":"record-7", "kind":"role",
    "predicate":"has_role", "value":"trunk", "question_id":"rationale", "basis":"inspected",
    "subject_locator_id":"prior-work",
    "evidence":[{"locator_id":"prior-work","inspection_id":"body","stance":"support",
                 "basis":"inspected","rationale":"The passage names the earlier evaluation to locate"}]}]
}
```

Only attest what was actually inspected. Copy question/scope/format policy from
the work; report partial coverage when any requested format or extent is missing.
Completion also checks source version or the explicit freshness policy. A new
inspection of old bytes cannot satisfy `captured_after_assignment`. Scanning
links does not inspect their targets. Include every extracted reference and
every unlinked vocabulary/record lead in the original return before selection.
Preflight and coverage checks validate declared structure and retained evidence;
they do not perform the reading or accept its interpretation.

## Reconcile Obligations And Continue Fairly

Every declared reference and lead creates a pending obligation automatically.
Ingestion does not decide whether to follow it or prove that the reader extracted
all references. Resolve mentions through evidenced identity mappings; retain
original wording and previous resolutions. Record semantic relationships such
as `approves` separately, with section/question/time scope and inspected support
or a labeled inference. An accepted inference remains an inference. Keep
qualifying and conflicting evidence queryable beside support.

Read plausible Content, Critical/Review and Trunk artifacts. For a relevant
source, default to reading exposed research-artifact children lacking matching
coverage; interleave useful terminology searches across branches. Stop an
irrelevant inspected leaf or record a specific scope/access/effort bound. An
unread or inaccessible source cannot close a subtree as irrelevant.

Save concrete follow-up plan items with original `origin.source_id`, `read_id`
and `locator`. Their `graph` annotations can name the normalized `lead_id` or
`reference_id` and original `parent_operation`; obtain those IDs from queries.
Registration retains every item/question origin even when several routes reuse
one logical work item. Ordinary reads deduplicate by canonical identity and
intent; a deliberate independent review declares its distinct purpose. Existing
terminal work reports `reopen_required`; reconcile the changed basis before
explicitly reopening it. Neither a new alias nor a shorter path rewrites old
assignments, generations or admissions.

For page two of a source-derived search, keep the original source/locator in
`origin`, but use the preceding search as the immediate parent:

```json
{
  "origin": {"source_id":"record-7", "read_id":"history-read-01", "locator":"line 8"},
  "graph": {"parent_operation":"history-term-search-page-1"}
}
```

Copy the preceding page's exact provider, query, filters, cap, search branch and
input kind, and its returned `next_cursor`. The operation's
`research.parent_operation` must name that same preceding search. The helper
verifies every successful indexed page back to the original source read; a
changed query or route is new expansion, not pagination. Each page spends a
search attempt but keeps the source-derived generation. Retain closed historical
items and their original parents when saving later plans; they cannot dispatch
through replay or reset a later retry's ancestry.

`graph next-work` interleaves baseline with the earliest available follow-up
generation in protected runs, then balances branch, question and role before
numeric priority. Legacy runs retain strict earlier-wave ordering. A coordinator can override fairness only with
an explicit `fairness_override_reason`; an ordinary worker or delegated lead
cannot silently bypass it. Keep the [shared caps](research-budgets.md) and every
actor's activity intervals current.

Finish an assignment with a `complete`, `bound` or `release` work request,
including `assignment_id`, its expected revision and a concrete reason. `complete`
requires actual matching inspections, or a complete indexed search with every
discovery retained. A command's success, capped pagination or plan `done` label
is insufficient. `bound` adds `state` (`deferred`, `blocked` or `cancelled`);
it does not claim discovery completed. Reconcile lead/obligation dispositions
through `graph reconcile` only after every known question has linked work that
is resolved or explicitly bounded. One completed question cannot hide another
unassigned question. New origins or scopes can reopen a terminal registration.

## Recover Originals Without Repeating Research

```sh
python3 /skill/scripts/research_bundle.py graph ingestion-status /owned/run
python3 /skill/scripts/research_bundle.py graph sync /owned/run
python3 /skill/scripts/research_bundle.py graph work-status /owned/run
python3 /skill/scripts/research_bundle.py graph recover /owned/run
```

`sync` retries unfinished original indexing/returns once; select one with
`--operation-id ID` or `--return-id ID`. `recover` reports retained pending packets;
`--batch-id ID --apply` validates and applies a selected packet without provider
dispatch. Keep failed originals intact. Repair a missing graph mirror from its
admission/receipt, never by repeating the call or rewriting the return.

An interrupted normalization install retains a reservation before its first
packet. It appears as pending even before `plan.json` exists and blocks another
normalizer from claiming overlapping identities. Resume that original with
`sync` or `index-operation`/`ingest-return`; its reserved manifest and chunk limits
must match before missing packet files can be installed. Do not delete the
reservation or orphan packets to let later work proceed.

For an older retained index whose unsupported optional version metadata fails
before normalization planning, a narrow omission repair preserves `result.json`:

```json
[
  {"source_index": 0, "source_id": "record-7", "fields": ["modified_at"]}
]
```

Save that array as `/owned/version-omissions.json`, then run:

```sh
python3 /skill/scripts/research_bundle.py graph omit-index-version /owned/run --id search-history --sha256 ORIGINAL_RESULT_FILE_SHA256 --file /owned/version-omissions.json --reason "Retain raw provider timestamp; omit unsupported version normalization"
```

`source_index` is the zero-based position in the original `sources` array; its
`source_id` must match exactly. Fields can only be unsupported `revision` or
`modified_at` values. Use `fields: ["version"]` for a malformed whole version,
including a scalar version. Supported metadata cannot be omitted by this command.
It requires the SHA-256 of the retained `result.json`, no existing normalization
plan or packets, and no accepted index content. A lifecycle admission/completion
mirror may already exist. The helper proves the original structural failure and
validates the corrected index with the real normalizer on a disposable snapshot
before retaining `operations/OPERATION/index-version-omission.json`.

The omission receipt binds the original bytes, evidence dependencies, reason,
failure and graph sequence. Normalization then reads an effective view with only
those version fields omitted. Every original candidate, occurrence, ID, title,
capture, pagination field and indexed timestamp remains unchanged. No operation
is dispatched and no budget credit is added or refunded. Exact repeated intent
is idempotent; an interruption after receipt retention resumes through the same
command or ordinary `graph sync`. Status and exports retain the original and
omission proof. Missing evidence, other malformed fields, accepted or partially
accepted indexes, and candidates omitted from an earlier index need their own
supported reconciliation; this command cannot discard them or rewrite history.

If an already retained return is structurally invalid, explicitly reject its
normalization before submitting a linked correction:

```sh
python3 /skill/scripts/research_bundle.py graph reject-return /owned/run --id reader-index-return --sha256 ORIGINAL_RETURN_FILE_SHA256 --reason "Requested coverage exceeded the inspected scope"
python3 /skill/scripts/research_bundle.py graph check-return /owned/run --file /owned/reader-index-correction.json
python3 /skill/scripts/research_bundle.py reader-return /owned/run --return /owned/reader-index-correction.json
```

Use the SHA-256 of the original `reader-returns/reader-index-return.json` file,
not its budget payload hash. Rejection reruns the exact retained packet against
a disposable snapshot. Only a nonretryable `invalid_packet` or `scope_mismatch`
from one wholly unaccepted atomic return qualifies; missing dependencies,
integrity failures or accepted history cannot be discarded this way. A rejection
also requires the complete retained atomic normalization plan; unsupported
originals stay pending. A rejection sidecar preserves the original and failure.
Unrelated indexing can proceed, but
the rejected return stays incomplete until a valid correction is accepted.

The correction is a full reader-return draft with a new ID and this linkage:

```json
{
  "id": "reader-index-correction",
  "corrects": {
    "return_id": "reader-index-return",
    "original_sha256": "ORIGINAL_RETURN_FILE_SHA256",
    "reason": "Retain the inspection and leads; downgrade unsupported sufficient coverage to partial"
  }
}
```

Add that linkage to the complete original payload; the fragment alone is not a
return. Preserve read/source/receipt, reader actor and assignment, inspection
time, captures, methods and inspected extents. Keep the same inspection, locator,
scan and assertion IDs/data. Each question/scope/policy key stays the same;
coverage can stay unchanged or fall from `sufficient` to `partial`, with a
corrected basis. Preserve every original reference and lead ID, value, locator
and question origin; additional references/leads are allowed. A correction may
not add an inspection/assertion or upgrade scope. It gives no new read or
assessment credit, refreshes no timestamp and refunds no prior spending.

Carried assertions remain proposed; structural repair does not re-endorse them.
For a known-wrong interpretation, retain the reason and append a `challenged` or
`withdrawn` assertion decision. A semantic replacement uses an ordinary return
or reconciliation, then `superseded` with `replacement_assertion_id`, preserving
the original. New inspection or upgraded scope follows the ordinary return and
budget rules. Do not label it a structural correction.

Query `ingestion-status`, `frontier` and `coverage` after repair. A partial
correction can settle normalization while leaving work and question obligations
unfinished. Replaying the rejected raw packet cannot bypass its disposition.
Status and exports retain the original rejection and verified correction chain;
one accepted correction maps the original inspection once, without hiding any
unresolved reference or question.
An export's `ingestion.json` freezes that inventory and its retained proofs at
the export's graph snapshot. `validate-ledger` reports verified
`return_corrections` and `stop_checks.unhandled_originals` from that snapshot.
A later accepted correction cannot repair an older export retroactively; create
a new export. Older exports without correction proof grant no correction credit.

An expired lease or unknown operation needs the original worker checked.
Use `reconcile-assignment`, then `resume` only for that same identified worker,
or reconcile its real receipt. `reconcile-operation` needs the operation's
expected revision, `worker_identity`, `worker_stopped`, reason and optional
`cancel`. Cancellation requires explicit evidence that the original worker
stopped; it never refunds spending or grants a fresh retry. Terminal source
access/readability also needs a changed-basis correction, plus the original
budget recorder's `budget-reopen` where applicable.

A failure before `request.json` is persisted is recoverable when the original
admission contains `request_intent`. Planning exposes that unresolved intent;
it does not count as execution. After checking that the original worker stopped,
submit the following through `graph work ROOT --file REQUEST.json`:

```json
{
  "action": "recover-admission",
  "operation_id": "ORIGINAL_OPERATION_ID",
  "worker_identity": "ORIGINAL_DECLARED_RUNTIME_IDENTITY_OR_ACTOR_ID",
  "worker_stopped": true,
  "reason": "Observed persistence failure and actual original-worker stop check."
}
```

This records restoration provenance under `admission-recoveries/`, materializes
only the exact journaled request, and cancels its graph mirror. It grants no
redispatch, provider receipt, refund, or new retry epoch. The action resumes
idempotently after interruption. Existing requests or late output use ordinary
reconciliation; older admissions without retained intent require their original
evidence and must not be reconstructed from guesses. A separately counted retry
still needs `recover-retry` and the checks below.

A local failure can leave a spent admission containing only `request.json`,
without a provider receipt. First mirror the original admission with
`record-admission` if needed, check the original worker and use
`reconcile-operation` to cancel it. Then record the observed local failure with:

```json
{
  "action": "recover-retry",
  "operation_id": "ORIGINAL_OPERATION_ID",
  "expected_revision": 2,
  "reason": "Observed local orchestration failure; original worker checked stopped."
}
```

Submit through `graph work ROOT --file REQUEST.json`, using the current operation
revision. This verifies the original request, cancelled mirror and retained stop
reconciliation, then appends a budget classification. It creates no provider
receipt and changes no graph evidence. A new operation may use `retry_of` with
the exact original request and ancestry, subject to the original attempt limit
and normal assignment checks. No spending is refunded or retry epoch reset.
Do not substitute `refresh_of`: that is a separately justified repeat, not crash
recovery. Retained outputs or a real receipt require their ordinary reconciliation
path. Missing output alone never proves that a tool was not called.

If a real terminal result arrives after a checked request-only cancellation,
import the original output under the same operation ID. Automatic completion
reconciles that receipt on storage revision 3. The cancelled event, worker-stop
statement, original request and spent admission remain intact; the effective
operation outcome becomes `succeeded` or `failed`. A restored admission without
an observed dispatch retains that gap. This does not replay a provider or create
another attempt, and it grants no inspection credit.

On revision 1/2, import retains the real receipt and output before reporting that
an explicit upgrade is required. Run `graph upgrade-schema ROOT`, then submit
this through `graph work ROOT --file REQUEST.json`:

```json
{"action": "record-completion", "operation_id": "ORIGINAL_OPERATION_ID"}
```

Index the retained result normally, then run `graph sync ROOT`. If an original
index was already retained before interruption, `graph sync` also performs the
same guarded receipt reconciliation. Repeat completion or synchronization after
a local persistence failure; do not import a second receipt or repeat the
provider call. A late imported timeout/interruption still supplies no terminal
outcome by itself. Successful retrieval still needs actual inspection and a
reader return before its work can complete.

Keep the original cancelled operation in every final coverage checkpoint. A new
export records a verified request-only cancellation as `cancelled` in
`ingestion.json`; no receipt or result index should be invented for it.

If current checks reject an old assignment's saved question or empty scope,
retain that record and use the ordinary `release`, `bound`, or `reconcile-assignment`
assignment action with an explicit reason. Any unresolved original operation
still needs checked reconciliation before release/bounding; scope rejection
does not prove that its worker stopped. Retirement earns no evidence credit.
For a wrong-question assignment, claim the released work through the saved item
that actually covers that question. For an empty scope, mark its original plan
item dropped/bounded and add a corrected active item with explicit scope. The
closed item must match already released historical work; it cannot introduce new
empty-scope work. Read and inspect the corrected scope before completing it.
Validation reports recorded attempts separately from `executed_operations` and
lists `cancelled_operations`. Cancellation settles that operation's bookkeeping,
not its work item, source scope or question. It cannot establish a read, satisfy
a done plan item, or count as an executed follow-up for a bounded central gap.
Close or reassign the remaining work explicitly. Older frozen exports retain
their original pending status; make a new export after reconciliation.

## Withdraw An Accepted Inspection

Structural rejection cannot undo an accepted inspection whose declared reading
scope later proves wrong. Retain the admission, original return, actual display
evidence or reader correction, and any uncertainty about that evidence. Withdraw
the inspection through coordinator reconciliation. The helper verifies the basis
file's identity, not the truth of its explanation.

Older graphs first need this explicit, resumable local upgrade:

```sh
python3 /skill/scripts/research_bundle.py graph upgrade-schema /owned/run
```

It preserves the original history and adds missing storage revisions through 3,
including revision 2's inspection withdrawals; it spends and
refunds no research budget. Use the usual current-contract reconciliation packet
with a mutation such as:

```json
{
  "op": "insert",
  "table": "inspection_withdrawals",
  "record": {
    "withdrawal_id": "withdraw-reader-7-full-scope",
    "inspection_id": "COPY_ACCEPTED_INSPECTION_ID",
    "actor_id": "coordinator",
    "reason": "The reader displayed excerpts but declared the full body inspected",
    "evidence_path": "reviews/reader-7-scope-correction.json",
    "evidence_sha256": "COPY_RETAINED_BASIS_FILE_SHA256",
    "withdrawn_at": "COPY_CURRENT_UTC_TIMESTAMP"
  }
}
```

Save the complete packet and submit it with `graph reconcile ROOT --file FILE`.
Withdrawal is permanent for that inspection ID. The raw inspection, references,
leads and return remain historical records; usable coverage and dependent
findings/claims are withheld. Challenge or withdraw affected assertions, revise
affected claims, and explicitly reopen work whose completion depended on the
withdrawn inspection. New genuine inspection uses an ordinary return with honest
extent and time. Reading retained content does not require another provider fetch.

For that reread, pin the retained capture IDs in the new saved assignment. If an
operator mistakenly assigned a fresh-capture requirement, keep the failed check
and original plan. Active work intent is fixed. Once the assigned scope has
actually been reread, a coordinator can submit one atomic reconciliation packet:
first complete the original assignment with its expected revision and actual
`finished_at`; then update the work's `request_json` to the intended capture set,
recompute `logical_key`, and set `state: "completed"` with its expected revision
and explicit correction/completion reasons. The work validator checks the new
binding against that assignment's actual inspection; failure rolls back both
mutations. This changes current intent without declaring the old freshness
requirement satisfied. Do not backdate reading or capture times, invent a provider
version, or alter the saved plan. Later assignments need a new matching plan.

After a reread, revalidate claims in a new revision with current inspected
locators. Neither that reread nor a repaired export endorses old assertions
automatically. `validate-ledger` exposes accepted dependent assertions/claims and
invalidated completed work as unresolved stop checks. Old exports retain their
original state; generate a new export for the correction. Bounded prior imports
carry selected inspections' withdrawal history and basis, without current credit.

## Freeze A View, Review It And Deliver

For protected runs, save the final plan and close research clocks, then retain
the [follow-up stop checkpoint](protected-followups.md#release-or-stop-explicitly).
Export registration alone does not invalidate it; new evidence, activity, scope
or plan changes do. Complete the independent semantic review as well.

```sh
python3 /skill/scripts/research_bundle.py graph export /owned/run --id final-evidence
python3 /skill/scripts/research_bundle.py graph verify-export /owned/run/graph/exports/final-evidence
python3 /skill/scripts/research_bundle.py graph validate-ledger /owned/run --ledger /owned/run/graph/exports/final-evidence/ledger.json
```

The export contains `manifest.json`, `graph.json`, `ingestion.json`, `ledger.json`, `sources.json`,
`references.json`, `worklist.json`, `companion.md`, schema and retained evidence.
Its graph facts share one sequence; the manifest also records the contract,
saved plan and observed external budget head. Export registration is a later
audit event. Keep those identities distinct. Exported `companion.md` is a starting
inventory, not a finished substitute for the reader's evidence companion.

Registration keeps a compact binding to the immutable manifest, whose full file
inventory is verified during registration, graph validation and ledger use.
Ordinary evidence-record size limits remain unchanged as exports grow. Existing
expanded registrations and interrupted registration packets retain their exact
bytes. If an export's files were retained before registration failed, retry the
same `graph export --id` after repairing the cause; preserve its manifest and
earlier failure instead of rebuilding or editing that snapshot.

Finish the plan's coverage checkpoint using a generated ledger, save it, then
export the final sequence. Refresh `plan-view` from that final ledger; do not
re-save the plan merely to render it. Review source passages and claim qualifiers
independently of coverage holes. Retain the explicit semantic review in the bundle,
for example `reviews/semantic-review.json`, with `reviewer`, `note`,
`ledger_sha256` from `graph validate-ledger` and `graph_sequence` set to its
`represented_sequence` (the export's sequence, not the later registration event).
This file attests a real human/agent review; an export or completed work item
cannot supply it automatically. Changed evidence or claims require a new view
and review.

```sh
python3 /skill/scripts/research_bundle.py validate /owned/run --ledger /owned/run/graph/exports/final-evidence/ledger.json --phase synthesis --semantic-review /owned/run/reviews/semantic-review.json
python3 /skill/scripts/research_bundle.py plan-view /owned/run --ledger /owned/run/graph/exports/final-evidence/ledger.json
```

Use that ledger and `--semantic-review` for `publication` as well, alongside its
artifact manifest and reviewed plan-view hash. Include the required candidate
table, coverage, conflicts and remaining frontier in the companion. Preserve
the brief's answer-first structure, prose pass, rendering and authorized editing
workflow in [briefs and collaborative editing](brief-and-editing.md).

## Reuse A Prior Investigation Or Migrate A Legacy Bundle

Ordinary investigations may use prior companions as Trunk sources. Before import,
the current contract explicitly records `prior_research.policy` as `allow` or
`exclude`, plus any `excluded_run_ids`, `excluded_export_ids`,
`excluded_artifact_ids` and `excluded_artifact_keys` (`namespace`, `external_id`).
Independent-discovery tests exclude prior outputs and their inherited lineage;
do not silently apply that testing restriction to ordinary research.

Verify the retained export, then choose bounded records and map its questions:

```json
{
  "policy": "leads_only",
  "artifact_ids": ["PRIOR_ARTIFACT_ID"],
  "record_ids": {"lead":["PRIOR_LEAD_ID"]},
  "question_map": {"prior-rationale":"rationale"},
  "reason": "Use the earlier companion to locate the primary evaluation"
}
```

```sh
python3 /skill/scripts/research_bundle.py graph import /owned/run --directory /owned/prior-export --id prior-navigation --selection /owned/reuse-selection.json
python3 /skill/scripts/research_bundle.py graph migrate /owned/legacy-run --destination /owned/migrated-run --run-id legacy-retained --actor coordinator
```

Imports preserve selected records and full declared lineage without recursively
fetching ancestors. `attested_evidence` additionally carries selected historical
evidence; neither policy creates a fresh native inspection, independent
corroboration or current execution authority. Exclusions cover aliases and
inherited lineage. Record missing ancestor provenance as a limitation.
Export a current inventory before planning imported reads. Use its source `id`
in new plan requests; registration binds that local name to the existing
`graph_artifact_id` without creating a provider identity or seed discovery.
If later metadata changes the exported source name, map a new draft through
`graph_artifact_id`, preserving earlier drafts and saved plans.

Imported leads remain visible before any current retrieval. Their
`graph_prior_return_refs` retain historical return provenance separately from
native `return_refs`, which bind only this run's original reader returns.
Historical leads and inspections do not add current budget or reading credit.
Migration reads a graph-less source bundle into an absent separate destination,
preserving originals. Old read flags are not inspections; missing receipts or
scopes stay pending. Query the migrated graph and create real current assignments
when further investigation is needed.
