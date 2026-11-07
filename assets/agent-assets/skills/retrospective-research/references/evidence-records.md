# Search, Source, And Evidence Records

Use the shared [graph workflow](graph-workflow.md) for delegated or multi-wave
investigations; smaller tasks may use tables, structured records or linked
sections. In graph mode, ledgers and inventories are generated, read-only views
of one recorded sequence. Stable IDs connect records without relying on citation
numbers that change during editing.
When the user asks for a table, preserve that requirement in the delivered
companion; an internal JSON inventory is not a substitute.
The [research plan](research-plan.md) and its generated working document connect
planned coverage to these records. One source inventory drives reading and
analysis; do not create separate candidate lists for each stage.

## Search Log

Record each executed query or distinct search operation with:

- Question, source/tool, exact terms and filters, and search time.
- Result scope: pagination completed, caps, date coverage, or indexing limits.
- Candidate source IDs, meaningful exclusions, and errors or unavailable routes.
- Assigned plan-item ID and snapshot, so intended work can be reconciled with execution.
- Selected artifact/location/route branch and query mode, with the input kind.
  Retain every discovery path when several searches return the same stable source ID.
- Record source-derived vocabulary, record-neighborhood leads and their assigned
  follow-ups or explicit bounds in the same inventory. Preserve original wording
  and locator; an observed term is not automatically a verified project alias.
- Retain research stage, branch, generation, parent operation and retry chain.
  Attempts consume the shared research allowance even when they fail or are later
  rejected as evidence. Separate research spending from synthesis and publication.

Group repeated operations when the actual searches remain recoverable. Keep
planned searches separate from executed ones. Distinguish no results from not
searched, inaccessible, discovered but unread, and read but not used.
Retain the request, timestamp, returned IDs and permitted raw output during the
operation. Preserve partial output and errors; record a rerun as a new retrieval.
Use [the bundle procedure](bundle-procedure.md) for capture and acceptance checks.

## Source Inventory

Include all plausible candidates, not only sources cited in the brief. Useful
columns are **ID; title/link; date/version; contributor basis; relevance; read
coverage and reader(s); one-line finding; limitations or exclusion reason**.
Also record analysis state and disposition: pending, used, contextual, excluded,
or blocked, with reasons. A saved response is only a capture; record an inspection
after someone actually reads or views it, and analysis after they interpret it.
Unread candidates need an honest relevance note, not an invented finding.
Keep readability/retry state separate from relevance: access blocked, unreadable,
transient retry pending, retry exhausted and budget/depth deferred are not synonyms
for irrelevant. Record a substantive relevance finding only for the actual inspected
extent; metadata-based triage remains labeled as such.

Use stable provider IDs and precise locators where available. Distinguish copies,
revisions, exports, and summaries of the same original from independent sources.
Record the event date separately when a source describes an earlier event.
Keep same-title records with different IDs distinct. Reconcile proven aliases
with a recorded basis, preserving original keys, occurrences and spent operations.

Contributor basis should say what was observed: named author, revision author,
commenter, credited collaborator, or a participant's account. Ownership and shared
access alone leave contribution unconfirmed. Preserve uncertain matches rather
than silently promoting them to verified authorship.

Read coverage is a description, not a single boolean. For example:

- Discovered from title/metadata; body unread.
- Search snippet or preview only.
- Body sections named; remaining sections unread.
- Full extracted body; comments and embedded visuals uninspected.
- Slides or visual frames inspected, with page/node range and method.
- Comments or revisions inspected, with scope and relevant dates.
- Blocked, with observed failure and any known cause.

In a graph read plan, use an explicit structured extent. A whole-body request
`{"body":"all"}` is satisfied only by a complete captured component with numeric
bounds, completeness attestation and limitations, plus actual inspection of all
those bounds. Preserve both requested and inspected scope; prose such as “full
body” or a successful export alone does not establish it. See the
[capture fields](graph-workflow.md#capture-index-and-actually-inspect).

Identify who actually inspected the source, separately from who captured its
output or maintains the inventory. Check reader and operation-actor labels
against assignments and researcher returns; a shared recorder's default label
does not establish identity. If a captured label is wrong, retain the original
receipt and explain the attribution discrepancy and supporting basis in the
companion. Newly attributed observations still need ordinary validated records;
structural return correction cannot change actor, assignment or receipt.
Do not rewrite retrieval history to conceal the error.

Record headings or a section map when needed to show what selective extraction
could miss. Text extraction, image inspection, node parsing, and caption reading
provide different evidence. A root frame listing does not establish full board
coverage; a rendered screenshot does not establish interactive behavior.
Retain the [reference scan](reading-and-expansion.md) for each analyzed source:
capture scope/method, outgoing artifact references and their assessed dispositions.
Add linked artifacts to this inventory before choosing follow-up reads. Preserve
each referring source and locator; deduplicate targets, not discovery paths.
Connect useful references and unlinked vocabulary to the existing source leads
and planned work. This surrounding record chain may include the original proposal,
prototype, review, feedback, implementation or outcome measurement. Account for
exclusions and unavailable link metadata; resolve or explicitly bound consequential
leads during wave reconciliation.
Retain the original structured reader return and stable returned-lead IDs. Map
them to these consolidated leads and dispositions, so a coordinator's omission
can be detected without reconstructing the return from a later summary.
If an original return cannot normalize, its
[rejection and linked correction](graph-workflow.md#recover-originals-without-repeating-research)
retain the original file, failure, hashes and full reference/lead inventory.
Only graph-verified correction lineage can satisfy the original return mapping;
it adds no new inspection credit, refreshes no date and refunds no budget spend.
Pending or rejected originals and all unresolved question scopes stay visible.

## Evidence Ledger

Keep two kinds of connection separate. An **observed reference** records what
linked or named another artifact, its original wording, and where it appeared.
A **semantic relationship** records an assessment such as organizes, reviews,
challenges, approves, implements, tests, revises or supersedes, with its scope,
evidence and basis. A link alone never establishes approval or implementation.
Explicit source statements and analytical inferences retain different labels,
including after coordinator acceptance. Corrected resolutions and disputed
assessments preserve earlier observations.
Structural correction retains assertion fields as proposed historical content;
it is not renewed agreement with a finding. Record known-wrong interpretations
with `challenged` or `withdrawn` assertion decisions. Record semantic replacements
as new assertions and an explicit `superseded` decision linking the replacement,
while preserving original evidence and disagreement.
For mixed approvals, put the approved and deferred behaviors in their respective
section/question scopes. Do not turn one section's approval into a whole-source
decision. Preserve both incoming and outgoing observed references; an inferred
backlink is not an observed reference.

Use the network to expose holes: an uninspected review of a pivotal proposal,
an implementation without located outcomes, or an organizing record whose
children remain unread. These are research leads, not facts that missing records
must exist. A coverage check examines these holes independently of the brief.

For each material claim, retain:

- Stable claim ID and the assertion, with its temporal and comparative scope.
- Source ID, version/date, quotation or faithful excerpt, and precise location
  such as section, page, slide, node, comment, timestamp, or repository revision.
- Basis: documented fact, participant account, inference, or recommendation.
- Supporting and conflicting evidence, limitations, and unresolved questions.
- Where the claim is used in the brief or companion.

Distinguish what a source says from what it demonstrates. An approval record can
establish a decision without establishing implementation; a metric target is not
an achieved result. A user's recollection can add evidence without becoming an
independently verified fact. Preserve competing accounts and explain the limits
of any reconciliation.

Check numerical units, population, denominator, task, and measurement period.
Before using a comparison, record which components differ and which remain fixed.
Avoid extending component evidence to a whole-system result without identifying
that inference. Repeated claims copied between reports count as one evidence chain.

## Retain And Recheck

Keep sufficient excerpts, locators, and analysis in the authorized evidence
companion or supporting bundle to survive loss of temporary working files. Link
to originals; retain snapshots only where permitted and useful. Match storage
and sharing to the source restrictions and intended audience.

When research or wording changes, update affected claims, citations, source
coverage, and methodology together. Preserve uncertainty if a previously cited
source is no longer accessible; an old summary is not a fresh verification.
Audit the final companion against actual reads and tool outputs before claiming
coverage or completion.

Prior companions and bounded graph exports can seed later investigations as
Trunk sources. Preserve their source IDs, original-run lineage and historical
limits. Reassess pivotal material for the new question and freshness policy;
reuse does not create a new inspection or independent corroboration of copied
primary evidence. Export and import through the helper so these distinctions
survive future reuse.
