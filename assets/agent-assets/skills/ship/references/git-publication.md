# Exact Git publication

This guard targets one logical branch with ordinary non-force Git, on a trusted
server with a known **ordinary-ref contract**: the destination is not an alias,
and the server does not create/retarget destination aliases during publication.
The client cannot discover or enforce that server behavior. Unknown servers are
unsupported; acknowledgment needs a basis in the hosting/server contract, not an
assumption inferred from a successful read or a passing code review.

The guard does not promise physical-ref isolation against a server that violates
that contract: a server can retarget a symbolic destination between the client's
last check and receive-pack's update, including toward a protected branch. SHA
compare-and-swap alone cannot prevent that. Observed aliases are rejected, but
absence of an advertised alias does not prove the server contract.

It does not grant authority, review code, merge pull requests, discover forge branch
protections, or sandbox repository/server hooks. Keep native PR merge/check
requirements; do not substitute a direct push for the user's requested PR flow.

## Plan, approve, apply

Resolve this skill's `scripts/git_publish.py` relative to its installed directory
(the same asset is available to both harnesses). Use an access-controlled durable
directory outside the repository for the plan and receipt. New files are created
with mode 0600 and never overwrite existing files.

After reviewing the candidate, name the repository, source revision, configured
remote **name**, full branch ref and publication intent explicitly. For example:

```sh
python3 /path/to/ship/scripts/git_publish.py plan \
  --repo /path/to/repository --source HEAD --remote origin \
  --destination refs/heads/example-feature --intent feature \
  --server-contract ordinary-refs \
  --output /path/to/evidence/publication-plan.json
```

This contacts the effective **push** endpoint read-only, even when the fetch URL
differs. The resulting JSON records the canonical worktree/Git-directory paths,
resolved commit, endpoint, destination and previous remote SHA/default branch.
`--server-contract ordinary-refs` explicitly records the supported server
assumption in both plan and receipt; the flag itself enforces no server behavior.
Inspect those exact values and the digest before any mutation. `HEAD` is allowed
as a source selector, but moving it invalidates the plan; the push itself always
uses the resolved immutable commit, never moving `HEAD`.

`feature` authorizes only an ordinary feature-branch publication. The remote's
advertised default branch and common default names require `protected` intent.
Supply additional protected destinations discovered from repository/forge policy
using repeated `--protected-ref refs/heads/...`. The helper cannot discover every
forge's protection rules; the caller must classify the destination correctly.
`protected` still requires explicit user authority for that exact branch and must
respect required forge checks. Review approval alone is insufficient.

After confirming that the existing user authorization covers the exact plan,
apply with the digest printed by `plan` and the matching authority class:

```sh
python3 /path/to/ship/scripts/git_publish.py apply \
  --repo /path/to/repository \
  --plan /path/to/evidence/publication-plan.json \
  --approve EXACT_PLAN_DIGEST --authorize feature \
  --receipt /path/to/evidence/publication-receipt.json
```

No fresh interactive approval is needed when the user already authorized these
exact actions/targets. Missing or changed scope needs approval, not inference.
Changing the plan or candidate invalidates the previous approval/review binding.
Never edit a plan or recompute a digest to evade a failed precondition.

## Safety and limitations

- Multiple push URLs, mirror remotes, custom transports/helpers and ambiguous
  chained URL rewrites stop before publication. A single `insteadOf` or
  `pushInsteadOf` expansion is resolved and bound to the plan.
- Configured `push.pushOption` is unsupported, including empty or multivalued
  settings: options can trigger forge actions beyond the approved ref. Presence
  is checked before planning/applying and again after an existing pre-push hook;
  values are not included in diagnostics, plans or receipts. Reconciliation with
  `verify` remains available because it does not push.
- Use credential helpers/SSH agents. URLs with passwords, HTTP userinfo, query
  strings or fragments are rejected before contacting the remote or storing the
  plan, as are ambiguous scp-like endpoints with `@` after the first `:`.
  Ordinary `user@host:path` endpoints remain supported. Never place tokens in
  scp/SSH userinfo, URL paths or remote names either. The helper does not print
  Git/credential-helper diagnostics, which may contain secrets.
- The caller must trust its Git executable, transport environment, SSH/HTTP
  routing and credential helpers. Binding an endpoint string does not establish
  transport authenticity or sandbox those components.
- Existing destination history must be an ancestor of the candidate. The helper
  never fetches missing objects, rewinds a branch or enables force-pushing; fetch
  separately if authorized and generate a new plan when the baseline is missing.
- The push uses one explicit commit/refspec. Configured tracking/push refspecs
  cannot select another destination; automatic tag/submodule publication is
  disabled. Server-side hooks and forge branch protections remain authoritative.
  Pushing to the bound URL does not update local remote-tracking refs; use the
  receipt's remote read-back as evidence, not local `origin/...` state.
- A temporary command-scoped pre-push wrapper verifies Git's advertised old SHA,
  candidate and sole destination, then chains the existing executable pre-push
  hook. Hook rejection remains a rejection, and candidate/endpoint checks run
  again afterward, including a fresh remote-state/alias check. Git's receive-side
  compare-and-swap catches later SHA races under the ordinary-ref contract; it
  does not prevent server-side alias changes.
  Nothing is installed in the repository's hooks or config. Hooks that expect to
  launch nested pushes under the inherited hook configuration are unsupported;
  do not bypass hooks to make a blocked publication succeed.
- The helper does not stage, commit, switch branches or clean files. Unrelated
  staged/unstaged/untracked work is preserved.
- Each Git subprocess has a 120-second timeout. A timeout after publication
  starts is an uncertain outcome requiring reconciliation, not an automatic
  retry or an assumption that the remote was unchanged.

## Verification and interruption

A receipt is checkpointed **before** attempting the push. The hook records its
verdict directly into that durable receipt, not temporary hook files, and the
parent preserves it through interruption/cleanup. Only `status: verified`
combines a durable accepted hook verdict (or an explicitly recorded no-op) with
the candidate at the planned logical destination at read-back, under the stated
server contract. A dry-run, successful exit, or matching SHA alone is insufficient.
`push_result: failed_or_interrupted` with `status: verified` requires that recorded
approval as well as read-back despite the uncertain transport result.

After interruption, preserve the plan and receipt and reconcile with `verify`.
This is **remote-read-only** but updates the local receipt's `observed` and
`status` fields. Each observation replaces the previous one; the receipt is not
an append-only history. Preserve a separate access-controlled copy first when
the previous observation is needed as evidence.

```sh
python3 /path/to/ship/scripts/git_publish.py verify \
  --repo /path/to/repository \
  --plan /path/to/evidence/publication-plan.json \
  --receipt /path/to/evidence/publication-receipt.json
```

Normal exception handling removes only receipt staging files allocated by that
invocation. An uncatchable termination such as SIGKILL can leave a mode-0600
staging file beside the receipt (and temporary hook resources); cleanup is not
guaranteed. Preserve and inspect such artifacts privately. Their existence does
not establish approval, and the helper neither automatically deletes stale files
nor treats them as authoritative receipt replacements.

An existing receipt prevents replaying `apply`. Failed read-back, changed
endpoint, `rejected`, or `needs_reconciliation` requires inspection of the original target;
report the planned and observed SHAs without automatically retrying, deleting
refs, force-pushing, rewriting remotes/authentication, or rolling back.
`rejected` remains a failure when a guard/existing hook rejected the operation,
even if another actor independently published the candidate in the meantime.
Pending/missing hook approval remains `needs_reconciliation`, even with a matching
SHA; do not edit the receipt or rerun a hook to invent historical approval.
`unsupported_server_state` means an alias was observed in violation of the
acknowledged contract. Stop and inspect all potentially affected refs: detection
after the write cannot undo a server-side redirect. No automatic rollback occurs.
