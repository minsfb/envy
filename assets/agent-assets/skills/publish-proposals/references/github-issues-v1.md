# GitHub Issues protocol — unavailable for live use

## Capability and authority

This is a fixed protocol implementation for trusted offline fixtures, not an
approved real GitHub client or server schema. No supported client/schema/retry/
redirect/credential-forwarding evidence is available. A single spawned client
does not prove one HTTP mutation; clientMutationId would not prove idempotency.

A separate [fixed HTTPS profile](https-transport-candidate.md) now has a narrow
source-qualified Linux implementation and owned TLS/CONNECT coverage. It does
not enable or relabel this legacy v1 provider.

The legacy provider's normal client resolver unconditionally returns
`github_compatibility_unavailable`. The public GitHub entry points now route
only to the separate HTTPS profile, whose unsupported source contexts refuse
before input/store access or mutation intent. No runtime plan, environment variable, test switch,
registry, executable argument or remote response can enable the profile.
Do not replace it with ad hoc gh commands, another transport or changed security
configuration. A real profile requires a separately reviewed source change and
explicit operation authority. Local CLI help can establish flags only, not the
missing transport/server guarantees.

`validate`, `prepare`, `status` remain offline. This legacy provider's binding and
adapter API remain for inspected synthetic fixtures, not a CLI writer. A trusted
test can replace the fixed resolver with an owned fake executable; untrusted
data cannot do so. Such tests do not establish real backend compatibility.

Preparation of any future real plan needs explicit target-read authority.
Filing needs the exact approved digest and filing reference; reconciliation
needs its own read/receipt-write authority. Positive discussion, a code review,
technical permissions, or a fixture plan is none of these grants.

## Fixed operations and identity

The proposed command is the trusted resolved client followed by exactly
`api --hostname HOST --method POST graphql --input -`. JSON stdin contains one
compiled operation and typed variables. No shell, extensions, aliases,
auth/login/token command, jq/template, automatic pagination, caller query,
arbitrary URL, module or executable loader is provided.

Only five operations exist: viewer/named repository context; viewer/pinned
repository node context; bounded Issue pages; pinned-node Issue-number read;
and createIssue with only repositoryId, title and full inline body. Read-only
operations also use HTTP POST; their compiled GraphQL semantics determine the
effect, not the HTTP verb. The queries are proposed strict schemas and cannot
be described as accepted server behavior before separate compatibility review.

Plan v2 binds the core source hash and exact fixed sibling source hash, protocol
version/profile, observed client selected/resolved paths/hash/version, immutable
repository node and numeric ID, canonical names, account login/numeric ID,
visibility, Issues availability, archived state, observed permission role and
the complete policy. Executable paths in a record are comparison data, never
used to choose code. The transport resolves its client independently through
trusted code and compares its actual identity on each call. This is not complete
executable-chain or hostile-writer attestation.

Unknown/missing/extra response fields, partial data, errors, invalid scalars and
wrong repository/type/URL stop. Read permission or a WRITE/MAINTAIN/ADMIN role
observation does not guarantee the selected credential can create Issues.
Server authorization remains decisive; no test write or scope repair is made.
Context comparison cannot freeze credentials, ACLs or visibility atomically.
One canonical store and operator coordination against concurrent critical
context changes remain explicit assumptions, not a hostile-admin guarantee.

## Discovery, recovery and privacy

The digest-bound policy is `best-effort-single-attempt`. Fully traversing the
bounded Issue connection is an observation, not linearizable absence or global
uniqueness. Search is not substituted. Cursors, Issue IDs and Issue numbers must not repeat;
empty continuation pages, partial traversal and exceeded bounds stop.
Marker matching is independent of exact body equality, so an edited same-marker
Issue is a conflict, not filtered away to permit a duplicate. Two matches block.

Before durable intent, unavailable, malformed or incomplete v2 discovery appends
only `preflight-paused` with a fixed sanitized reason, leaving the operation
planned/attempt0. A complete observation of a valid same-marker Issue instead
records terminal `blocked`, even if its title/body was edited or multiple matches
were observed. It records no create intent; its attempt1 state prevents reopening
the plan after a later empty traversal. Reconciliation cannot clear that block;
do not recreate the batch or adopt an artifact to evade it. Explicit later
continuation may repeat reads only for a paused plan; there is no automatic retry. Once intent
is durable, timeout, lost/malformed response or failed checkpoint/readback never
permits a second create. Preserve the last receipt and stop later operations.

Reconciliation reads a recorded artifact directly, or uses a complete marker
traversal followed by a direct read of its sole exact candidate. It verifies a
required artifact's exact destination/title/body/URL/ID, not its creator or
original-attempt authorship and not absence of unseen duplicates. Creator data
is not requested by this schema. Empty, edited, duplicate or incomplete results
remain unresolved; never recreate a batch to evade that state. No adoption,
update, delete, rollback, project/label/assignee/comment or Gist action exists.

Known private repositories permit matching repository-scoped or already
internet-shareable material. Public repositories require independently approved
internet-shareable content, including conservatively GHES-public. Internal or
unknown visibility and hosting-instance audiences are unsupported. There is no
corporate-host exception. Human review of arbitrary prose and embedded links
remains necessary; destination labels do not declassify content. Oversized or
private material never falls back to uploads or secret/unlisted Gists.

## Bounds and effects

- At most 100 proposals; body at most 60 KiB UTF-8, with conservative dependency
  URL room checked before creating the store. Other P1 data/store limits remain.
- 100 nodes/page, 10 pages, 32 MiB discovery. No automatic unbounded pagination.
- 30 seconds per child call; TERM 5 seconds then KILL/reap 5 seconds on failure.
- 900 seconds, 2048 calls and 256 MiB output per transport instance; use a fresh
  trusted instance for a later invocation. A reused instance can refuse earlier,
  never extend the budget. Trusted synchronous in-process fixture callbacks are
  not preempted by the core; their QA runner supplies its own outer bound.
- 8 MiB stdout and 64 KiB stderr per call, enforced while streaming. Overflow is
  failure, never valid truncated JSON. No raw stderr enters errors or receipts.
- Each call owns one process group and reaps its direct child. It never retries,
  intentionally signals a reaped child, or claims descendant/process closure.
  Killing the client could not undo a remote write if a future live profile ran.

The transport inherits the caller's environment, security/auth/proxy context and
umask unchanged. It does not log that environment or repair client configuration.
The source fingerprints the trusted client; its own child uses pipes only from
the caller's perspective. A future admitted real client could contact a server,
which is exactly why the normal resolver and public commands remain unavailable.

Owned fake-client tests cover exact arguments/stdin, strict normalization,
pagination/conflicts, unchanged environment, output/time budgets and reaping.
Core fixtures exercise approval/version binding, preflight pause, lost-result
reconciliation, and actual owned intent/result fsync/replace boundaries. They
cannot certify server consistency, retry/redirect behavior, backend identity,
power-loss durability or live authorization. Repository QA and independent
review are additional gates, not authority to publish or activate the skill.
