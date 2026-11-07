# Retrospective research graph evaluation

Status: G7 implementation checks, controlled fresh and reuse trials, and the
seeded diagnostic are complete. The live research phase is frozen with material
coverage and execution failures retained. The two native documents passed final
content and publication checks, with formatting limits below. Two independent
review rounds identified scope, evidence and recovery defects. Their corrections
passed final repository validation; exact-source review remains pending. This
document does not establish release readiness.

## What is being evaluated

The [design](retrospective-research-graph-design.md) extends the research skill
with a shared SQLite graph, immutable evidence, saved execution plans and a
shared research budget. The graph retains discoveries, actual inspections,
observed references, proposed interpretations and unfinished work. Its exports
provide the source inventory and evidence companion; the brief remains authored
and reviewed prose.

The evaluation separates five questions:

1. Does the implementation preserve identity, evidence, accounting and recovery?
2. Do actual delegated workers follow the protocol and share the same allowance?
3. Does research select useful sources and pursue terminology and references?
4. Do the conclusions preserve source meaning, uncertainty and contradictions?
5. Are the brief and companion usable, accurately cited and visually checked?

Structural checks address the first question and parts of the second and third.
They cannot prove that a reader understood a passage or chose the right leads.
Source-reading and editorial reviews remain necessary.

## Completed controlled trials

The fictional archive is available in
[the agent fixture](../tests/fixtures/retrospective-research/graph-agent/README.md).
Each completed run used a fresh coordinator and four actual nested research
workers, including separate coverage and evidence review. Earlier conclusions
were withheld in fresh runs. The reuse arm explicitly received a prior companion
and a bounded graph export.

| Run | Search attempts | Read attempts | Assessed identities | Active research minutes | Aggregate agent-minutes | Pivotal records | Readable reference triples | Brief + companion pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fresh R2, versioned continuation | 25 | 34 | 21 | 29.68 | 56.10 | 7/7 | 72/72 | 1 + 64 |
| Fresh R3, one candidate | 20 | 29 | 21 | 26.49 | 42.83 | 7/7 | 72/72 | 1 + 9 |
| Assisted reuse, versioned continuation | 19 | 38 | 22 | 25.48 | 32.88 | 7/7 | 77/77 | 1 + 7 |

Attempts include failures, retries and independent review refreshes. They are not
distinct documents. Reuse includes an additional prior-companion Trunk artifact
and five readable reference triples, so its denominator differs. The inaccessible
source remains unknown rather than being classified as irrelevant. Concurrency
differed across runs; this table is not a controlled speed comparison.

The first fresh attempt failed before useful completion and remains recorded as
a failure. R2 continued through explicit implementation repairs, retaining the
old captures, failed checks and source transitions. R3 completed on one candidate
and recovered an interrupted worker using the same identity and retained capture,
without another provider fetch. It supports the fixed-corpus recovery and
discovery results; it does not establish discovery probability for other corpora.

The reuse import supplied 11 original source identities, 70 historical leads and
66 historical references, with **zero current inspection credit**. Current
research produced 36 native inspections and 128 reference occurrences. These
remain separate from the historical network. A coordinator mistakenly selected
historical locators during synthesis; the exporter withheld historical-only
claims, and new revisions corrected the bindings without changing original
returns. Parent verification checked all 253 current locator uses supplied by the
final audit. Reuse made known source IDs available, but the experiment does not
isolate a causal time saving or demonstrate independent rediscovery.

## Selection and reading diagnostic

A separate diagnostic classified 1,017 retained metadata identities from 35
search-result sets: 575 plausible, 344 unresolved and 98 excluded. Two known
relevant organizing/review sources ranked 49 and 160. Their ranks alone do not
show whether a complete workflow would read them within its assessment budget.

Five explicitly assigned body probes then tested reading usefulness separately:
three selected examples and two evaluator assignments. They produced 43 accepted
assertions and exposed 1,187 references, of which 1,072 fell outside the inspected
ranges. Four reads were partial and one had sufficient captured-text coverage;
38 terminology leads remained pending. These are diagnostic results, not an
independent discovery success or a completed research investigation.

## Live investigation

The live research phase is frozen and final native document checks are complete
with the limits below. It used the same fresh coordinator and journal
through disclosed source revisions, with earlier outputs and evaluator holdout
identities withheld.

At the natural freeze, it had 43 search admissions, 92 read admissions and 55
assessment reservations. The journal recorded 308.03 active research minutes and
476.81 aggregate agent-minutes under amended 330/600 time caps. Measurement gaps
described below prevent an exact cost or budget-adequacy claim. The ledger held
1,037 source/alias rows representing 967 canonical identities; 52 identities had
effective inspection records. Partial readings remain partial. Parent verification
checked all 2,914 initial freeze bindings (263.7 MB), then all 2,919 bindings
(267.0 MB) in the final delivery export. These are logical file totals, not
distinct provider content or physical storage consumption.

**Independent discovery remains unresolved.** None of three withheld known
sources appeared in the final graph. Two relevant organizing terms were retained
from actual reading, but their work became an explicitly deferred frontier
without an executed search. The graph preserved the omission and its origins;
it did not make the coordinator prioritize those terms. This locates part of the
failure after terminology recognition and before follow-up execution. It does
not prove that an additional query would have returned the withheld sources.

Allowed source surfaces also exceeded executed coverage. Figma/FigJam links were
retained, but no native board/file operation or final plan item was executed.
The lack of verified global search did not establish that linked files were
inaccessible. A separate image-library route received one successful scoped
metadata query and no media inspection. These are explicit coverage gaps, not
provider denials or evidence of irrelevance.

The run completed several balanced follow-up waves and independent coverage and
evidence reviews, while requiring substantial operator intervention. Structural
settlement includes explicitly bounded unfinished work; it is not evidence of
domain completeness. Subsequent document editing and artifact checks are
reported separately below.

The initial checkpoint contained 429 source/alias rows representing 394 canonical
artifacts from 11 searches. Three records in one search response exposed
body/comment content, so this selection checkpoint was not wholly metadata-only. Subsequent execution
also exposed malformed reader returns, substantive reference/scope mistakes and
one provider call made outside the recorder. Original evidence and failures are
retained; the unadmitted call counts as additional actual cost and cannot acquire
a fabricated native receipt.

A later audit found that readers claimed complete inspection after displaying
only filtered excerpts or truncated combined output. Three already accepted
inspections overstated their scope; other affected returns were withheld.
Assertions from the accepted returns were challenged, and the coordinator
arranged bounded rereading. The original claims and worker admissions remain
retained. Original display transcripts are unavailable, so the admissions are
not presented as independently captured execution telemetry. This is a material
reading failure, not successful full coverage.

Commit `93e182e0` adds the missing public withdrawal path through an append-only
storage upgrade. At that checkpoint, the live graph retained all 21 original
inspections and withdrew three from effective coverage. Those counts describe attestations,
not an independent proof of reading. Parent verification checked the unchanged
6,328-event prefix and 764 original operation, return, contract, plan and update
files. A copy rehearsal also preserved all 8,130 original non-database files.
New full reads and revalidated claims remain necessary; withdrawal itself does
not complete the research.

Later corrective work completed additional readings, but readers twice performed
reading without an active timer. Those durations remain unmeasured; the recorded
aggregate is therefore incomplete. A separate worker draft was edited
in place despite instructions to preserve it. These execution failures remain
part of the evaluation, even where later reading and native returns succeeded.

Four corrected readings subsequently completed against pinned retained captures.
Parent verification checked 24 original evidence files, the unchanged
9,811-event prefix and the completed assignment/work bindings. The coordinator
also recorded an in-process budget-byte comparison, but did not retain separate
before/after copies; the parent cannot independently verify that comparison.

The first follow-up searches subsequently returned actual results. To preserve
breadth while a long baseline source was still being read, the parent explicitly allowed the
first saved terminology-search wave to interleave that reading. This was a
scheduling intervention; the parent supplied no new search terms or source IDs.
Later waves and independent reviews completed before the natural freeze; the
three withheld discovery targets remained absent.

A subsequent coordinator check caught another long-source overclaim: later
section notes contained only display-method placeholders. The reader retracted
semantic coverage for those sections. Its already-submitted inspection was
retained and explicitly withdrawn, bringing the live withdrawal count to four.
This shows a limit of structural validation: successful display and hash checks
cannot establish substantive reading. Follow-up work required content-specific
section assessments before advancing, keeping the remaining range visibly open
and other research branches continuing. Independent reviews must sample other work
from the affected reader as well as verify the brief's cited passages.

Commit `4cf7f3db` makes that checkpoint explicit in the reading and coordinator
guidance. It changes no executable code. A hash check confirmed that the live
transition retained the tested helpers, and separate before/after budget files
were identical. This is a procedural safeguard to evaluate, not proof that the
same reading failure can no longer occur.

A final expansion wave repeated the problem before native acceptance: a worker
reported that its purported chunk reads had selected at most 18 strings per
chunk and truncated each to 300 characters. The coordinator withheld full-read
credit for both sources and retained the admission and original notes. This is
an additional reading failure, not another withdrawal of accepted evidence.
The trial therefore does not establish that the strengthened instructions alone
prevent reading overclaims. Source-level acceptance review remains necessary.

The coordinator also required two operator-assisted resumptions after unexplained
periods without progress. The same coordinator, journal and allowance continued;
no reset or replacement run concealed the interruption. In the second case,
the coordinator reported no remaining pending tool session. The evidence does
not establish the cause of the stall.

To preserve baseline breadth, the operator explicitly increased this trial's
time caps from 120 to 240 active research minutes and from 300 to 600 aggregate
agent-minutes. Later, about 7,200 lines of required baseline reading, remaining
comments and material visual checks were still unfinished. The coordinator
estimated another 75–115 active minutes including final research review, with
about 40 minutes available. The operator authorized an active cap of 330 minutes;
the aggregate cap remains 600. The original spending is retained, and search,
read, assessment, depth and retry caps are unchanged. These trial-specific
amendments neither change the skill's defaults nor establish that the original
time allowance was sufficient. The incomplete baseline is explicitly separate
from the deeper frontier; the amendment itself does not complete either.

## Live document delivery

The final brief is one page and contains 318 whitespace-separated words. Native
text/link comparison and parent PDF inspection passed. The brief links to the
complete replacement companion, which links back to the brief. Study population,
task and measurement limits remain visible; a proposed product direction is not
presented as an established head-to-head result.

The first companion stopped after its narrative, exact search requests and first
1,000 source rows. Content/revision/tab calls repeatedly timed out. It remains
an untouched historical delivery failure. One supported HTML-to-native-document
import produced the complete replacement, including all 1,037 source rows,
inspection and frontier tables, saved plan, coverage limits and 52 separately
labeled reader assessments. Those assessments distinguish proposed findings from
accepted assertions and preserve partial reading scopes.

Final native Drive HTML and PDF exports succeeded. The full text, ordered table
cells and 1,098 link occurrences matched the expected document. The comparison
used a disclosed, byte-checked unwrapping of editor-export redirects and exactly
two independently verified equivalent URI spellings. Neither transformation
changed visible text or fetched a source URL. The public publication checker
passed all 1,037 candidate rows and the reciprocal document links.

The companion renders to 241 PDF pages. All pages were rendered; the parent
inspected eight selected pages spanning the opening, inventory, actual reading
scopes and final assessments. Four late inventory tables retain narrow columns
that wrap row IDs. An additional paragraph explaining source-ID aliases could
not be inserted: semantic and positional preflights timed out, and no insertion
was dispatched. Original alias rows retain their own operation/inspection counts;
findings may belong to the shared canonical artifact. Zero on one alias row does
not establish that the underlying source was unread. This is usable complete
content with known presentation limits, not a claim of flawless layout or an
all-page visual review. Large companions need a provider-aware delivery strategy
independently of graph correctness.

## Implementation validation

The integrated suite at `29061914` passed 1,348 Python tests, nine shell suites
and skill validation, with no skips. The subsequent `c8c39dea` correction passed
224 graph tests and 136 other research compatibility tests, plus skill validation.
It validates review refresh stages during planning while retaining exact existing
historical intent for replay and closure; that compatibility does not authorize
new invalid admissions.

The subsequent inspection-withdrawal implementation passed 241 graph tests and
136 research compatibility tests. It tests interrupted and concurrent storage
upgrades, legacy snapshots, dependent evidence, partial rereads and inherited
withdrawal history. The full repository run at `93e182e0` then passed 1,369 Python tests in
850.75 seconds, nine shell suites and skill validation, with no skips.

Commit `d773ded4` adds explicit retained-capture bindings for new rereading
assignments when provider revision metadata is unavailable. It passed 252 graph
tests, including 11 new retained-read cases, and 136 compatibility tests. A copy
rehearsal completed two actual rereads while preserving the original 7,017-event
prefix and 1,027 evidence files. The original full-suite result remains bound to
its earlier revision; the subsequent change received affected-suite validation.

The live trial then exposed a remaining planning mismatch: retained-only work
could complete in the graph, but a saved plan still demanded a new provider
execution. Commit `b1677642` closes that boundary using the verified frozen
assignment and inspection evidence. It passed 256 graph and 136 compatibility
tests. A separate copy rehearsal saved both completed reread items, preserving
1,028 original files and its 7,021-event prefix. This rehearsal retained separate
before/after budget files and verified their equality.

Another live failure left four spent admissions with only retained requests and
no receipts. Public cancellation reconciled the stopped workers, but the budget
still classified those requests as running. The added `recover-retry` action
checks the cancelled request-only record and retained worker-stop evidence,
then records the local failure so an ordinary bounded retry can proceed.
It neither fabricates a provider receipt nor starts a fresh retry epoch. Tests
cover preserved originals and spending, the attempt cap, ancestry, stale or
unresolved operations, changed originals, and precedence of a late real receipt.
The affected checks passed 262 graph tests and 136 other research tests. A private
copy rehearsal preserved all 21,018 original evidence files and 23,850 graph
events while adding four budget classifications, with no provider calls or new
admissions.

A subsequent validation check exposed a separate boundary: final coverage
required every cancelled request to have a receipt and result index. The fix
retains verified request-only cancellations in checkpoints and reports them
separately from executed operations. It checks the original admission and
worker-stop evidence, preserves old export semantics, and gives cancellations
no read, claim or executed follow-up credit. The affected checks passed 269
graph tests and 136 other research tests, including a complete synthesis case
with both a real read and a cancelled attempt. Skill validation also passed.
A public-command rehearsal on the retained trial copy passed research
validation with 94 recorded operations: 88 with receipts and six request-only
cancellations. Its 585 operation and reader-return files and separate before/after
budget files were unchanged. Research remained structurally unfinished, as
expected; this was not a final trial pass.

The full repository run at `472c12a3` then passed 1,397 Python tests in
878.40 seconds, all nine shell suites and skill validation, with no skips.
The source remained clean before and after the run. The subsequent clock-owner
guidance changes Markdown only; it does not change the tested implementation.

Final synthesis exposed another integration gap: the budget checker demanded
inspection credit for four withdrawn returns and one accepted return that
declared no inspection. Frozen export verification now supplies their exact
non-inspecting dispositions, preserving all returned-lead obligations. On an
owned copy, the old checker failed and the repaired checker passed with all 927
original files and the budget bytes unchanged. Neither reading credit nor a
correction chain was inferred. Six new regression tests cover these boundaries.

Publication also needs to represent explicitly excluded prior outputs without
linking or fetching them. An optional visible row reason is now bound to the
frozen contract's exact excluded identities, including known aliases. Ordinary
source rows still require links. This changes navigation only; excluded sources
remain in the inventory and acquire no inspection or completion credit.
Ten additional navigation tests exercise the public publication boundary.
After both repairs, all 421 affected graph/research/legacy tests and the skill
validator passed. The final full repository run at `722038f6` passed 1,413
Python tests in 875.78 seconds, all nine shell suites and skill validation,
with no skips. The frozen source remained clean before and after the run.
The live delivery-only transition preserved separate budget/contract byte pairs
and all 18 checked old/new runtime-file hashes. Public synthesis on `722038f6`
then passed for the unchanged final ledger: 135 admissions, 129 receipts, six
cancellations, 1,037 source rows and eight claims. Parent verification checked
all 2,919 final export bindings, totaling 267.0 MB of logical files. Native
document publication subsequently passed the separate check described above.

Actual trials drove repairs to source scopes, rejected-return recovery,
terminology projection, pagination ancestry, bounded export manifests, display
metadata, provenance projection, capture lookup performance, standalone SQLite
snapshots, optional metadata recovery and prior-run planning. Retesting a repaired
continuation does not erase the original failure or make it a single-candidate
trial. See [the test procedure](../tests/fixtures/retrospective-research/RUNNING.md)
for the portable validation workflow.

## Independent review and correction status

The first independent GPT-family team covered all 135 file and checklist
obligations using six researchers. It reconciled 14 proposals into seven blockers
and six nonblocking follow-ups. A second team reviewed the correction delta and
continued applicability of that original analysis, covering all 136 obligations
with two new researchers. It found an additional budget-initialization failure
window, unreconciled late terminal receipts, and old invalid work that could no
longer be safely retired. Eight nonblocking findings are separately tracked.
Both original verdicts, proposals, reclassifications and coordinator decisions
remain preserved. These reviews do not substitute for the separately required
reviewer family, whose execution routes were blocked.

The corrections cover nonempty read scopes, effective completion after capture
withdrawal, normalization recovery, locator bounds, saved-question bindings,
interrupted admission and contract/plan publication. Budget initialization now
resumes without replacing its journal and establishes the return directory before
completing publication. Storage revision 3 accepts exact late terminal receipts
with retained original-worker cancellation evidence; revisions 1/2 SQL bytes,
history and snapshots remain unchanged. Narrow coordinator retirement permits
closing old wrong-question or empty-scope work without evidence credit, then
continuing through valid scoped work. Original unresolved admissions still need
checked reconciliation; no refund or new execution authority is inferred.

Focused validation passed 81 budget/publication tests, 313 worker-run graph tests,
and 119 integrated retirement/lifecycle tests. These sets overlap and must not be
summed. The dedicated continuation cases perform actual fictional reads and
inspections after retiring old invalid work. An initial retirement test failed
because its fixture omitted discovery provenance; the setup was corrected and
the failure retained. The earlier full run of 1,438 Python tests had one malformed
amendment lock-file regression, followed by its fix and 53 affected passing tests.

The final required repository run on integrated commit `fed486a4` passed all
1,463 Python tests in 904.79 seconds, all nine shell suites, and the skill
validator, with no skips. All 326 tracked source-file hashes stayed unchanged
during validation. Fixtures used owned local files, repositories and test
servers; no live research providers or managed activation were exercised.

Read-only synthesis validation of the same frozen live trial passed under the
integrated recovery corrections in 74.11 seconds. Storage remained at revision 2;
the graph sequence and budget, contract and bundle metadata hashes were unchanged.
This checks compatibility without rerunning discovery or improving coverage.
This compatibility result does not replace exact-source independent review.

The user then authorized a second independent GPT team in place of the
unavailable Claude team. Its full original-base review covered all 139
obligations and identified eight blocking defects. The correction batch adds:

- Typed locator containment and inspected support for accepted documented
  claims, including withdrawal-aware projection.
- Verification of generated evidence before its first export registration.
- Exact saved-plan work intent through assignment, execution and completion,
  with narrow historical compatibility for original routed searches.
- Idempotent recovery when a reader return was journaled but not yet published.
- Preservation of multiple source-version observations in search results,
  while captured content still requires an unambiguous version.
- A bounded standalone-WAL fallback on actual Python 3.9 runtimes.
- Current contract, plan and question bindings at synthesis, while historical
  plans retain their original wording.

The first integrated check ran 1,498 Python tests and failed with 15 failure
records and seven errors. Most were older fixture assumptions about saved plans
and inspection scope. One also exposed a real regression: a valid atomic
freshness-to-capture correction could make its own earlier operation unreadable
to historical validation. The repair preserves that exact admitted operation
and receipt, without permitting new dispatch. Its regression test covers the
same assignment's reread, completion, replay and export, plus changed-operation
and fresh-dispatch rejection. The original failures remain retained.

The complete corrected implementation at `f426c111` passed all 1,499 Python
tests in 922.48 seconds, all nine required shell suites and skill validation,
with no skips. All 332 tracked source hashes stayed unchanged. The new fixtures
use fictional evidence; existing withdrawal tests retain their insufficient
capture/extent cases and valid-survivor control. All 37 graph-store tests also
passed on Python 3.9.6 before the later work/ingestion compatibility repair.

Read-only synthesis compatibility on `f426c111` passed against the same frozen
live trial, preserving its revision-2 graph, sequence and metadata. It took
214.63 seconds while repository QA ran concurrently; this is not a controlled
performance comparison. No new discovery, provider calls, native document edits
or managed activation occurred. Both independent teams must endorse the same
final candidate after reviewing the correction impact and continued validity of
their own prior analysis. The integration PR records their final outcomes and
separate dispositions; tracked nonblocking findings are not a clean review.

## Costs and limits

The journal records declared activity intervals, not authenticated model reading
telemetry. Retrieval latency, helper execution time and overall task duration
must not be presented as per-document reading time. Synthesis and delivery use a
separate allowance.

The live continuation has three documented substantive reading intervals
without activity timing, including a corrective reread. Their duration cannot
be reconstructed from retained evidence. Recorded aggregate research time is
therefore incomplete; no estimated interval was inserted into the journal.
Two later verification intervals recorded only 0.17–0.19-second script calls,
not the surrounding semantic assessment. They are additional measurement gaps,
not evidence of near-instant document review. The remaining live assignments use
supervising-coordinator clocks from dispatch through observed completion. The
skill now makes that clock ownership explicit in assignment and budget guidance;
this is a recorded workflow intervention, not authenticated model telemetry.
A final coverage clarification also had a short dispatch-to-clock gap. It is
recorded separately from the three substantive reading gaps; no duration was
invented for it.

Bookkeeping costs are material. R3's retained helper log contains 490 calls,
including 46 nonzero exits, totaling 266.44 seconds. An earlier summary listed
589 total calls without a reconciled broader denominator; this report uses the
observable saved log. Reuse recorded 526 calls,
including 35 nonzero exits, totaling 289.12 seconds. R2's 51 exports represented
716.6 MB of logical files; R3's 40 represented 213.1 MB. These are helper and
storage measurements, not token costs. Failed calls include rejected operator
drafts as well as implementation defects.

Parent editorial review improved the controlled briefs and reduced companion
density. The final R3 brief contains 235 authored words; reuse contains 191.
Rendered PDF checks and citation/target checks were completed with their scope
recorded. HTML browser layout and interactive PDF-relative-link behavior were
not established. Some early rejected drafts were overwritten in R3; their
absence remains a preservation limitation rather than being reconstructed.

The retained private trial records include original operations and returns,
version transitions, manifests, budgets, review reports, artifacts and parent
checks. Internal documents and source identities are deliberately absent from
this portable repository. No result promises exhaustive provider coverage or
turns a bounded, inaccessible or unread source into evidence of irrelevance.
