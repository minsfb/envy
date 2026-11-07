# A Living Research Plan

Keep a durable plan before substantial searching. Use it to choose and execute
bounded work, then revise it as sources reveal earlier names, missing periods,
related prototypes, review discussions, and competing explanations. It is not a
fixed outline for the eventual brief. The coordinator owns plan changes; assigned
researchers can capture independent operations against retained plan snapshots.
In [graph mode](graph-workflow.md), the saved plan remains the execution contract;
the graph records current work, ownership and evidence. Draft plan changes from
the generated worklist and retain them with the original `plan` command. The
generated ledger is read-only, not a second source of research decisions.

For new multi-wave work, include the [artifact/location/route mapping](search-routes.md)
and enable both `research_plan` and `search_routes` in the contract requirements.
Also enable `discovery_followups` and use [discovery waves](discovery-waves.md)
for source-derived leads, inspected-read provenance and coverage checkpoints.
Enable `research_budget` and `protected_followups` for the shared reserves and
[allocation/stop checks](protected-followups.md).
The minimal example below demonstrates the base plan format; the route reference
shows its additive fields and checks. Existing bundles retain their original
requirements and receipts; do not fabricate earlier route assignments.

Describe the questions, terms and aliases, source surfaces/providers, people,
periods, and work types worth covering. Include prototypes, experiments, reviews,
organizing indexes/trackers, and surrounding discussions when they bear on the question. Explain deliberate
omissions. Coverage entries group useful combinations; do not generate every
possible combination or treat a query count as completeness. Each executable
work item specifies one concrete search or read, its assignment, priority, and
reason. Add separate items for additional pages, changed queries, and new reads.

Translate the important combinations into actual requests: for example, a topic
with review, prototype, experiment, or measurement terms, alongside precise
project/person searches. Include a broader route without person or current-project
restrictions where useful and supported. Give omitted combinations a scope reason.
Following known links can reveal surrounding records, but does not establish that
unlinked records were sought. At coverage checkpoints, compare executed query
patterns with the planned dimensions, not just whether work items say `done`.

## Enable And Save

Use the [evidence bundle procedure](bundle-procedure.md) for the contract,
capture, inventory, and artifact checks. Add `"research_plan": true` inside the
contract's `requirements`. This capability is additive: existing bundles without
it retain their earlier behavior. When enabling it in an existing bundle, retain
old receipts; only operations whose original contract required a plan need prior
plan bindings. Use graph migration for an old bundle, preserving missing scope
and provenance rather than inventing historical assignments or inspections.

For the fictional `rationale` question and `local` provider in that procedure,
start with an intake ledger `{"sources": []}` and this base plan. Add the route
and wave fields from the linked references when their requirements are enabled:

```json
{
  "version": 1,
  "owner": "coordinator",
  "coverage": [{
    "id": "historical-records",
    "questions": ["rationale"],
    "terms": ["Harbor", "Quay", "reconnect"],
    "providers": ["local"],
    "people": ["named authors and reviewers in supplied records"],
    "periods": ["2021–2024"],
    "work_types": ["proposal", "prototype", "experiment", "review", "decision"],
    "reason": "Supplied body exports and review records; referenced boards require a separate read route"
  }],
  "items": [{
    "id": "older-name",
    "coverage": ["historical-records"],
    "kind": "search",
    "actor": "history-researcher",
    "provider": "local",
    "priority": "high",
    "status": "queued",
    "reason": "Look for the deferral under its earlier project name in the supplied records",
    "request": {"query": "Quay", "filters": {}, "cap": null, "cursor": null},
    "graph": {"allocation": "baseline"}
  }]
}
```

```sh
python3 /skill/scripts/research_bundle.py plan /owned/run --plan /owned/plan-draft.json --ledger /owned/ledger.json
```

The result reports `plan_sha256`. The helper saves `plan.json`, a generated
readable `plan.md`, and immutable `plans/<hash>.json` snapshots with automatically
retained `plans/<hash>.ledger.json` inventory checkpoints. Its `_record` metadata
binds save time, prior snapshot, contract, and ledger hash. Keep editing a draft
and use `plan` to save; do not modify retained snapshots or `plan.json` directly.
The Markdown view is generated from these records, not a second manual worklist.
The first save can register supplied seeds with exact `seed:` provenance.
Subsequent graph-mode saves use a fresh `graph export` ledger. `plan` automatically
registers its immutable snapshot and synchronizes amended contract questions;
`graph register-plan ROOT --sha256 HASH` replays interrupted registration without
resaving or rewriting the original. Save a new plan after a contract amendment
before assigning work under its changed questions.

Coverage `questions` and `providers` must reference the contract. Every contract
question needs coverage. The `terms`, `people`, `periods`, and `work_types` arrays
can be empty if the coverage rationale explains why. Name distinct source
surfaces in coverage entries and their reasons: for example, document bodies,
comments, revisions, review threads, slides, and linked visual artifacts. A
provider's presence does not establish that all its surfaces were searched.

## Execute The Worklist

1. **Claim, then capture a planned search.** Use `graph next-work` and the
   claim/start requests in the [graph workflow](graph-workflow.md). Add the
   returned assignment, `plan_item` and `plan_sha256` to the operation:

   ```json
   {
     "id": "history-search-01",
     "actor": "history-researcher",
     "kind": "search",
     "provider": "local",
     "plan_item": "older-name",
     "plan_sha256": "REPLACE_WITH_RETURNED_SNAPSHOT_HASH",
     "graph_assignment_id": "REPLACE_WITH_CLAIMED_ASSIGNMENT",
     "research": {"stage":"baseline", "branch":"local", "allocation":"baseline", "purpose":"research", "parent_operation":null,
                  "root_basis":"Search the supplied historical records under the older name"},
     "request": {"query": "Quay", "filters": {}, "cap": null, "cursor": null},
     "command": ["rg", "-n", "Quay", "/owned/records.txt"],
     "cwd": "/owned"
   }
   ```

   Use `capture`, or `begin` before a native tool call followed by `finish`, then
   `index` the actual result IDs and pagination as in the bundle procedure.
   Actor, provider, kind, and exact request must match the saved assignment.
   The snapshot must precede execution. A retained older snapshot works while
   the same current item remains queued/active, allowing other work to evolve.
   A current closed item cannot execute via a stale snapshot.

2. **Index every returned asset.** The result normalizer registers all declared
   IDs and discovery occurrences, including exclusions, duplicate routes, unread
   candidates and blocked assets. Use stable provider identity where known;
   title equality is not identity. A successful retrieval is not an inspection.
   A generated compatibility-ledger entry has fields such as these; do not edit
   the export to supply a finding or read state:

   ```json
   {
     "id": "record-7",
     "title": "Release review",
     "url": "https://example.invalid/record-7",
     "discovered_by": ["history-search-01"],
     "reads": [],
     "coverage": "Title and search result only; body unread",
     "finding": "Potential decision record; not assessed",
     "contributor_basis": "Named reviewer in search metadata; contribution unverified",
     "inspections": [],
     "analysis": {"status": "pending", "reason": "Body not yet inspected"},
     "disposition": {"status": "pending", "reason": "Potentially decisive source"},
     "leads": []
   }
   ```

3. **Save a planned read.** Retain `older-name`, change its status to `done`
   with an execution/coverage reason, and append a read item:

   ```json
   {
     "id": "read-review",
     "coverage": ["historical-records"],
     "kind": "read",
     "actor": "history-researcher",
     "provider": "local",
     "priority": "high",
     "status": "queued",
     "reason": "Inspect the potential deferral record and its surrounding links",
     "request": {"source_id": "record-7", "format": "text", "extent": "entire supplied body; note unavailable linked surfaces"},
     "graph": {"extent": {"body": "all"}, "allocation": "baseline"}
   }
   ```

   Save using `plan` with the latest generated ledger, then claim/start the
   registered read. Capture and index it using the new snapshot and assignment.
   Every new graph read item requires a structured `graph.extent`. Request known
   numeric bounds such as `{"body":{"unit":"line","ranges":[{"start":1,"end":20}]}}`,
   or `{"body":"all"}` for the complete captured body. The latter requires a
   numeric complete-capture attestation and full numeric inspection; see the
   [capture and return example](graph-workflow.md#capture-index-and-actually-inspect).
   Other `graph` annotations can specify formats, version/freshness, question
   subset and parent operation. `plan` preflights the projection before immutable
   saves, including seed identities and linked references. Fix an invalid draft;
   do not edit an already saved snapshot. Historical graph-bound plans retain
   their original scope semantics when replayed.
   The default freshness policy requires captures after the assignment; an exact
   known graph version can be requested instead. Preserve the provider's actual
   fidelity requirements in the requested formats and scope policy.

4. **Inspect, then analyze.** Submit the actual reader's immutable `reader-return`,
   including its graph assignment, inspected capture extents, matching question/
   scope/format-policy fields, source-grounded assessments and limitations.
   Capture actor and reader may differ. Add findings and evidence as proposed
   scoped assertions; coordinator reconciliation decides shared claim state.
   The generated ledger projects these records. A plan `done` label establishes
   execution; graph `complete` additionally requires actual matching inspection
   and source freshness. Neither label establishes independent semantic review.

5. **Index references, then follow the surrounding source chain.** Retain the
   source's [reference scan](reading-and-expansion.md), including coverage and
   limits. The graph retains declared reference/lead obligations before selection.
   Unresolved names stay visible without inventing an identity.
   Assess prototypes, prior/later reviews, comments and conflicting accounts.
   When a lead needs a search or read, append a concrete item with an `origin`:

   ```json
   {"source_id": "record-7", "read_id": "history-read-01", "locator": "Related prototype section"}
   ```

   `read_id` is optional for a metadata/snippet lead; if supplied it must be a
   successful indexed read of the origin source. In graph mode also identify
   the normalized `lead_id` or `reference_id` and `parent_operation` in the item's
   `graph` annotation. This connects all question-specific work origins to the
   pending obligation. For example, the generated ledger may project:

   ```json
   {"id": "prototype-link", "locator": "Related prototype section", "reason": "May establish what was actually exercised", "plan_item": "find-prototype"}
   ```

   A saved destination keeps the same origin source and locator. Before assignment,
   a generated lead may instead carry verified `graph_lead_id` and
   `graph_obligation_id`; retain it as pending rather than inventing a plan item.
   Every known question eventually needs matching work or an explicit bound.
   Equivalent work can serve several paths while retaining every origin; one
   completed scope cannot hide another. Save the revised plan with the generated
   ledger. Do not drop prior sources, leads, work items or coverage entries.

## Revise And Reconcile

Work priorities are `high`, `normal`, or `low`. Status is `queued`, `active`,
`done`, `bounded`, or `dropped`, always with a reason. In ordinary recorder work,
`done` requires a successful indexed operation. Graph-enabled work additionally
requires verified completion for its assignment and scope. A graph read can use
an exact, pinned native retained capture with a new, sufficient, verified return
for the current assignment; it does not require another provider call. Imported,
withdrawn, or unverified evidence cannot establish current completion. See
[graph completion and retained reads](graph-workflow.md).

`done` does **not** mean an uncapped search, full read coverage, or exhaustive
discovery; the view shows actual requests, caps, cursors, result IDs, pagination
states, receipt links, and observed notes. Record failed attempts as receipts;
they may justify `bounded` with a specific access or budget limit.
Use `dropped` with the scope decision, not to erase an inconvenient lead.

Work definitions stay fixed: assigned actor, provider, kind, exact request,
coverage references, and origin. For a revised query, another page, reassignment,
or new read, add a new ID and explicitly bound/drop the old item if needed.
Status, priority, and reason can evolve; reopen an unchanged item to execute it
again. Do not rewrite old queries or receipts. One coordinator saves the plan
and reconciles shared decisions; workers submit independent immutable returns.
Short helper transactions serialize graph updates. Do not amend the contract
concurrently with captures.
If the current provider policy requires a more faithful read format, retain the
old item as closed and add a new read item. Old closed work can retain its format;
new execution and the current supporting reads must satisfy the current policy.

Source analysis status is `pending`, `complete`, or `excluded`, with a reason.
Disposition is `pending`, `used`, `context`, `excluded`, or `blocked`, with a
reason. Metadata can justify an exclusion without inspection; label that basis.
A blocked source may remain unread with pending analysis and an explicit blocked
disposition. These states describe different things and must not be collapsed
into a single “read” checkbox.
These compatibility fields are projections in graph mode. Record their evidence
and scoped decisions in the graph, then export; do not mark the ledger by hand.
Ordinary same-source reads use a stable logical purpose/scope rather than a plan
row ID. Explicit independent reviews have a different purpose. A newly queued
item that deduplicates to terminal work requires explicit changed-basis reopening
when more execution is needed; it does not silently reset old coverage or budget.

Before synthesis, reconcile the questions with actual execution, source states,
and surrounding leads. Resolve high-priority queued/active items by executing
them or recording justified limits/scope decisions. Give every source an
intentional disposition. The helper rejects pending high-priority work, pending
source disposition, complete analysis without inspection, unsupported done work,
unknown references, and claims relying only on uninspected captures. The existing
claim checks also require completed analysis of their inspected source evidence.
Question/claim checks still apply; a bounded central gap needs executed follow-ups.
Human or agent review must judge whether the chosen searches and limits actually
address the question. Demoting work or writing a reason is not proof of adequacy.
With `discovery_followups`, the same source leads carry observed vocabulary and
neighborhood work, explicit disposition and question relevance. Analyzed sources
record expansion assessments; append coverage checkpoints after useful waves and
before synthesis. See the linked reference for the additive fields and checks.

```sh
python3 /skill/scripts/research_bundle.py graph export /owned/run --id coverage-checkpoint
python3 /skill/scripts/research_bundle.py plan-view /owned/run --ledger /owned/run/graph/exports/coverage-checkpoint/ledger.json
```

The view refreshes from current receipts and ledger. Omitting `--ledger` uses the
last saved inventory checkpoint; supply a fresh generated ledger if the graph has
advanced. Save substantive plan/checkpoint changes, then export the resulting
sequence for final review; rendering does not require another plan save.
Keep `plan.md` and its bundle at the authorized
durable location; do not depend on temporary extraction directories.

## Bind The Delivered Companion

Include a usable account of plan coverage, completed work, and remaining threads
in the evidence companion. Review its placement with the artifact/provider tools
after refreshing `plan.md`. Add a `plan_review` object to the publication manifest:

```json
{
  "reviewer": "coordinator",
  "note": "Verified the companion reproduces current coverage, executed work and bounded prototype lead",
  "companion_locator": "Research coverage and remaining leads",
  "plan_view_sha256": "REPLACE_WITH_PLAN_VIEW_HASH"
}
```

`publication` regenerates the view, requires that reviewed hash to match, and
reports plan and view hashes with its artifact checks. This placement review is
an explicit attestation; it does not make the helper understand the companion.
Graph-mode synthesis and publication also take `--semantic-review` with the
separately retained passage-review file bound to that export's canonical ledger
hash and represented sequence; see [the final graph review](graph-workflow.md).
Actor identity, search adequacy, truthful extraction, semantic support, and
readable/attractive document design remain independent judgments. Review the
actual artifacts and attribution rather than treating structural validation as
a quality or truth score.
