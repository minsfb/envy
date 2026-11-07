# Offline review bundle procedure

## Boundary and storage

Requires Python 3.9+, Git, and Unix advisory file locks. Only fixed read-only
Git builtins are invoked, never a reviewer, arbitrary command, test suite,
provider, fetch, push, or merge. Caller-selected Git and inherited harness/
security context remain trusted. This is not an OS sandbox for a malicious
Git executable or concurrent hostile filesystem writer. Accepted Git output
sizes and execution time are bounded; output capture is not a hard memory
limit on Git itself.

Choose an existing private parent outside source repositories and their Git
metadata, then a new store path; keep request outputs and other review evidence
outside them too. `prepare` requests creation modes of 0700 for the directory
and 0600 for the ledger and lock; the caller's umask can further restrict them.
The helper does not chmod them to override the caller's security policy.
Records contain private local paths, diffs, and sanitized evidence; being
outside Git is not permission to publish them. Never put credentials in inputs,
paths, arguments, or logs. `--sanitized` requires the operator's pre-capture
audit, not a machine proof arbitrary text is safe. Only UTF-8 text attachments
are supported, up to 8 MiB each. Every tracked regular file read for parent
cleanliness is also limited to 8 MiB, even when unchanged in the diff; a larger
tracked asset blocks preparation/readiness. Each accepted Git output, including
the full diff, is limited to 8 MiB. Generated diff text must also decode as UTF-8;
decoding failure produces `invalid_input_or_local_operation`, which alone does
not establish that cause. Inputs, result and ledger JSON reads are
limited to 32 MiB each; serialized ledger/request writes, including their final
newline, are limited to 32 MiB. Schema field/history limits apply as well.
Schema lists default to 100 entries in v1, v2 and v3 unless a field-specific
bound below overrides that limit.

The shared bounded-text guard applies to copied artifacts and the bound diff,
IDs, and report/provenance references, not only coverage paths. Characters below
U+0020 other than newline/tab fail with `invalid_string`, including carriage
returns in CRLF attachments or diffs even when raw worktree bytes match Git.
Text matching the credential-shaped guard patterns fails with `sensitive_input`.
Legitimate diff content or opaque native IDs can therefore be unsupported. Report
the limit without exposing rejected values; do not rename native IDs, rewrite
user source or weaken the guard to make evidence fit. Text fields default to
4,096 characters unless a field-specific bound states otherwise.

Repository/store and external input/output paths are made absolute and lexically
normalized with `os.path.normpath` before symlink checks. No remaining component
may be a symlink; components discarded by normalization are not inspected.
This is lexical dot-segment removal, not symlink resolution.
Normalized absolute paths checked by the controller, including ordinary tracked
regular-file paths opened during cleanliness checks, also pass the shared
bounded-text guard and its default 4,096-character limit. Legitimate local paths
can therefore be unsupported.
Parent components must be directories; inputs, results,
attachments, ledger and lock must be regular files, never FIFOs, sockets or
devices. The lock must be empty. New request outputs are created exclusively.
Resolve intended operator-owned paths before capture: on macOS `/tmp` is normally
a symlink, so use its real `/private/tmp` path when appropriate. Do not replace
user links or move user work to satisfy this gate. Ordinary tracked Git symlinks
are compared as raw link-target bytes, not followed. A non-UTF-8 target unchanged
between base and head can pass this comparison. The UTF-8 tree-path and
generated-diff limits still apply: changing target bytes can produce a non-UTF-8
diff and fail with `invalid_input_or_local_operation`. That generic code alone
does not establish this cause; byte-exact link hashing is not universal
non-UTF-8 repository support.

The candidate digest covers repository identity, base/head/tree, full generated
diff, gitlink scope/pins, questions/dependency changes, policy, and copied
evidence. Original attachment paths are not retained or consulted after copying.
Changing an original scratch file does not update the frozen bundle: prepare a
successor to introduce new evidence. Tampered content/digests are rejected;
hashes are not authentication against an operator able to rewrite both.

The ledger is a serialized series, not a general workflow database. Atomic
checkpoints retain candidate and attempt history. `supersede` selects a new
active candidate in one checkpoint, without approval carryover, even for the
same tree at a new commit. Limits are 32 candidates, 32 reviewer slots, and 128
attempts per candidate, plus 512 disposition records per v2/v3 candidate. The
32 MiB ledger limit may be reached first. Do not delete history to make room;
the helper never cleans or publishes this evidence.

## Inputs and preparation

The input/result examples below describe the unchanged v1 schema. Explicit team
reviews use v2, described below, or explicitly opt into v3 for
[same-session synthesis continuation](continuation.md). Read that reference before
preparing v3 or using its continuation commands. V3 otherwise retains the team
policy, result and coverage contracts below; old v1/v2 candidates are unchanged.
Shared storage, source checks, supersession, retry, exit-code and diagnostic
rules apply across versions unless
explicitly qualified. A v1 input must omit `version`: even `version: 1` is rejected
with `invalid_schema`. V1 results, requests and ledgers still carry their explicit
version; existing histories remain valid.

All object fields shown are required; unknown fields and duplicate JSON keys
are rejected. Identifiers are at most 128 characters, use ASCII letters, digits,
dots, underscores, and hyphens, and start with a letter or digit. Hashes are
lowercase hex. At least one slot must be required. Questions number 1–50;
supplied artifacts and derived gitlink entries are each capped at 100. The
generated diff is separate from the supplied-artifact list. Exceeding these caps
reports `invalid_list`, which also covers other malformed or oversized lists.

Example `inputs.json` beside an audited `checks.txt`:

```json
{
  "scope": "Review the committed parser change. No publication.",
  "questions": ["Are malformed inputs rejected without partial output?"],
  "dependency_changes": "No dependency or contract changes.",
  "gitlink_scope": "none",
  "reviewers": [
    {
      "id": "independent-reviewer",
      "required": true,
      "adapter": "native-manual",
      "requested_model": null,
      "identity_basis": "harness_metadata"
    }
  ],
  "artifacts": [{"name": "supplied-checks", "path": "checks.txt"}]
}
```

Artifact paths are relative to the inputs file, cannot traverse `..`, and
cannot use symlinks. The helper reads only these attachments, not a directory
tree of logs. The full diff comes from explicit base/head commits, which must
be full 40- or 64-character IDs; the head must be checked out.

Use a dedicated clean worktree or clone pinned at that committed head when the
user's working checkout contains ongoing work. Do not clean/reset their checkout
to satisfy preparation. Give restricted reviewers readable diff/base views and
an inventory alongside the bound request; an escaped single-line JSON diff is
not a substitute for accessible review material.

```sh
python3 -I -B /path/to/review/scripts/review_bundle.py prepare \
  --repo /path/to/candidate-worktree --base FULL_BASE --head FULL_HEAD \
  --inputs /private/review-inputs/inputs.json --sanitized \
  --store /private/reviews/parser-round
```

The shared parent cleanliness check compares tree/index records and raw worktree
bytes/modes without clean filters, textconv, external diffs, or fsmonitor.
Regular-file mode comparison uses the raw owner-execute bit regardless of
`core.fileMode`. The setting itself is accepted, but a Git-clean checkout that
loses the committed bit can still be rejected; the checkout/filesystem must
preserve that mode for this workflow.
This check also applies when revalidating v1 source: unlike the former
any-execute-bit comparison, group/other execute bits neither supply a missing
owner bit nor cause a mode mismatch for a committed non-executable file.
The owner-execute comparison changes source revalidation, not the schema or
interpretation of retained v1 records.
It rejects staged/index changes relative to HEAD, untracked files including
ignored files, line-ending/filter-converted bytes, and sparse/missing parent
files. Preserve user work rather than resetting it. The enumerated routing
variables `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`,
`GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, and
`GIT_NAMESPACE` are refused when present. Other inherited Git configuration,
including configuration-injection variables, remains trusted context, not
sanitized input. Partial-clone detection refuses repositories with local Git
configuration names matching `remote.*.promisor` or `extensions.partialclone`;
it does not inspect all inherited configuration scopes. Separately, every fixed
Git call sets `GIT_NO_LAZY_FETCH=1`
to suppress lazy object fetching; missing objects are not acquired.

For compositions, choose `gitlink_scope: "pins-only"` and describe the dependency
change and evidence explicitly. The bundle binds every base/head gitlink ID,
including unchanged pins. It checks parent index pins and, for initialized
submodules, matching checked-out HEADs. Uninitialized submodules need no object
fetch. Nested content, dirty state, object availability, compatibility, and
submodule code review are not established; the status/request reports this
limitation. Add separate reviewed source/evidence for those claims. With
`gitlink_scope: "none"`, any gitlink in base or head blocks preparation.

The fixed source check repeats on current status/import/start, plain `resume`,
request re-export, and v2/v3 `disposition`. Record a fix decision before editing
the bound checkout.
These operations repeatedly walk and validate the full parent tree, including
unchanged files, rather than checking only the diff; account for that cost on
large repositories. Checks before and after capture remain necessary.
Source validation cannot guarantee an untrusted concurrent writer leaves files
unchanged afterward.

## Native/manual adapter

`adapter` is a policy label, not an executable. `identity_basis` is exactly
`harness_metadata` or `operator_attestation`, chosen before review begins.
The latter means a trusted operator attests an observation from an identified
source, not a model self-report. In v1, an exact `requested_model` requires a
matching observed model and admissible evidence; use `null` deliberately for
an unconstrained model. V2/v3 require an exact non-null `requested_model` and
apply the [versioned identity rules](#team-results-identity-and-coverage)
below. No version silently expands aliases or substitutes models.

```sh
python3 -I -B /path/to/review/scripts/review_bundle.py start \
  --store /private/reviews/parser-round --expect CANDIDATE_DIGEST \
  --slot independent-reviewer --reason "Authorized initial review" \
  --output /private/reviewer-inputs/request.json
```

The new 0600 output has `request`, `request_digest`, and `bundle`. V1/v2 outputs
contain no result history. V3 adds `continuation_history`: empty for an initial
attempt or ordinary retry, otherwise only that team's linked predecessor
attempts and results, as specified in [continuation](continuation.md).
The start receipt is committed before output creation; an
export failure can leave an in-flight attempt. `resume` identifies it and can
re-export the identical request. Outputs never overwrite files. The store/output
path guard excludes only the candidate worktree, not Git metadata outside it or
other repositories; operators must keep evidence outside those locations too.
Give only this exported request and authorized source/report material to a
reviewer, never peer results or access to the complete result ledger.

Launch through the available native harness or separately authorized external
reviewer. Record actual run/session identity, observed metadata source and its
documented meaning, terminal outcome, and tool evidence when available. The
controller does not parse provider streams or infer runtime facts from flags.
If a harness supplies only requested/inherited model selection without
established observed-model semantics, preserve that limitation: a v1 exact
observed-model requirement remains blocked. In v2/v3, actual launch evidence judged
admissible by the operator can satisfy `launch-selection` while served identity
remains unknown; `served-model` additionally requires matching served identity.
Requested-only evidence is insufficient, and contradictory identity blocks completion.
No new provider call is necessary to acknowledge missing evidence. Thin
provider adapters can later implement this contract.

## Results and import

Example shape (replace binding fields from the actual request):

```json
{
  "version": 1,
  "candidate": "CANDIDATE_DIGEST",
  "attempt": "ATTEMPT_ID",
  "request_digest": "REQUEST_DIGEST",
  "slot": "independent-reviewer",
  "adapter": "native-manual",
  "run_id": "actual-session-id",
  "verdict": "pass",
  "identity": {
    "basis": "harness_metadata",
    "reviewer": "actual-reviewer-id",
    "model": null,
    "evidence": "Sanitized observation and its source semantics, not a flag."
  },
  "findings": [],
  "executed_checks": [],
  "supplied_evidence": ["supplied-checks"],
  "limitations": ["Source review only; no checks executed by this reviewer."],
  "provenance": {
    "importer": "operator-id",
    "source": "Durable sanitized source reference or observation summary",
    "source_sha256": "SHA256_OF_THE_REFERENCED_SOURCE"
  }
}
```

Verdicts are `pass`, `changes-required`, `failed`, `cancelled`, or `interrupted`.
Identity basis also accepts `self_report`, `requested_only`, and `unknown` to
preserve incomplete evidence, but those cannot satisfy required readiness.
Reviewer/model may be `null`; missing reviewer or empty identity evidence
blocks required readiness. The importer must verify that its reference/hash
and observation correspond; the controller checks structure, not external
provider authenticity.

A non-passing result may have `run_id: null` when no runtime ID was observed
(for example, interrupted before launch). Do not invent an actual session ID
to close that attempt. A passing result requires an observed run ID.

In v1, non-null `run_id`, `requested_model` and identity reviewer/model values
must use the 128-character identifier grammar above; slash-containing native
task paths do not fit. Report that compatibility limit without renaming actual
IDs. V2's distinct run-ID and model-string rules below do not relax v1 history.

A finding is `{ "id": "finding-id", "severity": "blocking", "summary": "..." }`;
severity is `blocking` or `nit`. Any blocking finding, even from an optional
reviewer or paired with a pass, prevents readiness. Limitations are a list of
strings. Supplied evidence names only bundle attachments.

Each `executed_checks` entry has these fields:

```json
{
  "command": "python3 -B tests/check_parser.py",
  "cwd": "owned-review-workspace",
  "mode": "isolated",
  "revision": "FULL_TESTED_COMMIT_AND_RELEVANT_DIRTY_SURFACE",
  "outcome": "pass",
  "exit_code": 0,
  "writes": "Owned fixture files only",
  "evidence": "Sanitized captured result or durable reference",
  "provenance": "reviewer_reported"
}
```

Modes are QA's `isolated`, `integration`, or `live`; outcomes are `pass`, `fail`,
`skipped`, `interrupted`, or `unknown`; exit code may be null. Provenance is
`reviewer_reported` or `adapter_observed`. A reviewer's own account of running a
command is `reviewer_reported`; `adapter_observed` requires adapter-captured
execution evidence, not that account or a self-applied label. A supplied green
log belongs in supplied evidence, not executed checks. The controller cannot
prove that a reviewer executed a reported command or judge test sufficiency.

QA's `blocked` is not a schema outcome. For a blocked check that never ran, use
`skipped` with a null exit code and evidence/limitations explicitly stating
"not executed" and the reason; alternatively record only that limitation.
Neither represents a test execution. If a check started but could not complete,
use its actual `interrupted` or `unknown` outcome and preserve observed facts.

QA's `unresolved` mode is not a schema mode. If the execution boundary could not
be established, retain the known facts and the gap in limitations; do not guess
an isolation mode to create an `executed_checks` row. If a command did run,
preserve its observed execution facts there without inventing the missing mode.
Checks that are only recommended also belong in limitations, not executed checks
or supplied execution evidence; explicitly label them "recommended, not run".

```sh
python3 -I -B /path/to/review/scripts/review_bundle.py record \
  --store /private/reviews/parser-round --expect CANDIDATE_DIGEST \
  --result /private/review-results/result.json --sanitized
```

Imports bind candidate/request/slot/adapter/attempt. Reusing one adapter run ID
for independent slots/rounds is refused; this does not prove independence
outside the workflow. Results cannot be overwritten. Preserve bad/incomplete
reports and make a new candidate when substantive corrections change inputs.

## Interruption, revision, and readiness

`resume --store STORE` is ledger-read-only and reports completed, not-started, and
in-flight slots. It never launches or retries. Its optional request export
creates a new file exclusively without modifying the ledger. Re-export saved
input with
`resume --attempt ID --output NEW_PATH`; first reconcile whether the original
reviewer is still running or completed. Import a later terminal result normally.
If abandoned, record an honest interrupted/cancelled result with unknown facts
as appropriate. Then `start --retry LATEST_ATTEMPT_ID --reason REASON` creates a
distinct attempt. The retry value is the latest controller attempt ID for that
candidate's slot, not the native reviewer run/session ID. Native-run
reconciliation remains an operator step, not a controller launch or retry.
An explicitly opted-in v3 candidate can instead use
[lead-only continuation](continuation.md) after an interrupted result. That
separate transition binds prior evidence and additional resource authority;
ordinary `--retry` still requires fresh runtime identities. `resume` itself
never runs a reviewer or creates a continuation.
Only failed/cancelled/interrupted attempts can be retried;
a pass or rejected review cannot be rerolled in place. `changes-required` is
terminal for that candidate's slot even with incomplete coverage or identity.
Preserve the rejection and gaps for coordinator reconciliation; use supersession
for genuine fixes or changed review inputs. Never relabel a completed rejection
as interrupted or change inputs cosmetically to obtain another attempt.

Even an interrupted/cancelled import requires the exact bound source to remain
unchanged. If work already changed, preserve that result outside the store for
authorized reconciliation; do not restore user work or edit the ledger to force
an import. A successor does not turn the old pending attempt into a completed one.

Fixes or changed questions/evidence/policy require `supersede`, with prepare's
repo/base/head/inputs/sanitized arguments plus `--expect OLD_DIGEST --reason
REASON`. New candidates have no results. Historical `status --candidate
OLD_DIGEST` reports superseded; stale imports/starts are refused. Findings and
failures remain in the ledger.

Ordinary status/resume exit 0 means the query completed, not review passed.
Both `status --require-ready` and `resume --require-ready` return 2 unless the
result state is ready; invalid input/local operations return 1; interruption
returns 130. Argument-parser usage errors return 2; `--help` returns 0 without
evaluating any candidate. Exit zero
alone is therefore not a gate: require an actual readiness JSON result with
`state: "ready"` and the expected head/digest. Ready requires every
required slot's valid approval/identity, no blockers, unchanged artifacts, and
the exact source under the recorded scope. Inspect head/digest/scope before
handing it to a separately authorized ship workflow. Publication authority is
always false; ship's existing publication guard remains separate.

In v1, an optional slot's incomplete or nonpassing state alone is not a blocker;
`changes-required` results and blocking findings from any slot or attempt still
block. V2 additionally requires completion of participating optional teams, as
described below; it does not change historical v1 semantics.

Advisory locks do not use PID ownership guesses. Busy stores fail without
waiting or deleting their lock; normal process death releases it. Uncatchable
death can leave private checkpoint staging files or an incomplete new store.
Neither is accepted as an authoritative replacement or auto-cleaned. Preserve
them privately and resume from a valid ledger. If atomic replacement succeeded
but its response was lost, read back before repeating any mutation.

### Path, size, and local failures

Diagnostics are static codes, not evidence of tampering or a reason to bypass a
gate. They can reflect ordinary unsupported inputs, permissions or local I/O.
Caught controller errors emit a JSON `{"error": "CODE"}` object on stderr,
without a readiness result on stdout. Argument-parser usage/help output is not
this error protocol; continue requiring the actual readiness payload at gates.
The CLI normalizes its defined input, local-operation and subprocess exceptions,
not every possible programming or resource failure. Unexpected exceptions can
still produce a traceback; preserve that evidence privately, not as a static
diagnostic guarantee. For example, v2/v3 slot lookup relies on prior validated
attempt-slot membership and exact result binding, not a fallback for a broken
internal invariant.
A controller defect caught by this exception allowlist can also yield
`invalid_input_or_local_operation`; the code alone does not distinguish that
cause from malformed input or a local-operation failure.

Separately, v1, v2 and v3 ledger validation checks candidate envelopes and bundles
before repository-identity initialization and comparison. A non-object candidate
or one missing `bundle` yields `invalid_schema`; a non-object bundle or a missing,
non-integer, or unsupported bundle version yields `unsupported_version`. The
bundle version check precedes required-field checks, including a missing
`repository` field.
This changes diagnostic precedence for malformed data, not valid v1 history
semantics or the exception-normalization limits above.

| Code | Meaning |
| --- | --- |
| `invalid_file` | A required regular-file check failed. |
| `input_too_large` | A read exceeded its applicable byte limit, including a nonempty lock. |
| `ledger_too_large` / `git_output_too_large` | Serialized output / accepted Git output exceeded its bound. |
| `git_read_failed` | A fixed Git read failed, for example because a needed object is unavailable; the code alone does not identify the cause. |
| `dirty_candidate` | The index/tree or raw worktree membership, bytes or owner-execute differs from the bound head. |
| `candidate_moved` | Checked-out HEAD no longer matches the head being validated. |
| `git_routing_override` | One of the listed repository-routing environment variables is present, including an empty value. |
| `partial_clone_unsupported` | Local Git configuration contains a matching `remote.*.promisor` or `extensions.partialclone` name; this is not an all-scope configuration or fetch check. |
| `stale_candidate` | Supplied `--expect` does not identify the active candidate, or `resume --output` selects a non-active candidate for request re-export; this differs from a moved checkout HEAD. |
| `worktree_unreadable` | Walking the candidate tree encountered an I/O error; this is not evidence of tampering. |
| `symlink_path` / `invalid_directory` | A path or guarded-open symlink/directory constraint failed. |
| `missing_path` / `unsafe_permissions` | A required path is missing, or the store directory/private-file ownership or permissions check failed. |
| `store_busy` / `invalid_input_or_local_operation` | Another operation holds the advisory lock / an otherwise unclassified failure arose from input, local operations or a caught controller defect. |
| `interrupted_reconcile_store` | A caught interruption returned 130. Reconcile the store, outputs and original execution before another write; this code does not establish whether the last checkpoint completed. |
| `duplicate_identity` | A required uniqueness constraint failed, including artifact names; v2/v3 also requires unique owners within each coverage row and unique `(researcher, finding)` references within each lead finding. |
| `invalid_json_number` | An input used a nonfinite JSON numeric constant such as NaN or Infinity. |
| `unsupported_tree_entry` / `unsafe_tree_path` | A Git tree entry has an unsupported type/mode or a forbidden path shape/component. |

Preserve existing evidence and reconcile the intended path, type, size and owner
with the responsible operator. Do not delete a ledger/lock, truncate tracked
files or reports, chmod/chown paths, or alter configuration just to pass a gate.
An oversized or otherwise unsupported candidate needs an explicitly agreed
supported workflow, not a weakened cleanliness claim. Use a valid private ledger
for resume; missing or incomplete stores are not authoritative replacements.
Changed source, policy or copied evidence requires a new candidate.

## Version 2: independent lead teams

V2 is an explicit opt-in input schema, not an upgrade of old approvals. The
offline helper still invokes only its fixed read-only Git operations. It does
not select available models, launch/delegate, enforce live concurrency, read
provider output, or authenticate the operator. The review skill owns default
families, model-selection policy, and the baseline checklist; the controller
does not hardcode a vendor, model name, checklist vocabulary, or severity map.

Use the same six v1 input fields plus `version: 2` and `team_review`:

```json
{
  "checklist": [
    {"id": "integration", "description": "Assess cross-component contracts",
     "paths": ["src/parser.py", "docs/contract.md"], "lead_required": true}
  ],
  "severity_scale": ["P0", "P1", "P2", "P3"],
  "max_researchers": 8,
  "accepted_risk": false,
  "release_policy": "blocking-only"
}
```

This is the `team_review` value, not a complete input. Supply the full checklist
floor required by the skill/task, not just this example. The shown release policy
is the skill's default for new feature worksets, not an implicit upgrade of old
inputs; omit it for an explicitly required historical clean-only gate. Checklist IDs are
unique identifiers not beginning with reserved `file-`; paths must exist in
the base or head tree. Severity identifiers are a nonempty unique list (at
most 32); checklist size is 1–100 and each path list at most 1,000. The declared
researcher limit is 0–128 **records per lead result**, not live concurrency or
enforcement of cumulative operational budgets. Track and preserve those budgets
across retries/successors as described in [team review](team-review.md).
`accepted_risk` only enables explicit later decision records; it approves
nothing automatically. It is frozen with the policy at `prepare` or `supersede`.
Changing it later requires a successor candidate and fresh reviews, without
approval carryover; it is not a switch to enable after reading a rejection.

Team inputs may additionally bind `"release_policy": "blocking-only"` inside
`team_review`. Omission preserves the historical clean-only policy and original
status payload; no old candidate is opted in by upgrading the controller. See
[blocking-only release eligibility](#blocking-only-release-eligibility) below.
Binding a changed policy requires a successor. Current endorsement may use the
qualified prior evidence and impact-scoped process in [worksets](worksets.md),
not silent transfer of old approval.

Each v2 reviewer retains `id`, `required`, `adapter`, `requested_model`, and
`identity_basis`, and adds exactly:

```json
{
  "model_selection": {
    "family": "selected-family",
    "basis": "Current reliable selection evidence or explicit user override",
    "source": "durable sanitized selection evidence reference",
    "source_sha256": "SHA256_OF_SELECTION_EVIDENCE"
  },
  "identity_requirement": "launch-selection",
  "researcher_model_overrides": []
}
```

`requested_model` is now an exact non-null, nonsecret string of at most 256
characters. `identity_requirement` is `launch-selection` or `served-model`.
`model_selection.family` uses the 128-character identifier grammar above,
not a free-form display title.
The empty override list permits only the lead's selected model for children;
up to 32 unique explicit exact model alternatives, each at most 256 characters,
may be bound at `prepare` or `supersede`, before any child launch. A later
override change requires a
successor candidate and fresh reviews without approval carryover, not a
mid-round policy edit. There is no silent alias expansion, cheaper substitution, or
unresolved runtime default. Model selection evidence is referenced by source and
hash in the candidate; its text is not copied or authenticated by that reference.
Attach an audited text artifact when a frozen copy is required. The operator
qualifies its meaning, not the JSON parser.

### Derived scope and versioned history

The bundle adds `team_review` and `coverage_requirements`. Requirements include
every path whose `(mode, object ID)` differs between base and head, so added,
deleted, mode-only, and gitlink changes cannot be omitted. Renames are
represented honestly as old/new paths, without heuristic rename inference.
Each file requirement has `id`, `kind`, `path`, `base`, and `head`; entries are
`[mode, object ID]` or null. The ID is `file-` plus the first 24 hex characters
of SHA-256 of the UTF-8 path. Kinds are `changed-file` and `supporting-file`;
unchanged checklist support paths are not mislabeled changed. The checklist
requirements retain their supplied fields plus `kind: "checklist"`. IDs are
unique, with at most 10,000 total requirements. Current source checks rederive
the complete list from trees rather than trusting a supplied omission.
Exceeding that count currently reports `invalid_list`; the same diagnostic also
covers other invalid or oversized lists. Establish the cause from the inputs,
not the diagnostic alone.

V2 rereads both bound base/head trees on each current-source check: keep the
required commit/tree objects locally available. Missing objects can produce
`git_read_failed`; an intact ledger is not a substitute and the helper never
fetches missing objects.

The bound obligation IDs stay fixed. Map new in-scope investigations to existing
IDs in durable team maps/reports, not extra result rows; unresolved investigations
block complete coverage. Changes to bound scope, checklist, policy or supplied
candidate inputs require coordinator-managed supersession without approval
carryover. See the [checklist](checklist.md) for adaptive investigation guidance.

Coverage path labels must be valid UTF-8, at most 4,096 characters, and satisfy
the shared bounded-text guard described above. Some legal Git filenames are
therefore unsupported. A non-UTF-8 tree path currently produces
the generic `invalid_input_or_local_operation` diagnostic, not a path-specific
code. Report a compatibility limit only when established by evidence; do not
infer it from that generic error or rename or discard user files to pass it.
An empty diff is permitted for an explicitly scoped review of
unchanged supporting contracts; it does not establish review of omitted changes.

Every lead receives the same complete candidate, including requirements.
`lead_required` requires the lead to assess that checklist obligation, not to
personally read every source path cited under it: those file obligations can
be delegated. The supplied source/diff and task determine required region and
contract detail; a path record alone is not a claim of actual read coverage.
Checklist `paths` name tracked files or gitlinks, not directory prefixes.

Requests and results have `version: 2`. Request routing fields are unchanged;
the request digest binds the candidate, which binds the full team policy.
Explicitly superseding a v1 candidate with v2 promotes the ledger version to
2 while retaining old candidate/attempt/result objects unchanged. Mixed v1/v2
history uses each candidate's original validators and readiness semantics.
No old aggregate record is reinterpreted as a v2 lead/child run or approval.
The highest explicit schema version present determines ledger version.
After a ledger has opted into v2, a new `supersede` cannot introduce a v1
candidate (`version_downgrade`); accidentally copying a legacy input example
must not lower that workflow's gate. This prospective rule does not reinterpret
or reject previously recorded valid history. An explicitly separate v1 workflow
can use its own store.

### Team results, identity, and coverage

V2 results retain the v1 top-level field names and add `launch`, `researchers`,
and `coverage`; their value schemas and identity semantics are version-specific.
Existing check/supplied-evidence/limitation/provenance schemas are
unchanged. Result/disposition lists have a default cap of 100 entries: this
includes `findings`, `executed_checks`, `supplied_evidence`, `limitations`, each
finding's `evidence` and `assumptions`, and each disposition's `decisions`.
The v2/v3 result exceptions are:

- `researchers`: the bound `max_researchers`, at most 128 records per result.
- `coverage`: at most 10,000 rows, exactly matching the bound requirements.
- Each coverage row's `owners` and `evidence`: 129 entries each.
- Each finding's `assessments`: 128 entries total; multiple proposals from one
  child still count separately.

These structural caps do not authorize operational work. Never drop original
proposals or evidence to fit; preserve them and stop for an explicitly authorized
supported workflow when the material exceeds these limits.

`run_id` is an actual bounded nonsecret string (at most 512 characters),
including native canonical task paths containing `/`; do not
rename actual IDs or invent aggregate sessions. If a harness returns a child
task path scoped to its parent session (for example `/root/researcher`), retain
both actual components in a documented qualified ID such as
`PARENT_NATIVE_SESSION::/root/researcher`. This is the identity of that one
observed child, not a fabricated aggregate session. Preserve the raw parent
and child identifiers and the adapter's qualification rule in the referenced
launch/report evidence. `parent_run_id` still equals the lead's recorded native
session. An identical task path under a different real parent is a different
child. Reusing the same `(adapter, run_id)` pair is rejected except on the
explicitly validated v3 [continuation chain](continuation.md). Distinct real
adapters may use identical opaque run ID strings; no global string inequality
is required. Actual distinct child execution, parentage and adapter identity
remain operator-verified facts, not consequences of relabeling an adapter.
A prelaunch nonpassing result can retain null run/launch and unknown identity.
All required obligations must still be represented, with unresolved state where
appropriate. A null lead `run_id` cannot declare researcher children: there is
no observed parent runtime to bind them to. Preserve uncertain launch evidence
externally and describe the gap in limitations instead of inventing IDs.

`launch` is null when unobserved, otherwise exactly
`{model, source, source_sha256}`. The lead's model must equal the selected model;
a researcher's model may instead be a prebound child override. `identity`
retains `{basis, reviewer, model,
evidence}`. In v2/v3 `identity.model` means **served** identity and can be null;
non-null reviewer/model strings are bounded to 256 characters. Launch evidence
judged admissible by the operator plus matching policy basis, reviewer, and
nonempty evidence satisfies `launch-selection` even if served identity is unknown.
Contradictory served identity blocks completion; `served-model` additionally requires it.
Self-report/requested-only/unknown bases remain preservable, not sufficient.

Each researcher record has exactly:

```text
id, adapter, run_id, parent_run_id, status, identity, launch, report
```

The researcher ID is unique and cannot be `lead`. `parent_run_id` must equal
the actual lead run: one level only. Status is `complete`, `failed`, `cancelled`,
or `interrupted`. A report is `{source, source_sha256}`, required for complete
children and otherwise nullable. An attempted launch with no actual child
session belongs in limitations, not a fabricated child ID. Actual
`(adapter, run_id)` pairs cannot be reused across leads, children, attempts or
candidates, except for the unchanged actors on a validated same-candidate,
same-slot v3 continuation chain. Ordinary retries retain the uniqueness rule.
This structural check does not independently prove separate
provider execution. Preserve original child reports and launch observations.

Every declared child must satisfy its lead slot's `identity_basis` and
`identity_requirement` for team completion, even with another adapter or a
prebound model override, and even if it failed and no coverage depends on it.
Model overrides do not override identity policy. A failed or interrupted child
with qualified identity may remain recorded after all of its
obligations are reassigned and completed by qualified owners. Unknown or
contradictory identity still blocks completion; do not hide that child or invent
identity to recover. A launch failure with no observed child stays a limitation.
The operator verifies actual adapters, parent linkage and any qualified-ID
components against launch evidence. String uniqueness is not authentication of
those external facts.

Each coverage row is exactly `{obligation, state, owners, evidence}`. State is
`assigned`, `reviewed`, or `unresolved`; owners are `lead` or declared researcher
IDs. Evidence entries are `{owner, source, source_sha256}` and must match that
owner's report reference (top-level provenance for lead). Every requirement
must occur exactly once per team, even in a nonpassing report. Reviewed rows
need nonempty owners, one matching report reference per owner, completed and
identity-qualified owners, and lead ownership for `lead_required` checklist
items. Assigned/unresolved rows cannot qualify PASS. Reports and source/run
bindings provide accountable coverage, not per-file OS enforcement or raw-read
proof. Operators must reconcile original reports; hashes are not authenticity.
Coverage unions between independent teams cannot fill either team's gaps.
Different owners may reference one document containing their separate reports;
hash distinctness is not proof of independent research.

Each finding has exactly:

```text
id, kind, severity, confidence, recommendation, summary, rationale,
evidence, assumptions, requested_outcome, assessments, classification_reason
```

Kind is `defect` or `suggestion`; severity is a configured identifier, nullable
only for suggestions. Confidence is `high`, `medium`, or `low`; an explicitly
blocking-only candidate also permits `null` for unknown confidence on a
`follow-up` or `optional` proposal/finding. Preserve the original evidence and
the uncertainty, rather than inventing confidence to satisfy formatting.
Missing blocking disposition, source, identity or substantive coverage is not
relaxed by that representation. Recommendation
is `block`, `address-before-merge`, `follow-up`, or `optional`. Evidence and
assumptions are string lists; defects require nonempty evidence, while
assumptions may remain empty. Each child proposal in `assessments` is exactly
`{researcher, finding, kind, severity, confidence, recommendation, rationale}`,
referencing a child with a preserved report and that report's finding ID. The
lead owns final top-level classification. Any change to the proposal's kind,
severity, confidence, or recommendation requires nonempty
`classification_reason`; the proposal is preserved, not overwritten.
A single original child proposal may support multiple split lead findings.
Preserve its original reference and classification in each and explain the split;
it remains one original proposal, not independent corroboration.

The operator must reconcile the original reports for omitted proposals and
correct finding IDs. The parser cannot detect a child finding that was never
disclosed in the result, or authenticate the referenced report contents.
Severity communicates impact; actionability depends on kind and recommendation,
not a hidden severity-to-approval mapping.
Status is a summary, not a substitute for reading original reports and the lead's
reclassification reasons. It does not separately enumerate downgraded proposals.

Any defect or non-optional recommendation requires a nonpassing verdict,
regardless of severity. Optional informational suggestions can coexist with
PASS. There is no automatic severity downgrade/waiver. A decisive blocker can
be recorded as `changes-required` with unresolved coverage, but cannot become
a whole-candidate PASS or accepted-risk completion. Known incomplete/failed
optional participating teams also block readiness; never hide their outcome.
Actionable findings from earlier attempts of the same candidate remain in its
readiness calculation even after a retry. A later PASS does not erase them.
Supersession retains the old history and starts a new candidate with no inherited
approval; explicit risk acceptance remains the separate non-clean route below.

### Separate coordinator decisions

Use `disposition --store STORE --expect DIGEST --disposition FILE --sanitized`
to append a coordinator record, not edit a review. The v2 record is exactly
the shape below; for a v3 candidate use `version: 3` with the same other fields:

```text
version: 2, candidate, id, actor, authority,
decisions: [{attempt, finding, decision, rationale}],
provenance: {importer, source, source_sha256}
```

Each decision references an existing recorded finding. Decision is
`request-fix`, `dispute`, `defer`, or `accept-risk`. All require rationale;
`authority` describes the asserted authorization and is **not authenticated**
by the offline helper. `request-fix` requests a fix; it never claims code
changed on the same candidate. Dispute/defer do not erase findings or approve
publication. Accept-risk additionally requires candidate `accepted_risk: true`
and the explicit invocation flag `--authorize-accepted-risk`. This records the
operator's declaration, not an external permission check. Unique record IDs,
hashes and authorization flags are retained append-only. Later decisions for
the same finding govern disposition without removing earlier records/reports.
The limit is 512 disposition records per candidate, with 1–100 decisions per
record; the shared ledger byte limit also applies. Preserve earlier history
rather than deleting records to fit either limit.
`disposition` requires the exact clean bound source, like result import.
Only the active candidate is writable: `--expect` must match its digest, and
decisions must reference its findings. Record decisions before editing that
source or superseding the candidate. After supersession, preserve any further
discussion externally with references to the original candidate and findings;
the controller cannot append dispositions to that historical candidate.

V2/V3 status exposes `clean_review_ready`, `accepted_with_findings`, per-slot
`complete`, disposition ID/hash references, and
`coordinator_authority_authenticated: false`. V3 additionally exposes per-slot
`continuation_of`, as described in [continuation](continuation.md); this identifies
recorded lineage, not a live process. Complete accepted findings yield
the unmistakable state `accepted-with-findings` only if every actionable
finding has an explicit latest accept-risk decision and all required and
participating teams have complete identity/coverage and completed verdicts.
Incomplete identity/source/coverage or a superseded candidate cannot be waived.
`unresolved_review_verdict` checks the result of each slot's latest attempt:
`changes-required` without its own actionable findings blocks both clean readiness
and `accepted-with-findings`, even when every earlier actionable finding has an
accept-risk decision. Earlier findings remain retained; accepting them does not
resolve that later rejection.
`--require-ready` still returns 2 for accepted-with-findings: only clean `ready`
qualifies. `publication_authority` remains false in every state.
The `recorded_findings` blocker remains in that non-clean state's `blockers`
list even after explicit risk acceptance; it is not a contradiction of
`accepted_with_findings: true` or permission to treat that state as clean ready.
The retained per-record authorization flag is available in the ledger, not the
status summary or record-content digest; it is an operator declaration, not a
signed authorization receipt.

### Blocking-only release eligibility

For an explicitly bound `team_review.release_policy: "blocking-only"`, the
controller additionally reports `release_ready`, `release_blockers`,
`release_state` and `follow_ups`. The original `state`, reviewer verdicts,
`clean_review_ready` and accepted-risk results are retained unchanged. A
`release_state` of `ready-with-followups` is not a clean PASS.

Two additional coordinator decisions are available only under this policy:

- `nonblocking`: concrete rationale explains why the finding does not have a
  serious release-blocking consequence; the decision additionally supplies
  `follow_up: {issue, source, source_sha256}` identifying the follow-up Issue and
  the operator's verified tracking evidence.
- `false-positive`: rationale and existing record provenance explain why the
  finding is not valid. Do not create an unnecessary Issue or delete the
  original finding.

Use the ordinary append-only `disposition` command and its candidate/actor/
authority/provenance fields. Neither a low severity label nor a legacy `defer`,
`dispute` or `accept-risk` decision implicitly establishes one of these new
dispositions. Disagreeing with a lead's blocking recommendation requires an
explicit evidence-backed explanation, not a severity downgrade. Preserve all
findings across attempts and the latest decisions; incomplete required or
participating teams, missing coverage/identity and moved source remain ineligible.
A findings-only rejection can be resolved by explicit dispositions of its own
findings; an unexplained rejection cannot borrow resolutions from an earlier
attempt. A preserved `changes-required` verdict is not itself a release veto.

The operator verifies the Issue and referenced evidence; this offline controller
does not contact a tracker or authenticate the coordinator. Essential project QA
remains the separate validation gate. Preserve exploratory check failures without
automatically equating each one to a serious product defect; actual essential
failures must be resolved and represented honestly.

Gate this policy with `status --require-release-ready` (or the corresponding
resume flag), requiring exit 0, the expected exact head/digest, `release_ready:
true` and no release blockers. It returns 2 when ineligible or when the policy
was not explicitly enabled. `--require-ready` remains the historical clean-only
gate and is not weakened. Neither gate grants publication or deployment authority.
