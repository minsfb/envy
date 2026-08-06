# Remote development over SSH

Status: implemented for v1

Last reviewed: 2026-08-06

## Decision summary

Add an optional `remote-development` feature that runs a pinned open-source
editor on an Envy-managed remote host and opens it locally through the user's
existing OpenSSH connection:

```text
local browser or PWA
        |
        | http://127.0.0.1:<ephemeral-port>
        v
system OpenSSH tunnel
        |
        | SSH stream-local forwarding
        v
private remote Unix socket (mode 0600)
        |
        v
code-server -> remote files, terminals, extensions, and debug processes
```

Use `code-server` as the initial editor engine, behind an Envy-owned adapter.
Do not expose the editor on a remote TCP interface. Do not require VSCodium or
install a proprietary Microsoft extension.

Version 1 decisions:

- browser/PWA client first;
- Linux and WSL development-server hosts only, with macOS supported as a local
  client;
- `remote-development` as the feature and `envy remote editor` as the public
  command; and
- Open VSX as the default registry, overrideable by trusted bundles, with no
  Envy-managed extension preset in version 1.

This implementation is not an irreversible product
choice. Keep the public command, SSH session broker, policy, and engine
lifecycle behind explicit adapter boundaries. A future native client will need
a different remote engine and a local editor connector, but should not require
changing `envy remote editor`, host setup, bundle policy, or the verified
provisioning framework.

## Product definition

The feature gives one developer a local-quality editing experience for files
that stay on a machine they can already access over SSH. Language servers,
terminals, extensions, builds, and debuggers execute on that remote machine.
The local machine renders the UI in a browser or installed PWA.

The intended flow is:

```sh
# One time on the remote host
envy install --reconfigure       # enable remote-development
envy upgrade --only remote-development

# On the local host
envy remote editor devbox /work/project
```

`devbox` is an ordinary OpenSSH destination or config alias. `envy` must honor
the user's OpenSSH configuration, including `Host`, `Match`, `ProxyJump`,
`IdentityFile`, agent, and host-key policies.

### Goals

- Open a remote folder with one command and no public service deployment.
- Keep source code and compute on the remote host.
- Run editor extensions and terminals beside the source tree.
- Work with an existing SSH host and user account; require no root access.
- Use only open-source runtime components and extension registries.
- Pin and verify every artifact Envy installs.
- Preserve Envy's idempotent converge, read-only verify, focused doctor, and
  ownership-safe removal contracts.
- Support remote Linux on `amd64` and `arm64`; treat WSL as Linux.
- Support local launchers on macOS, Linux, and WSL.

### Non-goals for the first release

- Provisioning VMs, containers, Kubernetes workspaces, or cloud credentials.
- A multi-user control plane, shared tenancy, quotas, or an administrator UI.
- A publicly reachable HTTPS endpoint or access from devices without SSH.
- Microsoft Visual Studio Marketplace compatibility.
- Exact compatibility with proprietary VS Code extensions or debuggers.
- Automatically installing or reconfiguring Envy on an arbitrary SSH host.
- Syncing a local source checkout to the remote host.
- Dev Container orchestration. It may be added independently later.
- A native desktop editor client. The browser/PWA is the first supported UI.
- A macOS development-server host. macOS remains a supported local client.

## Options considered

### VSCodium + Open Remote SSH + VSCodium remote extension host

This is the closest open-source match to VS Code Remote SSH. VSCodium publishes
desktop and remote-extension-host builds, and Open Remote SSH connects them
with a familiar command palette, Remote Explorer, remote extension host, and
port forwarding.

It is not the initial recommendation because the current trust boundary does
not meet Envy's requirements:

- Open Remote SSH uses the Node `ssh2` implementation rather than the system
  OpenSSH client. As of version `0.3.0`, its
  [connection construction](https://github.com/jeanp413/open-remote-ssh/blob/95069bfa33babdd95c1e8b09301112eb95fa56f0/src/authResolver.ts)
  does not provide an `ssh2` host verifier, so the normal `known_hosts` trust
  decision is not enforced by that path.
- Its
  [remote setup script](https://github.com/jeanp413/open-remote-ssh/blob/95069bfa33babdd95c1e8b09301112eb95fa56f0/src/scripts/server-setup.sh)
  checks that the downloaded archive is readable and contains the expected
  executable, but does not verify a pinned digest or signature before
  executing it.
- It must continuously track private remote-authority APIs and exactly match
  desktop and remote server commits.

This route can be reconsidered if host-key verification and cryptographic
artifact verification land upstream, or if Envy deliberately owns and
maintains a hardened connector. It should not be enabled as an undocumented
or experimental fallback.

### code-server over system OpenSSH

`code-server` is a maintained MIT-licensed build of Code OSS for the browser.
It adds self-hosting features that matter here: Unix-socket binding, persistent
settings on disk, PWA support, authentication controls, sub-path support, and
an integrated proxy for remote application ports.

It does not reproduce the native Electron UI/remote-extension-host split. The
browser is the UI client and all editor extensions run remotely. This is the
main experience tradeoff, but it leaves SSH authentication, host verification,
proxy jumps, and hardware-backed keys with the mature system OpenSSH client.
It also fits Envy's pinned-release and lifecycle model.

Decision: use this option for the first implementation.

### OpenVSCode Server

OpenVSCode Server is an MIT-licensed, deliberately minimal server adaptation of
upstream Code OSS. It is a credible alternative when staying as close as
possible to upstream is more important than self-hosting ergonomics.

Its narrow scope means Envy would need to own more authentication, lifecycle,
port-proxy, and browser-installation behavior. `code-server` already supplies
those operator-facing pieces while still letting Envy keep the network surface
private. Keep OpenVSCode Server as the comparison engine in the technical
spike, but do not implement two engines in the first release.

### Eclipse Theia

Theia is an EPL-2.0 cloud and desktop IDE framework with VS Code extension API
support. It is appropriate for building a differentiated IDE product, not for
adding a small remote editor feature. Adopting it would make Envy responsible
for composing and maintaining an editor distribution.

Decision: out of scope unless Envy later needs a custom IDE surface.

### DevPod, Coder, and Eclipse Che

These solve the workspace-provisioning or team-control-plane layer rather than
the editor-server layer:

- DevPod is a client-side devcontainer and provider orchestrator.
- Coder provisions and governs team workspaces.
- Eclipse Che is a Kubernetes cloud-development platform.

They may provision a machine that runs this feature in the future, but they
should not become dependencies of it.

### Upstream `code serve-web`

Upstream Code OSS contains much of the browser/server architecture. Official
VS Code builds, the Microsoft marketplace, and several Microsoft extensions
have separate product terms, however, and the upstream command does not by
itself provide the reviewed packaging and operator lifecycle this feature
needs. OpenVSCode Server already represents the minimal-upstream option more
cleanly for this evaluation.

## Proposed user experience

### Commands

```text
envy remote editor [--no-browser] [--local-port PORT] [--transport NAME] HOST [REMOTE_PATH]
envy remote shell [--transport NAME] [--cwd REMOTE_PATH] HOST
envy remote editor --origin [--via ROUTE] [REMOTE_PATH]
envy remote status HOST
envy remote doctor HOST [--verbose]
```

- `editor` does not install or reconfigure the remote Envy installation. If the
  feature is missing, it exits with instructions to enable and converge it.
- `REMOTE_PATH` is interpreted on the remote host. It is transported as data,
  not interpolated into a remote shell command. The default is the remote home
  directory.
- The default local port is selected dynamically and bound to
  `127.0.0.1`. `--local-port` is primarily for browser profiles and automation.
- `--no-browser` prints the local URL and keeps the tunnel in the foreground.
- `status` is read-only and reports the installed engine version, active lease
  count, and pending shutdown deadline.
- `doctor` checks local browser/OpenSSH prerequisites and remote feature,
  platform, engine, disk, and stale-session state without persistent mutation.
  `--verbose` additionally reports the selected local opener, the fixed SSH
  hardening and tunnel policy, and detailed remote engine and provisioning
  checks. The flag is forwarded as a fixed literal rather than incorporated
  from user-supplied remote shell text.

The editor or shell command remains in the foreground. `Ctrl-C`, terminal
close, or loss of the SSH connection releases its remote lease. The last lease
stops the editor after a five-minute reconnect grace period and removes
session-owned files. A hard-killed client may leave stale files, which the next
`editor` command or feature convergence cleans after confirming no owning
process exists. Read-only status and doctor commands report stale records
without removing them.

`remote shell` installs a private, connection-scoped origin callback over the
same Envy-owned SSH master. From that remote shell,
`envy remote editor --origin [REMOTE_PATH]` asks the originating machine to
lazily create the ordinary editor lease and loopback browser tunnel. If more
than one callback is live, `--via ROUTE` selects one explicitly. Ending the
shell removes its reverse stream-local forward, callback socket, and any lazy
editor lease. The callback grants no reverse login and creates no remote TCP
listener. Normal teardown removes every owned route. An abrupt connection loss
may leave an inactive socket entry; a later shell preparation removes only
valid, owned, mode-0600 entries that are at least five minutes old and still
inactive, preserving fresh or unexpected entries. Origin requests allow a
60-second response window, covering the full 30-second cold editor startup and
bounded local-forward retry budgets before reporting failure. The shell exports a generic
origin-transport hint so a return request uses the transport that created it.
Because a persistent tmux server can retain stale environment, origin requests
inside tmux ignore that hint and use the configured default. When the shell
transport differs from that default, use
`envy remote editor --origin --transport NAME`; add `--via ROUTE` when more than
one origin is active.

SSH is the default transport. An explicit `--transport NAME` overrides
`settings.remote_development.preferred_transport` from the composed manifest.
Non-SSH transports are exact, executable, non-symlink adapters at
`$ENVY_BIN_DIR/nv-remote-transport-NAME`; they are never discovered through
`PATH`. A configured adapter that is absent fails closed and tells the user how
to select `--transport ssh`; Envy does not silently fall back.

The first release runs one editor engine per remote Unix user and allows
multiple concurrent leases from that user to the shared process. The engine
stops five minutes after its final lease closes unless another lease arrives.

### Browser behavior

The local opener is selected by platform (`open`, `xdg-open`, or a WSL browser
bridge) and the URL is always printed as a fallback. Envy does not install or
choose a browser. Documentation should recommend installing the site as a PWA
when users want native-window behavior and fewer browser keybinding conflicts.

Configure Open VSX as the default extension registry. A trusted bundle may
replace that registry as described below. Envy does not preinstall or reconcile
extensions in version 1; users manage them through the editor. The feature must
clearly document that some Microsoft extensions and native debuggers are
unavailable or licensed only for official VS Code builds.

The pinned code-server release advertises browser entrypoints for several Code
OSS built-ins without including those files. Provisioning removes those exact
manifest fields only when their reviewed values match, allowing the included
Node entrypoints to load in the remote extension host on the first connection.
The provisioned-tree ownership manifest records every correction. A missing or
changed field fails closed during installation instead of silently rewriting a
new artifact layout.

### Future native editor support

Browser-first does not prevent a native client, but it also does not make one a
small engine swap. A VS Code-style native client speaks the remote extension
host protocol rather than HTTP and needs a local remote-authority connector.
The VSCodium Open Remote SSH connector evaluated above is not acceptable until its SSH and
artifact-verification gaps are addressed.

Version 1 establishes two internal interfaces:

- A local client adapter owns presentation and protocol-specific connection
  setup. Version 1 has `web`, which opens a loopback HTTP URL. A future
  `native` adapter can launch VSCodium or another desktop client and provide a
  verified remote authority.
- A remote engine adapter owns artifact selection, startup arguments,
  readiness, shutdown, and capability reporting. Version 1 has `code-server`.
  A future native adapter would use a matching remote extension host instead.

Common code must own SSH destination handling, system OpenSSH control
connections, host verification, structured remote requests, feature probing,
ownership, bundle policy, and diagnostics. The readiness record should name
its engine and protocol rather than assuming HTTP, for example:

```json
{
  "engine": "code-server",
  "protocol": "http",
  "transport": "streamlocal",
  "socket": "/absolute/private/path/server.sock"
}
```

A future command may add `envy remote editor --client native`; the unqualified
command remains stable. Engine artifacts and user data must be namespaced by
engine so a native pilot neither migrates nor corrupts `code-server` state.

Reusable for native support:

- the public CLI and remote feature selection;
- system OpenSSH connection and forwarding management;
- safe structured requests and remote path handling;
- verified archive-tree provisioning and license inventory;
- engine-neutral session ownership, bundle policy, status, and doctor output.

New work for native support:

- a reviewed desktop editor distribution;
- a local remote-authority connector that preserves system OpenSSH trust;
- matching remote extension-host artifacts and protocol/version negotiation;
- native-specific extension data, UI settings, and compatibility tests.

This boundary avoids product and framework rework, but the native engine and
connector remain a substantial separately reviewed feature.

### Tmux integration

Do not make `remote-development` depend on `terminal` or `tmux-persistence`, and
do not run the editor server inside tmux. Supervising the server with tmux would
make SSH-lease shutdown ambiguous, invite Resurrect to restart stale engine
versions, and turn an optional terminal tool into part of the security
boundary.

Tmux is still useful for durable integrated terminals. The separate optional
`remote-development-tmux` feature depends on both `tmux-persistence` and
`remote-development` and contributes an integrated-terminal profile named
`Envy Remote (tmux)`. A small feature-owned local extension contributes the
profile as a configuration default, so any user setting remains authoritative
and its JSON-with-comments file is never rewritten. The `nv-remote-terminal`
helper attaches to a workspace-scoped tmux session. This gives users persistent
shells and the existing agent-session metadata without coupling the editor
process to tmux.

The workspace session name must use a bounded hash or sanitized identifier,
not a raw path. Opening the profile attaches one terminal to the tmux UI; users
create further panes and windows inside tmux. Envy must not automatically
restore an SSH tunnel, open a browser, or reconnect a remote editor during
tmux-resurrect startup.

Removing the feature deletes only its unchanged Envy-owned local extension; it
never modifies user settings or kills workspace tmux sessions. A modified
extension directory is treated as user-owned and retained.

`envy remote editor` may itself run in a local tmux pane, but local Resurrect must
not relaunch it automatically because reconnection can prompt for credentials
and opens network and browser state. Restoring the pane should leave an
actionable command for a deliberate reconnect.

### Naming

Keep the following naming layers:

- `remote-development`: manifest feature ID;
- `envy remote editor`: stable user-facing command (`remote open` remains a
  hidden compatibility alias);
- `nv-remote-serve`, `nv-remote-terminal`, or similarly narrow `nv-*` names:
  installed implementation helpers, when helpers are needed.

`envy-remote-development` would introduce a second public executable namespace,
and `nv-remote-development` is too broad for an internal helper. Existing Envy
uses `envy` for product commands and `nv-*` for focused managed helpers; tmux
integration does not change that distinction.

## Architecture

### Feature lifecycle

`remote-development` is optional and in scope on Linux and WSL. It has no
dependency on a desktop editor or tmux. The `envy remote` client command remains
available on macOS, Linux, and WSL regardless of whether the local machine can
host the server feature.

Converge:

1. Select the exact reviewed `code-server` release for the host OS and
   architecture.
2. Download the upstream standalone archive directly, enforce the download
   size limit, verify its recorded SHA-256 digest, and safely extract it into a
   versioned Envy-owned directory.
3. Atomically update the internal selected-version link. Do not place a
   conflicting `code-server` command on the user's `PATH`.
4. Configure the selected extension registry without installing extensions.
5. Create only the private runtime/state directories Envy owns.

Verify, without mutation:

- Confirm the selected archive, version, executable, ownership record, and
  directory permissions.
- Confirm the host satisfies the engine's libc/runtime requirements.
- Report drift when the pin, selected link, permissions, or recorded files do
  not match.

Immediately before starting or restarting an engine, repeat the full pinned
version and provisioned-tree verification. A running engine that was already
verified may be reused without rehashing its tree for every new lease. If a
pre-start verification fails, fail closed with focused upgrade guidance and do
not stop or replace any live engine.

Doctor:

- Include the verify checks plus an executable version probe, private socket
  capability probe, stale-session diagnosis, available disk space, and an
  optional loopback-only smoke start.
- Check registry reachability only in verbose network diagnosis; ordinary
  verification remains deterministic and works offline.
- Explain the `glibc`/`glibcxx` requirement on unsupported Linux hosts rather
  than falling back to an unpinned npm build.

Remove:

- Stop only sessions recorded as owned by this feature.
- Remove owned launch links and session state.
- Retain editor settings, extensions, and workspace data as user data.
- On `--purge`, remove verified Envy-owned engine versions. Never remove an
  externally installed `code-server` or user workspace.

The existing provisioner extracts one file from an archive. Implementation
therefore needs a reviewed safe-directory extraction mode with path traversal,
symlink, hardlink, device, ownership, and size checks before this feature can
install a release tree.

### Paths

Use the existing path helpers rather than spelling home-directory defaults in
handlers. The conceptual layout is:

```text
$XDG_DATA_HOME/envy/tools/remote-development/
  code-server/<version>/                           # Envy-owned immutable tree
  code-server/current                              # Envy-owned selected link
$XDG_STATE_HOME/envy/remote-development/           # ownership and stale state
$XDG_RUNTIME_DIR/envy/remote-development/
  code-server/server.sock                          # private engine socket
  leases/<id>.json                                 # private connection leases
$XDG_DATA_HOME/envy/remote-development/user-data/
  code-server/                                     # retained engine-specific data
```

When `XDG_RUNTIME_DIR` is unavailable, create the live session under the Envy
state directory with a mode-0700 parent. The editor socket must be mode 0600.

### Session protocol

Use a small Python helper on each side and subprocess argument arrays. Do not
construct commands by concatenating user input.

1. The local helper creates a private OpenSSH control connection so the user
   authenticates once and all probes and forwarding share the same verified
   connection.
2. It opens a fixed internal remote `envy remote _lease <session-id>` channel and sends a
   bounded JSON request containing the remote path on standard input. Session
   IDs are locally generated hexadecimal values and are validated again
   remotely.
3. The remote lease helper resolves the path without shell evaluation, creates
   a private lease record, and uses a file lock to inspect or start the selected
   engine. The engine is detached from any one SSH channel but remains owned by
   its PID, version, socket, and lease records. Additional leases reuse the
   same recorded process and socket.
   The engine record also fingerprints the provisioned tree and installed
   extension manifests. A changed inventory clears only code-server's extension
   discovery caches and restarts an idle stale engine. Active leases are never
   interrupted; their engine is replaced after the normal final-lease shutdown.
4. The engine listens on one private Unix socket with HTTP authentication
   disabled, telemetry and update checks disabled, Open VSX configured, and
   engine-specific Envy data directories.
5. After the socket accepts connections, the lease helper emits one bounded
   readiness record containing the canonical remote path, absolute socket
   path, engine, protocol, and platform data. It then holds the channel open.
6. The local helper asks the same system OpenSSH control connection to forward
   only `127.0.0.1:<local-port>` to that remote Unix socket and opens the
   URL-encoded canonical folder in the browser.
7. Closing the local channel removes its lease. The last lease starts a
   five-minute grace period, after which an unchanged lease set causes Envy to
   terminate the recorded engine PID and remove the socket. A later connection
   during the grace period cancels the shutdown and reuses the engine.

HTTP authentication may be disabled only when all of these invariants hold:

- the engine is listening on a Unix socket rather than remote TCP;
- the socket is inside a mode-0700 directory and has mode 0600;
- the only local listener is explicitly bound to `127.0.0.1` or `::1`; and
- access traverses the already authenticated OpenSSH connection.

If any invariant cannot be established, startup fails closed. There is no
first-release option to bind a public interface or disable SSH host checks.

### Engine concurrency and data isolation

There are three plausible process models:

| Model | Benefits | Costs and risks |
| --- | --- | --- |
| One shared engine per remote user | Lowest memory; one extension host and extension store; settings have a single writer; fast second connection | Needs lease counting and multi-client testing; one crash affects every workspace; ports and logs are shared |
| One engine per workspace | Better crash and workspace-state isolation; stable reconnection target | More memory; duplicated state; extension installation coordination; workspace rename/move changes identity |
| One engine per connection | Simplest SSH-bound shutdown; strongest failure isolation | Highest memory and startup cost; concurrent writers cannot safely share user-data directories; fully isolated directories fragment settings and duplicate extensions |

Separate engine processes under the same Unix account are not a security
boundary: each process and extension can still access everything that account
can access. Real hostile-workspace isolation requires separate OS users,
containers, or provisioned workspaces and is outside this feature.

Version 1 uses one shared `code-server` process per remote Unix user with one
private socket and a lease for each `envy remote editor`. This gives settings and
extensions one writer and makes concurrent browser windows inexpensive. The
spike must verify this supported configuration; a failure blocks the chosen
engine rather than silently changing the process model.

The default idle grace is 300 seconds. That is long enough for routine
Wi-Fi/VPN interruption, a quick laptop wake, or a launcher restart, while
bounding the memory cost after intentional disconnect. A trusted bundle may
set `settings.remote_development.idle_grace_seconds` to an integer from 0 to
3600. Zero means stop immediately. The status command reports the lease count
and any pending shutdown deadline.

Do not default to per-connection isolated engines.

If later demand justifies workspace isolation, add it as an explicit policy or
option. Each isolated engine needs its own user-data and extension directories;
sharing writable extension directories across processes is not safe enough to
be the default. User-installed extensions and settings belong to that isolated
namespace and would be duplicated. The disk and consistency cost must be
visible to the user.

### SSH behavior

Always execute the user's system `ssh` binary. This preserves mature support
for `known_hosts`, certificates, FIDO/security keys, agents, `ProxyJump`, and
organization-specific SSH configuration. Envy must not:

- set `StrictHostKeyChecking=no` or write `known_hosts` entries;
- copy private keys or credentials to the remote host;
- put tokens, passwords, or key material in arguments, URLs, logs, or state;
- enable agent forwarding, X11 forwarding, remote TCP forwarding, or reverse
  login access for this feature; or
- rewrite the user's SSH configuration.

Only the loopback-to-stream-local editor forward and, for `remote shell`, one
connection-scoped reverse stream-local callback are added. The callback joins
two private mode-0600 Unix sockets beneath mode-0700 directories; it does not
listen on TCP and carries only bounded, versioned editor requests and results.
Every SSH invocation that can create a connection or remote session explicitly
sets `ForwardAgent=no`, `ForwardX11=no`, `ForwardX11Trusted=no`, and
`ClearAllForwardings=yes`; this process-local policy does not edit the user's
SSH configuration. Control operations then add only those required forwards.
Host-key changes and forwarding restrictions remain visible as ordinary SSH
failures with focused guidance.

## Supply chain and licensing

- Record `code-server` in `dependencies.json` with its exact version, MIT
  license, upstream URL, distribution description, per-platform artifact URLs,
  archive members, and SHA-256 digests.
- Add it to `THIRD_PARTY.md` and independently verify the license at the pinned
  release before shipping.
- Download from the reviewed upstream release, not an installer script,
  package-manager repository, mutable tag, or npm fallback.
- Fail closed when an artifact or digest is absent for the current platform.
- Disable the engine's self-update path; upgrades occur only through a new Envy
  pin and normal `envy upgrade` convergence.
- Never configure a non-Microsoft editor to use the Visual Studio Marketplace.

### Extension registry policy

Base Envy configures the public Open VSX service and does not install any
extensions in version 1. Code OSS built-ins remain available except for the
bundled Copilot Chat, GitHub, GitHub Authentication, and Microsoft
Authentication service integrations, which provisioning deterministically
excludes from Envy's verified editor tree. Users install and update additional
extensions interactively. All installed extensions are user data that Envy
neither claims nor removes.

A trusted bundle can replace the registry. Keep the Open VSX default in handler
policy rather than as conflicting scalar values in `envy.json`; the current
manifest composer can then accept one explicit bundle override. The intended
settings shape is:

```json
{
  "settings": {
    "remote_development": {
      "extension_registry": {
        "service_url": "https://open-vsx.org/vscode/gallery",
        "item_url": "https://open-vsx.org/vscode/item",
        "resource_url_template": "https://open-vsx.org/vscode/unpkg/{publisher}/{name}/{version}/{path}"
      },
      "idle_grace_seconds": 300
    }
  }
}
```

When no setting is present, the handler defaults to Open VSX. Conflicting
registry scalar choices continue to fail manifest composition instead of
depending on bundle order. There is no command-line flag for an arbitrary
registry URL: the public Open VSX default or a registry explicitly selected by
a trusted bundle is the effective allowlist.

Registry settings contain endpoints only. Credentials, tokens, private
identities, and signed URLs must not enter a manifest, process arguments, logs,
or state. Registry authentication, when needed, is an external user or
organization concern. Managed extension presets can be designed later as a
separate ownership and supply-chain feature.

## Implementation stages

### 1. Technical spike

Before changing the manifest, prove the following with the currently reviewed
`code-server` and OpenVSCode Server releases:

- start on a private Unix socket and forward it with system OpenSSH;
- open a selected remote folder with spaces and shell metacharacters safely;
- terminal, language server, debugger, Git, WebSocket, clipboard, and browser
  refresh behavior;
- remote application-port discovery/proxy behavior;
- lease release and clean shutdown on `Ctrl-C`, terminal close, and dropped
  SSH; closing only a browser tab must not corrupt the reusable engine;
- remote behavior on Linux `amd64` and Linux `arm64`, including WSL where
  practical;
- local launch behavior on macOS, Linux, and WSL;
- browser/PWA keybinding limitations;
- two simultaneous clients against one engine, plus measured memory and disk
  costs for shared, per-workspace, and per-connection directory models;
- the engine-neutral readiness record and client/engine adapter boundary; and
- Open VSX configuration plus one trusted-bundle registry override.

OpenVSCode Server remains in this spike only as a comparison. Choose it instead
if it reaches the same operational and security criteria with materially less
patching or dependency surface.

### 2. Verified archive-tree provisioning

Extend `lib/provision.py` and focused tests to install a complete release tree
atomically and reject unsafe archive entries. Preserve current single-binary
behavior for existing dependencies.

### 3. Remote feature lifecycle

Add the manifest entry, dependency inventory, lifecycle handler, ownership
state, and tests for converge, verify, doctor, remove, purge, drift, dry-run,
unsupported platforms, registry policy, shared-engine leases, and grace-period
shutdown.

### 4. Local SSH launcher

Add `envy remote` routing and a Python session helper with bounded JSON
messages, system OpenSSH control/forward lifecycle, local port selection,
browser opening, signal handling, and focused errors.

### 5. End-to-end hardening

Test against an isolated SSH server with a temporary home and known-hosts file.
Cover host-key mismatch, disabled forwarding, malicious paths, interrupted
downloads, corrupt archives, stale PIDs, concurrent starts, and disconnects.
Then run the repository's full documented test suite.

## Acceptance criteria

- A pre-provisioned supported host opens a requested folder through
  `envy remote editor HOST PATH` without a remote TCP listener.
- A macOS local client can open a supported Linux host; attempting to enable
  the server feature on macOS reports that the host platform is out of scope.
- A changed or unknown host key is handled by system OpenSSH policy; Envy does
  not bypass or silently accept it.
- `ss -ltn`/`lsof` shows no editor-owned remote TCP listener during a session.
- No credential or connection token appears in process arguments, output,
  logs, URLs, or Envy state.
- Paths containing whitespace, quotes, dollar signs, and command substitutions
  open as data and cannot execute shell syntax.
- The local listener is loopback-only and disappears on disconnect.
- The remote editor process exits and its socket/session state is removed after
  the last SSH lease and documented reconnect grace period expire.
- Extension execution, integrated terminals, Git operations, and application
  port access happen on the remote host.
- `verify` is mutation-free, `doctor` gives actionable failures, and dry-run
  downloads or writes nothing.
- Removal deletes only recorded Envy-owned runtime/tool resources and retains
  user settings, extensions, source trees, and external installations.
- The feature works without network access after its pinned engine and desired
  extensions have been installed.
- A default installation uses Open VSX and installs no extensions; a trusted
  bundle can replace the registry without editing core files.
- Browser-specific code does not leak into the engine-neutral SSH broker or
  ownership model; a test adapter can exercise a non-HTTP readiness record.

## Spike exit gates

1. One `code-server` process safely serves multiple simultaneous leases and
   distinct workspace URLs for the same remote Unix user.
2. The 300-second last-lease grace period is race-free: a new lease cancels
   shutdown, and an unchanged empty lease set stops only the recorded engine.
3. Open VSX and one trusted-bundle registry override work without embedding
   credentials or modifying interactively installed extensions.
4. Browser-specific behavior remains behind the client adapter and a non-HTTP
   test adapter can exercise the engine-neutral readiness contract.

Failure of the shared multi-client gate blocks `code-server` as the selected
engine for version 1; it does not authorize silently switching to isolated
per-connection processes.

## References

- [VS Code Remote SSH architecture](https://code.visualstudio.com/docs/remote/ssh)
- [VSCodium](https://github.com/VSCodium/vscodium)
- [VSCodium extension and marketplace notes](https://github.com/VSCodium/vscodium/blob/master/docs/extensions.md)
- [Open Remote SSH](https://github.com/jeanp413/open-remote-ssh)
- [OpenVSCode Server](https://github.com/gitpod-io/openvscode-server)
- [code-server](https://github.com/coder/code-server)
- [code-server installation](https://github.com/coder/code-server/blob/main/docs/install.md)
- [code-server secure exposure guide](https://github.com/coder/code-server/blob/main/docs/guide.md)
- [Eclipse Theia](https://github.com/eclipse-theia/theia)
- [DevPod](https://github.com/loft-sh/devpod)
- [Coder](https://github.com/coder/coder)
- [Eclipse Che](https://github.com/eclipse-che/che)
