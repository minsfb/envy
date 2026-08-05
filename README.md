# envy

`envy` is a portable, stateful developer-environment manager built around
Chezmoi and Mise. It selects features once, converges them repeatedly, verifies
drift, diagnoses focused areas, and conservatively removes only state it owns.

## Requirements

- Bash 3.2 or newer
- Python 3.9 or newer
- Git and standard Unix command-line tools
- Chezmoi and Mise on Linux; macOS can install them through an existing
  Homebrew installation
- zsh and tmux for the recommended terminal preset; the terminal feature
  installs them itself through Homebrew on macOS or the first native Linux
  package manager (apt, dnf, pacman, or apk) via `mise bootstrap packages`
  and the installer-only `bootstrap-packages.toml`

Android SDKs, agent CLIs, and terminal applications remain externally supplied.
envy does not redistribute them.

## Install

```bash
./bin/envy install --preset recommended
```

Interactive installation selects recommended and optional features. For
automation, choose `--preset core|recommended` or use `--yes`.

```bash
envy install --reconfigure
envy upgrade
envy upgrade --only chezmoi,mise
envy status
envy verify
envy doctor tmux-persistence --verbose
envy uninstall --yes
```

`core` and `recommended` are selection presets. They are not environment
profiles and do not change feature implementations.

## Explicit bundles

One or more trusted bundles can add features, managed-file source trees,
agent-asset manifests, settings, and environment facts:

```bash
envy --bundle /path/to/team-bundle install --preset recommended
```

Bundles are never discovered automatically. Their IDs, versions, paths, hashes,
feature origins, and order are stored in state. Subsequent launcher invocations
reuse those recorded paths. Duplicate feature IDs, resource paths, environment
values, or recommended selections fail before any handler runs.

Use the lifecycle commands to change that persisted set:

```bash
envy bundle detach team-tools
envy bundle attach /path/to/team-bundle
```

Detach runs enabled bundle removal handlers in reverse dependency order without
purging user data. It journals each phase, so repeating the command resumes an
interrupted operation and detaching an already detached bundle is a no-op.
The journal pins the composed manifests and referenced package content; source
changes during an interrupted operation must be restored before resuming (or
explicitly abandoned with `bundle forget`).
`envy bundle detach ID --abort` reconverges the original bundle features before
the manifest commit point; after that point, attach is the reversal. Attach
restores the bundle's prior order and enabled feature selections. Both commands
reconcile only surviving features that were current before the bundle change
and became drifted because of it; unrelated pre-existing drift is left alone.

While a lifecycle transaction is pending, status, verify, and doctor remain
available, but other mutating commands refuse to run. `--dry-run` prints a plan
without writing state. `envy bundle forget ID` is a separate unsafe escape hatch
that discards bundle metadata, saved selections, and feature history while
skipping removal handlers; it warns because owned resources may remain behind.
After installation, `--bundle` may repeat the already recorded bundle set but
cannot add, omit, reorder, or replace it; use the lifecycle commands instead.

A portable bundle sets `"portable": true`. Its handlers, fact provider,
resources, and feature PATH entries must remain inside its package root. A
structured `facts` provider may report a host platform hierarchy and ephemeral
runtime environment facts as JSON; the core validates them without shell
evaluation. Explicit user environment overrides retain precedence.
`ENVY_NETWORK_PROXY` is applied only by core providers that fetch public
dependencies; explicit HTTP(S) proxy variables win, and bundle/internal
handlers do not inherit it automatically.

A bundle is a directory containing `envy.bundle.json`. Handlers use
structured `run`/`command` plus `args` fields and resolve relative to the owning
manifest; manifest strings are never evaluated as shell source.

Chezmoi sources are assembled in order into an owned working tree. Exact source
path collisions are rejected. Shared top-level shell profiles instead load
lexically ordered bundle fragments from `~/.config/envy/shell.d`. A bundle adds
tools to the managed toolchain by shipping a chezmoi-managed
`~/.config/mise/conf.d/NN-<bundle>.toml`. Machines with conf.d tools converge
through an unlocked `mise install` (core versions stay exact through the pins
in `config.toml`); machines without them keep the locked install.

Verification recomposes the configured chezmoi sources and compares the result
with the active composed tree, so edits inside core or bundle source trees
register as drift and converge on a plain `envy upgrade`.

## Features

| Feature | Preset | Purpose |
| --- | --- | --- |
| `smart-merge` | core | Initialize the JSON/text merge engine before managed-file convergence |
| `chezmoi` | core | Managed shell, tmux, Alacritty, Mise, and agent adapters |
| `mise` | core | Install the exact core tool set plus unlocked bundle conf.d layers |
| `path` | core | Generate the selected command environment |
| `terminal` | recommended | Ensure zsh/tmux through the native package manager and provide Alacritty on macOS |
| `agent-notifications` | recommended | Route Claude, Codex, MetaCode, and Pi attention events through terminal BEL |
| `tmux-persistence` | recommended | Resurrect/Continuum layouts and agent-session metadata |
| `agent-assets` | recommended | Common QA/workflow assets plus manifest-selected bundle assets with ownership tracking |
| `adb` | optional | Select an existing Android SDK without downloading it |
| `secrets` | optional | Manage SOPS+age recovery, plaintext restoration, and identity transfer over SSH |
| `remote-development` | optional | Run a shared browser editor on a Linux/WSL host through OpenSSH |
| `remote-development-tmux` | optional | Add workspace-scoped persistent tmux terminals to the remote editor |

### Shell fragments

The managed `.zshrc` and `.bashrc` stay small and portable. Everything
environment-specific loads from `~/.config/envy/shell.d`, which is applied
by Chezmoi like any other managed path:

- `*.pre.zsh` and `*.pre.bash` load early, before history, prompt, and aliases,
  for anything later configuration depends on, such as proxy variables.
- `*.zsh` and `*.bash` load late, for aliases and helper functions.
- Any other suffix, such as `.sh`, is ignored by both loaders and is useful for
  a shared body that a `.pre.zsh` and a `.pre.bash` fragment both source.

Machine-local additions still belong in `~/.zshrc.local` or `~/.bashrc.local`,
which envy never overwrites. Fragments carrying organization-internal detail
belong in a bundle rather than in envy.

### Tool provisioning

Chezmoi and Mise are used as found when they are already installed. When a tool
is missing, `pkg_ensure` resolves it in order: the exact pinned release recorded
in `dependencies.json`, then Homebrew on macOS, then any working copy already on
`PATH`. A pinned release is downloaded from upstream and installed into
`~/.local/bin` only when its SHA-256 digest matches the recorded value; an
unrecorded or mismatched digest fails closed rather than installing.

A tool found on `PATH` at some other version still converges — the pin is
reported as drift by `lib/dependencies.py` instead of blocking every feature
behind it. `envy` repins only binaries it installed itself, tracked by
`~/.local/state/envy/providers/<tool>.installed`, and `remove --purge`
refuses to delete an owned binary this host cannot reinstall.

Digests are recorded per platform from a network-capable host, and must be
refreshed whenever a pinned version changes:

```bash
python3 lib/provision.py record chezmoi
python3 lib/provision.py record mise
python3 lib/provision.py can-install chezmoi
```

### Remote development

Enable `remote-development` during `envy install --reconfigure` on a Linux or
WSL host, then connect from macOS, Linux, or WSL with the system OpenSSH client:

```bash
envy remote open user@host /path/to/project
envy remote status user@host
envy remote doctor user@host
```

The command prints a loopback URL, opens it in the default browser, and stays in
the foreground to own the SSH lease. Use `--no-browser` for PWA or manual
workflows and `--local-port PORT` when a stable local port is needed. No remote
TCP port is exposed: the pinned `code-server` process listens on a private Unix
socket and is reachable only through the SSH control connection.

One editor process is shared by all leases for the remote Unix user. The final
lease starts a 300-second shutdown grace period; a reconnect cancels it. Base
Envy uses Open VSX and installs no extensions. A trusted bundle can replace all
three registry endpoints and the grace period through
`settings.remote_development.extension_registry` and
`idle_grace_seconds` (0–3600). User editor data, interactively installed
extensions, and source trees survive both ordinary removal and purge. Envy's
provisioned Code OSS tree excludes the bundled Copilot Chat, GitHub,
GitHub Authentication, and Microsoft Authentication service integrations. It
also removes only the built-in browser entrypoints that the pinned artifact
advertises but does not ship, so those extensions use their included remote
Node entrypoints on the first connection. Extension discovery caches are
refreshed when the provisioned tree or installed extension inventory changes.

The browser client is the v1 adapter. Its session and engine boundary leaves
room for a future native client, but a VS Code-style native client will require
a compatible remote extension host and protocol negotiation rather than simply
reusing the browser engine. See [the design and security model](docs/remote-development.md).

Enable the separate `remote-development-tmux` feature for durable interactive
terminals. It depends on `tmux-persistence` and `remote-development`, adds an
`Envy Remote (tmux)` terminal profile, and uses a bounded workspace-derived
session name. Envy leaves existing tmux sessions running when the profile or
editor server is removed.

### Smart merge and managed files

The core `smart-merge` feature is initialized before Chezmoi and is an explicit
prerequisite for managed-file convergence. `bin/nv-smart-merge` handles identical
and one-sided changes, additive or three-way JSON, textual three-way merges,
and an optional external agent. Agent fallback is disabled by default. Enable
it explicitly with `--agent`, `ENVY_SMART_MERGE_AGENT`, or
`ENVY_SMART_MERGE_AGENT_COMMAND`.

`bin/nv-smart-merge-git` is the provider-neutral Git mergetool adapter. Install it
explicitly with `bin/nv-smart-merge-git-setup`; its agent policy also defaults to
`none`.

The Chezmoi conflict broker presents Enter-confirmed diff, overwrite, merge,
skip, and quit actions. Accepted local merges retain a managed base and hashes
under `~/.local/state/envy/chezmoi-merges` for later three-way updates.
`bin/nv-managed-json` exposes the additive JSON policy for application settings.

### Tmux and agent sessions

The persistence feature installs immutable Resurrect and Continuum revisions,
saves on detach/session-close/manual events, and restores when a new tmux server
starts. Selected Claude, Codex, and Pi adapters attach versioned session IDs,
working-directory hints, and transcript-path hints to panes.

Every save is a timestamped checkpoint; rapid manual saves stay distinct even
within the same second. The tmux action menu's `Session snapshots` entry (or
`nv-tmux-snapshot choose`) opens a paginated chooser that labels each
checkpoint with its agent count and restores the selected layout. Codex session
IDs are verified at save time against the primary non-subagent rollout file
held open by the running Codex process, correcting hook-reported IDs before
they are persisted; corrections are logged next to the snapshots.

Restoration invokes each agent's supported resume command. It does not preserve
process memory or copy transcript databases between hosts.

### Agent notifications

The notification feature uses terminal BEL as the transport across nested
shells, remote shells or SSH, and tmux. Claude and Codex receive additive
lifecycle hooks, MetaCode loads a global event plugin, and Pi uses the managed
lifecycle extension. The emitter writes directly to the originating tmux pane
so captured hook stdout does not swallow the alert. By default, it suppresses
alerts while that pane's tmux window has a focused client.

On macOS, Alacritty converts BEL into a visual flash and a deduplicated
Notification Center alert through `osascript`. macOS attributes these alerts to
Script Editor, which must be allowed under System Settings > Notifications.
Codex may also require one-time approval of the managed hooks through `/hooks`.
Terminal notifications require a live remote connection and attached tmux
client; use an out-of-band channel when disconnected delivery is required.

Adapters and focus behavior are manifest settings:

```json
{
  "settings": {
    "agent_notifications": {
      "adapters": ["claude", "codex", "metacode", "pi"],
      "condition": "unfocused"
    }
  }
}
```

`condition` accepts `unfocused` or `always`. Run
`nv-agent-notify --test` inside the target pane to test raw BEL delivery.

### Secrets

The disabled-by-default secrets feature restores a local plaintext runtime file
exclusively from SOPS documents encrypted to age recipients. Plaintext and
legacy string sources are rejected. Enable the feature before generating or
transferring its identity:

```sh
envy install --reconfigure
# Select secrets, then let the install converge its dependencies.
```

Feature installation applies the managed Mise configuration and installs the
locked `sops`, `age`, and `age-keygen` tools before running the secrets handler.
First convergence bootstraps the rest: a software age identity is generated at
the configured path when none exists, and when the plaintext target is present
but no encrypted source file exists yet, the target is encrypted to the
identity's recipient at the configured source path. An existing-but-invalid
source file is never overwritten. Only a machine with neither a plaintext
target nor a ciphertext source stays incomplete; convergence then fails with
guidance, and the transfer commands are available once the toolchain is
current.

Standalone installs default to `~/.secrets.sops`,
`~/.config/sops/age/keys.txt`, and a restored `~/.secrets`. A bundle or local
manifest may instead supply an explicit identity file and one or more typed
encrypted sources:

```json
{
  "settings": {
    "secrets": {
      "target": "~/.secrets",
      "age": {"identity_file": "~/.config/sops/age/keys.txt"},
      "sources": [{
        "type": "sops-age",
        "path": "~/persistent/secrets.sops.env",
        "input_type": "dotenv",
        "output_type": "dotenv"
      }]
    }
  }
}
```

The identity file contains one or more native age identities. Its configurable
path keeps private key material out of the manifest.

On the first machine, write the plaintext target (for example a shell fragment
of `export KEY=value` lines at `~/.secrets`), then let convergence generate the
identity and the encrypted source in one step:

```sh
envy upgrade --only secrets --force
envy doctor secrets --verbose
```

The equivalent manual `age-keygen`/`sops --encrypt` commands remain valid for
externally provisioned identities or hardware-backed setups.

To provision the same software identity on another trusted Unix host, enable
the secrets feature on the machine that will invoke the command, then use the
built-in SSH/SCP transfer instead of manually creating directories or fixing
permissions:

```sh
envy secrets push-key user@host
# Or, from a new machine:
envy secrets pull-key user@source-host
```

`push-key` and `pull-key` refuse to run unless the local envy installation
has the secrets feature enabled and its managed SOPS+age toolchain is current.
This check happens before any SSH connection; a missing local identity is valid
only for `pull-key`. Command help remains available before installation. If the
toolchain is incomplete, repair it with:

```sh
envy upgrade --only chezmoi,mise --force
```

Both transfer commands validate the software identity, inspect the relevant key
paths, display a private-key warning, ask once for confirmation, transfer
through `scp`, install atomically with mode `0600`, and verify the SHA-256 digest
over `ssh`. They are idempotent when the same identity is already present and
refuse a different destination identity unless `--force` is explicit. SSH
aliases and configuration are honored; `--port` and a home-relative
`--remote-path` cover simple exceptions. `--yes` is available for an
already-authorized non-interactive operation. On Unix, a private short-lived
OpenSSH control socket reuses the authenticated connection across inspection,
SCP, verification, and optional restore, avoiding repeated password or MFA
prompts.

The encrypted source remains transport-independent and is never copied by
either key-transfer command. Once it is available at the receiving end, add
`--restore` to run the remote upgrade after a push or the local upgrade after a
pull:

```sh
envy secrets push-key --restore user@host
envy secrets pull-key --restore user@source-host
```

This copies neither the plaintext target nor any other secrets. Use Git, a
private file-sync service, or another appropriate channel for the SOPS
ciphertext. After the identity and ciphertext are present, finish and verify the
local restore with:

```sh
envy upgrade --only secrets --force
envy doctor secrets --verbose
```

Use `binary` for an existing shell fragment because it round-trips `export`,
comments, and other shell syntax byte-for-byte. Use `dotenv` only for a strict
`KEY=value` document that SOPS may parse and normalize.

Identity files, encrypted sources, and the target must be regular files;
symlinks are refused. Identity permissions must exclude group and other access
on Unix. SOPS decrypts through a captured pipe, and installation is atomic with
mode `0600` on Unix. Windows targets inherit their directory ACL; envy does
not currently rewrite Windows ACLs. Contents and private identities are never
printed, backed up, or stored in envy state. Uninstall retains the user-owned
target, identity, and encrypted sources. The encrypted source is protected at
rest; the restored target is a plaintext runtime file protected by filesystem
permissions, not an in-memory-only secret agent.
`envy doctor secrets --verbose` performs a real decryption and comparison
check.

## State and ownership

| Path | Purpose |
| --- | --- |
| `~/.config/envy/state.json` | Schema-v3 selections, origins, bundle hashes, platform hierarchy, and results |
| `~/.config/envy/init-sh.env` | Generated environment sourced by managed shells |
| `~/.local/state/envy/` | Logs, backups, merge bases, agent metadata, and ownership ledgers |
| `~/.local/share/envy/chezmoi-source` | Composed, non-overlapping Chezmoi working tree |

Detached bundles retain their metadata, order, and feature state so reattaching
does not silently lose prior optional selections. An in-progress attach or
detach is also journaled in this file and is visible in `envy status`.

## Development

### Adding A Feature

Add generic, portable features to `envy.json`; keep organization-, team-,
or machine-specific features in a bundle. Feature IDs are unique, lowercase
kebab-case names. Each definition requires a description and a
`core`, `recommended`, or `optional` tier, and may declare platform scope,
dependencies, environment values, and structured lifecycle handlers:

```json
{
  "example-tool": {
    "description": "Configure an existing example tool",
    "tier": "optional",
    "scope": {"darwin": true, "linux": true, "wsl": true},
    "depends_on": ["path"],
    "converge": {"run": "features/example-tool.py", "args": ["converge"]},
    "verify": {"run": "features/example-tool.py", "args": ["verify"]},
    "doctor": {"run": "features/example-tool.py", "args": ["doctor"]},
    "remove": {"run": "features/example-tool.py", "args": ["remove"]}
  }
}
```

Handlers run from their owning package root with `ENVY_FEATURE` and
`ENVY_FEATURE_ROOT` set. Convergence and non-purge removal must be idempotent
because interrupted bundle transactions may retry a handler. Verification must
be read-only, diagnosis should explain actionable drift, and removal must
delete only owned state while honoring `ENVY_DRY_RUN` and `ENVY_PURGE`.

Add non-core defaults to `recommended_features` when the recommended preset
should select them. Document the feature in the table above and add a focused
test.

### Adding A Bundle

A bundle is a trusted directory rooted by `envy.bundle.json`. Version-1
bundles can contribute features, Chezmoi source trees, agent-asset manifests,
settings, environment facts, and non-reserved environment values:

```text
team-bundle/
|-- envy.bundle.json
|-- features/
|-- chezmoi/
`-- assets/
```

```json
{
  "version": 1,
  "id": "team-tools",
  "bundle_version": "1",
  "portable": true,
  "resources": {"chezmoi_sources": ["chezmoi"]},
  "features": {
    "team-tool": {
      "description": "Configure a team-provided tool",
      "tier": "optional",
      "scope": {"global": true},
      "converge": {"run": "features/team-tool.sh", "args": ["converge"]},
      "verify": {"run": "features/team-tool.sh", "args": ["verify"]}
    }
  }
}
```

Relative paths resolve from the bundle root. A portable bundle must keep its
handlers, fact provider, resources, and feature PATH entries inside that root.
Add `recommended_features` only for features the recommended preset should
select. Validate composition before lifecycle testing:

```bash
python3 lib/manifest.py compose \
  envy.json /path/to/team-bundle/envy.bundle.json >/dev/null
./bin/envy --bundle /path/to/team-bundle install \
  --preset recommended --dry-run
```

Use a temporary `HOME` and XDG state directories for full bundle tests. Bundle
paths and hashes become persistent installation state, so move or replace a
configured bundle deliberately.

### Testing

```bash
tests/integration.sh
tests/smart-merge.sh
tests/chezmoi-conflicts.sh
tests/agent-notifications.sh
tests/tmux-persistence.sh
tests/agent-assets.sh
tests/shell-smoke.sh
tests/agent-guidance.sh
tests/bundle-lifecycle.sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

See `dependencies.json` for exact external revisions and licenses.
