# Optional interactive secrets synchronization

Bundles may opt into `settings.secrets.mode: "interactive-sync"`. Omitted mode
retains the existing recovery lifecycle. Interactive mode disables automatic
restore/export/identity generation and key-transfer commands. Existing recovery
material is retained, including on removal.

Example bundle settings:

```json
{
  "secrets": {
    "mode": "interactive-sync",
    "target": "~/.secrets",
    "sync": {
      "repository_root": "${BUNDLE_ROOT}",
      "ciphertext": "secrets/development.sops.json",
      "blocked_names": []
    }
  }
}
```

The checkout and ciphertext parent must already exist. Plaintext lives outside
the checkout. Parent paths cannot be symlinks or writable by other users; targets
must be owned regular files with one link. Crypto tools must already be available
on PATH or installed in mise (resolved offline before unlocking; never installed
by sync). SOPS 3.10.2/native age 1.3.0 are the tested pinned format/tool pair.

`envy secrets setup`, `merge`, and `export` require a foreground controlling TTY
and fresh hidden native-age identity entry. All entries start at keep. Select
individual `a/o/d/k ID` actions or batches: `a1,2 o3` adds entries 1 and 2 and
overwrites entry 3. `a 1, a 2, o 3` and `a 1 2 o 3` are equivalent; `a12` is
entry 12. Spaces/commas delimit whole IDs and action groups; no ranges/wildcards.
Invalid lines change no selections. Conflicting actions for the same entry on
one line are rejected; later lines can revise choices. `k1,2` resets both entries.

The readable menu shows direction, paths, stable IDs, comparison, planned actions,
counts, blocked reasons and an example. It appears initially and on `list`;
edits show only changed rows and totals. Errors stay next to the prompt. Optional
color supplements labels, with NO_COLOR and missing/unknown/dumb TERM fallback.
Enter `help`/`?` for short contextual guidance without losing the plan.
Enter `apply`, inspect the review, then separately type `y`/`yes` at `Proceed? [y/N]`.
Enter or `n`/`no` returns to selection, keeping choices; `cancel` exits entirely.
Help preserves the plan and still requires fresh confirmation. No `review` command
or alias exists. Use commands standalone, not mixed with actions. Applying an
empty selection writes nothing; blank selection input simply reprompts.
No values appear in the UI and there is no noninteractive approval path.

Setup imports selected entries into an absent local file; merge imports into an
existing file; export updates a repository envelope or creates the first one.
Source absence never deletes destination entries. Literal flat-shell assignments
are parsed without execution; ambiguous names are blocked and compound/multiline
shell files are refused. Local comments and excluded content are preserved.

The entry map is a versioned JSON payload encrypted as one SOPS binary value, so
names are encrypted too. Only a single native age recipient is allowed. The
private identity stays in process memory and anonymous child pipes, never argv,
environment values or files. Crypto HOME/config/environment are isolated; raw
child errors are suppressed. Python memory is not guaranteed to be zeroized.

No Git transport, commits, vault or clipboard access is performed. Saved GitHub
credential integration is opt-in as described below. Bundle authors should mark the ciphertext path `-diff -merge` and
document their separate identity custody and blocked-name policy.

Do not run editors or Git writes concurrently with sync. Advisory locks serialize
participating commands, snapshots detect intervening changes, and replacement is
atomic. Arbitrary external edits between final check and rename are outside the
cooperative guarantee. Mode-0600 plaintext staging is cleaned on handled errors;
SIGKILL/power loss may leave a sibling behind. This does not defend against root,
same-user compromise, swap, or malicious crypto executables.

Exit codes: 0 applied/no-op; 1 runtime/preflight refusal; 2 usage/configuration;
3 committed but completion/durability uncertain (inspect before retrying);
130 cancelled before commit. Routine checks verify presence, permissions and
public envelope structure, never decryption/equality. Shell startup continues
loading the local plaintext without unlocking. Start a new shell after setup/merge
of the configured shell secrets file. Export reports that local secrets are
unchanged and Git commit/push were not performed; no shell reload is needed.

## Optional saved GitHub logins

Provider policy does not require its optional tools to be installed. Routine
secrets checks report missing gh/native Git prerequisites without failing the
feature or inspecting saved credentials. An explicit sync blocks that provider's
rows, preserving encrypted records while unrelated environment, SSH and native
Git operations continue. Missing CA/network helpers are provider-local too.
Install the declared tooling normally and retry; sync never installs it. Tooling
present does not imply verified native compatibility or working authentication.

Bundles may add `sync.github` with an explicit `accounts` list of canonical
`{"host": "github.com", "account": "example"}` objects and a private
`config_directory` such as `~/.config/gh`. `executable` selects an already
installed native command; verified support is macOS gh 2.83.2 and Linux gh 2.100.0.
Linux currently requires a reachable session Secret Service with no login/default
keyring collection, matching the verified native file-fallback configuration.
Existing Linux keyrings and unavailable metadata refuse pending separate native
verification; the real session bus is preserved, never masked to force fallback.
Wrappers and other versions refuse until verified. Optional `ca_file`
and argv-array `network_command_prefix` supply bundle-owned trust/routing.
`platforms.darwin` and `platforms.linux` may override only those three command/
trust settings; no routing policy is inferred by the core.

After the fresh manual age unlock, configured accounts are compared through
qualified saved-token lookups. Rows are `gh:HOST/ACCOUNT` or `env:NAME`, with
explicit destinations. GitHub tokens never become shell token overrides; all
four global GitHub token names are blocked in this mode. Missing environment
files are allowed and created only when environment entries are selected.
Setup is add-only; merge reconciles selected destinations. Import validates the
matching account and classic `repo`, `read:org`, `gist` scopes over verified HTTPS.
It refuses changes that would switch another active account. Local GitHub removal
is unsupported; deleting a repository record does not log out or revoke a token.

All selected imports are preflighted before writes. Native updates commit in
host/account order, then one environment-file replacement. They are not atomic
as a group: per-entry outcomes distinguish applied, failed, uncertain and not
attempted; partial or possibly committed failure returns exit 3, without rollback.
Cancellation before writes returns 130. Native gh owns secure storage and its
0600 plaintext fallback. Routine diagnostics never inspect saved tokens.

Adding GitHub records upgrades the encrypted payload to version 2 while retaining
unselected records. Version 2 stays version 2; no-op leaves ciphertext bytes
unchanged. Unconfigured GitHub records remain encrypted and blocked. Omitting
all credential providers selects the version-1 environment engine, which refuses
typed versions; an empty configured account list supports typed stores without
credential reads.

## Optional Git and SSH file credentials

Bundles using these extensions must declare `requires: ["secrets-fleet-v1"]`
in a version-2 bundle manifest (alongside any other required capabilities).
`sync.git` declares an explicit `backend` and bounded `accounts` list. Backend
`gh` references the same configured saved-gh record, with no second writer.
Backend `osxkeychain` selects the verified Apple Command Line Tools helper on Mac,
with exact HTTPS host/account/default-port-zero/empty-path contexts. Optional
`ca_file` and `network_command_prefix` apply to fixed-endpoint identity validation;
this independent Git import does not impose gh-specific OAuth scopes. It never
installs helper config, runs a helper chain, or broadcasts approve/reject.
Other stores/refresh-token formats are currently unsupported, not overwritten.

`sync.ssh` contains `directory` (owner-controlled absolute/home-relative path) and
`filenames` (at most 32 explicit safe basenames). No recursive key discovery.
Envy leaves existing directory permissions unchanged: 0700, 0750 and 0755 are
accepted when owned by the user and not writable by others. Symlinks, unsafe
parents and shared-write permissions are refused, not repaired. Private key
files still require mode 0600; public files may be 0600 or 0644. Only a confirmed
selected import creates an absent SSH directory, using mode 0700. Permission
changes detected during a sync invalidate the session rather than trigger chmod.
Supported software OpenSSH keys are Ed25519, RSA and NIST ECDSA. Hardware keys,
certificates/certificate companions, legacy PEM and unsafe files are refused.
SSH rows are identified by their public SHA256 fingerprint. Every row defaults
Keep; private bytes enter the encrypted repository only after explicit selection.

On import, `name ID BASENAME` chooses an alternate target and resets that row's
selection. A different fingerprint at the same filename requires explicit
overwrite, showing old/new fingerprints and exact paths before fresh confirmation.
Public-only imports cannot leave a mismatched existing private key. Local key
deletion, SSH config edits, agent loading and remote registration are unsupported.
Protected selected keys ask for a separate hidden passphrase during preflight.
OpenSSH validates the pair using mode-0600 disposable staging outside Git; the
original bytes/protection are retained, and passphrases are not saved. Cancellation
or validation failure before writes leaves all selected destinations unchanged.

Git/SSH records require payload version 3; known unselected records are preserved,
versions do not downgrade, and empty selections leave ciphertext unchanged.
After all preflights, commit order is gh, native Git, SSH private/public pairs,
then the environment file. A key pair is not atomic: partial/uncertain writes
return exit 3, stop later destinations, and require a fresh comparison to retry.
