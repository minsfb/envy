# Protected follow-up research

Initial retrieval must not use the allowance needed to investigate what reading
reveals. New delegated investigations reserve 30% for baseline breadth, 50% for
at least two evidence-derived follow-up rounds, and 20% for expansion and coverage
review. These are shares of one global allowance, including complete supervised
reading/assessment/coordination windows. They are not new per-worker budgets.

The default research allowance is 330 active minutes and 600 aggregate
agent-minutes. Existing count, retry and depth limits remain. Baseline means
sufficient diverse seed coverage, not inspecting every initial hit. Follow-ups
start as soon as useful reader returns arrive. A bucket can borrow unreserved
expansion capacity; it cannot borrow another unfinished follow-up's reserve.
If adequate baseline still cannot fit, retain an explicit cap amendment and its
reason. Delivery, synthesis and editing remain outside research admission.

## Implementation boundaries

- Opt in with `requirements.protected_followups`; historical journals retain
  their original accounting and scheduler behavior. New protected workflows
  require the graph, saved research plan, and budget recorder.
- Classify activity by allocation, purpose and branch. Bind operation allocation
  to immutable saved work and actual ancestry. Generation zero is baseline;
  generations one and two are the two full follow-up cycles. Search results and
  their reads retain their search's generation. Coverage reviewers use expansion.
- Keep one atomic budget journal. Attribute counts and observed time to each
  bucket. Individual bucket active-time unions may overlap; aggregate effort and
  the global active-time union retain their meanings. Unreported model work
  cannot be forcibly stopped by a provider admission helper.
- Permit baseline and the first available follow-up generation to interleave.
  Balance eligible branches/questions and favor unserved work. Known terms and
  reference surfaces must be promoted to executable work or explicitly triaged
  before deeper research advances; a plan containing only established branches
  is not a complete frontier.
- Derive follow-up status from the graph rather than maintaining another source
  inventory. Preserve every original lead/reference, including opaque IDs and unresolved
  title mentions; group duplicate reference
  targets, and show uninspected source surfaces even when global search is absent.
  Host-to-provider mapping is run configuration, never platform-specific code.
  Metadata-only discoveries on known hosts create a surface coverage obligation,
  without requiring an individual read of every initial result.
- Retain graph-bound allocation checkpoints. Releasing unused reserves requires
  completed work, verified full-cycle evidence or an explicit no-opportunity
  explanation, and item-specific dispositions for unworked leads. Released
  capacity joins one shared pool. Later leads retain their ancestry and compete
  for that pool; release never makes late work impossible.
- A final protected stop checkpoint distinguishes a reached cap, a user stop,
  and a discretionary stop with allowance remaining. It retains remaining
  capacity, completed cycles, pending work and reasons. New graph evidence or
  contract/plan changes invalidate the final stop review. A legitimate bounded
  stop still permits the separately funded deliverables to be completed.
- An explicit partial stop preserves plausible unresolved frontier and work
  without changing scheduling state. Item-specific decisions distinguish verified
  local allocation exhaustion from a choice to stop with usable capacity. These
  decisions do not satisfy completion, reserve release or deeper-work admission.
  Partial synthesis retains unresolved graph checks and returns incomplete
  coverage; source scopes, questions, claims, receipts and semantic review still
  require their normal evidence. Its snapshot also binds reserve-release funding
  changes. See the [partial delivery procedure](../assets/agent-assets/skills/retrospective-research/references/protected-followups.md#deliver-with-unresolved-research).

## Verification and limitations

Use isolated fictional records and retained receipts to test reserve exhaustion,
concurrent admissions, borrowing, amendments, late leads, generation/allocation
binding, branch fairness, unscheduled terminology, uninspected reference surfaces,
false cap claims, stale checkpoints and historical compatibility. Exercise a fresh
agent on a local provider corpus through the public workflow. Run repository QA
and independent exact-commit review before publication.

Mechanical checks establish recorded work and explicit decisions. They cannot
prove query recall, semantic relevance, complete extraction, or that a prose
reason is sound. Independent coverage review still reads source passages,
organizing records and rejected leaves and adds follow-up work. Count a query as
part of a full cycle only when its results receive the selected inspection pass,
or its retained result establishes no results; repeated searches alone do not
prove two rounds of investigation.
