# Versioned agent-asset ownership handoff

The public asset manifest uses version 2 and selects `common / pr-triage / 1`
for both Claude and Codex. Public-only installation can start from an absent
generic pair. Migration from a bundle's existing generic skill requires compatible
paired declarations and the exact recorded state below; do not deploy the public
skill while another selected bundle still claims the same name.

## Manifest contract

Version 2 preserves the ordinary destination/group fields and adds only:

- Skill-group `exclude_names`: exact unique child names, never patterns or paths.
  Excluded children do not enter discovery or duplicate claims. They are not
  deleted or renamed.
- `skill_contracts`: records with exactly `group`, `name`, and `version: 1`.
  The named provider must actually be selected for its group's harnesses.
- `skill_handoffs`: at most one record with exactly `group`, `name`,
  `replacement: {group, version: 1}`, `adapter`, and `legacy_files`.
  The adapter is a distinct skill in the declaring group. The predecessor is
  that group's excluded child with the same name as the replacement skill.
  `legacy_files` is exactly one `SKILL.md` with a reviewed lowercase SHA-256,
  never a baseline learned from the installed host.

The first supported handoff is deliberately limited to two generic targets and
two companion adapter targets, selecting both Claude and Codex. No chained
handoffs, executable hooks, arbitrary operations, aliases, model preferences, or
organization-specific paths belong in the public schema. Unknown v2 fields and
duplicate JSON keys fail closed. Ordinary valid v1 manifests remain compatible;
old installers reject a version-2 activation manifest before mutation.

The public feature is already scoped to Unix in `envy.json` (Darwin, Linux and
WSL), not native Windows/Git Bash. V2 selection and v2 ownership require Unix
locking. Direct use of an explicitly selected valid v1 manifest retains its
legacy non-Unix fallback; this is not Windows support for the public v2 feature.

## Ownership and source lifetime

A contract-protected skill must be an entirely absent/unrecorded pair, an exact
current/recorded pair, or the exact declared recorded predecessor pair. Mixed
states and unrecorded already-current links are not adopted. Ownership uses the
raw generated absolute link text, not equivalent relative paths or source-root
containment. Foreign entries, edited predecessors, extra files, symlinks and
unknown predecessor versions block handoff.

The predecessor and new sources must be real directories. All three source
inventories bind regular file bytes, relative directories, and executable bits.
The legacy inventory check applies to entering/resuming a transfer, not to an
already completed transfer. Keep the predecessor physically present during the
supported migration window. Its eventual retirement is a separately authorized
source change, not installer cleanup.

Transfer requires every unrelated managed target and launcher already current.
When a composed upgrade also selects ordinary additions, exactly owned source
moves or deselections, convergence first journals those ordinary link changes,
leaving the provider and adapter pairs untouched. It then rebuilds and validates
the transfer plan against the resulting state under the same writer lock.
The transfer itself performs no ordinary sweep, install or launcher update.
Missing recorded links, foreign entries, unmanaged links, edited predecessors,
other fresh protected pairs and launcher drift still refuse preparation. Do not
manually rewrite installed links or ownership records to pass these checks.

Preparation uses the ordinary-operation journal with an optional
`handoff_preparation` SHA-256 of all three source inventories. It binds the exact
selection and preserves the recorded predecessor/adapter mapping instead of
installing those protected targets. Every continuation rechecks those pairs,
source inventories, launcher and ordinary preimages/prepared identities.
Both ordinary checkpoints and prospective transfer checkpoints must fit the
16 MiB limit before preparation writes. The actual transfer's unrelated-state
fingerprint is captured only after ordinary completion.

An interrupted preparation resumes via the same `converge` command; removal
cannot replace it. An interruption after ordinary completion leaves a nonpending
v2 store with null handoff and the exactly recorded predecessor pair, from which
convergence can start the declared transfer. Transfer provenance accepts either
a v1 prior store or a nonpending null-handoff v2 prior store; nested transfers and
pending prior operations are refused. No v2 store is downgraded to v1. Earlier
installers reject the preparation extension or v2 transfer provenance, so retain
the updated installer through completion and subsequent ownership maintenance.
Dry-run lists the ordinary preparation targets and subsequent transfer without
writing either stage. Read-only checks continue to report outstanding links.

## Ordinary drift and mixed-manifest upgrades

Any v2 manifest promotes the whole composed selection to strict recorded-link
ownership, including ordinary assets supplied by v1 bundles. Successful
convergence then writes v2 ownership. A still-intact exactly recorded ordinary
link can move to its newly selected source; an extant link retargeted by another
writer is not adopted, even if it points inside a selected source root.

Outside an active legacy/pending transfer, convergence can recreate a missing,
currently selected ordinary recorded link. Dry-run only plans that repair;
status, verify and doctor continue reporting the missing target until it is
repaired. This exception excludes declared generic/adapter targets, targets
retained in completed handoff provenance, and deselected recorded entries.
Protected pair checks and all legacy/pending untouched-state checks still apply.

For drift, inspect `agent-assets status --json`, `agent-assets verify --json`
and `agent-assets doctor --json`, then compare the reported target's raw link
text or entry type with its ownership record. A changed entry is preserved and
the ownership-drift diagnostic identifies its exact target/state. Preserve any
foreign content before explicitly reconciling that target under a reviewed
source selection; do not delete the ledger, adopt a source-root-contained link,
or rewrite protected links to manufacture an admissible pair.

For a reviewed nonpending selection, `agent-assets remove --dry-run` followed by
an explicitly authorized remove/reconverge is an existing feature-wide recovery
option: it temporarily removes all exactly owned links and the recognizable owned
launcher, not just one target. Run the installer directly after launcher removal.
Changed entries remain preserved and require a separate decision. Remove is not
a cure for pending handoff/converge state, unexplained staging or arbitrary
protected-pair drift; pending removal resumes only removal.

### Protected source relocation is not an ordinary source move

An exactly recorded public `pr-triage` pair at an old source prefix is neither
fresh nor current at a newly selected prefix. Deleting its links alone leaves
the ownership records and does not make it fresh. Do not delete/rewrite the
ledger or handoff, or adopt an unrecognized launcher, to bypass this refusal.
Where supported, restore the trusted installer and sources at their recorded
canonical prefix through the established same-path bootstrap/update workflow.
A symlink alias is not equivalent: launcher generation resolves the installer
path. Prefix restoration alone does not satisfy pending identity/digest pins.

The narrower cleanup/reinstall case requires nonpending **null handoff**,
otherwise exactly owned entries and an unchanged recognizable launcher. Review
the exact direct installer invocation with `remove --dry-run` first. Only after
its successful result and explicit authorization may the same invocation run
`remove`, followed by reviewed `converge --dry-run`, `converge`, and `verify`.
Keep the same HOME/XDG context and exact state/ownership paths. For a public-only
selection, pass its explicit `--manifest`; for composed public/private resources,
retain the reviewed ENVY_MANIFEST selection and omit `--manifest`, which selects
one asset manifest. ENVY_DRY_RUN=true also forces dry-run; do not silently change
that caller setting.

Whole-checkout relocation can change both asset paths and launcher content.
The moved installer preserves the unrecognized old launcher and may return a
partial removal after removing other exactly owned links. Inspect dry-run first;
there is no universal arbitrary-relocation recovery command. Completed handoff
provenance keeps its exact provider/adapter source mapping even after remove,
so feature-wide cleanup does not authorize its relocation either.

## Ordinary v2 intent and recovery

Ordinary v2 link updates/additions, deselection and removal use an optional
pending-only `ordinary_operation` record. Its immutable plan binds baseline,
selection, ordered targets/preimages and exact staging names; a cursor records
acknowledged progress. The next replacement first creates an exclusive staged
object and checkpoints its observed device/inode/type identity. Only that active
prepared object can explain a lost rename response; identical symlink text on a
different object is not adoption authority. Unlinks bind the recorded preimage.
Completed entries must retain their postconditions and future entries their
preimages. Source moves replace directly rather than unlinking first.

The existing 16 MiB limit covers initial, prepared, progress and final checkpoints
before any payload staging or mutation, including every fresh protected-pair
intermediate and subsequent ordinary operation. Fixed-width prepared identities
and the maximum valid cursor width bound every progress encoding. Resume repeats
capacity admission; it never rewrites pending evidence to make it fit. Intent
overhead may require headroom even when completed ownership fits. A genuine
current no-op does not create intent or require extra journal headroom.

Capacity admission checks serialized checkpoint size, not available disk space:
every required encoding must be at most 16 MiB. It does not reserve free space,
guarantee later I/O success, or provide an fsync/durability guarantee.

Only the recorded action resumes. Pending remove is ledger-only, precedes
protected-pair completeness checks, and needs no source discovery; convergence
cannot recreate its already removed links. Pending converge retains its exact
selection and cannot be replaced by remove. Status/verify/doctor report a validated
pending ledger before resolving current manifests, reading current state or
discovering sources, even when those manifests are missing or malformed. Invalid
ledger content does not become pending-operation authority. Actual same-action
converge, including dry-run, still requires the full original selection checks;
an admitted dry-run describes resume without advancing it. Foreign removal entries
remain preserved with their records, and diagnostics are derived from the bounded plan.

Ordinary staging uses `.<target>.agent-assets-ordinary-<nonce>`. Every relevant
reserved sibling is inspected without following it; the only exception is the
current prepared object with matching identity and payload. A planned name alone,
another cursor's stage or an extra sibling is not owned evidence. Death between
stage creation and its identity checkpoint leaves an unexplained stage requiring
reconciliation. Never infer ownership or clean it automatically.

If a checkpointed prepared object later disappears from both its staging path and
destination while the original target preimage remains, ordinary convergence
refuses rather than recreating the stage. Pending converge also prevents removal:
there is no supported automatic continuation or cleanup for this state. Preserve
the ledger, remaining payload/staging evidence and original selection inputs,
and escalate for explicit recovery review. Recreating identical bytes, restoring
source paths alone, or deleting/editing the ledger does not restore the recorded
object identity and is not a supported recovery procedure.

Fresh protected pairs remain outside ordinary intent: after all capacity is admitted they are
installed and checkpointed before ordinary payload work; their partial-pair and
orphan-staging refusals are unchanged.

The identity hash is an equality witness, not proof against inode reuse or a
hostile same-user writer. Descriptor-relative rechecks retain the final POSIX
check/mutation gap; there is no general concurrent-writer or power-loss guarantee.
Completion omits the extension and preserves the existing five-field v2 shape
and handoff provenance. Older strict-v2 code rejects the pending extension and
cannot resume it; older-code rollback/downgrade while pending is unsupported.
Explicit v1 and the capability-only v1 no-lock cleanup path remain unextended.

## Handoff pending operation and recovery

The existing ownership JSON holds a version-2 `handoff` record with `version: 1`,
`state: pending|complete`, `plan_sha256`, and an immutable `plan`. Its exact plan
fields are `manifest_inputs`, `selection_sha256`, `prior_ownership`, `contract`,
`links`, `adapter_links`, `sources`, and `untouched_sha256`. Canonical sorted-key
compact UTF-8 JSON is SHA-256 hashed. Hashes detect drift, not operator forgery.

Prepare/read back pending ownership before touching either generic link. Create
any absent adapter links first, then replace the generic links individually via
exclusive owned temporary symlinks and `os.replace`, never unlink-before-replace.
Verify all four links before the final ownership checkpoint. Keep unrelated
ledger entries and the exact prior ownership/provenance in the complete record.
Both prospective ownership checkpoints are schema-checked and encoded within
the same 16 MiB limit before pending ownership or any link is written. Resume
also checks completion capacity before advancing; it does not rewrite an
existing pending record to make completion fit.

Resume requires the exact manifests, selection, inventories, unrelated snapshot,
and recorded old/new link states. Only a pending adapter creation explicitly
recorded as initially absent may remain absent. A missing generic target, changed
input, third state, or unexplained staging entry blocks automatic recovery.
When an enumerated entry is itself an exact directory in which the plan writes
participating links or ownership controls, its snapshot binds guarded device,
inode, owner and permissions instead of self-mutated directory size/timestamps.
Every unrelated immediate entry is still separately observed; unrelated child
directories retain their ordinary metadata checks. This supports explicit
ownership paths in a destination's parent without exempting that destination's
entries or allowing directory replacement. Old pending fingerprints are not
rewritten or adopted if recomputation differs.
Remove refuses pending state, including dry-run removal. There is no rollback,
automatic orphan cleanup, lock stealing, or source retirement.

Automatic recovery is limited to completed atomic steps/named checkpoints. A
fresh protected-pair installation is not journaled; interruption before its pair
checkpoint can leave a partial unrecorded pair requiring explicit reconciliation. Uncatchable
death can leave staging entries; preserve them rather than infer ownership from
their names. For selected contract and adapter targets, including adapters
retained in completed provenance, any immediate `.<target>.agent-assets-` entry
requires reconciliation before convergence or successful verification, even
when the final pair is wholly absent. This reserved-prefix check does not follow
the entry, establish ownership, remove evidence, or apply to ordinary v1 assets.
Refusal precedes destination link scans and stops subsequent doctor metadata,
audit-root and plugin inspection.
Per-link atomicity is not simultaneous two-harness visibility or a power-loss
durability guarantee. Handoff dry-runs report outstanding adapter creation and
individual atomic generic replacements; they do not perform an ordinary stale
sweep or launcher update.

## Persistent version-2 semantics

Once a valid v2 store exists, new-installer mutators never emit v1 or discard its
complete provenance. V1-only converge refuses before payload writes. A completed
handoff declaration may be omitted only if the actual public contract/provider
and both adapter Assets remain selected compatibly. Disabled/missing required
selection refuses convergence instead of silently removing those targets.

Explicit nonpending v2 remove works independently of current selection, using
only exact current ledger entries. It preserves drifted entries and their records,
never expands targets from old manifests, and retains an empty v2 store and the
complete/null handoff even after all links are gone. Historical completion is
not evidence of current installation or readiness.
Ownership is loaded before selection; v2 removal and its locked reread do not
resolve manifests, read current state, or discover sources. An unconfigured or
unavailable manifest, or a source that would fail discovery, does not prevent
ledger-only removal. Pending handoff/converge and invalid stores still refuse
removal; a pending ordinary remove resumes only its exact recorded operation.

One capability-only cleanup path preserves existing legacy stores on a host
without Unix locking: when a validated v1 store encounters a structurally valid
current v2 composition, remove can use only the v1 record, without asset
discovery. It removes exact recorded symlinks and an explicitly recorded exact
current launcher, preserves changed/unrecorded entries, and retains remaining
v1 records on partial refusal. Dry-run writes nothing. Other selection errors,
invalid stores and all v2 stores still refuse; there is no manifest downgrade,
v2 write, source-root adoption or fallback to recorded old manifests. This
legacy path retains its non-Unix pathname/race and JSON-reader limitations.

Old binaries with old manifests cannot be fenced by a new ledger version.
Downgrade while pending is unsupported. Deleting ownership/provenance is not a
supported migration or downgrade mechanism.

## Writer gate and filesystem footprint

On Unix, new mutators share a nonblocking advisory lock on the stable empty
`<ownership filename>.lock` sidecar. Busy fails immediately. Process death releases
the advisory lock; the sidecar remains. Ownership/lock opens use lstat checks,
`O_NOFOLLOW|O_NONBLOCK`, regular-file/owner/link-count/mode checks, and opened
descriptor/directory-entry identity comparison. FIFOs, symlinks, hardlinks and
invalid modes are refused without repair. New v2 stores and locks are mode 0600.
Before scaffolding or payload changes, the ownership file and its derived lock
must be disjoint from the launcher and participating asset leaves: equality or
either ancestor/descendant relationship is refused. A control also cannot replace
a required destination directory or its ancestor. Harmless same-parent siblings
remain valid. Namespace checks use absolute lexical components with Unicode
canonical normalization and case folding, without following payload symlinks; Unix
descriptor walks still enforce the separate ancestor rules. Ledger-only v2 removal
and pending operations use recorded targets (including prepared stage names),
without requiring current manifests or provider discovery. A collision preserves
existing evidence; it does not authorize deleting or rewriting a stuck store.
The launcher itself must also be disjoint from asset leaves and all declared
destination directories: equality and both ancestry directions are refused before
any checkpoint or mutation, including fresh protected-pair installation. Recorded
links, ordinary link steps/stages and retained handoff targets are checked without
current discovery. The launcher's own typed ledger entry and launcher-update step
are not asset targets and do not collide with themselves. Separate sibling paths
remain valid; overlapping selections require explicit reconciliation, not cleanup
of the ledger or existing payloads.
Distinct selected, protected, or recorded retained/removed/pending payload leaves
must not be ancestors of one another. Recorded payloads are checked even during
ledger-only removal without current manifests; ambiguous records are not repaired.
An asset leaf also cannot equal or contain a required destination directory, even
when that directory currently has no selected assets. These component checks run
before any checkpoint or mutation, including fresh protected-pair installation.
The inverse containment is normal: destination directories may contain asset
leaves or other destination directories, provided their asset leaves are disjoint.
Same-parent siblings and shared directory roots remain valid. Claims to the
same asset leaf must select the same resolved source, including across
harnesses; conflicting selected source claims are refused before checkpoint or
mutation. Existing handoff restrictions still apply to otherwise valid sharing.
Fresh protected installation uses full declared target sets, merging overlaps and
writing each shared destination once before its unit's ownership checkpoint.
Multiple link writes are not atomic: existing interrupted, unrecorded partial
pairs still require reconciliation and are not adopted automatically.
Distinct raw leaves that become equal under canonical case folding are refused.
This is deliberately conservative even on case-sensitive filesystems: a layout
distinguished only by case or canonical Unicode spelling can be rejected there.
Raw paths remain unchanged in records and diagnostics. These comparisons do not
establish equivalence for arbitrary mount, hardlink or other filesystem aliases.
A refusal does not authorize rewriting a pre-existing pending operation or
deleting its evidence.
Ownership JSON and transfer-inventory reads through `regular_bytes`, and encoded
ownership checkpoint output, are bounded to 16 MiB. This is not a universal
input bound: ordinary manifest/state JSON, frontmatter and launcher readers are
unchanged except for Unix launcher removal, and do not generally acquire these
nonblocking/type/size guarantees.

Unix launcher removal holds the parent descriptor, reads an owned single-link
regular leaf without following symlinks, bounds that read to the expected
launcher length plus one byte, and rechecks its named identity before unlinking
relative to that parent. A replacement after content observation is preserved
and reported. Explicit v1 removal retains its existing treatment of an already
foreign launcher; v2 and the capability-only legacy cleanup retain changed
records on partial refusal. A final identity-check/unlink gap and ancestor
replacement limits remain: POSIX unlink is not an atomic conditional unlink.
This does not promise safety against arbitrary concurrent same-user writers.

V2 removal dry-runs list the remaining planned removal count and paths, plus
changed entries that will be preserved. A pending removal preview excludes an
already removed current step and does not advance the journal. Refusal diagnostics
include their recorded target paths; a preview is not permission to remove foreign
entries and does not make a subsequent operation race-free.

For an ordinary first install, existing `--ownership` / environment / XDG-default
selection authorizes only its exact missing parent chain under the nearest safe
current-user-owned directory. Retain the preliminary existing-chain/anchor
identities and exact missing suffix; recheck that observation before creating
only the recorded missing chain, one component at a time, mode 0700. An appearing/changing
component is a refusal, not adoption. Existing platform ancestors are traversed,
never created or chmodded/chowned. No new root option changes path precedence.
That refusal also applies when the first missing component or entire suffix
appears before the later creation walk, even if its owner and permissions look safe.

Preparation failure can leave exactly a created prefix of that chain and its
empty owned sidecar. The command reports created paths; preserve them for
reconciliation. Legacy handoff/pending resume require existing parents and may
create only the sidecar. No-op remove with no store and all dry-run/status/verify
operations create no scaffolding, sidecar, ownership, launcher, or links.

The gate covers cooperating new handlers sharing one store, not arbitrary
external filesystem writers or separate stores targeting the same destinations.
V2 handoff requires supported Unix locking; it is not enabled on other platforms.

## Verification boundary

`tests/test_agent_assets_handoff.py` reuses the existing reflect fixture's owned
path/context policy. It runs only explicitly selected Python installer/driver
subprocesses with 15-second timeouts and one fixed local `git show` blob read for
the old-installer rejection case. All fake source trees, state, ledgers, locks,
targets, sentinels, and fault-injection artifacts live in owned temporary roots.
The driver is test-only and injects failures into the real checkpoint/replace
paths; no production fault-switch environment variables are added.
Launcher/destination collision fixtures compare full owned-tree content and metadata
before and after refusal. Pending-converge fixtures cover missing/malformed manifests
and forbidden discovery, with a positive interception control proving that actual
resume still attempts selection validation.
The launcher witness replaces a real owned leaf after its content read and
supports both the old path reader and the descriptor reader. The no-lock fault
sets only the imported module's `fcntl` capability to `None`; it does not emulate
native Windows paths, ACLs or symlink behavior. Actual-public-v2 ordinary-link
tests use the real public manifest in the triage fixture, with the same owned
HOME/state policy and 15-second child bound. They are installer tests, not
actual-harness skill evaluations.

These tests do not establish agent behavior, live reviewer identity, adapter
access, provider availability, managed rollout, or harness reload. Those remain
separate authorized acceptance work. Run the repository's full Development QA
as well as focused tests before shipping; source-level design/static checks are
not runtime test evidence.
