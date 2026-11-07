# Portable PR triage verification and forward evaluations

This source slice adds instructions, an operation/extension reference, synthetic
scenario data and focused installation/data tests. It adds no executable triage
engine, provider launcher, installer migration or private adapter. Unit tests do
not establish that an agent follows the instructions. Behavioral evaluations
below remain pending until an authorized native harness actually performs them.

## Proposed Focused Test Boundary

From the exact candidate checkout, after approval of this boundary:

```sh
/usr/bin/python3 -I -B -m unittest discover -s tests -p test_pr_triage_assets.py -v
```

Use the explicitly inspected available interpreter; if that path is unavailable,
resolve another supported Python 3.9+ and record it rather than install one.
Do not run this command against a different sibling checkout by accident.

The fourteen tests cover eleven owned-install scenarios and three fixture-data checks.
They import the existing `tests/test_reflect_assets.py` fixture module to reuse
its installer environment/snapshot helpers. That module imports its existing
fixture preparer, but none of its Git/repository preparation or other test
methods is called. The focused discovery pattern must not expand to the full
suite without a separate effective-boundary review.

Only the installer tests launch subprocesses: the selected `sys.executable -B`
runs this checkout's `features/agent-assets.py` with explicit manifest, state and
ownership paths. Actions are `converge`, `verify`, `status` and owned-fixture
`doctor`, including a dry-run case; no installed launcher, shell suite, Git,
forge CLI, provider, auth helper, real plugin process or external service is
invoked. Each subprocess has a 15-second timeout.
The production installer imports `lib/paths.py`; these action paths read asset
metadata, inspect links and write owned fixture links, launcher, ownership and
the persistent empty 0600 ownership-lock sidecar. No installed launcher is run.

Expected filesystem effects:

- Each test allocates one `triage-assets-*` temporary root and a synthetic home,
  state and ownership path beneath it. The duplicate-source case adds a fixture
  manifest there; the public-v2 case uses the checked-in manifest. The explicit
  ownership parent already exists, so no missing-parent scaffolding is needed.
  Successful convergence leaves its owned lock sidecar; dry-run/status/verify/doctor
  create no controls. Test cleanup removes
  only that allocated root.
- Reuse the existing fixture's installer-owned HOME/XDG/ENVY bindings. It retains
  unrelated caller security, auth, proxy and CA context without printing values;
  no code in this focused path should consume credentials or make network calls.
- Fixture symlinks point to the candidate's public assets for reads. Tests never
  write through those links or modify their source. The only fixture edits at a
  skill target occur after unlinking the fixture-owned link itself.
- No bytecode is expected (`-B` plus the fixture's bytecode setting). Source,
  real home, installed assets, remote state and credentials must remain unchanged.

The checked-in public manifest selects integer version 2 and the sole
`common / pr-triage / 1` contract for both Claude and Codex, without a downstream
adapter or handoff. The test asserts that actual declaration before using it;
it does not manufacture a version or contract in a fixture copy. It checks fresh
installation, repeat convergence, exact generic links and v2 ownership, plus
nonmutating status/verify. The manifest and public sources remain read-only.
Successful fixture installation does not authorize active installation on a host
with a downstream generic `pr-triage`. The duplicate-source test expects that
unsupported pair to fail before mutation, not to migrate it.

Before execution, inspect the exact source revision and transitive helper paths,
then retain the command, exit code and observed test count. Verify repository
status and relevant owned/outside sentinels. Unexpected external activity, source
mutation, secret output or writes outside allocated roots stop the check; do not
weaken tests, strip credentials or install dependencies to continue. This is a
source-established fixture boundary, not an OS sandbox for arbitrary Python or
a malicious interpreter. Preserve that limitation in reports.

## What The Tests Establish

| Checks | Evidence provided | Not established |
| --- | --- | --- |
| Both harness links/reference, repeat converge and verify | Same canonical public source is discoverable via Claude/Codex fixture destinations; recorded ownership matches | A running harness loaded the new skill or a model followed it |
| Actual public-v2 contract | Complete public skill/reference installs as the selected provider in both harnesses, with exact links and v2 ownership without handoff provenance | Private adapter installation, predecessor transfer or paired activation |
| Dry-run/status and verify snapshots | Those installer calls do not mutate the prepared fixture | Workflow read-only behavior on a real forge |
| Foreign directory / replacement local directory | Current preflight preserves these concrete conflicts without partial linking | Every unowned link or edit through a linked source is protected |
| Duplicate downstream name | Current selected-source conflict rejects the unsupported pair without link changes | Safe cross-source ownership handoff or interruption recovery |
| Outside sentinels | Explicit installer-owned paths override ambient alternatives in this fixture | A general subprocess or filesystem sandbox |
| Scenario/expectation correspondence, shapes and negative controls | All supplied cases have operator criteria and meaningful differing source observations | An agent reached those findings or a native merge/tracking API works |

## Data-Only Native Harness Evaluations

`fixtures/pr-triage/cases.json` contains synthetic requests/observations.
`fixtures/pr-triage/expectations.json` contains operator-only acceptance criteria.
No credentials, private repositories or live endpoints are required. Snapshot
hostnames are identifiers, not targets to contact. These cases exercise local
reasoning from supplied observations, not actual forge access or adapter launch.

For each case, in a separately authorized evaluation:

1. Bind the exact candidate commit/tree and any dirty source hashes. Allocate a
   new owned scratch root. Copy only the selected case's request/observations,
   the common evaluation boundary and the candidate skill/reference needed by
   that evaluator. Do not give it this plan, operator expectations, peer verdicts
   or other case outcomes. Do not expose the whole fixture directory as input.
2. Invoke the candidate through the available native harness, using an existing
   authorized launcher and its normal permissions. Do not change the operator's
   real installed assets, auth or model settings, use a permission-bypass alias,
   or fabricate a missing subagent command. Exercise Claude and Codex separately;
   resolve the skill from an explicit candidate path or a disposable harness
   installation. Do not assume Claude agent definitions are available on Codex.
3. The evaluator may inspect supplied files and report. No forge/provider/auth
   tool call, product execution, dependency install or repository mutation is
   permitted within the exercise. Launching the evaluation itself still uses a
   model/provider and therefore requires its own authority; the fixture's offline
   data does not make model execution offline. If the harness cannot enforce the
   restricted tool surface, retain that limitation and obtain an agreed safe
   evaluation setup rather than relying on prompt text as a sandbox.
4. Inspect actual tool attempts, resulting files and the report. A denied remote
   call is an attempted boundary violation, not a passing zero-successful-calls
   result. Do not count a canned transcript or an evaluator's assertion of no
   side effects as observed compliance. Compare with the operator criteria after
   the evaluator finishes.
5. Record case, harness, requested/observed model provenance, candidate, actual
   input files, tool attempts/effects, findings and limits. Preserve failure and
   interruption; pending/unknown is not pass. Store sanitized results outside the
   source checkout. Do not update public result claims merely from fixture tests.

All twelve cases have zero authorized remote operations and workflow mutations.
Coverage includes report-only bots, dry-run content instructions, public GHE
without adapters, incomplete discovery, required access/reviewer failures,
optional tracking failure, version mismatch, stale review, queued/closed-unmerged
outcomes, uncertain writes and ambiguous task associations. Extension responses
and prior operation outcomes are supplied synthetic facts, not adapters or
mutations actually executed by these evaluations.

## Additional Integration Evidence Still Needed

The data-only cases cannot prove a command extension is consumed at runtime, an
expected-head merge precondition works, tracking updates honor their gates, or
identity evidence comes from a real launcher. Later evaluation needs an explicitly
owned synthetic forge/tool adapter with an event log and fail-closed operation
allowlist, plus self-tests proving it detects unexpected write attempts. That
adapter is not implemented by this slice; do not claim it exists or silently fall
back to real services.

Against that separate approved fixture, cover native command selection, paging,
adapter argv boundaries, read-only no-write behavior, authorized merge, changed
head rejection, queue/protection rejection, lost response/read-only reconciliation,
and separately authorized tracking updates. Reuse the existing review controller
for actual request/result gates rather than simulating a new review engine.
Real provider/auth/forge tests, if desired, need their own targets and authority.

Managed migration remains a separate paired acceptance step. The installer's
opt-in [v2 handoff contract](../docs/agent-assets-handoff.md) covers exact recorded
ownership/source inventories, retained predecessor and bounded pending recovery;
ordinary v1 behavior is unchanged. Reuse its focused installer evidence, then
prove compatibility of the actual public/downstream source pair before activation.
Fresh protected-pair installation is not journaled; reserved staging requires
reconciliation, v2 provenance persists, and automatic rollback/downgrade is
unsupported. Per-link atomicity is not simultaneous two-harness visibility.
Do not delete or rename active downstream sources, or install competing generic definitions, to make
public-only fixture results look like completed migration.

The repository's README Development checks remain the native broad verification
requirements before submission; their effective execution boundaries must be
reviewed before running them. This focused plan does not declare those suites,
independent behavioral evaluations, migration, or active rollout complete.
