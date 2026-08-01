# mins-env

`mins-env` is a portable, stateful developer-environment manager built around
Chezmoi and Mise. It selects features once, converges them repeatedly, verifies
drift, diagnoses focused areas, and conservatively removes only state it owns.

## Requirements

- Bash 3.2 or newer
- Python 3.9 or newer
- Git and standard Unix command-line tools
- Chezmoi and Mise on Linux; macOS can install them through an existing
  Homebrew installation
- zsh and tmux for the recommended terminal preset

Android SDKs, agent CLIs, and terminal applications remain externally supplied.
mins-env does not redistribute them.

## Install

```bash
./bin/mins-env install --preset recommended
```

Interactive installation selects recommended and optional features. For
automation, choose `--preset core|recommended` or use `--yes`.

```bash
mins-env install --reconfigure
mins-env upgrade
mins-env upgrade --only chezmoi,mise
mins-env status
mins-env verify
mins-env doctor tmux-persistence --verbose
mins-env uninstall --yes
```

`core` and `recommended` are selection presets. They are not environment
profiles and do not change feature implementations.

## Explicit bundles

One or more trusted bundles can add features, managed-file source trees,
agent-asset manifests, settings, and environment facts:

```bash
mins-env --bundle /path/to/team-bundle install --preset recommended
```

Bundles are never discovered automatically. Their IDs, versions, paths, hashes,
feature origins, and order are stored in state. Subsequent launcher invocations
reuse those recorded paths. Duplicate feature IDs, resource paths, environment
values, or recommended selections fail before any handler runs.

A portable bundle sets `"portable": true`. Its handlers, fact provider,
resources, and feature PATH entries must remain inside its package root. A
structured `facts` provider may report a host platform hierarchy and ephemeral
runtime environment facts as JSON; the core validates them without shell
evaluation. Explicit user environment overrides retain precedence.
`MINS_ENV_NETWORK_PROXY` is applied only by core providers that fetch public
dependencies; explicit HTTP(S) proxy variables win, and bundle/internal
handlers do not inherit it automatically.

A bundle is a directory containing `mins-env.bundle.json`. Handlers use
structured `run`/`command` plus `args` fields and resolve relative to the owning
manifest; manifest strings are never evaluated as shell source.

Chezmoi sources are assembled in order into an owned working tree. Exact source
path collisions are rejected. Shared top-level shell profiles instead load
lexically ordered bundle fragments from `~/.config/mins-env/shell.d`.

## Features

| Feature | Preset | Purpose |
| --- | --- | --- |
| `smart-merge` | core | Initialize the JSON/text merge engine before managed-file convergence |
| `chezmoi` | core | Managed shell, tmux, Alacritty, Mise, and agent adapters |
| `mise` | core | Install the exact tool set in the active locked Mise configuration |
| `path` | core | Generate the selected command environment |
| `terminal` | recommended | Require zsh/tmux and provide Alacritty on macOS |
| `agent-notifications` | recommended | Route Claude, Codex, MetaCode, and Pi attention events through terminal BEL |
| `tmux-persistence` | recommended | Resurrect/Continuum layouts and agent-session metadata |
| `agent-assets` | recommended | Common QA/workflow assets plus manifest-selected bundle assets with ownership tracking |
| `adb` | optional | Select an existing Android SDK without downloading it |
| `secrets` | optional | Manage SOPS+age recovery, plaintext restoration, and identity transfer over SSH |

### Smart merge and managed files

The core `smart-merge` feature is initialized before Chezmoi and is an explicit
prerequisite for managed-file convergence. `bin/smart-merge` handles identical
and one-sided changes, additive or three-way JSON, textual three-way merges,
and an optional external agent. Agent fallback is disabled by default. Enable
it explicitly with `--agent`, `MINS_SMART_MERGE_AGENT`, or
`MINS_SMART_MERGE_AGENT_COMMAND`.

`bin/smart-merge-git` is the provider-neutral Git mergetool adapter. Install it
explicitly with `bin/smart-merge-git-setup`; its agent policy also defaults to
`none`.

The Chezmoi conflict broker presents Enter-confirmed diff, overwrite, merge,
skip, and quit actions. Accepted local merges retain a managed base and hashes
under `~/.local/state/mins-env/chezmoi-merges` for later three-way updates.
`bin/managed-json` exposes the additive JSON policy for application settings.

### Tmux and agent sessions

The persistence feature installs immutable Resurrect and Continuum revisions,
saves on detach/session-close/manual events, and restores when a new tmux server
starts. Selected Claude, Codex, and Pi adapters attach versioned session IDs,
working-directory hints, and transcript-path hints to panes.

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
`mins-agent-notify --test` inside the target pane to test raw BEL delivery.

### Secrets

The disabled-by-default secrets feature restores a local plaintext runtime file
exclusively from SOPS documents encrypted to age recipients. Plaintext and
legacy string sources are rejected. Enable the feature before generating or
transferring its identity:

```sh
mins-env install --reconfigure
# Select secrets, then let the install converge its dependencies.
```

Feature installation applies the managed Mise configuration and installs the
locked `sops`, `age`, and `age-keygen` tools before running the secrets handler.
The initial secrets convergence can remain incomplete until an identity and an
encrypted source exist; the feature and its transfer commands are nevertheless
initialized once the toolchain is current.

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

On the first machine, generate a software identity and encrypt the current
plaintext target to its public recipient:

```sh
umask 077
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
AGE_RECIPIENT="$(age-keygen -y ~/.config/sops/age/keys.txt)"
sops --encrypt --age "$AGE_RECIPIENT" \
  --input-type binary --output-type binary \
  --output ~/.secrets.sops ~/.secrets
chmod 600 ~/.secrets.sops
unset AGE_RECIPIENT
```

To provision the same software identity on another trusted Unix host, enable
the secrets feature on the machine that will invoke the command, then use the
built-in SSH/SCP transfer instead of manually creating directories or fixing
permissions:

```sh
mins-env secrets push-key user@host
# Or, from a new machine:
mins-env secrets pull-key user@source-host
```

`push-key` and `pull-key` refuse to run unless the local mins-env installation
has the secrets feature enabled and its managed SOPS+age toolchain is current.
This check happens before any SSH connection; a missing local identity is valid
only for `pull-key`. Command help remains available before installation. If the
toolchain is incomplete, repair it with:

```sh
mins-env upgrade --only chezmoi,mise --force
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
mins-env secrets push-key --restore user@host
mins-env secrets pull-key --restore user@source-host
```

This copies neither the plaintext target nor any other secrets. Use Git, a
private file-sync service, or another appropriate channel for the SOPS
ciphertext. After the identity and ciphertext are present, finish and verify the
local restore with:

```sh
mins-env upgrade --only secrets --force
mins-env doctor secrets --verbose
```

Use `binary` for an existing shell fragment because it round-trips `export`,
comments, and other shell syntax byte-for-byte. Use `dotenv` only for a strict
`KEY=value` document that SOPS may parse and normalize.

Identity files, encrypted sources, and the target must be regular files;
symlinks are refused. Identity permissions must exclude group and other access
on Unix. SOPS decrypts through a captured pipe, and installation is atomic with
mode `0600` on Unix. Windows targets inherit their directory ACL; mins-env does
not currently rewrite Windows ACLs. Contents and private identities are never
printed, backed up, or stored in mins-env state. Uninstall retains the user-owned
target, identity, and encrypted sources. The encrypted source is protected at
rest; the restored target is a plaintext runtime file protected by filesystem
permissions, not an in-memory-only secret agent.
`mins-env doctor secrets --verbose` performs a real decryption and comparison
check.

## State and ownership

| Path | Purpose |
| --- | --- |
| `~/.config/mins-env/state.json` | Schema-v3 selections, origins, bundle hashes, platform hierarchy, and results |
| `~/.config/mins-env/init-sh.env` | Generated environment sourced by managed shells |
| `~/.local/state/mins-env/` | Logs, backups, merge bases, agent metadata, and ownership ledgers |
| `~/.local/share/mins-env/chezmoi-source` | Composed, non-overlapping Chezmoi working tree |

Removed bundles are retained as detached feature state so reattaching a bundle
does not silently lose prior optional selections.

## Development

### Adding A Feature

Add generic, portable features to `mins-env.json`; keep organization-, team-,
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

Handlers run from their owning package root with `MINS_FEATURE` and
`MINS_FEATURE_ROOT` set. Convergence must be idempotent, verification must be
read-only, diagnosis should explain actionable drift, and removal must delete
only owned state while honoring `MINS_ENV_DRY_RUN` and `MINS_ENV_PURGE`.

Add non-core defaults to `recommended_features` when the recommended preset
should select them. Document the feature in the table above, add a focused
test, and—for core files—add every new source path to
`exporter/allowlist.txt`.

### Adding A Bundle

A bundle is a trusted directory rooted by `mins-env.bundle.json`. Version-1
bundles can contribute features, Chezmoi source trees, agent-asset manifests,
settings, environment facts, and non-reserved environment values:

```text
team-bundle/
|-- mins-env.bundle.json
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
  mins-env.json /path/to/team-bundle/mins-env.bundle.json >/dev/null
./bin/mins-env --bundle /path/to/team-bundle install \
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
tests/exporter.sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

See `dependencies.json` for exact external revisions and licenses. Release
staging, provenance, content scanning, and licensing gates live under
`exporter/`. The exporter can prepare a deterministic Git commit without
changing its remote; publishing requires an explicit `--push`.
