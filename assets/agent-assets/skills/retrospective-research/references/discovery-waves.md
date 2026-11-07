# Read-Driven Discovery Waves

Discovery improves when inspected records change the next search. Start with
bounded queries, select a small diverse set of promising sources, read them,
then expand through their language and record relationships. Reserve effort for
this loop before spending the research budget on broad inventory collection.
Use [shared research caps and balanced scheduling](research-budgets.md); there is
no requirement to inspect unrelated results. Default to reading plausible
in-scope Content, Critical/Review and Trunk seeds. Interleave baseline breadth with the first useful follow-ups. Protect
at least two complete follow-up cycles before optional expansion spends their
reserved allowance. Baseline does not require reading every initial hit.
Repeat the loop while consequential leads remain within the generation limit.

## Choose Seeds And Expand

Choose seeds for their likely contribution to unanswered questions and ability
to reveal other records. Include different work types and routes; do not select
only familiar titles or the highest ranked hits. Organizing artifacts—indexes,
trackers, recurring meeting notes, review schedules and team landing pages—can
locate work whose title does not describe the subject. Their pointers are leads,
not substitutes for inspecting the underlying evidence.

Use [the reading procedure](reading-and-expansion.md) to enumerate outgoing
artifact references before selecting reads, reconcile targets into the common
inventory and assess each candidate. Alongside that scan, extract new terms,
expanded acronyms, older names, cited reviews and unlinked references to preceding
or subsequent work that could change discovery or interpretation.
Record the actual source and locator. Search useful terms separately or in small
combinations; a long conjunction can exclude the record being sought. A term is
an observed lead, not automatically a verified alias or the same project.

Inspect the immediate neighborhood of pivotal sources where tools support it:
linked proposal/prototype/review/outcome, enclosing thread and attachments,
containing folder, relevant project index, and references to its title or URL.
For relevant sources, default to reading their research-artifact children lacking
matched coverage. Distribute this work across fair waves with terminology queries;
retain explicit bounds when scope or allowance prevents it. Available file access does not establish
backlink search, folder listing, historical comments or global design discovery.
Keep relationships directional: a proven backlink, a shared outward reference,
and membership in a folder are different evidence. Two records linking the same
design file do not establish a discovery path from that file back to both records.

Check the query itself against the intended coverage. A search restricted to one
activity word in the title can miss a differently named record of the same work.
Useful body/topic searches should not all require topic or activity words in the
title. Vary or remove restrictive clauses before concluding that a work type is
absent. Group plausible activity terms where appropriate without generating every
combination. Query labels and structural validation do not verify query semantics.

Current ownership may differ from historical authorship, particularly after
migration or archiving. Keep broad routes alongside person-specific searches;
use body credits, comments, revisions or corroborating records to establish
contributions. Do not treat an owner mismatch as a reason to discard a source.

## Record Leads And Checkpoints

For new delegated or multi-wave bundles, enable `requirements.discovery_followups`
alongside `research_plan`, `search_routes`, `research_budget` and
`protected_followups`. This is additive; old bundles do not
need reconstructed checkpoints. Use the [shared graph workflow](graph-workflow.md)
and its generated worklist and source inventory; keep the original saved plan
and checkpoints as execution contracts.

Add a nonempty `wave_strategy` to the plan describing the allocation between
discovery, seed reads and follow-up. Start `checkpoints` as an empty list. Analyzed
sources need an `expansion_review`: explain useful new language, records and
context, or why no further expansion is warranted. This assessment is required
only for analyzed sources, not every metadata-only candidate.
Use a source-specific string, for example:
`"The index calls the earlier pilot Harbor and links its evaluation; both are
assigned leads. Its staffing links do not address the outcome question."`
Name the consequential observed terms or records and their disposition. A generic
wave summary repeated across sources does not fulfill this assessment.

Retain each reader's original structured lead return before consolidating it.
Automatic ingestion retains every declared lead/reference and creates its pending
obligation before triage. Reconcile every returned lead ID to that graph record
and all affected question scopes, then to assigned work or a source-specific bound.
A reader mentioning an organizing label in prose
is not completed follow-up work. Check the return itself for terms omitted from
its structured list; the recorder can detect lost imported leads, not words the
reader failed to extract. Apply this to coordinator-owned reads as well.

Return source leads with `kind`, `value`, relevant `questions` and provenance;
coordinator decisions and matched work determine their consolidated status.
The generated compatibility ledger projects fields such as these after reading
a fictional planning index; do not edit it to manufacture an assignment:

```json
{
  "id": "earlier-work-name",
  "kind": "vocabulary",
  "value": "An earlier internal label found in the index",
  "questions": ["rationale"],
  "locator": "Earlier investigations, second entry",
  "reason": "May connect the proposal to its original evaluation",
  "status": "pending",
  "plan_item": "search-earlier-label"
}
```

Kinds are `vocabulary`, `linked-record`, `neighborhood` and `question-gap`.
The concrete destination item retains the existing matching `origin` source and
locator. Include `origin.read_id` for discoveries made while reading; its actual
inspection must already be present in the operation's retained plan snapshot.
Before assignment, a generated lead can retain its verified `graph_lead_id` and
`graph_obligation_id` instead of a fictitious destination. Add the normalized
lead/reference ID and parent operation to a new plan item's `graph` annotation
when scheduling it. Deduplication retains every origin, including distinct
questions; completing one question never closes another unassigned scope.
Metadata or snippet leads may omit `read_id`, with their limited basis explicit.
Keep the operation's research ancestry as well: source-derived searches advance
generation just like linked reads. A newly phrased query does not start a fresh
baseline branch. Pagination and alternative reads of the same source preserve
their generation.

Compatibility lead states are `pending`, `addressed` and `bounded`; graph views
also expose registration and per-question work states. `addressed` requires a `done`
plan item with successful indexed execution; a read also needs actual inspection.
In graph mode, verified resolved work supplies the binding and inspection must
match requested question, scope, formats, version and freshness.
It means the stated follow-up happened, not that it exhausted the surrounding
record chain. Record consequential further leads from the result. `bounded` needs
a specific reason; an unassigned bounded term or neighborhood may omit a target.
Retain observed terms and prior assignments rather than deleting awkward leads.
Use the helper to record bounds and reconciliation. Do not change exported
status strings to close a pending graph obligation.

Append a checkpoint after a useful wave and before synthesis:

```json
{
  "id": "after-seed-reads",
  "operations": ["initial-search", "index-read"],
  "inspected_reads": ["index-read"],
  "questions": ["rationale"],
  "next_items": ["search-earlier-label"],
  "decision": "continue",
  "reason": "The index supplies an earlier name; the proposal's evaluation remains unlocated",
  "limits": "Initial search is ranked and capped; inspect the older-name results before broadening it"
}
```

`operations` references completed, indexed operations, including retained failures
when relevant. `inspected_reads` references actual inspections; capture alone
does not qualify. `questions` names contract questions; `next_items` names existing
worklist assignments. A `continue` decision needs concrete next work. Decisions
may also be `synthesize` or `bounded`. Explain both the decision and its limits.
Checkpoint records are append-only. Save them through the ordinary `plan` command.

Before synthesis, every recorded lead must be addressed or explicitly bounded.
Use [protected follow-up checkpoints](protected-followups.md) to review leads
that never became work and references on uninspected source surfaces. Repeated
queries on established branches do not establish a second complete cycle.
Save the final plan, close research clocks, then retain the protected stop review
before exporting and validating the final delivery evidence.
The final checkpoint must cover every current operation, inspection and contract
question, with no pending work in its `next_items`. New operations or inspections
make that final review stale; append another checkpoint after reviewing them.
An inaccessible corpus can justify a bounded checkpoint with no inspected reads;
do not invent an inspection to satisfy a workflow.

The generated plan shows checkpoints, pending leads and searches with `more`,
`capped` or `unknown` pagination. Use that view to review unused cursors, unread consequential
sources, competing accounts and unresolved questions. Explain whether further
work is unlikely to change the answer or whether time/access ended the wave.
At each checkpoint report which baseline branches and follow-up generations have
had a useful pass, current shared spending, and the remaining frontier. Distribute
the next assignments across uncovered priority questions and source neighborhoods.
A large outgoing-link list is not a reason to spend all remaining effort in that
branch. If a cap stops deeper work, retain it as budget/depth deferred and finish
the separate synthesis and delivery phases.
Checklist completion alone is not a stopping criterion. The companion includes
this coverage account; keep process detail out of the short reader-facing brief.
Review pivotal sources' assessments against their actual passages and links,
including organizing labels that the main topical summary may have overlooked.
Return generic or unsupported assessments for source-specific review before
accepting the research; a successful structural check does not replace this pass.
Reconcile per-source reference scans as part of this review. In delegated work,
use the independent coverage assignment described in the reading procedure and
resolve any new leads it returns before accepting the stopping decision.
Choose sources for this review by their relevance and navigational role, including
a sample of rejected leaves and unread organizing records. Do not restrict review
to evidence cited in the brief. Coverage review asks what the investigation missed;
claim review asks whether the resulting prose follows from the evidence.

The helper checks recorded obligations, provenance and freshness. It cannot
discover omitted terms, judge whether a bound is reasonable, interpret arbitrary
query predicates, authenticate inspection or prove research completeness.

## Diagnose Misses Without Overfitting

Keep a known-source diagnostic separate from a blind topical evaluation. Directly
inspect missed sources to propose explanations, then test paths from the original
inputs or previously discoverable sources. Distinguish a proven link or successful
primary-source result from an untested plausible path. Exact-title recovery and
terms learned solely from the missed document are assisted diagnostic evidence.

Translate findings into general behaviors, not a permanent list of target names.
Retest with fresh researchers denied previous synthesis and diagnostic hints,
and use different topics or unseen sources to assess generality. Report benchmark
recovery as a regression check when those benchmarks informed the revision.
