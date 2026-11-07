# Research Graph v1 Contract

Use this reference when implementing graph storage, normalizing worker returns,
or interpreting graph responses. The executable schema is
[research_graph_schema.sql](../scripts/research_graph_schema.sql). This G0
contract defines the interface. G1 implements `graph init`, `ingest`, `reconcile`,
`checkpoint`, `recover` and `validate`; G2 adds automatic ingestion, `sync` and
`ingestion-status`. G3 adds the seven bounded queries below; G4 adds plan bindings,
balanced work selection, leases and recorder lifecycle hooks. Export commands
become available in G5. The original operation files, reader returns,
contract/plan snapshots and budget journal keep their existing authority.

## Storage and bootstrap

The live database is `graph/research.sqlite` inside one investigation bundle on
a local filesystem. Remote workers return packets to its host. SQLite 3.38 or
newer is required for built-in JSON functions; Python's standard-library
`sqlite3` is the only runtime dependency. Reject an unsupported SQLite runtime
before creating the database. No network database, provider plugin, or new
Python package is required.

Every writable connection sets and verifies `foreign_keys=ON`, `journal_mode=WAL`,
`synchronous=FULL`, and a bounded `busy_timeout` (5,000 ms). Read-only connections
use URI `mode=ro`, `query_only=ON`, and a read transaction. Packet protocol and
`graph_metadata.schema_version` remain 1. `PRAGMA user_version` identifies storage
revision 1, 2 or 3; applied migration IDs and exact file hashes are retained in
`schema_migrations`. The original v1 schema and v2 migration stay byte-for-byte unchanged.
[Storage revision 2](../scripts/research_graph_inspection_withdrawals.sql) adds
inspection withdrawals. [Storage revision 3](../scripts/research_graph_late_receipts.sql)
permits verified late terminal receipts to reconcile request-only cancellations.
New graphs include both; existing graphs require explicit
`graph upgrade-schema`. Reading does not upgrade a database. Unknown revisions,
migration identities or table inventories fail closed.

An upgrade retains its packet before transactionally applying the installed SQL
and appending the migration event. Recovery applies that same SQL at the same
event boundary. Original events, returns, budgets, checkpoints and exports remain
unchanged. Export and checkpoint schema hashes identify the exact storage at their
represented sequence, so older snapshots remain verifiable after an upgrade.
Upgrades apply each missing revision in order, in its own journal transaction.
An interrupted upgrade resumes from its last committed revision. Retrying the
historical `schema-upgrade-inspection-withdrawals-v2` packet targets revision 2;
it does not silently adopt revision 3. The Python `upgrade_schema` API also
accepts an explicit `target_revision`; the CLI upgrades to the current revision.

Initialization executes the schema into a new temporary database; `executescript`
must not be called inside an already-open ingestion transaction because Python
may commit that transaction first. Before installing the database, bootstrap one
`BEGIN IMMEDIATE` transaction containing the migration record, root actor,
bootstrap batch, initial immutable contract binding, questions, metadata and
events. `actors.registration_batch_id`, `ingest_batches.actor_id`, and the batch's
event-tail FK are deferred to commit, so a real actor and batch can reference
each other without dummy IDs. There is one parentless coordinator. All other
actors have an existing parent; immutable parent links cannot acquire cycles.
Check FKs/integrity, close/checkpoint the temporary database and atomically
install it with its directory synced. A partial bootstrap is never a usable run.

For each later packet, retain and fsync its complete bytes under
`graph/updates/<batch_id>.json`, using exclusive immutable installation and a
directory fsync. Validate external files before entering a short write
transaction, then recheck their identities and all state-dependent conditions
under `BEGIN IMMEDIATE`. Allocate a contiguous event sequence, insert the batch
with its planned final sequence, apply mutations, write events, and commit.
There are no provider calls or model analysis inside the transaction.

`resulting_sequence` must equal the maximum event sequence **belonging to that
batch**. The deferred FK guarantees that its stated tail exists and belongs to
the batch; the helper verifies the maximum and that every requested mutation is
accounted for. Each event retains `before`, `after`, and any `derived` rows,
including trigger-created obligations or claim revisions. One mutation may
therefore have one event containing several inseparable row changes. Creation
events have `prior_revision=null,next_revision=1`; updates advance by one.

Batch IDs identify exact retained bytes. Reusing an ID with the same SHA-256
returns the original receipt and sequence without another mutation, event or
budget admission. Different bytes under that ID produce `batch_conflict`, even
if they differ only in formatting. A packet written before a failed database
commit is pending. A committed packet with a lost response is idempotently
recoverable. Pending packets never become accepted merely because a recovery
scan finds them. Rebuilds use accepted packets in event order, with verified
checkpoint/manifests; never edited inventory exports as presumed observations.
They reproduce accepted event snapshots without re-running current evidence
admission policy. An older accepted extent declaration survives replay as a
historical fact; it does not authorize a new sufficient inspection under a newer
coverage rule.

## Records and identity

Opaque IDs are nonempty, at most 200 characters, and match
`[A-Za-z0-9][A-Za-z0-9._:-]*`. IDs used as filenames (`batch_id`, operation and
return paths) additionally reject colon and any path separator; paths are
explicit packet fields, not string concatenations of arbitrary entity IDs.
The helper rejects booleans as integer revisions/counts and validates timestamps
as UTC RFC 3339, normalizing to one UTC representation before storage. Original
provider date precision remains in temporal metadata. SHA-256 values are
lowercase hexadecimal. Relative paths must resolve inside the bundle, with no
symlink escape; hashes are verified against retained files, never accepted on
the strength of a well-formed string.

The SQL table definitions are the complete column contract. Packet values for
columns ending in `_json` are JSON **values**, not serialized JSON strings. The
helper serializes these columns once for bound SQL parameters. `value_json` for
a Trunk role is consequently the JSON string `"trunk"`, while a request or
locator is an object. Core object keys and types are defined below. Bounded
provider metadata is retained as opaque data and never interpreted as authority,
scope coverage, SQL, or an instruction.

| Record family | v1 meaning and enforcement |
| --- | --- |
| `artifacts`, `artifact_keys`, `artifact_versions` | Provider namespace denotes stable identity, not retrieval route. Title is never a deduplication key. Known revision tokens are unique per artifact; unknown tokens remain NULL and may have multiple observations. Canonical redirects retain old IDs/keys, require a basis, and cannot cycle or be silently undone. Merges never refund budget admissions. |
| `captures`, `locators` | Every capture names an artifact even without a known version. A non-NULL version must belong to that artifact. A retained capture has a real path, byte hash, receipt hash, format, extent and capture time. Failed requests may have no capture. No retained bytes means an outcome/limitation, never an invented empty snapshot. Excerpt hashes cover the exact UTF-8 excerpt. |
| `reader_returns`, `inspections`, `inspection_captures` | A capture is not an inspection. Native inspection requires a verified original return, matching reader actor, and an inspected capture from the same canonical artifact. Native captures must come from verified successful operations. Attested start/end intervals are optional, paired and ordered; they are not authenticated runtime telemetry. |
| `inspection_withdrawals` (storage revision 2) | One immutable withdrawal per inspection, with coordinator, reason, timestamp and retained basis file/hash. The original inspection remains queryable. Withdrawn inspections supply no effective scope or new inspected evidence. Historical imports retain the withdrawal with its inspection. |
| `reference_scans`, `reference_occurrences`, `reference_resolutions` | A scan records its extraction boundary independently of reading. `(scan_id,local_reference_key)` identifies an occurrence. Its locator must belong to that scan/source. A mention initially has no target, even if a candidate is known; identifying it adds a resolution with a basis. One linear correction chain retains every previous target. Resolving a link may likewise correct its initially observed target. |
| `discoveries` | Exactly one immediate origin: operation, reference, supplied seed or prior import. Keep distinct occurrences from different routes even when identity deduplicates. A bounded prior import is a discovery route, not a new source inspection. |
| `assertions`, `assertion_evidence`, `assertion_decisions` | Assessments are immutable and begin proposed. Evidence occurrences retain support, qualification or conflict. Decisions are append-only with expected current revision; the initial decision expects 0. Corrected assertion content uses a new assertion and a supersession decision. Acceptance does not remove the original basis or disagreement. |
| `questions`, `claims`, `claim_evidence`, `claim_questions`, `leads`, `lead_origins` | Questions bind the immutable contract revision. Claims are conclusions; leads are unfinished domain research. Every returned lead retains its return ID, returned lead ID, wording, question and locator when one exists. Normalization aids lookup without asserting synonymy. A bound or terminal disposition requires a reason. |
| `work_items`, `work_origins`, `assignments`, `operation_links`, `actors` | Work intent, ancestry, delegated ownership and actual provider calls are separate. Several calls may serve one assignment; every call retains its own operation ID and admission. Work generation cannot decrease. Assignment plan, actor, generation and origin bindings are immutable. Actor labels and declared identities are attestations unless a harness separately verifies them. |
| `ingest_batches`, `graph_events`, `export_manifests`, `prior_run_imports` | Batches and events are append-only. Exports are bound to one sequence, contract hash, optional saved plan hash and observed budget head. Imports retain the exact prior run/export/manifest, bounded selection, policy and limitations. |

These supplemental tables close specific invariants in the design:

| Table | Reason it exists |
| --- | --- |
| `graph_metadata`, `schema_migrations` | Identify the run and exact installed schema without relying on a filename. |
| `contract_snapshots`, `plan_snapshots`, `plan_items` | Thin immutable hash/path/ID bindings let questions and assignments refer to real existing contracts. They do not duplicate the original file payloads or redefine their authority. |
| `followup_obligations` | SQL inserts one `reference:<reference_id>` or `lead:<lead_id>` pending registration obligation in the same statement that registers its origin. Its work_id is only the first routing pointer; all question-specific work is retained through work_origins and the scope views described below. |
| `inspection_scopes` | Record partial/sufficient coverage for an explicit question, required scope key and format-policy hash. This separates body, comments, revisions and visuals without a universal read checkbox. |
| `source_outcomes` | Preserve scoped readability/disposition observations and corrections separately from relevance. Several current tails can disagree; a later independent observation does not overwrite an earlier terminal access outcome. |
| `claim_revisions` | SQL snapshots each material claim revision. Evidence and question links cite the exact claim revision, so an edit cannot inherit support for different wording. Other mutable history is retained through events. |
| `role_vocabulary`, `relationship_vocabulary` | Seed the documented roles/predicates while allowing explicit coordinator-registered extensions; arbitrary JSON cannot create undeclared semantics. |
| `prior_run_records` | FK-backed typed mappings retain each imported record's old ID, current ID, original provider identity and complete declared export lineage. The mapping does not give imported assessments a fresh-read state. |

`source_outcomes.required_scope_key` is explicit, including a whole-artifact
scope when appropriate. A correction must remain within its artifact/question/
scope. Reopening access_blocked, unreadable or retry_exhausted requires
`changed_basis`. If another observer reports readable in a parallel base
observation, queries retain both tails. Scheduling cannot treat that second row
as permission to retry the terminal route; the coordinator must reconcile the
original tail with a changed capability/access basis or an explicit scoped
adjudication. Neither readability nor a failed call implies irrelevance.

`obligation_question_scopes` derives known questions from original lead mappings,
all work origins, the reference scan's original capture assignment, and source
assertions/inspection scopes. `obligation_scope_status` exposes each question as
unassigned, pending, resolved or bounded. A global terminal lead/obligation is
forbidden while any known question lacks work or has pending/active work; a
resolved label is also forbidden when any question is merely bounded. An
explicit exclusion/deferral creates a scoped reconcile work item and reason,
without dispatching a provider call. New question origins or pending work reopen
a formerly terminal registration. New source-question assertions/inspections
likewise reopen reference registration where that question remains unaddressed.
The triggering event must include these automatic revisions as derived changes.
Queries still evaluate new relevant question/scope requirements independently of
any old global label; a first routing pointer cannot hide other questions.
Identity merges also recheck derived question scopes and record any necessary
reopening updates in the merge transaction.

### Core JSON and provider payloads

Allow only the following core keys. Unless stated otherwise, optional keys may
be absent and strings must be nonempty. IDs/hashes use the common validators.
Opaque objects may contain ordinary JSON values, with maximum depth 32; the
helper bounds each record to 256 KiB and each packet to 16 MiB/10,000 mutations.
Split larger indexes into deterministic batches while retaining every occurrence.

| Column | Core shape |
| --- | --- |
| `request_json` | Object: optional provider/route/source_id/version_id/freshness_basis/format/purpose strings, formats string array, capture_ids nonempty unique ID array, extent object, query string or opaque provider query object, filters opaque object, cursor opaque JSON value, provider_metadata opaque object. Read/visual work requires source identity, extent and explicit format(s); search requires a query or a verified provider request in provider_metadata. Reconcile/review may have an empty object. Actual request/plan matching remains an adapter check. |
| capture/scanned/inspected extent JSON | Object with optional body/comments/revisions/visuals components and provider_metadata. Each component is all/none/not_retained/unavailable, or an object with unit, ids string array, ranges in the form `[{"start":1,"end":4}]` (inclusive nonnegative bounds), attestation string, limitations string and optional complete boolean. Unit is paragraph/line/page/slide/frame/node/comment/revision/seconds and is required for ranges. One range cannot mix units; integer units reject fractional bounds. `complete:true` requires nonempty numeric ranges, attestation and limitations; only the captured component's declaration can support a whole-component request. |
| `locator_json` | Object with optional id/path/heading strings, unit from the extent vocabulary, nonnegative start/end numbers, and provider_metadata. Supply an ID, path, heading, numeric range, or explicit provider locator. `locator_kind` determines which forms an adapter supports. |
| assertion `value_json` | Exact kind-specific shapes in the next section; arbitrary provider fields are not permitted here. |
| temporal/comparison scope JSON | Exact optional fields described below; string-valued except limitations, which is a string array. |
| prior import `scope_json` | Object with optional branches/questions/artifact_ids string arrays and record_ids object mapping artifact/assertion/inspection/lead/claim to string arrays. At least one nonempty bounded selection is required. |
| `lineage_json` | Array of objects with required run_id/export_id strings and optional manifest_sha256, artifact_id, original_namespace, original_external_id and limitations strings. Missing ancestor provenance remains a limitation; it is not a fabricated hash. |
| export `files_json` | Array of objects with relative_path, sha256, format strings and nonnegative integer size_bytes. New registrations contain one binding to their own manifest; its complete external inventory supplies the remaining file bindings. Older expanded arrays remain readable and replayable. Every listed file is verified. |

Normalized IDs and numeric ranges permit deterministic containment checks only
when units, source version and adapter meaning agree. Free-text scope, headings
and opaque provider extents require an explicit reader/adapter scope attestation
and limitations. They cannot match numeric extents by inference. The generic
helper cannot prove semantic section or visual inclusion from arbitrary text.
For a locator with both a normalized `unit` and `id`, inspected evidence requires
that ID in the corresponding inspected component, with the same unit. Capturing
another node does not establish its inspection. Opaque headings, paths and
provider locators remain explicitly attested rather than inferred into IDs.
`inspection_scopes.basis` records the coverage attestation;
`inspections.limitations` records what could not be checked.
A policy hash identifies rules; it does not itself prove that every required
format was captured or inspected.

New graph read plans require an explicit structured `item.graph.extent`.
Every required component needs nonempty position IDs/ranges or an `all`
request. An empty object or empty position array cannot request or establish
reading credit. Empty/unavailable capture metadata remains representable, and
accepted historical records remain unchanged during replay.
The provider's free-text `request.extent` remains the provider request; it is not
silently converted to a graph read scope. Use numeric bounds for a known section,
or `{"body":"all"}` for the complete body of the capture. A corresponding
capture can declare:

```json
{"body":{"unit":"line","ranges":[{"start":1,"end":8}],"complete":true,
         "attestation":"All eight lines of the returned document body were retained",
         "limitations":"Comments, visuals and revision history were not returned"}}
```

The actual inspected extent must cover every declared capture bound, for example
`{"body":{"unit":"line","ranges":[{"start":1,"end":8}]}}`. Consecutive
inclusive integer ranges may jointly cover the bounds; continuous time ranges
cannot bridge a positive gap. Partial reading, a plain `"all"` capture or
inspection, or `extraction_status:"complete"` alone is insufficient. Completeness
is a retained adapter attestation, not independent proof of the provider's full
source. A body declaration covers no comments, visuals or revisions. Every
requested component and format, and the work's version/freshness requirement,
must still pass. `complete` is capture metadata, not a plan request field.

`research_graph_work.validate_plan(root, plan, ledger)` is a pure preflight after
the ordinary plan/ledger checks. It uses the registration projection, validates
graph identities and annotations with field-path errors, and checks its bounded
generated rows against an in-memory graph snapshot's SQL constraints and FKs.
It writes no plan, packet, graph, budget or provider state. Graph-mode `save_plan`
holds the graph writer lock across this check, immutable file retention and graph
registration. Already graph-bound historical plans retain their original opaque
scope when replayed; newly saved plans must declare scope explicitly. Preflight
does not authorize dispatch or replace original recorder admission checks.

## Assertion and scope shapes

Built-in roles are `content`, `critical`, and `trunk`; several may apply at once,
to different questions or sections. Whole-artifact scope uses a NULL locator;
it is an affirmative scope declaration, never inferred from a section locator.

| Kind | Predicate / value |
| --- | --- |
| role | `has_role`; `value_json` is a registered role string; no object artifact. |
| relevance | `is_relevant`; a question is required; value is `unassessed`, `relevant`, `irrelevant`, or `uncertain`; no object artifact. |
| finding | `states`; value is exactly `{"text":"nonempty finding"}`; no object artifact. |
| contribution | `contributed`; value has nonempty `contributor`, `text`, and `attribution_basis` from named_author/revision_author/commenter/credited_collaborator/participant_account/uncertain. No object artifact. Ownership alone does not establish contribution. |
| relationship | Registered predicate and required object artifact; no value. Optional subject/object locators must belong to those endpoints. Built-ins: organizes, reviews, challenges, approves, implements, tests, revises, supersedes, responds_to. |

`temporal_scope_json` allows `event_start`, `event_end`, `precision` and `note`;
it describes the claimed historical event/period. Source `published_at`, version
`modified_at`, capture time, inspection time and assessment time remain distinct.
`comparison_scope_json` allows `baseline`, `alternative`, `population`,
`denominator`, `task`, `period`, `metric`, `units`, `measurement_kind` and
`limitations`. The helper validates provided value types and retains absent
fields as unknown; it does not manufacture a complete comparison.

An inspected assertion needs at least one supporting/qualifying inspected
locator from a matching return and inspection. The SQL evidence guard verifies
that the locator's capture was inspected. The helper checks normalized extent
containment where supported; otherwise it requires and exposes the scope
attestation and limitations. Provider adapters establish required format checks.
Metadata, participant account and inference stay labeled. A
relationship accepted as established requires inspected support and a reason;
inferential relationships may be accepted as **labeled inferences** with their
qualifications, never silently promoted to explicit source statements.

`sufficient_inspection` is a view of historical scope/policy matches, not proof
of freshness. Before reusing it, the helper also checks the work request's
`version_id`, explicit `freshness_basis` or pinned `capture_ids`, required formats and all required
extent components against `inspection_captures` and their versions. A new
provider revision or uninspected scope cannot reuse an old scope match by
itself. An unknown revision stays unknown; byte equality proves retained bytes,
not unchanged comments, visuals or provider revision. Partial and historical
attested inspections never enter the native sufficient-inspection view.

`capture_ids` explicitly requests a new inspection of an exact retained capture
set. It is mutually exclusive with `version_id` and `freshness_basis`; IDs are
unique and normalized to sorted order. Every selected capture must be native,
receipt-backed, hash-verified and associated with the canonical target and the
requested formats/extents. Imported historical captures do not qualify. A partial
inspection may cover a subset; sufficient coverage requires every selected ID
and the existing format/extent checks. This does not union incomplete captures
into a complete body or invent a provider revision for unversioned content.

Retained-only work cannot admit another provider call. Completion still requires
a new inspection under the latest assignment; an earlier reader's inspection is
not transferred. Active work intent remains fixed. After a genuine reread, a
coordinator can atomically complete its assignment and correct the work's
capture binding while completing that work. Both mutations require their
expected revisions; the work mutation includes an explicit reason and updated
logical key. Completion checks the new binding against the latest assignment's
actual inspection, and a failure rolls back both mutations. The original saved
plan and events remain retained. A future claim needs a matching new saved
plan; this correction does not reinterpret the earlier plan.

Frozen stopping checks use the same capture, extent, format and version/freshness
predicate as native completion. After a withdrawal, a surviving inspection only
satisfies completed work if it meets that work's exact current requirements.
An inspection of another capture or a shorter extent cannot hide the missing
proof. The raw completed state remains historical; the stopping objection stays
open until the work is actually satisfied or explicitly reopened and bounded.

A generated ledger also binds completed retained reads to their original plan
items and latest completed assignments. A new saved plan can mark the matching
capture-bound item `done` without a new provider operation. Changed scope,
ownership or request definitions, incomplete current readings, imported history
and withdrawn inspections cannot supply that completion. Historical rendering
uses its frozen evidence; current plan saves require a current export.

## Normalized update packets

The durable packet is strict JSON without duplicate object keys or non-finite
numbers. Required envelope keys are exactly those below; no SQL is accepted.

```json
{
  "schema_version": 1,
  "batch_id": "reader-2-return-17",
  "actor_id": "reader-2",
  "assignment_id": "assignment-42",
  "packet_kind": "observe",
  "contract_sha256": "64 lowercase hexadecimal characters",
  "submitted_at": "2026-01-02T10:00:00Z",
  "mutations": [
    {
      "op": "insert",
      "table": "assertions",
      "record": {
        "assertion_id": "assessment-17",
        "kind": "role",
        "subject_artifact_id": "artifact-17",
        "predicate": "has_role",
        "value_json": "trunk",
        "subject_locator_id": "locator-17-4",
        "question_id": "rationale",
        "basis": "inspected",
        "return_id": "return-17",
        "assessed_at": "2026-01-02T10:00:00Z"
      }
    }
  ]
}
```

The excerpt demonstrates shape; a real packet must also supply or reference the
matching evidence row. `assignment_id` is NULL only for coordinator-controlled
bootstrap, reconciliation, migration/import, or recorded unassigned intake.
Worker observations bind an active assignment and existing contract. Packet
kinds are `bootstrap`, `observe`, `reconcile`, `import`, and `migrate`.
Returned lead origins may name other known questions as proposals. Retaining
those questions does not authorize execution outside the worker's assignment.

Mutations are ordered; dependencies must already exist or occur earlier, except
the explicitly deferred bootstrap/audit FKs. Inserts use exactly
`op,table,record`. Updates use exactly `op,table,id,expected_revision,changes`.
Updates are allowed only on `artifacts`, `questions`, `work_items`, `assignments`,
`operation_links`, `claims`, `leads`, and `followup_obligations`, and only on the
role-permitted fields below. Expected revisions are positive integers; the helper
uses `UPDATE ... WHERE primary_key=? AND revision=?`, rejects zero affected rows
as `revision_conflict`, and increments by one. Assertion decisions instead insert
an append-only `assertion_decisions` row with `expected_revision` starting at 0.
Reference and outcome corrections specify the current predecessor ID; stale or
forked chains fail without partial acceptance.

The caller cannot supply `created_batch_id`, `updated_batch_id`,
`registration_batch_id`, `imported_batch_id`, or initial `revision`; the controller
injects them. Caller mutation of primary IDs/origins, schema metadata, batch
receipts, events, export manifests, claim revision snapshots, or insertion of
follow-up obligations is forbidden. Snapshot adapters insert contract/plan
bindings only after checking the existing immutable files and saved plan IDs.
Root bootstrap is the single controller-owned exception for creating metadata.

An observation packet cannot silently accept its assertions or close its newly
created obligations. Ingesting each reference/lead creates its pending obligation
before any priority decision. Scheduling an obligation requires a matching
`work_origins` link; several origins may converge on one work item. Terminal
obligations use `resolved`, `bounded`, `excluded`, or `deferred` with a reason and
retained supporting events/work. Resolving a mention's identity does not by
itself resolve the research work it exposed.

Existing result-index and reader-return adapters emit these packets automatically.
They preserve original source IDs, return IDs and lead IDs, enumerate all returned
candidates and extracted references, and verify that none disappeared during
normalization. A structured reader return's convenience observations and lead
proposals are not a second writable inventory. Raw return files remain immutable;
the normalized packet records the consolidated interpretation of their fields.

### Recorder ingestion and recovery

In graph-enabled bundles, `index` and `reader-return` preflight the prepared
draft against a disposable graph snapshot before retaining the original. If
preflight fails, correct and resubmit the unretained draft. Keep the raw provider
output; correcting a draft does not require another provider call.

If normalization fails after the original has been retained, preserve that
original and its receipts. Do not repeat the provider call, admission, index
write, or return write. Use `graph ingestion-status ROOT` to inspect dependencies,
then `graph sync ROOT` to try unfinished originals once; `--operation-id ID` or
`--return-id ID` selects one. Incomplete dependencies remain explicit and require
reconciliation. Sync never dispatches a provider or spends another admission.
See [recorder ingestion and recovery](graph-workflow.md) for the transaction
boundary and recovery commands.

Rich result and return fields are documented in
[the ingestion module](../scripts/research_graph_ingest.py). Every declared
source occurrence, reference and lead is retained, including repeated routes and
multi-question lead origins. The coordinator's normalization packet does not
claim the coordinator captured or inspected the material: original assignment,
retriever and reader identities remain separate.

Extraction is still performed by adapters and readers. The normalizer does not
scan HTML, Markdown or provider JSON for omitted links. A retained capture may
be the exact receipt-bound stdout bytes or an identical viewer copy. Derived
representations need an explicit future derivation adapter; an arbitrary local
file cannot become native evidence. Historical returns lacking graph scope and
assignment bindings remain `legacy_attested`, with no native sufficient coverage.

## Actor and column permissions

Role permissions are workflow enforcement by the helper, not authentication
against processes with database/filesystem access. Check the submitting actor,
its parent chain, assignment, branch, stage, questions and plan binding before
applying a packet. A packet cannot grant itself a role by declaring a label.

| Actor | Permitted normal mutations |
| --- | --- |
| Searcher | Insert observed identities/discoveries, verified operation/capture mirrors, scans/references, metadata assessments, lead origins and pending work proposals within its assignment. It cannot claim an actual inspection it did not perform. |
| Reader | Searcher observations plus verified own returns/inspections, scoped assertion evidence, role/relevance/findings/contributions/relationships, and leads. No shared acceptance, merge, exclusions or priority edits. |
| Coverage reviewer | Reader observations plus `challenged` assertion decisions, gap/term proposals and reread proposals within its review scope. No final bounds or cancellation of another branch. |
| Evidence reviewer | Reader observations plus `challenged` assertion decisions and proposed claim/evidence corrections. Revised shared claims and accepted decisions remain coordinator-controlled. |
| Research lead | Worker observations plus actor registration, assignment/lease and pending-work scheduling within its delegated branch/stage. May reconcile routine local returns where its saved delegation explicitly permits that action. No root budget/scope changes, global merges, cross-branch priorities or generation exceptions. |
| Coordinator | Explicit shared decisions, merges, priority/terminal transitions, claim revisions, scoped retry adjudication, vocabulary registration, imports, checkpoints and exports within the task contract. Root privilege does not skip evidence, revision or spending checks. |

Workers may propose a `work_items` insert only with `state=pending`, the assigned
branch/question, derived generation, and default priority 50. Worker updates to
work priority/stage/generation, artifact canonical fields, shared claim text/status,
question status or terminal obligation/lead dispositions are rejected. Adapters
may update a worker's own assignment/operation execution state from verified
receipts; a worker packet cannot assert successful admission or completion
without those receipts. A delegated lead can register only a direct child inside
its own scope and permitted roles; it cannot register another coordinator or a
broader research lead. Independent review has a distinct work item/purpose and
consumes the same budget.

## Work and recovery transitions

`plan` automatically registers its saved snapshot in graph mode. After an
interrupted registration, use `graph register-plan ROOT --sha256 HASH` to
reconcile that original. `graph next-work ROOT --actor ID --limit 50` returns
balanced candidates, while `graph work-status ROOT` exposes unfinished original
admissions and expired or reconciling assignments.

Use `graph work ROOT --file REQUEST.json` for structured mutations. The request
has an `action` and the corresponding public arguments documented in
[research_graph_work.py](../scripts/research_graph_work.py). Actions include
`register-actor`, `claim`, `start`, `renew`, `complete`, `bound`, `release`,
`reopen`, `reconcile-assignment`, `resume`, `reconcile-operation`, and the explicit
`record-admission`, `record-dispatch`, `record-completion` recovery hooks.
`recover-retry` appends a budget classification only after a request-only
admission has a verified cancelled mirror and retained original-worker stop
reconciliation. It preserves the original retry epoch and spending; it creates
no receipt or graph transition. Claims
name the work, actor, saved plan/item and expected work revision; later assignment
transitions require its expected revision. A fairness override requires a
coordinator's recorded reason. Ordinary reads use a stable logical purpose;
independent review must declare its distinct purpose.

The recorder automatically validates before admission and mirrors real admission
and completion afterward. Subprocess capture records dispatch intent immediately
before launch; a launch error retains its own receipt. Imported tool calls need
an explicit dispatch-intent record before the actual call, or final receipt
reconciliation that preserves the unobserved dispatch gap. Never repeat an
original operation to repair a missing graph mirror.

The following transitions are exhaustive. Revisions, actor authority, reasons,
and external checks still apply to every allowed arrow.

| Record / current state | Allowed next states |
| --- | --- |
| Work pending | leased, blocked, deferred, cancelled |
| Work leased | running, pending, blocked, deferred, cancelled |
| Work running | completed, pending, blocked, deferred, cancelled |
| Work completed/blocked/deferred/cancelled | pending, after explicit reopening with a reason |
| Assignment leased | running, reconciling, released, cancelled |
| Assignment running | completed, reconciling, released, cancelled |
| Assignment reconciling | running for the same recovered worker, completed, released, cancelled |
| Assignment completed/released/cancelled | No state transition; a new claim creates a new assignment. |
| Operation reserved | admitted, cancelled, unknown |
| Operation admitted | dispatched, cancelled, unknown |
| Operation dispatched | succeeded, failed, interrupted, unknown |
| Operation interrupted/unknown | succeeded, failed, cancelled, unknown after receipt/worker reconciliation |
| Operation cancelled | At storage revision 3 only: succeeded or failed from the exact original late terminal receipt, with accepted request-only cancellation and worker-stop evidence. No redispatch. |
| Operation succeeded/failed | No execution-state transition. Retries have new deterministic operation IDs and recorded ancestry. |

Same-state updates may record verified metadata or lease renewal, with a revision
increment; they cannot rewrite immutable identity, request, receipt, budget or
plan bindings. Metadata changes do not reopen terminal states.

Late receipt reconciliation preserves the original cancellation event and its
request-bound worker-stop statement. The new terminal event records the receipt
outcome; it does not claim that receipt was known at cancellation. Original
admission, request, assignment, ancestry, output hashes and receipt provenance
must agree. No dispatch event, provider replay, refund or automatic retry is
created. A restored pre-request admission remains declared intent without an
observed dispatch; accepting its later receipt does not invent one. Imported
timeout/interruption receipts alone do not establish a terminal outcome. Actual
inspection and sufficient reader returns remain separate requirements for work
completion. Revision 1/2 graphs retain late original files and return an
actionable explicit-upgrade error until their storage is upgraded.

Current scope checks do not prevent retiring historical invalid work. A
coordinator may release, bound or enter reconciliation for an existing assignment
with a wrong saved question or empty read scope, using an unchanged intent and
an explicit paired work reason. Original unresolved operations must first be
reconciled; a checked cancellation preserves their spending. These exceptions
grant no completion, inspection, renewed lease or new dispatch. A bounded/dropped
plan item may retain the exact already released historical empty scope; active
items and replacement claims must satisfy current scope checks. Accepted history
and original evidence are retained, not rewritten to look valid.

Claim atomically creates an assignment and changes pending work to leased;
starting work advances assignment before work to running. Expiry changes the
assignment to `reconciling`; this remains active in the unique index. Neither
expiry nor worker interruption proves its provider call stopped. Reserved,
admitted, dispatched, interrupted and unknown operations prevent releasing,
cancelling or completing the assignment until explicit reconciliation. No
replacement admission or refund is inferred from a missing graph mirror.

Finish/release the assignment before making work terminal or pending. Native
read/visual work completion requires a verified return from the most recent
assignment, an actual inspection of captured evidence, and sufficient requested
scope/policy/version. A partial return either continues that assignment or leaves
the outstanding scope pending/bounded with a reason. Search completion verifies
all pages, candidate indexing and declared caps; subprocess success alone is
insufficient. For source access failures, reopening also checks the explicit
changed basis in the relevant outcome chain.

Question states are open/answered/bounded/reopened; answered/bounded need reasons
and evidence or executed follow-ups. Claims are draft/accepted/challenged/
withdrawn/superseded; every edit creates the next retained revision and requires
fresh evidence links to that revision. Leads are pending/queued/investigating/
resolved/bounded/excluded/deferred. Terminal leads/obligations can reopen only by
coordinator reconciliation retaining the prior reason in the event history.

The dispatch order is: claim; bind the immutable saved plan; reserve the
deterministic operation ID in the existing budget recorder; mirror admission;
dispatch; retain its receipt; ingest result/return; complete or bound work. The
graph never counts spending independently. Known graph shortcuts do not lower
operation-derived generation. A terminal budget limit preserves pending frontier
and incomplete breadth while allowing exports, synthesis and delivery.

Saved `origin.read_id` names the semantic source of a follow-up. The operation's
`research.parent_operation` and planned `graph.parent_operation` describe the
immediate operation ancestry. For pagination they may name a later search while
the source origin stays unchanged. Shared planning/admission validation requires
an exact provider/query/filters/cap/search-branch/input-kind continuation with the
preceding result's non-NULL cursor, through successful indexed original receipts
back to that exact source read. Every search in this chain retains the read's
generation plus one; a lower-generation unrelated root cannot substitute for it.
Traversal rejects cycles and stops after at most 1,024 search ancestors. Every
page still spends its own search attempt; retries keep their original request,
immediate parent, generation, count and failure/refresh rules.

Closed plan items and replay of an already registered exact immutable plan
project their original ancestry, without comparing it to a later admission of
the same request through another retained path. This cannot authorize dispatch:
new queued/active candidates, claims and operation validation still enforce
current retry ancestry. Historical work/assignment/admission records are never
rewritten to match a later route.

## Queries and command responses

Invoke graph commands through `python3 research_bundle.py graph COMMAND ROOT`.
Initialization additionally requires `--run-id ID` and accepts `--actor ID`
(default `coordinator`). Ingestion and reconciliation require `--file PACKET`;
checkpoint requires `--id ID`. Recovery is read-only unless `--apply` is given:
`--batch-id ID --apply` retries that retained packet after ordinary validation,
while `--apply` alone rebuilds accepted history. Neither form dispatches research.
`validate` checks accepted graph history and retained file integrity, including
contract, plan, receipt, capture and return hashes. It ignores dependencies of
unaccepted packets and reports `fresh_evidence_validation:false`: file integrity
does not establish current provider state, source meaning or a fresh inspection.

Normal reads use `graph artifact`, `neighbors`, `frontier`, `leads`, `coverage`,
`evidence`, and `changes` with allowlisted filters and bound parameters. Default
page size is 50; allowed range is 1..200. Use deterministic ordering ending in a
unique ID. A cursor carries query name, filter digest, graph sequence and last
sort tuple. Each response is one read transaction. A cursor used after the graph
changes returns `stale_cursor`; it does not silently combine two snapshots.

CLI filters use hyphenated names: for example, `graph coverage ROOT --question-id
why --limit 50` or `graph artifact ROOT --artifact-id SOURCE --include metadata
references`. Pass the returned cursor through `--cursor TOKEN`. Page limits
count typed facts, each with `record_type`, rather than whole artifacts with
unbounded nested inventories. Retain IDs and continue through every page needed
for the selected section. Scope matches describe historical inspection; they do
not hide pending work or establish freshness and domain completeness.

| Query | Required selector / optional filters | Result records |
| --- | --- | --- |
| artifact | artifact ID; include metadata,coverage,references,assertions,work | Canonical ID plus preserved aliases, versions, captures, inspections and optional independently paged sections. No raw document bodies. |
| neighbors | artifact ID; direction in/out/both; observed/semantic/both; predicate, question, disposition | Observed occurrences with resolution history and unresolved targets; scoped semantic endpoints with evidence, basis and decision state. Only an explicit filter hides proposed/challenged edges. |
| frontier | branch, stage, question, role, maximum generation | Eligible pending work with priority, ancestry, existing scope coverage and blocking outcomes. Sorting is stage baseline/priority/deep, generation ascending, priority descending, branch, work ID. Eligibility never authorizes spend. |
| leads | kind, disposition, question, unresolved-only | Original returned wording, origins, unresolved names, work/obligation state and bounds. |
| coverage | question; branch, scope key, format-policy hash | Plausible unread roots, unworked exposed references, pending obligations, gaps/bounds and branch counts; conflicting relevance/outcome tails stay visible. |
| evidence | claim ID and optional retained revision | Text/scope with support, qualifications, conflicts, assertion state and exact capture/locator provenance. Defaults to current revision only; old evidence is not silently carried forward. |
| changes | after-sequence, optional through-sequence | Events in increasing sequence, affected identities/revisions and batch receipts. No timestamp-based cursor. |

`artifact_roles` and `artifact_question_assessments` include proposed, accepted,
challenged, withdrawn and superseded rows with their bases. A current accepted
projection must explicitly filter `disposition='accepted'`; conflicting accepted
scoped judgments must still be returned for reconciliation. Do not combine a
section-only role and a whole-source relevance statement into a broader approval.
`effective_reference_targets` includes unresolved rows; unresolved mentions are
never lost by an inner join. `sufficient_inspection` always needs the additional
work-version/freshness checks described above before reuse.

Every successful command emits JSON with `status:"pass"`, `schema_version:1`,
`run_id`, `graph_sequence`, and `command`. Query responses add `items`,
`next_cursor` (NULL at end), and `limits` describing applied filters/page size.
Mutation responses add `batch_id`, `payload_sha256`, `affected` (table/ID/revision),
and `idempotent`. Replays return the original accepted sequence/receipt; an
optional `current_graph_sequence` may report the later head separately.

Failure emits `status:"fail"`, `schema_version:1`, `command`, and an `error`
object with `code`, safe `message`, `retryable`, and relevant IDs. Codes include
`invalid_packet`, `unsupported_schema`, `unknown_identity`, `permission_denied`,
`batch_conflict`, `revision_conflict`, `lease_conflict`, `scope_mismatch`,
`pending_dependency`, `integrity_error`, `stale_cursor`, and `busy`.
Only busy/transient recorder conditions are retryable without changed input.
No failure response claims partial acceptance; pending file/budget side effects
are identified separately for recovery. Return exit 0 on pass and 1 on fail,
consistent with the existing helper.

## External validation and compatibility

The public G5 commands are `graph export ROOT --id ID`, `graph verify-export
DIRECTORY`, `graph validate-ledger ROOT --ledger FILE`, `graph import ROOT
--directory EXPORT --id ID --selection FILE`, and `graph migrate SOURCE
--destination NEW --run-id ID --actor coordinator [--ledger FILE]`.
Export IDs name immutable snapshots. Their represented sequence precedes the
later manifest-registration event; repeating an ID verifies its original bytes.

`validate-ledger` binds the parsed inventory to the exact registered export and
returns scoped source, work, obligation, question and claim facts from that
snapshot. Its `stop_checks` distinguishes explicit bounds from unfinished work,
unassigned follow-ups, unassessed sources and conflicting current outcomes.
Neither settlement nor an accepted finding establishes semantic correctness or
freshness for every possible question. Query the graph for those precise scopes.

New graph-mode plans require a current generated ledger. A hash-bound saved
plan may render or re-register its original older inventory. Only the first
pending seed inventory is hand-written. A `graph:ARTIFACT_ID` discovery entry
points to the retained graph provenance when no indexed provider result exists;
it does not assert a supplied seed or a search hit. Saving a generated view never
creates supplied-seed discoveries, including when replaying an older export with
a synthetic `seed:graph-*` label. Original history remains unchanged.
Graph-mode synthesis and publication
also require `--semantic-review FILE`: a separate retained file inside the
bundle with `reviewer`, `note`, `ledger_sha256` from `validate-ledger`, and
`graph_sequence` equal to its `represented_sequence`. This review attestation
cannot modify or authenticate the evidence. A changed ledger or research
snapshot requires renewed review; original operations and returns must be fully
synchronized and uncertain worker execution reconciled before synthesis.

Synchronization status version 2 distinguishes checked request-only
cancellations from missing or unresolved results. The original admission,
request hash, cancelled mirror and retained worker-stop statement must agree;
additional output bytes require ordinary reconciliation. Final checkpoints
retain these cancelled attempts, but they grant no executed follow-up, reading,
analysis or claim credit. Work, source and question closure remain separate.
Exports without this status version are verified under their original rules;
later cancellation handling does not rewrite their pending inventories.

SQL enforces FKs, identity uniqueness, immutable observations, linear reference
corrections, revision increments, claim snapshots, basic JSON/state shapes,
canonical cycles, scoped locator/capture membership, one active lease including
reconciliation, automatic obligations and key completion guards. These checks
do not authenticate actors, prove semantic truth, or inspect filesystem bytes.

The helper must additionally enforce:

- Exact packet/table/column permissions, bootstrap-only controls, batch event
  completeness/tail, expected revisions and current predecessor IDs.
- Original return/capture/receipt/budget/plan hashes; complete index/return
  reconciliation; scan extracted counts; lead-origin mapping; receipt identity
  and operation-to-source matching. Pending legacy cross-file writes remain
  `pending_dependency` packets and are not admitted as native inspections.
- Scoped inspection extent, format policy and requested version/freshness;
  assertion support/return matching; meaningful reasons and counterevidence for
  decisions; claim/question closure criteria.
- Delegation ancestry/scope, balanced scheduling and work-origin generation,
  logical-key derivation, completed source outcomes before retry, operation
  termination/admission recovery, and unchanged budget authority.
- Prior-export hash-bound provenance, bounded import selections, original
  identity deduplication and excluded output lineage. A prior claim is labeled
  prior-run evidence until inspected for the new question; it is never counted
  as independent corroboration of its copied original source.

The logical key is a SHA-256 of canonical structured intent: kind, canonical
target/provider query, requested source version or freshness policy, extent,
format policy, question and purpose. Normal reads reuse matching work/coverage;
independent review uses its own purpose. New aliases can reveal equivalent work,
but historical assignments, admission identities and generations stay unchanged.

Migration reads an old bundle into a new destination and retains a hash manifest
and import receipt. Existing source IDs become explicit legacy identity keys;
search result rows become discoveries; captures/returns retain their paths and
hashes; only attested inspections are imported as legacy_attested. Ledger roles,
findings, claims and dispositions become labeled historical assessments with
their provenance. Missing receipts, references, scopes or ancestry remain
limitations. Do not synthesize semantic edges or native inspection rows from a
successful retrieval or an old read flag. Once graph mode is enabled, ledger,
source/reference inventories and worklists are generated exports.

Export reads one graph sequence, includes all file hashes plus schema/contract/
plan/budget head, writes to a temporary versioned directory, syncs and installs
it, then registers the immutable manifest. Registration is an audit event about
the exported sequence and does not alter the represented evidence snapshot.
Checkpoints use SQLite's backup API under a consistent read snapshot; copying
only a live main database file would omit WAL state. Prior reuse imports only
selected identities/records and lineage from verified manifests. It preserves
historical gaps/readability as observations and does not recursively pull all
ancestors into the current run.

The fixtures under `tests/fixtures/retrospective-research/graph/` are fictional.
Their packets and retained file hashes exercise SQL contracts. Their small
`fixture_only` operation/return payloads are not full recorder acceptance
fixtures; G1/G2 integration tests must materialize valid recorder receipts and
exercise permissions, file/transaction crashes, idempotency and recovery through
the actual helper. SQL constraint tests do not claim those runtime behaviors.
