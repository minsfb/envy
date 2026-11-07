# Read Sources And Expand The Inventory

Each reading assignment returns both findings and discovery work. Read for the
assigned question, including contrary evidence, dates, contribution/authority,
proposals versus decisions, implementation and outcomes. Check methods and limits
before using comparisons. Also look for information that changes what to search
or read next; a topical summary alone is an incomplete return.
Assess relevance to the research question and its source network, not agreement
with a draft answer. Organizing records may matter chiefly for their pointers.

## Read For The Record's Role

Assign overlapping roles, scoped to the question and section where needed:

- **Content:** establishes what was proposed, designed, prototyped, implemented
  or measured. Extract the substance, alternatives, assumptions and limits.
- **Critical/Review:** tests, challenges, changes or gates that work. Distinguish
  feedback, approval, rejection, deferral and open questions, including exactly
  what a decision covered and whether it was acted on.
- **Trunk:** locates or organizes other work: trackers, indexes, schedules,
  guidelines hubs and earlier evidence companions. Extract their organizing
  terminology, attachments and paths even when they contain no direct answer.

Default to reading plausible in-scope material in all three roles. A generic
title or lack of direct support for a draft claim is insufficient to reject a
Trunk or review record. A source may contain several roles and conflicting
observations; retain their scopes rather than forcing one label.

When triaging a large result set, inspect manageable batches and preserve the
source IDs covered by each checkpoint. Identical repeated previews may be shown
once with their occurrence mapping; retain the original observations and do not
discard differing context. Distinguish a metadata judgment from a body read, and
leave incomplete judgments pending. A heuristic ranking can guide the next batch
without claiming that the unexamined candidates were read or found irrelevant.

For a graph assignment, inspect its explicit requested extent and format policy.
`graph.extent: {"body":"all"}` requests the whole captured body; a complete
capture needs numeric bounds, a completeness attestation and limitations, followed
by actual numeric inspection of every captured bound. It covers no other component.
Record partial scope if requested comments, visuals or body sections remain unread.
The [graph workflow](graph-workflow.md#capture-index-and-actually-inspect) gives
the capture and return fields. A complete reference scan is separate from a
complete substantive inspection.

When rereading existing captures, the coordinator pins their normalized IDs in
the saved assignment's `graph.capture_ids`. A new reading does not make old
content freshly retrieved. Verify the assignment's content binding and start the
shared activity timer successfully before reading; a failed timer command leaves
time unmeasured and must be reported. Do not invent a past interval to repair it.

For long sources, use bounded displays of complete meaningful text, tables or
visual pages, with stable locators. Keep the displayed ranges and any truncation
visible in the reader checkpoint. Faithful text extraction may omit markup;
keyword matches, line counts, `head` samples and clipped combined outputs inspect
only the excerpts shown. Split an oversized display before continuing rather
than calling the entire capture read. A successful command or a saved full file
does not establish that its full contents reached the reader.

For long assignments, return a content-specific assessment of each bounded
section before advancing to another batch. Include relevant findings, contrary
evidence, limits and discovery leads with locators. If a section adds no useful
evidence, explain why using its actual content; do not invent findings to fill a
quota. Display commands, hashes and generic method notes alone leave that section
unassessed. The coordinator checks these section returns before accepting a
whole-source completion claim. Reduce the batch size when substantive assessment
breaks down, and keep other priority branches moving while the remainder is open.

## Capture References Before Selecting Reads

Enumerate outgoing artifact links and embedded references from the accessible
source content before judging which deserve reading. Prefer structured hyperlink
or embedded-object extraction when the provider exposes it. Preserve a reference
scan with its source/read identity, extraction method, covered surfaces and limits.
Scan the whole captured body for references when practical even if substantive
reading is selective; distinguish that scan from inspecting all the source's text.
Include exposed tabs, notes, comments, attachments and visual-object links as
appropriate. Do not claim coverage of unavailable surfaces. If a plain-text export
drops link targets, use a richer available route or record the missing link scope.

In graph mode, return structured scans and reference occurrences; automatic
ingestion connects them to the common inventory and creates pending obligations.
Do not maintain a second reference list. Useful fields are:

| Field | Record |
|---|---|
| Origin | Source ID, captured read ID and section/page/slide/node/comment locator |
| Reference | Visible label, stable target ID or canonical source URL, surrounding context |
| Classification | Artifact, internal anchor, routine navigation/non-research link, or unresolved reference |
| Inventory target | Existing or newly added source ID; unresolved identity stays explicit |
| Assessment | Likely relationship and relevance, including uncertainty and contradictory evidence |
| Disposition | Read assignment, existing inspection, identification search, or reason for exclusion/deferral |

Add each distinct linked artifact to the common inventory before selecting reads.
Deduplicate by stable identity while retaining all referring locations and useful
fragments. Repeated references reuse one target record. An internal section anchor
is a locator within the same source; a linked file is a separate candidate. Classify
routine navigation and non-research links in the reference scan, without creating
unnecessary artifact records. Repeated boilerplate may share a classification if
its occurrences remain accounted for. Preserve canonical identities without
copying credential-bearing access parameters into authored records.

Do not silently drop an unfamiliar title or weak-looking link. Record whether it
appears to be an original study, precursor, review, prototype, tracker, later
outcome, duplicate, or unrelated work. If its identity or relevance is unclear,
retain that uncertainty and decide whether a cheap metadata lookup or read is
warranted. Metadata-based exclusion needs a concrete out-of-scope basis; it is
not a substantive finding about an unread document. Prefer a read when in-scope Content,
Critical or Trunk relevance remains plausible.

## Extract Unlinked Leads Too

Link enumeration does not replace reading for expanded acronyms, older project
names, terminology, named meetings/reviews, cited studies or preceding/subsequent
work with no surviving URL. Record the observed phrase and locator as a vocabulary
or record lead. Seek organizing labels even when they do not help the immediate
topical summary: they may identify an index or tracker connecting the work.
Return these in a structured lead list with stable IDs, preserving the original
return before consolidation. Extract named concepts and labels from pivotal
passages even when they look generic; the coordinator assesses whether focused
queries could locate their surrounding records.

Separate observed wording from a verified alias. Search useful leads individually
or in small combinations. Retain them in the existing source `leads` with their
relevant question and assigned follow-up or explicit bound, using
[discovery-wave records](discovery-waves.md). A source reference can identify a
useful neighborhood without proving a historical contribution or decision.

## Triage And Follow Through

The helper retains declared candidates, scans and leads before prioritization.
The coordinator reconciles each pending obligation with saved work or a reasoned
bound. An already-inspected target can reuse coverage only when its question,
scope, format policy, version and freshness requirements match; otherwise assign
the missing extent.
Deduplication must not discard a new reason to revisit a source.

A reader may continue into nearby references within its agreed scope and effort
after the coordinator records ownership and the next operation. Return larger
branches for distribution to other subagents. Keep one owner for shared decisions
and the plan; independent readers submit their own immutable returns through the helper. Short validated transactions
serialize updates. Never hold a database lock while a provider or reader works.
Capture operations before interpretation as usual.

Repeat reference capture and assessment for newly read sources. Keep depth and
breadth bounded by the reader question, remaining uncertainty and agreed effort.
Use [balanced expansion and shared budgets](research-budgets.md), interleaving
terminology queries and targeted child reads across priority branches. For relevant
sources, default to reading every exposed research-artifact child
that still lacks sufficient coverage, across balanced waves rather than one
depth-first branch. Stop a subtree at an irrelevant inspected leaf or an explicit
scope, depth, count or time bound. Other relevant origins can keep the same target
pending. Retain excluded, deferred, inaccessible and unread candidates with their
reasons; index completeness is distinct from reading
completion. Useful new leads can reopen a prior stopping decision.
Distinguish terminal access/unreadability from transient failures and exhausted
retries. None establishes irrelevance. A cap-deferred source stays potentially
relevant with its remaining extent and ancestry, rather than becoming a rejected leaf.

## Accept The Reading Return

Submit through `reader-return`, which preflights graph returns before retaining
them; `graph check-return` is an optional earlier check. Fix unsaved drafts directly.
For an invalid return already retained, use the
[append-only rejection and correction path](graph-workflow.md#recover-originals-without-repeating-research).
Preserve its inspection, reference/lead inventory and every question origin.
Scope downgrades do not add an inspection or accept its findings; new inspection
or semantic revision uses ordinary return/reconciliation and budget rules.
If an already accepted inspection overstated what was read, use
[inspection withdrawal](graph-workflow.md#withdraw-an-accepted-inspection),
challenge dependent assertions and reopen affected work. A later genuine read
creates a new inspection; it never makes the earlier full-read claim true.

Check the reference scan against the retained source or provider link metadata,
then reconcile artifact targets with the common inventory and assigned reads.
Account for each extracted reference through a target or explicit classification;
do not accept an unexplained empty scan when the source exposes links. A zero-link
result still needs the extraction scope and method. Assess whether unresolved
references, unread pivotal candidates or questionable exclusions warrant another
wave. Check unlinked leads against source passages separately.
Then match the immutable returned lead list to the common ledger and subsequent
work and every known question scope. This catches a lead that was correctly
returned but lost during coordination. A resolved identity or one completed
question cannot close the remaining obligations.

For a large delegated investigation, give another reader or review subagent the
pivotal source passages, reference scans, inventory and proposed stopping rationale.
Ask it to find missed references, organizing terms, unsupported exclusions and
consequential follow-ups. It returns concrete additions or a source-grounded
assessment, not only approval of the brief. The coordinator resolves those returns
before synthesis. Record any limits on this independent check.
Include a sample of rejected leaves and unread organizing records. Read a sampled
candidate when its available metadata makes relevance plausible; an inaccessible
candidate remains inaccessible, not irrelevant. Preserve follow-ups even if the
research cap permits only reporting them as the remaining frontier.

The graph helper retains every declared reference and lead and checks recorder
bindings, assignments and execution. It does not enumerate arbitrary provider
formats or detect references the extractor never returned.
Compare the scan to actual source content in acceptance review; use available
deterministic extraction to reduce omissions without claiming full-document
coverage from a lossy export or structural pass.
