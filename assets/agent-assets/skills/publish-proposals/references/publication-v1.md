# Offline publication v1

This document describes the preserved version-1 contract. The same core now also
understands an explicitly versioned, trusted-fixture-only GitHub protocol;
see [GitHub Issues protocol](github-issues-v1.md). No live compatibility profile
exists. Version-1 plans/receipts are not migrated, renamed or silently weakened.
The changed source identity still invalidates plans saved by older source bytes.

## Scope and authority

This first slice implements strict data, local planning/checkpoints and serial
recovery against a test-owned in-memory adapter. It does not implement GitHub,
Gist, Task, Paste, project creation, provider discovery, remote execution or a
real writer. The CLI cannot select a provider/module/executable. There is no
network or subprocess code, Git dependency, package dependency or model call.

Supported platform: Unix with `fcntl.flock`, no-follow opens and directory fsync.
Python 3.9+ standard library is required. Unsupported filesystems/platforms fail;
there is no weaker locking/durability fallback. Existing trusted source and the
operator-owned filesystem are assumptions, not a sandbox against a malicious
same-user writer or malicious in-process adapter.

The user requests preparation before any store is made. Initial reflection or
selection does not invoke preparation. The whole `ProposalSet` is the already
selected set. Local validation needs authorized access to the input but creates
no store. Preparation authority does not authorize filing. Filing authority is
an explicit exact-plan digest and reference pair, checked before adapter calls;
the reference is not authenticated consent. A hash detects change, not identity.

## Commands and records

Resolve the helper relative to the installed skill, not cwd. Input paths must be
owned regular files with no group/other access, hard links or symlink components.
Input and store paths reject every `..` component with `parent_traversal` before
normalization, including parent segments after an ordinary directory or symlink.
Other relative paths are supported from cwd; Python APIs also accept PathLike
values returning text paths under the same checks.
Choose a real owned private parent outside a repository. On macOS use the real
`/private/tmp` path when appropriate, not its `/tmp` symlink. Do not change user
paths or permissions to make a rejected input pass.

```sh
python3 /path/to/publish-proposals/scripts/publication.py validate \
  --input /private/owned/selected.json
python3 /path/to/publish-proposals/scripts/publication.py prepare \
  --input /private/owned/selected.json --store /private/owned/new-store \
  --prepare-authority 'reference to the explicit preparation request'
python3 /path/to/publish-proposals/scripts/publication.py status \
  --store /private/owned/new-store
```

These illustrative paths are not automatically created. No command repairs or
reuses an existing store. `status` is local read-only, not provider reconciliation;
zero exit merely means it read valid state, not that all operations are complete.
Structured status includes `complete` and per-state counts and IDs. A prepared
store has all operations planned and `provider_available:false`.

Store/data failures handled by the CLI use fixed diagnostic codes. Argparse usage
errors occur before that handler and may echo invalid argument text; do not put
secrets in command arguments. This is not a general stderr-redaction guarantee.

Only three versioned JSON record types are used. Unknown keys at every level,
duplicate JSON keys, floating-point numbers, unsupported versions and non-JSON
values are rejected. UTF-8 input is canonicalized to sorted, compact ASCII JSON;
integer fields exclude booleans and integers outside the exact JSON-safe range.
No custom object hooks, serializers, templates or general schema engine exist.

### ProposalSet

Exact keys: `version:1`, `type:"ProposalSet"`, `batch_id`, `destination`,
`proposals`. IDs are opaque lowercase 32-hex values supplied before planning.
They must not encode private planning names, paths or catalog identities.

The single batch destination has exactly `host`, `account`, `account_id`,
`owner`, `repository`, `repository_id`, `audience`. Account/repository IDs are
nonzero decimal strings up to 20 digits; names are bounded plain identifiers,
and hostname is a bounded lowercase DNS-style name without scheme, userinfo,
port, path or query. The owning repository for every proposal is this declared
destination. Multiple destinations per batch are not supported in this slice.
These supplied values are not evidence of real identity, access or ownership.

Each proposal has exactly `id`, `title`, `problem`, `evidence`, `scope`, `detail`,
`steps`, `acceptance`, `non_goals`, `dependencies`, `sensitivity`. The three
list-valued sections are nonempty string lists. Dependencies are distinct IDs
of selected proposals in the same batch, never self-dependencies. Cycles block;
stable input order breaks ties among ready proposals. No workflow/session field
or reflection-specific metadata is required.

Text checks reject C0 controls except newline and tab; titles also reject those
two characters. They do not reject DEL/C1 controls, Unicode line/paragraph
separators, bidi controls or zero-width marks, or guarantee a visually single-line
title. Human content review must account for these accepted characters; no Unicode
normalization or display-safety guarantee is provided.

Closed audiences:

| Value | Supported sensitivity |
| --- | --- |
| `{"kind":"internet","realm":"internet","id":"public"}` | `public` only |
| `{"kind":"repository","realm":<exact host>,"id":<exact repository_id>}` | `public` or `repository`, scoped to this one destination |

Instance-wide, author-only, arbitrary ACL, unknown and incomparable audiences
are unsupported. There is no GHE-public-to-internet inference. All generated
dependency links share the declared destination/audience. The core checks this
closed relationship, not actual remote ACLs or the privacy of arbitrary Markdown.
An authorized human must review content and embedded references before approving
disclosure. No automatic redaction/declassification or shared index is offered.

### PublicationPlan

Exact keys: `version:1`, `type:"PublicationPlan"`, `source_sha256`, `provider`,
`prepare_authority`, `proposals`, `operations`, `digest`. The source hash binds
the exact helper bytes; the provider is the fixed
`{"name":"offline-fixture","version":1}` protocol label, not an authenticated
implementation identity or production capability. Input cannot select code.

Each operation is exactly `id`, `kind:"issue.create"`, `proposal_id`, `marker`,
`body`, `requires`. Its ID/marker, ordering and complete inline body preview are
derived deterministically from the selected proposal. Required dependency URLs
are shown as fixed `<issue-url:create-OPAQUE_ID>` slots in the dependency section.
Only verified returned URLs for those predeclared operations fill those slots;
proposal text is never evaluated or interpreted as a template. This is bounded
topological ordering of at most 100 creates, not a general operation DAG.

Proposal prose is included verbatim and may itself contain a colliding tracking
marker or a literal slot string. Such text is not substituted, escaped or proof
of marker uniqueness. Any future marker-search adapter must retain exact
destination/title/body checks; a marker match alone cannot justify adoption.

The digest covers every plan field except itself, including preparation
reference, source identity, full proposals, target/audience and body previews.
Validation requires exact integer version types, independently recomputes the
saved digest, and compares canonical records against the regenerated plan.
Boolean versions are invalid even though Python normally equates `True` and `1`;
neither keeping nor recomputing a digest makes such a plan valid. The separate
saved ProposalSet must also match its validated plan copy canonically. The fixture
identity label has the same exact-type requirement before adapter reads. Changed
source/content/target/provider/authority needs a new preview and approval. Do not
edit or rehash stored records to circumvent a blocked plan.

### PublicationReceipt

Exact keys: `version:1`, `type:"PublicationReceipt"`, `plan_digest`,
`authorities`, `operations`, `history`. Authorities contain only `scope`
(`filing` or `reconciliation`), `reference`, `plan_digest`. Operation rows contain
`id`, `state`, `attempt`, `artifact`. Attempts are bounded to zero (planned) or
one (an attempted or preflight-blocked operation); this is not a remote-call
counter. No automatic reset or second create attempt exists.

History is an append-only bounded list with `sequence`, `operation`, `event`,
`artifact_id`. Its closed transitions must replay to current states/mappings.
Events are `intent`, `result`, `verified`, `uncertain`, `blocked`, `reconciled`.
Returned artifact records contain exactly `id`, `number`, `url`, `marker`,
`title`, `body`, `destination`; ID/URL relationships, target and exact body are
validated. The observed URL is retained, not fabricated from a guessed result.
Distinct creates cannot share returned IDs/URLs. Unknown/raw result fields and
provider exception bodies are never copied into receipts or diagnostics.

The v1 artifact rule accepts only the GitHub-shaped URL
`https://<host>/<owner>/<repository>/issues/<number>` for the exact declared
destination and returned number. Other provider URL shapes are unsupported, even
with otherwise matching fields. This fixed fixture contract is not a generic URL
resolver or evidence of an available GitHub provider.

## Durability and recovery

The store contains exactly `proposals.json`, `plan.json`, `receipt.json`, `lock`.
Directory mode is 0700; files are 0600 regardless of caller umask. Mode changes
affect only newly allocated owned resources, never the parent or caller umask.
Store/inputs use bounded regular no-follow reads; hard-linked files and
wrong ownership are rejected. No credentials, raw environment or transcript
belongs in these private records. This location is not cleared for publication.

Preparation creates files exclusively and fsyncs them and directory entries.
Failure preserves the incomplete owned directory; it is not silently repaired.
Readers and writers use actual shared/exclusive nonblocking advisory locks, not
the presence or emptiness of a file as evidence of lock ownership. An empty,
owned regular `lock` file is nevertheless a required input precondition: nonempty
content fails with `data_too_large`. An empty file does not prove the lock is free;
the actual `flock` decides contention. Receipt updates stage an exclusive
0600 file, fsync, replace and directory-fsync. Normal cleanup removes only that
invocation's staging file. An uncatchable crash may leave an extra file; reject
the store for private operator inspection, never infer approval or auto-clean it.

The non-CLI `apply(store, expected, authority, adapter)` function exists for
offline contract tests. A trusted in-process fixture implements only fixed
`identity`, `read_context`, `find`, `create`, `read` members. It is not loaded
from the plan and has no generic command or capability negotiation interface.
In-process calls are trusted synchronous fixture code; the core does not
preempt arbitrary callbacks. The test execution plan supplies an outer bound.
A real adapter/transport needs a separately reviewed execution boundary.

Before each create, exact context must match and complete, consistent discovery
must show no existing artifact. Existing matches block for future explicit
adoption; no automatic reuse. The core persists intent before invoking create.
Afterward it commits the validated result and ID mapping together, then reads
back the exact artifact before marking verified. Stop at the first failure.

States are planned, attempting, created-unverified, verified, uncertain, blocked.
An exception after intent leaves the last durable intent/result intact. Status
never presents either as verified, and `apply` will not retry it. A preflight
failure is blocked with no writer call. Even a crash immediately before create
remains unresolved absent independent nonlaunch proof.

`blocked` is terminal in P1, including blocks caused by a transient context or
discovery failure. `apply` reports `blocked_plan` if any row is blocked and makes
no adapter call; it does not offer a reset or retry. `reconcile` skips blocked
rows and cannot clear them. With only planned/verified/blocked rows it can return
an incomplete summary without adapter reads or a receipt change. That return is
not successful recovery. `reconciliation_required` instead identifies attempted
rows that must be investigated before any further apply.

`reconcile(store, expected, authority, adapter)` makes only adapter reads and
local receipt checkpoints. It checks current context and known IDs first; absent
a mapping, it requires one exact complete/consistent marker/content match.
The fixed discovery response is `complete`, `consistent`, `matches` (at most two
candidates; duplicates block). Empty, incomplete, inconsistent, malformed or
multiple results remain uncertain; no create is issued. Unsupported backend
consistency cannot be converted to an assertion of absence. A reconciled match
verifies required content, not that the original attempt authored the artifact.
That distinction remains in the `reconciled` event.

Receipt/result failures do not trigger another write or overwrite old evidence.
After successful read-only reconciliation, an explicit matching filing authority
can continue never-attempted operations only when every row is planned or verified
and the remaining authority/history/capacity gates pass. Reconciliation does not
permit continuation if even one blocked or unresolved row remains.

A partial batch with a blocked row is unfinishable in P1. Preserve its verified
artifacts and receipt and report the incomplete plan as abandoned; abandonment is
an operator outcome, not a new state or deletion action. A fresh store for the
same batch encounters existing deterministic markers and blocks for unsupported
adoption. Changing a batch ID to evade that check can duplicate artifacts and is
not a supported workaround. Do not copy, edit or rehash receipts to resume.
Finishing such a batch requires separately designed and reviewed recovery/adoption
capability, not another P1 apply. No body/edge update, adoption, rollback, deletion
or distributed uniqueness guarantee exists. One canonical store and operator
coordination remain necessary across separate processes/stores. Required
operations all verified means complete for this fixed plan, not complete ENV7
delivery or real backend acceptance.

## Finite limits and validation gates

- At most 100 proposals and 100 derived operations; dependencies at most 99 each.
- UTF-8 input and canonical ProposalSet, including final newline: at most 1 MiB.
- Detail: at most 64 KiB UTF-8; title 256 bytes; problem/evidence/scope 4096 bytes
  each; at most 32 strings of 2048 bytes per list section.
- Rendered body: at most 128 KiB both UTF-8 and canonical JSON; planning reserves
  conservative space for dependency URLs. This is a local limit, not a claim
  about a provider's maximum body size.
- Plan at most 4 MiB; receipt at most 8 MiB, including final newline. Committed
  JSON totals at most 13 MiB; one receipt staging file permits at most 21 MiB
  during an ordinary checkpoint. Unexpected files block rather than get read.
- Each data walk: depth at most 16, at most 50,000 nodes. No floats/nonfinite
  numbers; history at most 1024 events; authority references at most 64, each
  at most 512 UTF-8 bytes. Capacity checks precede planned write sequences.
- There is no provider pagination/timeout assertion in P1. Fake discovery is
  bounded data, not evidence of a remote API's search/consistency behavior.

`tests/test_proposal_publication.py` uses only standard-library owned fixtures,
the in-memory fake and one bounded owned fork for flock. Its actual import graph
and invocation must be reviewed before execution; syntax/JSON checks alone do
not validate recovery. Cases cover exact approval, changed source/target,
inline ordering, data/path bounds, held locks, checkpoint failures, success before
lost response, incomplete/duplicate search, wrong returned identity, unrelated
remote edits, no credential-error leakage and partial versus complete receipts.
Targeted persistence cases inject once before/after actual staged-file fsync,
replace and directory fsync at both intent and result checkpoints. They assert
that the intended syscall boundary was reached, the retained old/new receipt is
valid, catchable-error staging cleanup is owned, uncertain evidence is unchanged
by a blocked apply, and reconciliation adds no create. Initialization cases fail
before/after a real plan-file fsync and preserve the incomplete owned store.
These injected exceptions observe current filesystem state; they do not emulate
a power cut or prove hardware durability. All cases require an actual test run.

Remaining gates: actual Issues provider, explicit shareable Gist capability,
conditional/additive update policy, private adapter fit, both-harness handoff/
installation, repository-native QA, and separately authorized live trials.
This offline slice does not complete those requirements or change `reflect`.

## Explicit version-2 fixture delta

ProposalSet remains version1. A trusted in-process test may call
`prepare(..., binding=...)` or `make_github_plan` to make a PublicationPlan v2;
no CLI input accepts that binding and it cannot select or launch a client.
The plan retains the v1 fields, with a closed GitHub provider identity binding
the fixed sibling source, client observation, repository context and exact
resource/discovery policy. The core source hash remains separately bound.

Receipt v2 has the v1 fields plus an exact copy of the plan's `policy`. Every
history event additionally has `reason`: null except the fixed
`preflight_unavailable` reason on `preflight-paused`. This event transitions only
planned/attempt0 to planned/attempt0; it cannot clear durable intent or a blocked
row. A separately authorized later apply may repeat preflight reads for that
never-attempted operation. There is no automatic continuation or reset.

V2 discovery requires a complete bounded traversal with
`consistency:"not-guaranteed"`, not P1's `consistent:true`. Marker conflicts are
retained independent of body equality. One positive reconciliation match must
also pass a direct read and exact content/identity verification. Empty or
conflicting results never reopen an attempted create. The exact policy and
non-authorship/non-uniqueness limits are present in preview, receipt and status.
All filesystem, locking, authority, intent-before-create, staged checkpoint,
capacity, stop-on-uncertainty and one-attempt rules otherwise remain shared.

The separate [version-3 multi-artifact test API](publication-v3.md) reuses this
store and coordinator for seven finite roles. It supplies no live provider or
new CLI writer and does not expand this skill's offline-only authority.
