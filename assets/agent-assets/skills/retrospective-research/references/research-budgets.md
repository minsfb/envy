# Research Budgets And Balanced Expansion

Budgets prevent an expanding source graph from consuming unlimited work. They
must preserve useful baseline coverage, then permit repeated discovery through
new terminology and targeted child references. They are not deadlines for the
whole engagement or reasons to skip synthesis and collaborative editing.

## Set The Allowance Before Dispatch

Reuse explicit user limits. For a substantial investigation without a tighter
constraint, start with these shared research caps:

| Metric | Default cap | What counts |
|---|---:|---|
| Search attempts | 250 | Every admitted request, including pagination, retries, empty results and failures |
| Source fetch/read/export attempts | 400 | Every admitted source retrieval, including alternate formats and failures |
| Distinct assessed artifacts | 200 | Canonical sources admitted for substantive assessment; repeated extents reuse their identity |
| Active research elapsed | 330 minutes | Time in active discovery, reading, assessment and coverage-review intervals |
| Aggregate research effort | 600 agent-minutes | Research time across coordinator and workers; concurrent workers each consume effort |
| Follow-up generation | 3 beyond baseline | Each source-derived link or terminology expansion adds one generation |
| Attempts per logical operation | 3 total | Retry only a plausibly transient failure, with a recorded cause |
| Request timeout | 240 seconds | Shorten to the remaining research allowance |

For new multi-wave work enable `requirements.research_budget` and
`requirements.protected_followups`. Override only these supported keys in
the contract's top-level `research_budget` object. Omitted keys retain defaults:

```json
{
  "research_budget": {
    "search_attempts": 250,
    "read_attempts": 400,
    "assessed_artifacts": 200,
    "active_minutes": 330,
    "agent_minutes": 600,
    "followup_generations": 3,
    "attempts_per_request": 3,
    "request_timeout_seconds": 240
  }
}
```

Values must be positive finite numbers; count limits must be integers. Keep
delivery allowances and concurrency limits elsewhere
in the contract: they are not accepted `research_budget` keys. Translate intake
labels into these exact keys before initialization; preserve changes to an
existing allowance through the contract amendment command.

In graph mode, an amendment also needs a retained graph contract binding before
research resumes, even when only time caps change. Follow the
[contract amendment sequence](graph-workflow.md#contract-amendments). The recorder's
successful `amend` response alone does not establish this binding. Keep workers
idle during the change; do not spend retries on captures that fail before dispatch
because the new contract is unregistered.

Discovery metadata does not consume the assessment cap: thousands of candidates
may be indexed while a much smaller set is read. Claim the assessment identity
before substantive inspection so concurrent workers cannot oversubscribe it.
A failed retrieval still consumes an attempt; failure does not make an admitted
assessment slot available for an unrelated source. Count actual attempts even
when their results are later rejected from the evidence record.

Use the shared recorder for admission, not per-agent estimates or independent
budgets that multiply silently. Capture actor, phase, branch, generation,
parentage and retry identity before execution. The coordinator owns scheduling;
workers must return when their assignment or shared allowance ends. Preserve
the limits and changes in contract history. Do not quietly raise a reached cap
to improve the evaluation result; report why an amended allowance is necessary.

Time accounting must state what is observable. Record active research intervals
and participating actors, including reading/assessment time outside tool calls.
Do not call source-fetch latency document-reading time. Exclude genuine user or
infrastructure idle periods, with their reason, rather than subtracting ordinary
thinking or coordination. Attested actor intervals are not runtime telemetry.
The admission controller can stop tool dispatch at a limit; it cannot terminate
unreported model work. Check time at assignment boundaries and during long reads.

For delegated work, the coordinator or supervising lead owns the child's clock:
start it before dispatch and stop it after observing the completed return or
confirmed worker stop. Name that owner in the assignment and tell the child not
to close the clock independently. Keep it open through source interpretation,
citation checks and return preparation, including work on already retained files.
A start/stop pair around a shell script measures that script, not the surrounding
model assessment. Retain conservative dispatch/return overhead. If a clock did
not cover the work, preserve the original events and identify the affected work
and unknown duration; do not invent a replacement interval or use that total to
claim precise research cost or budget adequacy.

## Preserve Breadth Before Going Deeper

Reserve **30% for baseline breadth, 50% across at least two follow-up cycles,
and 20% for further expansion and independent coverage review**. Configure these
through the top-level `research_allocation` object, not `research_budget`.
The [protected follow-up interface](protected-followups.md) enforces capacity
across time, attempts and assessment slots. Existing unprotected runs retain
their historical 120/300-minute defaults and scheduler; do not reclassify them.

A cycle includes querying, triage, selected reads, assessment, further leads and
coordination. It is not a quota of searches. Baseline means sufficient coverage
of important questions, work types and routes plus diverse seeds; it does not
mean reading every initial result. Follow high-value new terminology and child
records as soon as returns arrive, while remaining baseline work proceeds.

Each bucket can borrow unused expansion capacity. It cannot consume another
unfinished follow-up's reserve. If established baseline coverage still cannot
fit, retain a reasoned allowance increase; never silently raid follow-up capacity
or shrink baseline coverage. A completed bucket's unused reserve can be released
through a verified checkpoint. Late leads keep their original generation and may
still compete for the shared expansion pool after that release.

Use the [graph scheduler](graph-workflow.md) to interleave baseline and the
first available follow-up generation, balancing branches, questions and source
roles. Do not spend every wave on established branches while newly observed
organizing terminology, unread reviews or an uninspected source surface waits.
Check `graph followup-status`, including leads that never became work items.
Before generation two or deeper can dispatch, earlier unassigned terms and
external references require executable work or an explicit typed review.
A claim fairness override does not waive that admission check. Batch comparable
reads; a large hub must not monopolize all readers.

Deduplicate canonical source identities across paths; retain the union of reasons
and inspected extents. Prefer a focused terminology search when it can open a
new record neighborhood more cheaply than reading a long list of weak links.

Baseline discovery has generation 0. A source at generation 0 can produce a
generation-1 linked read or term query; results of that query remain generation 1.
Leads found in those results produce generation 2. Changing tools, query spelling,
or routing through an organizing record never resets ancestry. Preserve alternate
paths, including a newly evidenced shorter route, without rewriting generations
already attached to work, assignments or admitted operations.
Retries, pagination and rereading the same source extent do not add generations.

Retain every exposed artifact reference before selecting work. For a relevant
source, default to reading each plausible in-scope research-artifact child that
lacks matching coverage, across fair waves. Interleave useful unlinked-term
queries and preserve every source/question obligation. An inspected irrelevant
leaf or explicit scope, access, depth, count or time bound ends that path; other
relevant origins may keep the target pending. This policy is bounded by the
shared allowance and does not promise exhaustive provider coverage.
Relevance means a source can answer, challenge or navigate the research question.
An index can be relevant without containing a quotable conclusion. Unknown or
inaccessible content is not evidence of irrelevance.

## Separate Readability, Relevance And Disposition

Record read coverage separately from substantive relevance and next action:

- **Read relevant / irrelevant / uncertain:** based on actual inspection, with
  a reason and extent. Metadata-only triage must remain labeled as such.
- **Access blocked / unreadable:** terminal for this run when the provider makes
  that clear. Retry only after a material access, capability or source change.
- **Transient retry pending / retry exhausted:** record the failure cause and
  linked attempt chain. An ambiguous failure is not proof of denied permission.
- **Budget deferred / depth deferred:** potentially useful frontier work left
  unread by a recorded limit, retaining priority, generation and originating lead.

An exported title with no intelligible body is not a complete read. An unfamiliar
format may need one supported alternate route before it is deemed unreadable.
Do not repeatedly retry a known denial or use renamed operation IDs to evade the
logical-operation retry cap.

## Review Coverage And Stop Discovery Honestly

At each checkpoint report attempts, distinct assessments, active elapsed and
aggregate effort, by allocation, branch and purpose (research, coordination,
repair and coverage review). Distinguish a local bucket limit from an exhausted
global allowance: a local limit does not justify abandoning funded follow-ups. Reconcile the actual reader
return files against graph obligations, generated ledgers and ensuing work. Review newly useful vocabulary,
unread organizing records, low-yield branches and capped result sets. An independent
coverage reader samples rejected leaves and pivotal source passages for omitted
leads; reviewing citations supporting the brief is a separate assignment.

Continue when new terminology, source neighborhoods, contradictions or gaps justify
it. Stop a branch when evidence supports low relevance or diminishing value;
stop admitting research at a shared hard cap. Record which useful passes finished,
which frontier remains, and whether baseline breadth was preserved. A cap reached
partway through a deeper wave does not invalidate earlier good passes, but it does
limit completeness claims. Never convert deferred or inaccessible sources into
irrelevant ones to close the queue.
Retain a protected final stop review after closing research clocks and saving
the final plan. Distinguish a reached global cap, explicit user stop, and a
discretionary stop with capacity remaining. Preserve consequential frontier
items and their individual reasons. Do not call scheduling preference a budget
limit. New evidence, activity or scope invalidates the final review.

Before reassigning expired or interrupted work, use `graph work-status` and
check the original worker and admission. An absent graph mirror does not refund
an attempt, and an expired lease does not stop an external call. Reconcile the
original receipt or retain an explicit unknown state; never dispatch a replacement
merely because it would fit another actor's apparent allowance.

## Keep Delivery Separate

After discovery stops, release research workers and finish the brief, evidence
companion, main author's substantive rewrite, citation/prose checks, rendering,
user review and authorized revisions. Their time and publication calls are
reported separately and are not charged against discovery caps. There is no
ordinary hard delivery cutoff by default; sustained work over hours is acceptable.

Use a progress watchdog for genuine runaway behavior: repeated identical failing
publication attempts, cyclic rewrites with no improved result, or expanding scope
without a reader need. Diagnose the problem and bound that activity rather than
abandoning the deliverables. A user request to hurry changes priorities. Extra
source discovery during synthesis still consumes research allowance and must
respect its stop; if unavailable, qualify the claim using inspected evidence.

See the [bundle procedure](bundle-procedure.md) for the recorder interface and
the [discovery-wave guide](discovery-waves.md) for retained work and checkpoints.
