# Retrospective research graph: design and implementation plan

Status: G0–G6 are implemented on the feature branch: schema, durable storage, native evidence ingestion, bounded queries, shared work lifecycle, immutable exports, migration, prior reuse and skill guidance. G7 has completed controlled fresh/reuse trials and frozen the live research phase; final document delivery, integrated review and release remain in progress. Trials exposed integration defects and repeated reading and prioritization failures, retained in the [evaluation report](retrospective-research-evaluation.md). Passing structural checks does not establish research quality or complete discovery. Final review, publication and merge remain separate release gates.

## 1. Purpose and decisions

The research graph should make discovered material, evidence and unfinished research durable and queryable. It should help researchers discover the domain before deriving conclusions, and preserve the reasons for both conclusions and coverage limits.

The default deliverables remain a purpose-specific brief and an evidence companion. Their source inventory, reference relationships and research worklist will be generated from the graph. The brief remains authored prose, with its material claims connected to evidence.

The accepted direction is:

- **One SQLite database per investigation**, within its evidence bundle, outside the source repository. Source captures and original worker returns remain separate immutable files.
- **One consolidated record.** The database owns current research state. JSON, CSV and readable inventories are exports, not independently maintained authorities.
- **Validated commands for every update.** Workers submit structured records; a helper validates and commits them. The coordinator owns shared scheduling and interpretation decisions.
- **Evidence before conclusions.** Coverage review investigates the domain, overlooked leads and contradictory evidence independently of whether a draft brief already has enough citations.
- **Balanced expansion.** Protect baseline breadth, then run repeated terminology and child-reference follow-ups across branches. Preserve the unfinished frontier when bounds are reached.

SQLite supplies transactions, indexes, uniqueness constraints and recursive queries through Python's standard library. A separately operated graph service is unnecessary at the expected scale. JSON alone remains useful for interchange but makes transactional reconciliation and simultaneous worker updates harder.

## 2. The graph's meaning

### Artifact roles

Roles are overlapping and extensible. They can apply to a whole artifact or a particular section, and can differ by research question.

| Role | Reading purpose | Typical useful findings |
| --- | --- | --- |
| Content | Establish what was proposed, built, specified, demonstrated or measured | Designs, guidelines, prototypes, implementation behavior, study methods and results |
| Critical / Review | Establish evaluation, objections, alternatives and gating decisions | Reviews, feedback, discussions, approvals, deferrals, unresolved decisions and their reasons |
| Trunk | Establish how the work is organized and how to reach related evidence | Trackers, hubs, indexes, roadmaps, milestones, ownership, handoffs and linked records |

Role is separate from importance. A Trunk artifact can be pivotal even when it does not answer the central question directly. Initial selection should normally read plausible in-scope artifacts in all three roles. Organizing work within the research domain establishes relevance without requiring a direct decision passage.

### Observed references and semantic relationships

An observed reference records a hyperlink or an unlinked mention. A semantic relationship records an interpretation supported by inspection, such as organizes, reviews, challenges, approves, implements, tests, revises, supersedes or responds_to.

Preserve both independently. A link does not establish approval, causality, implementation or chronology. A relationship can be supported without a hyperlink. Several reference occurrences may point to one artifact, and several evidence passages may support one semantic relationship.

Relationship endpoints may include section scopes. A review can approve one behavior and defer another; it must not become a blanket approval of the whole design. Record event time separately from source publication, modification, capture and assessment times.

### Coverage holes

Holes are leads connected to questions, sources and evidence, with follow-up work and explicit dispositions. Distinguish:

- **Observed gap:** a referenced review is unidentified, a relevant child is unread, or named results have not been inspected.
- **Inferred gap:** a proposal and implementation are known, but the transition between them is unexplained. This does not assert that a missing decision document exists.

Every hole retains its origin, affected question, significance, proposed action and outcome. Evidence may resolve a hole by establishing uncertainty or the absence of a recoverable record after bounded searches; finding another citation is not automatically resolution.

## 3. Storage and authority

The proposed bundle layout extends the current recorder:

```text
investigation/
  contract.json                 retained task and provider requirements
  contracts/                    immutable requirement revisions
  operations/                   existing requests, output and receipts
  reader-returns/               existing immutable researcher returns
  research-budget.json          existing authoritative spending journal
  graph/
    research.sqlite             current consolidated research state
    updates/                    immutable update packets
    checkpoints/                consistent database backups and manifests
    exports/                    versioned JSON, CSV and readable views
```

The graph does not duplicate raw document bodies. Captures are referenced by relative path, content hash, format, scope and receipt. Missing or prohibited-to-retain content is represented as a limitation, not replaced by an invented snapshot.

Generated source titles and URLs prefer successful current-run metadata observations over earlier seeds or reference placeholders. Retrieval completion time orders successful observations, with accepted batch sequence as a tie-breaker; missing fields retain the best available nonempty observation. Each selected field names its originating discovery. This display choice does not rename the retained artifact record, assert a provider revision or establish body inspection. Raw observations and earlier immutable exports remain unchanged.

Authority is divided deliberately:

| Information | Authority |
| --- | --- |
| Original tool requests, output and receipts | Existing immutable operation files |
| Original reader observations | Immutable reader-return files |
| Admitted operations and research spending | Existing budget journal |
| Consolidated identities, evidence, judgments and work state | SQLite graph |
| Reproducible changes to the graph | Immutable update packets plus accepted event sequence |
| Published inventories | Exports bound to a particular graph sequence |

Plan snapshots remain immutable execution contracts. The current worklist is generated from graph work items, then saved through the existing plan validation path. Operations continue to reference their saved plan item and hash. Editing a work item cannot rewrite the assignment under which an earlier operation ran.

Use a local filesystem for the live database. Processes on the same host can share it. A worker on another machine returns packets to the coordinator host; it does not open the database over a network filesystem. A shared multi-investigation database is outside the first implementation; using earlier research outputs as Trunk sources is part of normal discovery.

## 4. SQLite schema

This is a relational representation of a graph: explicit tables for artifacts, relationships, evidence and work. It does not require every record to be stored in one generic node table. A graph export may project questions, claims and leads as additional node types.

Use opaque TEXT primary keys, UTC timestamps, declared foreign keys and versioned payload schemas. External provider identities belong in their own table. Enumerated states use CHECK constraints or small vocabulary tables. JSON is reserved for structured locators, provider-specific metadata and immutable request payloads; frequently queried relationships and states are columns or join tables.

### 4.1 Identity and source observations

| Table | Main columns and constraints |
| --- | --- |
| artifacts | artifact_id PK; title; artifact_type; created_batch_id; canonical_artifact_id nullable FK. Canonical redirects record an explicitly reconciled merge; they cannot form cycles. |
| artifact_keys | namespace; external_id; artifact_id FK; basis; created_batch_id. UNIQUE(namespace, external_id). Several provider routes can resolve to one artifact. |
| artifact_versions | version_id PK; artifact_id FK; provider_revision nullable; modified_at nullable; first_observed_at; version_basis. Known revision tokens are unique within an artifact. |
| captures | capture_id PK; operation_id; version_id nullable FK; format; relative_path; sha256; capture_extent_json; extraction_status; receipt_sha256. A failed request need not have a capture. |
| discoveries | discovery_id PK; artifact_id FK; operation_id or origin_reference_id or supplied_seed_id; rank nullable; observed_title; observed_url; created_batch_id. Retain every discovery occurrence. |
| locators | locator_id PK; capture_id FK; locator_kind; locator_json; excerpt nullable; excerpt_hash nullable. Examples include section, slide, node, comment, paragraph and timestamp. |

A provider namespace describes identity, not the API used to retrieve it. For example, document and storage APIs exposing the same file ID should share its stable namespace. Changing a URL's view parameters does not make a new artifact. Two documents with the same title remain separate until identity evidence supports a merge.

An unknown provider revision stays unknown. A capture hash proves the retained bytes; it does not prove a provider version, completeness, or unchanged comments and visuals. Different exports of the same known revision can therefore have separate captures.

Canonical merges preserve old IDs and references. They require coordinator reconciliation and a recorded basis; their effect on distinct-artifact accounting must be reconciled with the budget recorder rather than silently refunding prior admissions.

### 4.2 Inspection and structured returns

| Table | Main columns and constraints |
| --- | --- |
| reader_returns | return_id PK; actor_id FK; file_path; payload_sha256; receipt_sha256; recorded_budget_event; created_batch_id. The stored payload remains immutable. |
| inspections | inspection_id PK; artifact_id FK; return_id FK; actor_id FK; inspection_extent; method; inspected_at; limitations. A return can describe multiple captured formats. |
| inspection_captures | inspection_id FK; capture_id FK; inspected_extent_json. Composite PK; connects an actual inspection to the bytes inspected. |
| inspection_withdrawals | withdrawal_id PK; inspection_id unique FK; actor_id FK; reason; evidence_path/evidence_sha256; withdrawn_at; created_batch_id. Append-only withdrawal of inspection credit, preserving the original observation. |

Capturing content is not reading it. Inspection extent is not a single read checkbox: it records body sections, comments, revisions and visuals separately where applicable. The actual reader may differ from the actor who executed the retrieval.

The live evaluation exposed accepted full-scope declarations based on excerpts.
Assertions could be challenged, but the original inspection still contributed to
coverage. Storage revision 2 closes that gap through a retained withdrawal basis
and effective-inspection views. It keeps packet protocol 1 and the exact base SQL,
and adds a journaled, transactional migration; older databases require explicit
upgrade. Historical exports and checkpoints keep their original schema identity.
Withdrawal changes usable coverage and evidence, not history or budget spending.
Affected completed work must be reopened or explicitly bounded; new reading and
revalidated claim revisions establish new credit. Reference scans remain separate
from semantic reading and are retained. Tests cover upgrade interruption/replay,
legacy snapshots, projection, partial rereads and inherited withdrawal provenance.

Final validation also accounts for accepted native returns with no inspection,
and returns whose every inspection was withdrawn. The verified frozen export
binds their original bytes and withdrawal evidence; budget reconciliation still
requires all returned leads. These are separate non-inspecting dispositions,
not replacement reads or implicit correction chains. An unaccepted return,
missing original, or unresolved source work remains a failure.

### 4.3 Reference graph and interpretation

| Table | Main columns and constraints |
| --- | --- |
| reference_scans | scan_id PK; capture_id FK; return_id nullable FK; method; scanned_extent_json; extracted_count; limitations; created_batch_id. Records the extraction boundary independently of substantive inspection. |
| reference_occurrences | reference_id PK; from_artifact_id FK; locator_id FK; scan_id FK; local_reference_key; kind link/mention; original_value; label; target_artifact_id nullable FK; extraction_method. UNIQUE(scan_id, local_reference_key). |
| reference_resolutions | resolution_id PK; reference_id FK; target_artifact_id FK; basis; actor_id FK; replaces_resolution_id nullable FK; created_batch_id. Retains corrected resolutions. |
| assertions | assertion_id PK; kind role/relevance/finding/contribution/relationship; subject_artifact_id FK; predicate; object_artifact_id nullable FK; value_json nullable; subject_locator_id nullable FK; object_locator_id nullable FK; question_id nullable FK; temporal_scope_json; basis; return_id nullable FK; created_batch_id. |
| assertion_evidence | assertion_id FK; locator_id FK; inspection_id nullable FK; stance support/qualify/conflict; basis metadata/inspected/participant_account/inference. Composite identity retains distinct evidence occurrences. |
| assertion_decisions | decision_id PK; assertion_id FK; disposition accepted/challenged/withdrawn/superseded; reason; actor_id FK; replacement_assertion_id nullable FK; expected_revision; created_batch_id. Append-only reconciliation history. |

Role and relevance assessments use assertions, so disagreements are retained. A Trunk role assertion might have predicate has_role and value trunk. A semantic relationship has a predicate such as reviews and an object artifact. Predicate-specific validation checks the allowed value shape; arbitrary JSON must not bypass it.

Reference occurrences are observations exposed by a capture or scan. Their presence does not assert that a human or agent inspected the target. A scan can index links from a body before substantive analysis, provided its extraction scope is recorded honestly.

Unresolved mentions retain their wording and locator. When a target is identified, add a resolution rather than replacing the original mention. Deduplicate targets without collapsing the passages or searches that led to them.

An assertion begins as a recorded assessment, not an automatically established fact. Its basis remains visible even after acceptance. The coordinator can accept uncontested routine assessments in batches; consequential relationships and disagreements need explicit inspection and reconciliation. Reviewers can challenge any assertion through a new review packet.

### 4.4 Questions, claims and follow-up leads

| Table | Main columns and constraints |
| --- | --- |
| questions | question_id PK; text; central; contract_revision; current_status; revision. A bounded answer retains its reason and executed follow-ups. |
| claims | claim_id PK; text; basis; temporal_scope_json; comparison_scope_json; status; revision. Material changes create a retained claim revision. |
| claim_evidence | claim_id FK; locator_id FK; assertion_id nullable FK; stance support/qualify/conflict; rationale. Qualifying and conflicting evidence are first-class. |
| claim_questions | claim_id FK; question_id FK; relationship answers/partly_answers/complicates. Composite PK. |
| leads | lead_id PK; kind vocabulary/linked_record/neighborhood/observed_gap/inferred_gap; original_value; normalized_key; significance; disposition; reason; revision. |
| lead_origins | lead_id FK; return_id nullable FK; returned_lead_id nullable; reference_id nullable FK; locator_id nullable FK; question_id FK; origin_basis. Preserve every original returned-lead mapping. |

Normalized terms support lookup, not automatic synonym claims. A newly observed phrase may be an alias candidate, a broader concept, or an unrelated use. Searching it does not establish equivalence with the initial project name.

Claims are authored conclusions derived from evidence. They do not determine which domain evidence is eligible for collection. The claim ledger and the coverage frontier receive separate review.

### 4.5 Work, ownership and execution

| Table | Main columns and constraints |
| --- | --- |
| work_items | work_id PK; kind search/read/inspect_visuals/review/reconcile; artifact_id nullable FK; question_id FK; request_json; logical_key; purpose; stage baseline/priority/deep; branch; generation; priority; state; terminal_reason nullable; revision; created_batch_id. |
| work_origins | work_id FK; lead_id nullable FK; reference_id nullable FK; parent_operation_id nullable; root_basis nullable; originating_generation. Many origins may converge on one work item. |
| assignments | assignment_id PK; work_id FK; actor_id FK; parent_assignment_id nullable FK; claimed_at; lease_expires_at; finished_at nullable; state; generation_at_claim; plan_item_id; plan_sha256. Only one active ordinary assignment per work item. |
| operation_links | operation_id PK; assignment_id FK; budget_event_id nullable; request_sha256; receipt_sha256 nullable; execution_state; return_id nullable. References existing execution records without independently counting spending. |
| actors | actor_id PK; parent_actor_id nullable FK; role; declared_runtime_identity nullable; registration_batch_id. Actor labels are attestations unless a harness authenticates them. |

One work item can require several provider calls, such as an initial page, pagination, another format or a comments read. Each real call still needs its own recorded operation and budget admission. A work item is not complete simply because a subprocess exited successfully.

The logical key includes target or query, requested extent, format policy and purpose. Ordinary duplicate reads reuse existing coverage. Independent review intentionally has a distinct purpose and assignment, while consuming the same shared budget. A changed source version or previously uninspected scope can justify another read.

An assignment distinguishes a new retrieval from a new inspection of retained
content. It binds either a capture made after assignment, an exact known provider
version, or an explicit set of verified native capture IDs. The last option
supports rereading unversioned captures without inventing revision metadata or
fetching them again. It admits no provider call and still requires an actual
inspection under the new assignment. Historical imported inspections remain
separate and gain no current credit. Explicit intent corrections retain the
original saved plan and event history; later claims need a matching current plan.

Assignments use leases to recover abandoned work, not to prove an operation has stopped. Expiry triggers reconciliation of the original operation and worker before reassignment; it must not automatically duplicate a still-running provider call.

### 4.6 Update and export integrity

| Table | Main columns and constraints |
| --- | --- |
| ingest_batches | batch_id PK; actor_id FK; payload_sha256; packet_path; accepted_at; schema_version; resulting_sequence. Repeating an ID with the same payload returns its original receipt; a different payload is an error. |
| graph_events | sequence INTEGER PK; batch_id FK; event_kind; entity_kind; entity_id; prior_revision; next_revision; payload_json. Every accepted state change has an event in the same transaction. |
| export_manifests | export_id PK; graph_sequence; schema_version; contract_sha256; plan_sha256; budget_event_head; files_json; created_at. New rows bind one external manifest by path/hash/size, keeping the complete file inventory outside the bounded row. Older expanded rows retain their original representation. |
| prior_run_imports | import_id PK; prior_run_id; prior_export_id; manifest_sha256; source_artifact_id FK; policy; imported_batch_id FK. Preserves the prior-run boundary and the scope of reused records. |

The database also records its schema version and applied migration identifiers. Accepted batches and events are append-only by the supported interface. This is an audit and recovery property, not protection against an administrator rewriting local files.

## 5. States and hard checks

Keep three independent dimensions visible in generated source views:

- Relevance is unassessed, relevant, irrelevant or uncertain, with its question, inspected scope and assessment basis.
- Readability distinguishes readable, access_blocked, unreadable, transient_retry_pending and retry_exhausted from unknown. Reopening a terminal failure requires a changed access or capability basis.
- Research progress tracks discovery, queuing, assignment, capture, inspection and analysis. Disposition records used, contextual, excluded or deferred, with an explicit reason.

These are derived from evidence and execution records, not a single mutable status pretending to summarize everything. An artifact may be relevant, partly inspected and blocked for remaining visuals at the same time. Work-item states are more compact: pending, leased, running, completed, blocked, deferred, cancelled, with documented transition rules and reasons.

Ingestion and reconciliation must enforce:

1. Every returned candidate and extracted artifact reference has an inventory identity or an unresolved-reference record.
2. Every returned lead retains its original ID, wording, locator and origin mapping.
3. Every new reference/lead receives an existing work-item link, a pending follow-up candidate or an explicit disposition. Registration and this obligation commit together.
4. A semantic assertion cannot present uninspected text as inspected evidence. Metadata triage and inference remain labeled.
5. A read assignment requires captured evidence, a matching actual inspection and a valid reader return before completion. Partial completion preserves work for outstanding required scope.
6. Every terminal follow-up disposition has a reason. Permission errors, timeouts and budget stops cannot become irrelevance.
7. Shared reconciled decisions use expected revisions. A stale update fails with a conflict rather than silently overwriting another decision.
8. Each saved export is internally consistent at one graph sequence. Edited exports cannot be imported as unproven research evidence.

Checks cannot prove that a reader noticed every useful passage, that a search service returned every matching document, or that a semantic interpretation is correct. Independent source review remains necessary.

## 6. Agent and subagent protocol

### Query interface

Agents use bounded, structured queries through the existing helper's proposed graph subcommands. Results include IDs, provenance, graph sequence, pagination and relevant pending work. Avoid repeatedly dumping the whole database into context.

| Query | Typical consumer | Returned information |
| --- | --- | --- |
| artifact | Reader or coordinator | Metadata, roles, existing inspections, captures, findings, outstanding scopes and work |
| neighbors | Researcher | Incoming/outgoing observed references or accepted semantic relationships, with evidence and disposition |
| frontier | Coordinator or delegated lead | Eligible pending work by branch, stage, generation, importance and source role |
| leads | Search worker | Unqueried terms, unresolved names and the exact originating passages |
| coverage | Coordinator or coverage reviewer | Unread plausible roots, unworked references, bounded holes, branch coverage and stopping reasons |
| evidence | Synthesis author or reviewer | Claim support, qualifications, conflicting evidence and exact source locators |
| changes | Coordinator | Updates since a specified sequence, including newly returned leads and challenged judgments |

Read-only SQL can be allowed for trusted diagnostic work through a read-only connection. Routine agents use named queries with parameter binding, bounded page sizes and allowlisted filters. Writes always use validated commands.

### Who can change what

| Role | Normal updates | Coordinator-controlled decisions |
| --- | --- | --- |
| Search worker | Register results and discovery paths; report route limits/errors; propose terms and read work | Shared priority, scope changes, broad exclusions |
| Reader | Submit inspections, reference scans, findings, role/relevance assessments and follow-ups | Global artifact merges, contested interpretation and cancellation of other branches |
| Coverage reviewer | Add overlooked references/terms; challenge relevance; propose gap work and leaf rereads | Final bounds and closure of material gaps |
| Evidence reviewer | Challenge claim support, scope, semantic edges and contradictions | Acceptance of revised shared conclusions |
| Delegated research lead | Assign work within its delegated branch and stage, reconcile routine local returns | Global budget, scope, generation exceptions and cross-branch priorities |
| Root coordinator | Reconcile judgments; approve merges; prioritize and assign work; record bounds; create checkpoints and exports | Remains accountable for final coverage and synthesis decisions |

These are controller-enforced workflow rules, not a security boundary against agents with direct filesystem access. No role label should be described as authenticated unless the runtime supplies verifiable identity.

### Delegation and nested workers

Delegation means launching additional agent processes or subagents for separate research assignments. It does not mean asking the current agent to role-play several researchers.

The root passes the same bundle path, contract, graph version and shared budget to every delegated lead. A lead registers its children with distinct actor IDs and parent assignments. Nested children claim bounded work from their authorized branch; they do not create separate databases, reset budgets or treat discovered terms as new baseline roots.

Each assignment includes its questions, source or route rationale, inspection extent, provider constraints, existing coverage, required return fields and completion condition. Supply neighborhood context without presenting a desired conclusion to confirm. Independent reviewers need task context, without being coached toward the originating reader's judgment.

### Implemented command flow

The public entry point remains `research_bundle.py`. Use the current
[graph workflow](../assets/agent-assets/skills/retrospective-research/references/graph-workflow.md)
for full request examples, required identities and recovery steps:

```text
research_bundle.py graph frontier ROOT --question-id rationale --limit 50
research_bundle.py graph next-work ROOT --actor reader-2 --limit 50
research_bundle.py graph work ROOT --file claim-request.json
research_bundle.py capture ROOT --spec operation.json
research_bundle.py reader-return ROOT --return reader-2-source.json
research_bundle.py graph artifact ROOT --artifact-id ARTIFACT_ID
research_bundle.py graph changes ROOT --after-sequence 310
research_bundle.py graph reconcile ROOT --file coordinator-decisions.json
```

Result indexing and accepted reader returns invoke graph ingestion automatically.
Explicit ingestion and synchronization support recovery; they are not a second
inventory that researchers maintain by hand. A plan is preflighted before its
immutable original is saved. Failed original returns remain retained, with
hash-bound rejection and correction records when recovery is necessary.

## 7. Transaction, concurrency and recovery design

Use short SQLite write transactions with foreign_keys enabled, WAL journaling on local storage, synchronous FULL and a bounded busy timeout. Readers use read transactions for a consistent view. No transaction stays open during a provider call or model reading/analysis.

For each update:

1. Write the complete packet to a temporary file, flush and fsync it, atomically install it under its immutable batch ID, then sync the directory. Reject a reused ID with different contents.
2. Validate its schema, actor/assignment scope, capture and return hashes, referenced identities and requested state changes.
3. Begin a short IMMEDIATE transaction, recheck state-dependent preconditions and idempotency, and apply the entire batch.
4. Write graph events and the ingestion receipt in the same transaction; commit. Return the resulting sequence and affected IDs.
5. Regenerate requested exports after commit. An interrupted export cannot corrupt accepted graph state.

A crash before database commit leaves a pending packet that can be ingested again. A crash after commit but before returning the receipt is handled by the batch ID and hash. Recovery replays only validated packets, in accepted order where applicable; unaccepted pending packets are not silently promoted to accepted history.

The legacy reader-return path currently records a budget event and a file as separate writes. The graph adapter must verify both before accepting an inspection, detect incomplete legacy writes, and expose a repairable pending state. A graph transaction cannot make the existing budget journal and filesystem writes one atomic transaction.

### Admission across the graph and existing budget recorder

Use an explicit sequence: claim assignment; save/bind the plan item; reserve the deterministic operation ID through the existing budget recorder; record its admission link; dispatch; retain the receipt; ingest result/return; complete or bound the assignment.

An admitted operation that was never dispatched retains its recorded reservation until explicit reconciliation. Do not silently refund it or launch another operation under a new ID. Recovery checks receipts and execution state before retrying. The graph tolerates a temporarily missing operation mirror while treating the budget journal as authoritative.

A checked request-only cancellation remains in the audit trail and final
coverage checkpoint without a fabricated receipt or result. Synchronization
distinguishes that accounted-for cancellation from an unresolved attempt;
execution counts and research credit exclude it. The associated work and
question still need completion or an explicit bound. Older exports retain their
original synchronization status when validated by newer helpers.

For concurrent claims, create the assignment and change the work state in one transaction. A partial unique index permits only one active ordinary lease for a work item. Claiming against an out-of-date revision fails cleanly. Independent review uses a separate work item rather than bypassing the uniqueness rule.

Corrections to evidence produce new assertions or returns and a supersession decision. Existing captures, returns and accepted events are not rewritten. Rebuilding the graph uses immutable accepted packets and checkpoints, preserving original ordering and reconciliation decisions.

## 8. Scheduling, graph traversal and bounds

The coordinator schedules breadth across questions and source roles before deeper expansion. Pending references are stored immediately but do not themselves authorize dispatch or spend budget.

Within each wave, balance baseline roots, new terminology searches, high-value organizing records, reviews and child reads. Batch several artifacts per worker to avoid one agent launch per hyperlink. Deduplicate against prior inspection extent and active work before assigning.

Size inspection checkpoints to actual context and output capacity, separately from the size of the worker's whole partition. Preserve completed and pending source IDs across continuations. A program loading a file does not establish that the reader saw its contents; truncated displays and heuristic default labels cannot support claimed inspection. Lossless display deduplication may collapse identical previews while retaining their source occurrences. Coordinator sampling checks the evidence behind assessments, not whether every reason uses different wording. These remain reader and acceptance responsibilities; structural validation cannot establish what a model understood.

Graph distance and budget generation are different fields. A newly discovered shortcut must not reset the operation-derived generation, retroactively refund effort or recast a follow-up as a baseline root. Every scheduled operation keeps its actual originating chain, while the graph retains all other paths for explanation and navigation.

The existing default research caps remain the initial policy:

| Resource | Default cap |
| --- | --- |
| Search attempts | 250 |
| Read/export attempts | 400 |
| Distinct assessment admissions | 200 |
| Active elapsed research time | 120 minutes |
| Aggregate research agent time | 300 minutes |
| Follow-up generations beyond baseline | 3 |
| Attempts per logical operation | 3, for plausibly transient failures |
| Per-request time | 240 seconds, limited by remaining allowance |

Indicative baseline/priority/deep reserves remain 50/30/20, with baseline borrowing permitted. Metadata-only candidates do not consume distinct substantive assessment slots. These are research caps; ordinary synthesis, revision and delivery retain their separate allowance. Actor-time intervals remain attested accounting, not measured model runtime.

The supervising coordinator owns delegated activity clocks from before dispatch
through observed completion, including interpretation and return preparation.
Assignments name that owner; children do not independently close the clock.
Short script timers cannot represent surrounding model assessment. Missing or
partial timing remains an explicit cost limitation, with original events intact.

Support balanced relevance-guided expansion by default, and exhaustive assessment of exposed children of relevant artifacts as an optional bounded policy. Both preserve baseline breadth. Exhaustive assessment requires each exposed child to have an inspection, a terminal access/readability outcome or a declared bound. Irrelevant inspected leaves stop their subtree. Unreadable is not irrelevant.

At a cap, preserve the pending frontier by branch, generation, role, reason and importance. State which waves completed and which were partial. A cap ends research admission; it does not prevent delivery from retained evidence. If baseline coverage cannot fit, expose that problem early rather than spending the remainder down one branch.

## 9. Earlier investigations as Trunk resources

Earlier evidence companions, source inventories, research plans and graph exports are valuable organizing artifacts for overlapping research. They preserve candidate lists, original URLs, terminology, record neighborhoods and unresolved holes that a new search would otherwise need to rediscover.

Normal intake and baseline discovery should include relevant prior research when permitted by the user and source restrictions. Register its companion or graph manifest as an artifact with a Trunk role. Follow its references into the original records, retaining the previous run, export version and original source identity as discovery provenance.

The prior run is a starting map, not an independent corroborating source for every claim it repeats. In particular:

- Reuse stable source IDs, links and terminology immediately as leads. Preserve known gaps and route limitations as historical observations, not permanent truths.
- Keep earlier interpretations labeled as prior-run assertions until evaluated for the new question, time window and comparison scope.
- Distinguish a newly retrieved source, a re-inspected retained capture, and a reused earlier assessment. Reusing a source list does not establish that its original documents were read in this run.
- Recheck pivotal evidence when source revisions, access, requested scope or the intended conclusion make fresh inspection necessary. A previously inaccessible artifact may now be readable.
- Deduplicate originals across prior inventories and current search routes. Several briefs copying the same primary passage form one evidence chain, not several independent confirmations.
- Do not recursively import every prior graph and all its ancestors. Admit overlapping sections and branches through the same worklist, relevance review and research bounds as other Trunk sources.

Evaluation runs may explicitly exclude previous outputs to test independent discovery. Record that policy in the task contract and enforce it in candidate import and search-result filtering. A title match alone is insufficient to identify an excluded output; retain known artifact IDs and export lineage. Production defaults should not inherit a trial's exclusion accidentally.

For the first implementation, support explicit bounded imports from versioned export manifests and ordinary reading of evidence companions. Each imported identity or assertion retains its prior-run provenance through prior_run_imports and the update packet. Unavailable original snapshots or incomplete export provenance remain visible limitations. A global cross-run knowledge service can be considered later without changing per-run accountability.

## 10. Implementation sequence and acceptance

This section records the dependency order and acceptance conditions. G0–G6 have source implementations and focused validation; G7 is exercising the complete workflow with real delegated agents before final release review. The existing recorder, budget policy and completed research trials form the baseline. Trial failures, corrections and source-version changes are retained separately, so a repaired continuation cannot be mistaken for a clean run of one candidate.

### 10.1 Dependency order and deliverables

| Increment | Depends on | Deliverable | Exit check |
| --- | --- | --- | --- |
| G0. Freeze contracts | Accepted design | Complete v1 schema, state transitions, packet formats, CLI response shapes, compatibility mapping and synthetic fixtures | Every required record, relationship and failure state has a defined representation; command and worker contracts agree |
| G1. Build durable storage | G0 | Schema migrations, identity keys, transactional ingestion, event history, idempotency, checkpoints and recovery | Concurrent updates, conflicting batch IDs, alias cycles and crash/replay cases pass |
| G2. Ingest evidence and follow-ups | G1 | Automatic search-result and reader-return ingestion; captures, inspections, reference scans, assertions, leads and pending work obligations | Every returned candidate, reference and lead is retained with provenance; incomplete legacy writes remain detectable |
| G3. Query and reconcile | G2 | Artifact/neighborhood/frontier/coverage/evidence/change queries; role and relevance views; scoped relationships, claims, contradictions and reconciliation | Bounded queries expose unread Trunk roots, unqueried terms and disagreements; scoped decisions retain their limits |
| G4. Coordinate execution | G2 and G3 | Claims and leases, nested actor assignments, balanced waves, saved-plan bindings, budget admission and operation recovery | Ordinary work is not duplicated; nested workers share limits; partial failures and depth limits preserve the frontier |
| G5. Export, migrate and reuse | G3 and G4 | Generated ledgers and companion inventories; provenance manifests; read-only historical migration; bounded prior-run Trunk imports | All views agree at one graph sequence; legacy limitations survive; reuse neither invents fresh reads nor double-counts evidence |
| G6. Integrate the skill | G3 through G5 | Updated workflow guidance, assignments, return examples, review passes, stopping rules and recovery instructions | Agents can execute the full loop through documented commands without manually copying records between inventories |
| G7. Validate and release for review | G1 through G6 | Deterministic and agent-level tests, independent review, fresh research trial, separate reuse trial and final delivery checks | Structural, behavioral, discovery and editorial results are reported separately; required checks pass before publication |

Keep the existing research_bundle.py entry point and introduce small storage/schema and query/ingestion modules behind it. Keep provider access in adapters. Avoid adding external Python dependencies for the store.

G0 must settle bootstrap creation, transaction boundaries, foreign-key enforcement, canonical redirects, unresolved-reference resolution and expected-revision conflict handling. Also specify how graph records refer to the existing contract, saved plan, immutable returns and budget journal. The representative SQL below is a starting contract, not the complete migration.

G2 must create follow-up obligations during ingestion, before prioritization. G4 decides when those obligations become active assignments. This separates retaining a lead from authorizing another provider call, and prevents either manual omission or uncontrolled automatic expansion.

### 10.2 Ownership and integration checkpoints

The root implementation coordinator owns the interfaces, integration branch, skill changes, acceptance evidence and final status. Storage and workflow contributors may work in parallel after their shared contracts are fixed. Assign separate file ownership and require an integration checkpoint before dependent work consumes a changed interface.

- A storage contributor owns migrations, packet ingestion, identity, events, recovery and storage tests.
- A workflow contributor owns queries, assignments, plan/budget integration and generated exports against the accepted storage interface.
- A validation contributor owns adversarial fixtures, compatibility/reuse cases and trial instrumentation, with independent reviewers checking consequential boundaries.

These are responsibilities rather than a requirement to launch a fixed number of agents. Use the available runtime slots, batch source-reading assignments and keep a single integration owner. Nested research workers share the same graph and budget; implementation workstreams do not create competing research databases.

Review the G0 contracts before dependent implementation, the G2/G4 recovery and admission boundaries before live provider calls, and the integrated G5/G6 behavior before the full trial. Review must examine source meaning and missed leads as well as schema validity.

### 10.3 Skill and compatibility changes

Update the existing skill and its evidence-records, bundle-procedure, reading-and-expansion, research-plan, discovery-waves, research-budgets and intake-and-assignments references together. Add focused graph storage/interface guidance only where it avoids duplicating the main workflow.

The instructions must make Content, Critical/Review and Trunk overlapping roles; default toward reading plausible in-scope organizing material; index exposed references; promote newly discovered terminology; and preserve observed references separately from semantic assertions. Coverage review examines the domain and unfinished frontier independently of the brief's conclusions.

Replace hand-maintained source/reference/work inventories with graph queries and generated views. Result indexing and reader returns trigger ingestion automatically. Delegation instructions explain actual child agent processes, assignment packets, shared storage, child registration, the return contract and the authority to propose versus reconcile changes.

During migration, import existing ledgers and returns as legacy-attested records with their original hashes. Do not infer absent links, reads, roles or semantic edges. A compatibility export preserves the existing validator's schema; it does not make newly introduced checks retroactively true of old research. Once graph mode is enabled for a run, its exported ledger is read-only output.

G5 also defines consistent checkpoint export/import and pending-packet recovery. Preserve old bundles unchanged and migrate into a new destination with an import receipt. Future investigations can use prior companions or bounded graph exports as Trunk sources, subject to the explicit reuse/exclusion policy in their contract.

### 10.4 Validation sequence and full retest

Run focused tests with each increment. Run the repository's required suites and skill validation at the integrated release checkpoint, then repeat only checks affected by subsequent fixes. Keep synthetic fixtures portable and live research records outside the repository.

1. Exercise identity, transactions, crash recovery, state transitions and concurrent claims on deterministic fixtures. Include thousands of metadata candidates with a much smaller inspected set to test bounded query/export behavior.
2. Exercise the complete agent protocol on a fixed fictional corpus: fresh coordinator, nested workers, automatic ingestion, term and child follow-ups, relevance reassessment, interruption/resumption and export. Verify actual tool/return evidence rather than role-played delegation.
3. Run the seeded selection/reading diagnostic on a frozen result set. Preserve initial selection before assigning any omitted source directly, so discovery, selection, reading usefulness and synthesis use remain distinguishable.
4. Run a fresh full investigation with the parent acting as user proxy, a fresh research coordinator and nested researchers. Preserve the agreed source scope and caps. Exclude earlier outputs and withheld benchmark identities from worker input when measuring independent discovery. Freeze the evaluation criteria before the run.
5. Run a separate overlapping investigation that is explicitly allowed to use a prior companion and graph export as Trunk resources. Assess discovery benefit, provenance, current-scope revalidation and original-source deduplication. Do not use this arm to claim independent rediscovery.
6. Review rejected leaves and pivotal passages independently; validate claim support and contradictions; then complete the prose, layout, publication and native-readback checks. Retain distinct results for each test stage.

The full trial retains the existing 250 search / 400 read / 200 assessment limits, 120 active research minutes, 300 aggregate agent-minutes, three follow-up generations and bounded retries. Preserve baseline breadth before deeper expansion. Synthesis and delivery have their separate allowance. Record any intervention, cap change or early stop with its reason instead of silently changing the trial.

Measure returned candidates, inspected artifacts, reference occurrences, promoted terms, pending/completed/deferred work, branch and generation coverage, cap use, query/ingestion overhead and unfinished frontier. Separate retrieval durations from attested reading/assessment intervals; report available average, tail and worst-case timings without treating actor labels or intervals as authenticated runtime telemetry.

### 10.5 Completion and release conditions

Completion requires the implemented commands, schema and skill examples to agree; no dropped returned leads or invented read states; consistent generated inventories; recoverable interruption behavior; and reviewed trial evidence. An unresolved consequential frontier must remain visible with its bound. Sufficient citations in a brief do not satisfy coverage acceptance.

Publish the completed, checked implementation for review through the existing feature-branch workflow. Preserve the previously authorized review destination; landing and managed activation remain separate actions. Report what was implemented, which tests and trials ran, discovery/selection misses, interventions, remaining limits and the exact source revision used for evaluation.

### Focused validation cases

- Two search routes discover the same external identity: one artifact, two discovery records.
- Two same-title documents have different provider identities: two artifacts.
- Repeated ingestion returns the original result; different contents under the same batch ID fail.
- A worker crashes before/after each file, transaction and budget boundary: recovery retains evidence and prevents accidental duplicate dispatch.
- A reference appears in two sections and two versions: preserve all occurrences while reusing the target.
- A mention is later resolved, and then corrected: preserve the original wording and resolution history.
- A reader calls a source irrelevant and a reviewer finds it organizing relevant work: retain both assessments and reopen its follow-ups with reasons.
- A review approves one section but leaves another open: claim and relationship queries preserve that scope.
- A failed fetch and a successful metadata fetch do not establish substantive inspection.
- Concurrent coordinator changes to the same disposition detect revision conflicts.
- A stale lease with an unfinished operation cannot trigger an automatic duplicate call.
- New graph paths do not lower previously assigned budget generations.
- Budget stops preserve incomplete breadth and descendants, while exports and delivery remain possible.
- A snapshot export, event replay and checkpoint backup yield equivalent consolidated records.
- A prior graph and a fresh search point to the same original: preserve both discovery paths without crediting a new inspection or independent corroboration.
- An excluded trial output reappears through another prior inventory: lineage filtering exposes it rather than contaminating the independent-discovery test.

Behavioral evaluation must separately ask whether agents extract the relevant terminology, select plausible Trunk/Content/Critical records, pursue returned leads, assess relevance correctly and synthesize evidence faithfully. Add independent samples of rejected leaves and inspected pivotal passages. Structural validation alone cannot answer those questions.

## 11. Representative SQL contracts

These excerpts specify key constraints. They are not the complete migration. Supporting tables, foreign keys, vocabulary validation and transition guards described above must be included in implementation.

```sql
CREATE TABLE artifact_keys (
  namespace TEXT NOT NULL,
  external_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
  basis TEXT NOT NULL,
  created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id),
  PRIMARY KEY (namespace, external_id)
);

CREATE UNIQUE INDEX one_active_assignment
ON assignments(work_id)
WHERE state IN ('leased', 'running', 'reconciling');

CREATE UNIQUE INDEX known_provider_revision
ON artifact_versions(artifact_id, provider_revision)
WHERE provider_revision IS NOT NULL;

CREATE INDEX reference_children
ON reference_occurrences(from_artifact_id, target_artifact_id);

CREATE INDEX pending_work_by_wave
ON work_items(state, stage, generation, branch, priority);

CREATE INDEX evidence_by_assertion
ON assertion_evidence(assertion_id, locator_id);
```

Views resolve canonical IDs and current reference resolutions, and expose reconciled assessments without hiding disagreement. Querying effective_reference_targets therefore includes both direct resolved links and later-resolved mentions. Missing targets stay visible in an unresolved-reference view.

```sql
-- Relevant organizing artifacts whose exposed targets still need inspection.
-- These views incorporate canonical identity and current scoped coverage.
SELECT DISTINCT r.from_artifact_id, r.target_artifact_id, r.reference_id
FROM effective_reference_targets AS r
JOIN artifact_question_assessments AS a
  ON a.artifact_id = r.from_artifact_id
JOIN artifact_roles AS role
  ON role.artifact_id = r.from_artifact_id
WHERE a.question_id = :question_id
  AND a.relevance = 'relevant'
  AND role.role = 'trunk'
  AND (role.question_id IS NULL OR role.question_id = :question_id)
  AND NOT EXISTS (
    SELECT 1 FROM sufficient_inspection AS i
    WHERE i.artifact_id = r.target_artifact_id
      AND i.question_id = :question_id
      AND i.required_scope_key = :scope_key
      AND i.format_policy_sha256 = :format_policy_sha256
  )
ORDER BY r.from_artifact_id, r.target_artifact_id
LIMIT :page_size;
```

The helper also returns readability and work dispositions for each target. The query above identifies missing inspection; it does not itself authorize retrying blocked sources. Sufficient inspection means the explicitly requested scope was covered under the current format policy, not that a document has any successful read operation.

## 12. Example update packet

The following is an illustrative reader delta. The schema must reject unknown IDs and enforce that its cited locator belongs to the inspected capture. All IDs are fictional.

```json
{
  "schema_version": 1,
  "batch_id": "reader-2-return-17",
  "actor_id": "reader-2",
  "assignment_id": "assignment-42",
  "reader_return_id": "return-17",
  "observations": [
    {
      "kind": "role",
      "subject_artifact_id": "artifact-17",
      "predicate": "has_role",
      "value": "trunk",
      "evidence_locator_id": "locator-17-4"
    },
    {
      "kind": "relationship",
      "subject_artifact_id": "artifact-17",
      "predicate": "organizes",
      "object_artifact_id": "artifact-28",
      "basis": "explicit",
      "evidence_locator_id": "locator-17-4"
    }
  ],
  "lead_proposals": [
    {
      "returned_lead_id": "guidelines-review",
      "kind": "observed_gap",
      "original_value": "Guidelines review",
      "origin_locator_id": "locator-17-4",
      "question_id": "rationale",
      "reason": "Named review is not yet identified or inspected",
      "suggested_action": "Resolve the named review, then assess it"
    }
  ]
}
```

The helper registers the observations and a pending lead/work obligation together. It does not automatically accept the relationship as final, treat the suggested action as executed, or start a new agent. The coordinator can then schedule it alongside other branches, and every subsequent read, interpretation and disposition retains the original path.

## 13. Existing implementation references

- [Research workflow](../assets/agent-assets/skills/retrospective-research/SKILL.md)
- [Evidence records](../assets/agent-assets/skills/retrospective-research/references/evidence-records.md)
- [Captured evidence and acceptance](../assets/agent-assets/skills/retrospective-research/references/bundle-procedure.md)
- [Reading and reference expansion](../assets/agent-assets/skills/retrospective-research/references/reading-and-expansion.md)
- [Research plan](../assets/agent-assets/skills/retrospective-research/references/research-plan.md)
- [Research budgets](../assets/agent-assets/skills/retrospective-research/references/research-budgets.md)
- [Current bundle helper](../assets/agent-assets/skills/retrospective-research/scripts/research_bundle.py)
- [Current budget recorder](../assets/agent-assets/skills/retrospective-research/scripts/research_budget.py)
- [Graph v1 contract](../assets/agent-assets/skills/retrospective-research/references/graph-contract.md)
- [Executable graph schema](../assets/agent-assets/skills/retrospective-research/scripts/research_graph_schema.sql)

The original recorder retains immutable operations, reader returns, saved plans and locked budget admission. Graph mode extends it through validated storage and work commands. Its ledger, source/reference inventory and worklist are generated views of one accepted graph sequence. The original JSON workflow remains available for older bundles; migrating one creates a separate destination.

Graph-mode planning validates the exact registered export payload. A new plan needs a current research snapshot; rendering or re-registering a hash-bound historical plan may use its older snapshot. Only the first pending, seed-only inventory can be supplied by hand. Export registration itself does not make the represented research stale.

Generated inventories point to existing graph provenance with `graph:ARTIFACT_ID` when no indexed provider result exists. Reference discoveries and prior imports must not become supplied seeds through a plan/export round trip. Plan registration therefore never creates seed discoveries from generated inventories, including authenticated older exports with synthetic `seed:graph-*` labels; their original history remains retained.

Synthesis checks scoped source outcomes, work, operations, questions and both link and terminology obligations. Explicit bounds preserve incomplete research. A separate retained semantic-review file names the generated ledger hash and represented graph sequence, plus its reviewer and rationale. This is an explicit review attestation, not proof of identity or correctness; it does not modify the immutable inventory. Published briefs still require evidence, prose, citation and rendered artifact review.
