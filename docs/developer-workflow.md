# Developer workflow skills

The shared workflow starts from user intent. `develop` recognizes project
exploration and change requests and selects the next needed phase. It does not
load every skill or turn a discussion into authorization to build or publish.
The short global guidance rule makes that entry discoverable without requiring
users to remember command names or a sequence.

| Phase | Responsibility | Useful output |
|---|---|---|
| `design` | Product outcome, architecture and consequential uncertainty | Agreed intent, constraints, success criteria |
| `plan` | Scope, dependencies and proportionate process | Checklist or workstream plan with integration ownership |
| `implement` | Bounded changes under the agreed design | Coherent increment and focused evidence |
| `qa` | Relevant validation and execution boundaries | Actual results, source identity and coverage limits |
| `review` | Independent findings and required gate | Candidate-bound evidence and dispositions |
| `ship` | Authorized local commit, publication or merge | Verified source/destination and receipt |
| `reflect` / `handoff` | Improve recurring practice or preserve continuation | Selected proposals or current recoverable state |

A clear small request can satisfy design and planning already. Larger or riskier
work warrants more explicit ownership, checkpoints, review, and validation.
`coordinate-workstreams` and `review-architecture` support those cases without
making a fixed agent team mandatory. The work plan separates agent concurrency
from expensive build/device ownership.

At the end of a major autonomous implementation and review cycle, use the
[cycle completion checkpoint](../assets/agent-assets/skills/implement/references/cycle-completion.md).
Completed repository work has local semantic commits; uncommitted work remains
awaiting commits even if tests and review are complete. The checkpoint reports
overall progress and per-task commits/diffs, review verdicts, test results and
test changes, device automation and fixture changes, human validation, and
design pivots. With applicable session or standing authority, publish each
task's bulleted report and verify it by reading it back. Publication, human
acceptance, task closure and final shipment remain distinct stages.

For substantial investigations of past work, use
[`retrospective-research`](../assets/agent-assets/skills/retrospective-research/SKILL.md).
It supports decision histories, outcome and contribution assessments, and recovery
of prior work through adaptive scoping, a living research plan, source coverage
records, and collaborative editing. Its default deliverable is a purpose-driven brief linked to an evidence
companion. This research workflow can inform design or stand alone; it does not
require entering the implementation and shipping phases. Platform-specific source
access and document operations remain with the available provider skills.
Its provider-neutral evidence helper captures operations and checks declared
record and HTML publication invariants. Planned searches and source-chain follow-ups
are bound to operations; the inventory distinguishes capture, inspection, analysis,
and disposition. Delegated investigations consume those checks before accepting
research and artifacts. Final prose and presentation passes complement the checks;
source recall, citation meaning, and rendered layout still require judgment and
provider tools.

The implemented [research graph workflow](../assets/agent-assets/skills/retrospective-research/references/graph-workflow.md)
uses SQLite to track sources, evidence, assignments, and follow-up work. The
helper supports graph initialization, recorder ingestion and recovery, scoped
read completion, bounded expansion, and prior-research import. The
[design and implementation plan](retrospective-research-graph-design.md) records
the architecture and delivery sequence; the workflow reference documents the
current commands and evidence requirements.

## Native procedures and ownership

Git task isolation uses the owning primary checkout's ignored `.worktrees/`,
including when invoked from an existing linked worktree. The
[placement reference](../assets/agent-assets/skills/implement/references/repository-placement.md)
documents the shared read-only planner and explicit preparation command, unusual
layouts and workspace organization. Environment-specific isolation and storage
locations belong in bundles.

Portable skills, `review-git`, `ship-git`, and GitHub/GHE `pr-triage` live in envy.
The native split preserves the existing reviewer-led team defaults, v3
continuation, workset cadence and blocking-only versus clean-only gate policy.
Environment bundles contribute uniquely named procedures for other VCSs and
provider-specific policies. GHE can reuse Git/GitHub mechanics with environment
access guidance rather than a duplicate lifecycle.

The existing Git helpers remain at `review/scripts/review_bundle.py` and
`ship/scripts/git_publish.py`, with their references and safety contracts intact.
Only the concrete Git skills invoke them. Other native procedures must establish
their own source and receipt evidence; they cannot report a Git-controller pass.

Descriptions carry concise trigger metadata. Supporting details are loaded only
when needed; no custom cross-harness workflow metadata is required. The same
sources are linked into Claude and `.agents/skills` discovery through the asset
manifest. Harnesses supporting `.agents/skills`, including Codex and compatible
Pi versions, can discover those files; this does not assert identical runtime
selection behavior across harnesses. Harness-specific agent wrappers delegate
to the shared skills rather than maintaining separate procedures.

Chezmoi links the canonical `agent-guidance/AGENTS.md` into Claude, Codex and
Pi's user context locations. Pi's native `~/.pi/agent/skills` directory is an
alias of the shared `~/.agents/skills` root, and Pi deduplicates those paths by
canonical skill identity. The asset engine continues owning individual skill
links in the shared root and Claude's selected root; Codex-owned system/plugin
assets and unrelated Claude skills retain their ownership. An existing Pi
skill directory requires explicit reconciliation through the normal Chezmoi
conflict workflow; it is not silently replaced. Restart active sessions after
guidance or skill changes to refresh their discovered context.

## Migration and validation

The existing version-2 [ownership handoff](agent-assets-handoff.md) governs
`pr-triage`; this workflow change does not replace that protocol. A legacy bundle
must exclude its duplicate from discovery while retaining the exact predecessor
files for the supported migration window. Fresh composed installations can use
the public provider. Migrating an existing private provider still needs its
compatible declared handoff and adapter; exclusion alone does not authorize
adoption of the old links. Preserve the handoff's staged rollout and source pins.
Compose exact revisions before separately authorized activation, preserve foreign
skills, and never relink or erase ownership state to make a review pass.

`test_workflow_assets.py` exercises real distribution, refusal of undeclared legacy
transfer, duplicate rejection, foreign-file preservation, and the existing Git helper entry points
in temporary homes. The existing controller/publication tests validate their
behavior. The decision probes under `tests/fixtures/developer-workflow` exercise
workflow selection and scope through an independent agent; structural tests do
not establish model behavior or successful live publication.
The synthetic cases under `tests/fixtures/retrospective-research` exercise
historical evidence, contribution attribution, comparisons, and finalization.
`test_retrospective_research.py` exercises capture failures, requirement changes,
evidence acceptance and artifact-content loss in isolated fixtures.
`test_research_work_plan.py` covers saved plans, assignment bindings, follow-up
provenance, source-state transitions, and synthesis/publication reconciliation.
