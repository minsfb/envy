# Protected Follow-Up Interface

Use this for new delegated or multi-wave research. Enable `protected_followups`
alongside the graph, `research_plan`, `search_routes`, `discovery_followups` and
`research_budget`. Historical runs retain their original behavior. The helper
rejects enabling protection on already unclassified spending, disabling it, or
changing shares after work starts. A reasoned increase in the global caps keeps
all prior spending and gives the same shares more capacity.

## Allocate Full Cycles

The optional top-level contract object defaults to:

```json
{
  "research_allocation": {
    "baseline_percent": 30,
    "followup_percent": 50,
    "extension_percent": 20,
    "minimum_followup_rounds": 2
  }
}
```

Positive shares total 100. Two through ten protected rounds are supported, within
the configured generation cap. Counts round down inside reserves; the remainder
joins expansion. The default time caps are 330 active minutes and 600 aggregate
agent-minutes; explicit `research_budget` values override them.

Buckets are `baseline`, `followup-1` through the configured number, and
`extension`. Generation zero uses baseline; each protected generation uses its
own bucket; deeper work uses extension. A term query and its returned-source
reads share a generation. An alternative format or reread retains ancestry.

Put `allocation` in each saved item's `graph` object. Optional
`activity_purpose` defaults to `research`. Copy those values to the operation's
`research.allocation` and `research.purpose`. They must match the active clock:

```sh
python3 /skill/scripts/research_bundle.py budget-activity /owned/run --actor reader-a --action start --allocation followup-1 --purpose research --branch older-work
python3 /skill/scripts/research_bundle.py budget-status /owned/run
python3 /skill/scripts/research_bundle.py budget-activity /owned/run --actor reader-a --action stop
```

The supervisor starts and stops a child's entire research/interpretation/return
interval. Switch labels with stop/start. Purposes are `research`, `coordination`,
`repair`, and `coverage-review`; charge coordination and repairs to the cycle
they support. Coverage-review uses extension and requires the graph actor's
`coverage_reviewer` role. This does not grant that actor wider source authority.
`budget-assess ROOT --read-id ORIGINAL_READ --actor NAME` verifies the active
read assignment and its allocation, including selected retained captures. Add
`--assignment ID` when several assignments match. Assessment can use the review
assignment's own branch; labeling an ordinary reader clock as coverage review
does not authorize spending from expansion.

Status reports guaranteed remaining reserve, shared borrowing, global capacity,
and time/counts by allocation, purpose, actor and branch. Any bucket may use its
reserve plus unused shared expansion capacity; borrowing never consumes another
unreleased reserve. Bucket active-time unions can overlap and must not be added
to claim global elapsed time. Aggregate effort counts every participating actor.
A local bucket limit is not a global research stop while funded follow-ups remain.

## Inspect The Unassigned Frontier

```sh
python3 /skill/scripts/research_bundle.py graph followup-status /owned/run
```

This derives records from the existing graph. It exposes returned terms even if
nobody created a search item, groups reference targets per question (including opaque record IDs and unresolved
title mentions),
retains original occurrence IDs, and summarizes inspected/uninspected hosts.
For known provider hosts, metadata-only candidates also create one surface
coverage item per question. A useful inspection serves that surface check; it
does not mean every candidate was read or the surface was exhaustively covered.
Optional provider `reference_hosts` lists literal lowercase hostnames to associate
those references with known routes. Unmapped hosts remain visible. A known URL
does not establish global search capability or access denial.

Promote useful items through the ordinary saved plan, with their normalized
`graph.lead_id`/`reference_id`, source/read origin and parent operation. Use
`graph next-work` to balance the available branches. Baseline and the earliest
follow-up generation can interleave. Before generation two or deeper dispatches,
earlier unassigned leads/references need work or a typed checkpoint disposition.
A search result is not a read; the cycle report requires a verified query with
selected child inspections, an actually empty complete result, or a directly
linked source inspection. Review the breadth of these cycles, not just their count.

For an unworked item, copy its current `key` and `basis_sha256` from status and
retain a source-specific decision. A review request has this shape:

```json
{
  "id": "after-first-reader-returns",
  "actor": "coordinator",
  "reason": "The older label is assigned for search; the staffing-only link is outside the outcome question.",
  "decisions": [{
    "key": "COPY_CURRENT_FRONTIER_KEY",
    "basis_sha256": "COPY_CURRENT_BASIS_SHA256",
    "disposition": "out-of-scope",
    "reason": "This reference is the index's staffing rota and cannot locate the proposal or its evaluation."
  }]
}
```

```sh
python3 /skill/scripts/research_bundle.py graph followup-checkpoint /owned/run --file /owned/coverage-review.json
```

Supported bounds: `out-of-scope`, `duplicate`, `diminishing-value`,
`budget-deferred`, `depth-deferred`, `access-blocked`, `unreadable`, and
`user-deferred`. Budget deferral requires a reached global cap. Access bounds require a retained failed read with the matching classification.
Unreadability can also use a scoped source outcome tied to a locator in actually
retrieved bytes; a successful fetch may contain unintelligible content. An unread
link alone cannot establish either. A transient error remains retry work.
Checkpoint bounds supplement the graph's per-question dispositions; settle both
through their supported commands, never by editing an exported ledger.
Scope/capability changes, new origins and reopened graph dispositions require
reconsideration. These reasons are coordinator attestations, independently
reviewed for semantic adequacy; no keyword checker proves their truth.

## Release Or Stop Explicitly

Add `"release":"followup-1"` to a checkpoint after completing or bounding its
work and accounting for its unworked leads. A release requires a verified cycle.
If earlier work is finished and genuinely provides no follow-up opportunity,
supply `"no_opportunity":{"followup-1":"Specific evidence and search limits"}`.
Do not use that explanation while prior work can still produce the missing leads.
The release transfers unused reserve into one shared pool. Retrying the exact
checkpoint resumes its interrupted publication or returns the original result;
changing the contents of an existing ID is rejected. Release does not erase
spending or prohibit later leads from that generation. A second checkpoint cannot
release the same reserve again.

Before final synthesis, save the final plan, settle active workers and close
research clocks, then retain a checkpoint with `stop`:

- `complete`: no bounded frontier or saved-plan items, unverified graph work,
  or missing cycle evidence. Items first saved `bounded` or `dropped` still count
  as bounds even when no graph work row was created for them.
- `discretionary`: coverage/value judgment with capacity remaining; individual
  unworked leads need reasons, and incomplete opportunities stay explicit.
- `cap`: a reached global cap, retaining the remaining frontier and spending.
- `user`: an actual user stop, with their instruction preserved in the reason.
- `partial`: an explicit delivery closeout retaining plausible unresolved work;
  see below. It does not require completed cycles or assert coverage completeness.

For complete/discretionary stops, each protected round needs a verified cycle or
an explicit `no_opportunity` explanation. Never invent a round to close a list.
A cap/user stop can honestly leave cycles unfinished. Delivery remains separately
funded. New graph evidence, plan/contract changes or research activity make the
final review stale; append another review after the additional work. The original
plan checkpoint and semantic source review are still required.

Checkpoint JSON and its graph/budget snapshot are retained under
`followup-checkpoints/`; the append-only budget journal binds the exact bytes.
Include the resulting coverage/budget account in the evidence companion. Keep
this process detail out of the reader's short brief.

## Deliver With Unresolved Research

Use `partial` when a local allocation is exhausted while global capacity remains,
or when you choose to deliver with usable capacity and plausible research still
unexecuted. Keep pending work in its current state. A partial checkpoint cannot
release a reserve, supply `no_opportunity`, or add ordinary `decisions` bounds.
Its acknowledgments never count as triage for deeper admission or completion.

First settle active assignments, reconcile every original retrieval and reader
return, and close research clocks. Retain source-specific outcomes through the
ordinary graph interface: an unread source can remain `readability: "unknown"`
with `disposition: "deferred"` and an honest gap reason. This source outcome does
not defer its work item. Questions still need their evidence/gap account; claims,
receipts, source scopes and semantic review retain their normal validation.
Save the final plan with unexecuted items still queued. If discovery follow-ups
are enabled, use a final `bounded` coverage checkpoint listing that pending work
in `next_items` and preserving all actual operations and inspected reads.

Run `graph followup-status` again. Its `unresolved` list includes each unworked
frontier item and each incomplete work item, with source/question, ancestry,
requests and saved priorities. Copy **every** key and basis hash into a separate
`unresolved` decision. A request can mix these two types:

```json
{
  "id": "partial-delivery",
  "actor": "coordinator",
  "stop": "partial",
  "reason": "Deliver the current account; the unread prototype and queued comparison may still change it.",
  "partial_basis_sha256": "COPY_CURRENT_PARTIAL_BASIS_SHA256",
  "unresolved": [
    {
      "key": "COPY_EXHAUSTED_ITEM_KEY",
      "basis_sha256": "COPY_ITEM_BASIS_SHA256",
      "disposition": "allocation-deferred",
      "allocation": "followup-1",
      "metric": "active_minutes",
      "priority": "high",
      "reason": "The queued comparison remains relevant; its actual follow-up allocation and shared time are exhausted."
    },
    {
      "key": "COPY_FUNDED_ITEM_KEY",
      "basis_sha256": "COPY_ITEM_BASIS_SHA256",
      "disposition": "discretionary-unresolved",
      "capacity_remaining": true,
      "priority": "high",
      "reason": "The prototype remains unread. Usable allowance remains, but this delivery stops before its inspection; its effect on the conclusion is unknown."
    }
  ]
}
```

Status preserves discovery `generation` and `discovery_allocation` separately
from `funding_options`. Scheduled frontier uses the actual eligible resolving
work for that question, including its allocation and incremental costs. A
retained-capture inspection needs no new read attempt; assessment costs use the
original captured read's source identity and omit another slot when that identity
is already assessed. A fresh read of an assessed source still needs a read attempt.

`allocation-deferred` requires an exhausted `required_metrics` resource for
**every** resolving option, including shared capacity. Each corresponding global
metric must still have capacity. The `allocation`/`metric` shorthand above is for
one option. For multiple options, replace those two fields with explicit proofs:

```json
{
  "blocked_options": [
    {"option_id": "COPY_FIRST_OPTION_ID", "allocation": "baseline", "metric": "active_minutes"},
    {"option_id": "COPY_SECOND_OPTION_ID", "allocation": "followup-2", "metric": "active_minutes"}
  ]
}
```

Copy every current option ID; each option can have a different limiting metric.
An exhausted ancestral bucket cannot justify stopping when a scheduled
resolution has funding. `discretionary-unresolved` requires at least one option
with usable capacity and a reason for stopping anyway; never use it to imply
exhaustion. Unassigned frontier has no saved request fixing its count costs.
Its `possible_metrics` cannot prove exhaustion, but must have capacity to support
a discretionary decision. If those counts are exhausted, save concrete work
first so the helper can determine its actual costs. The decision priority records
the closeout judgment while the snapshot retains the original work priority.

Submit with `graph followup-checkpoint`, export a fresh ledger, obtain the usual
semantic review for that exact export, and run synthesis validation. A successful
partial delivery returns `status: "partial"` and `coverage_complete: false`.
The graph's `stop_checks` continue to show unresolved work and obligations;
validation permits only the acknowledged work/frontier gaps. Active workers,
unreconciled originals, conflicting source outcomes and unsupported claims still
block delivery. Missing, duplicate or stale decisions are rejected. A new graph,
plan, contract, research activity or reserve release requires a fresh checkpoint.
Retried identical checkpoints and historical receipts remain intact.
For a question-scoped obligation, independently verified resolved/bounded scopes
need no unresolved acknowledgment; every remaining scope still does. Changed
funding options or corrected cost interpretation require refreshed item hashes
and a new partial checkpoint; earlier snapshots remain historical evidence.
