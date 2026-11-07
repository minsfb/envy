# Version 3: finite multi-artifact test API

This is a source-only integration contract, not a live publisher, registration
mechanism or extension of the skill's offline authority. No v3 CLI writer or
native provider is supplied. Version1/2 schemas and their fixed provider paths
remain distinct. Existing stores retain their original source binding; changing
code is not automatic migration or renewed approval.

## Inputs and exact binding

`make_multi_plan(selection, prepare_authority, binding)` produces a version3
PublicationPlan. It reads the trusted core source to bind its digest, but creates
no store, provider, process or network request. `prepare(..., binding=binding)`
may persist this plan in the same private four-file store after a preparation
request. Generic CLI `prepare` still accepts only the original ProposalSet.

Selection fields are exactly `version:3`, `type:ArtifactProposalSet`, `batch_id`
(32 lowercase hex), `audience`, `project`, `proposals`. Each proposal contains
`id`, `title`, `detail`, `issue_body`, `tracker_body`, `dependencies` (selected
proposal IDs). The three bodies are independent approved-content candidates;
the core does not summarize, redact or declassify private detail. Audience is
`{kind,realm,id}`: known `author` or `group`, or the exact internet/public tuple.
All artifacts and the project must have equal audience. An author ID must match
the observed actor. A host/visibility label does not imply group membership.

Project is `{mode:existing,id}` or `{mode:create,title}`. Creation is explicit,
never fallback for a missing project. There is no adoption/update/delete operation.

Binding fields are `name:multi-artifact`, `version:1`, `source_sha256`, `context`,
`policy`. `context` contains:

- `actor_id`: bounded opaque actor identity;
- `destinations`: exactly `project`, `details`, `issue`, `tracker`, each with
  `{realm,account_id,target_id,audience}`; realm is the returned-link DNS host,
  not a data-selected transport endpoint;
- `existing_project`: `{id,owner_id,audience}` matching the selection and actor,
  or null for explicit creation;
- `capabilities`: exact booleans `read`, `find` and each of the seven role names.
  Read/find and every selected role must be true. Unsupported unused roles may
  be false. The dependency capability means a genuinely additive native primitive.

`policy` is the exact `MULTI_POLICY` constant in the implementation. The binding
is a trusted adapter's normalized observation, not proof that a live tool or ACL
is qualified. Native names, status mappings, auth, commands and policy belong to
the statically selected integration, not the public input schema.

## Seven roles and typed references

The fixed sequence is an optional project prelude, then stable dependency order
over proposals: details, issue, tracker, details backlink, issue backlink, optional
additive tracker dependencies. Each operation is exactly
`{id,kind,proposal_id,marker,destination,audience,requires,data}`.

| Kind | Resolved data fields |
| --- | --- |
| project.create | title, owner_id |
| details.create | title, body, owner_id |
| issue.create | title, body |
| tracker.create | title, body, owner_id, project_id, external_url |
| details.comment.create | parent_id, body |
| issue.comment.create | parent_id, body |
| tracker.dependencies.add | target_id, dependencies, mode:additive |

Unresolved bodies are lists of `{text:literal}` and `{ref:operation-id,field:url}`.
ID slots use `field:id`. Only declared earlier operations may supply references;
URL slots can reference only details/issue/tracker creates. Proposal text is not
a template, even when it resembles a reference. Requirements are regenerated,
not accepted as an arbitrary caller DAG. Resolution uses only verified receipt
rows. All generated bodies include an exact `proposal-v3` marker and the fact
that tracking does not authorize implementation.

## Shared coordinator and provider boundary

Plan fields and digest retain the common shape. Receipt v3 uses the v2 policy and
reason fields, the same state machine, authority records, append-only history,
exclusive lock, intent/result/verification checkpoints and canonical store.
There is no second ledger. `status` performs no backend reconciliation.

Trusted code may call `multi_execute(store,digest,authority,factory,scope=...)`,
where scope is filing or reconciliation. The factory has one static absolute
`source` path and `open(expected_binding)`. Plan/environment data cannot select
this object or its source. Inside the existing lock, exact source/plan/receipt,
authority, state and capacity checks precede lazy construction. The core checks
the factory source's current bounded bytes against `source_sha256` before each
provider entry; symlinks, group/other-writable files and foreign owners fail. The factory's
qualified source closure, dependencies and native restrictions remain the trusted
integration's responsibility. A matching file hash is not an executable sandbox
or evidence of native startup/transport safety.

The opened adapter exposes exact `identity`, `read_context()`, `find(request)`,
`create(request)` and `read(request,artifact_id)`. `create` dispatches only these
seven fixed kinds; for dependencies it means additive mutation, not another
artifact creation. Context must exactly match the plan before each write and
verification. No public data chooses a module, executable, command or endpoint.

A resolved request is `{operation_id,kind,marker,destination,audience,data}`.
The normalized result is those fields plus `id,url`. Returned URLs are bounded
HTTPS links on the observed realm, without userinfo, fragment or whitespace;
provider-specific path/ID validation remains mandatory in the native adapter.
Comments, projects and dependency results have null URL rather than invented
links. Dependency result ID is the verified target's ID. Artifact IDs are scoped
by destination and role; duplicate mappings or non-null URLs are refused.

Exact content/parent/owner/project/link fields must match. Additive dependency
readback includes all requested edges and preserves unrelated edges; concurrent
unrelated changes do not turn into replacement writes. The receipt stores the
latest verified edge readback under the same ID.

## Uncertainty and bounds

Create/comment preflight discovery returns
`{traversal_complete:true,consistency:not-guaranteed,matches:[...]}` with at most
two records. A marker conflict blocks without adopting or editing it. Unavailable
preflight pauses the never-attempted row; a later authorized invocation may
repeat those reads. Empty discovery is a best-effort observation, not an absence,
uniqueness, authorship or permission guarantee.

Every selected write has at most one attempt. A lost result requires read-only
reconciliation: one exact discovery match plus direct read for creates/comments;
direct read of the verified target for additive edges. Zero/ambiguous matches,
edited content, failed reads or missing edges remain uncertain. No possible
write is replayed, even when the test knows it failed before an effect. A blocked
row cannot be reset, and a fresh store is not an adoption/recovery workaround.

Bounds are20 proposals,121 operations,1MiB selection,4MiB plan,8MiB receipt,
1024 history events and64 authority records. Generated details/tracker bodies
are<=64KiB and Issue bodies<=60KiB, including fixed text and512-byte URL slots;
canonical JSON body size is also bounded. IDs are<=64 characters, URLs<=512
bytes, dependency readback<=100 IDs. Before store creation, capacity reserves
all maximum-width mappings/results plus complete bounded history/authorities.
Per-operation capacity checks still precede effects. No limits are silently
raised for a larger batch, and no runtime retry loop is introduced.

Owned in-memory fixtures validate this contract. Native provider qualification,
broader audience relations, adopted artifacts, managed delivery and separately
authorized live acceptance are not supplied or implied by these tests.
