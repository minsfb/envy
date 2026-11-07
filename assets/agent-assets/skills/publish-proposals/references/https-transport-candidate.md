# Fixed HTTPS transport — qualified Linux subset

The fixed HTTPS replacement has a source-qualified Linux profile and owned
TLS/CONNECT tests. The normal CLI is wired through that source admission gate;
there is no enable switch, arbitrary client, URL, or query selector. Capability
is not operation authority: this implementation does not grant permission to
read a target or publish anything. The publish-proposals skill remains offline.

## Supported client and runtime

Admission supports CPython 3.12.14 on Linux, OpenSSL 1.1.1, and the exact inspected
http.client, ssl, and socket source fingerprints compiled into github_issues.py.
The tested installations use OpenSSL 1.1.1zh with vendor modifications. Runtime
and native SSL-extension identities are also bound into each plan; this is not
an attestation of every possible vendor build or a hostile-writer guarantee.

The credential client is gh 2.86.0, built with Go 1.25.5, go-gh 2.13.0,
safeexec 1.0.1, go-keyring 0.2.6 and Cobra 1.10.2. Direct 64-bit little-endian
Linux ELF clients are checked using their embedded, inline Go build metadata.
Version probing does not execute gh: even gh version can migrate old config.

The public CLI selects the fixed gh name on PATH and admits native binaries
only. Installed package launchers require a separately authored private adapter;
package formats, cache layouts and package-specific identities do not belong in
this public implementation. There is no automatic adapter discovery or fallback.

The internal trusted-code boundary is a transport subclass's
_select_credential_client method, returning a native path, fixed version and at
most eight nonsecret selection-artifact paths. A _CredentialCommand additionally
names a source-qualified credential launcher, not arbitrary arguments or an
environment map. The core independently validates the native gh, fingerprints
the launcher/artifacts/selector source, and binds the current working directory.
Non-executable static configuration artifacts are permitted; executable clients
and launchers must still pass executable metadata checks. A static
provider subclass supplies that transport to the existing authority-checking
publication functions. Only authored code chooses the subclass: CLI, plan and
environment values never choose or import an adapter. The public CLI retains its
fixed provider. A private entrypoint must preserve the same preflight and binding
contract. This is trusted code composition, not a sandbox for arbitrary plugins.
The wrapper's complete effective config, environment, setup effects and sandbox
must be admitted read-only before this hook returns. A worker outside a wrapper
must not escape its effective route/trust/sandbox restrictions. This interface
does not establish that any installed wrapper currently meets that contract.

The wrapped-client metadata profile also recognizes gh 2.100.0, Go 1.26.8,
go-gh 2.16.0 and go-keyring 0.2.8 (same safeexec/Cobra versions). It requires a
separately qualified static launcher; the public PATH resolver does not enable
direct 2.100 use. This is token-command qualification, not package admission.

macOS is deliberately unsupported: Go's Apple SecTrust verification and the
installed Python/OpenSSL verifier are different trust mechanisms. Windows,
other Python source versions, old gh versions, and GitHub tenancy/development
host mappings are not admitted by this profile. They require a separate source
qualification, not a runtime override.

## Credential and configuration boundary

There is one fixed credential command per operation:
gh auth token --hostname HOST, executed through the qualified launcher (the
native binary for the public direct profile). The core never bypasses a wrapper
by executing its final payload instead. The lookup returns at most 4 KiB,
exactly one validated token. That token
travels to the HTTPS worker only through owned pipes, never arguments, environment
additions, diagnostics, receipts, or transport-owned files. Memory zeroization
and protection from a process-memory observer are not promised.

The inherited POSIX config precedence is GH_CONFIG_DIR, XDG_CONFIG_HOME/gh,
then HOME/.config/gh. Before launching gh, the guard privately reads at most
64 KiB of general config and only stats hosts.yml (regular, at most 1 MiB).
It accepts a conservative one-line scalar/mapping YAML subset with schema
version 1, or gh's empty-config fallback when both files are empty/missing.
Duplicate keys, document markers, multiline/tagged/aliased/flow constructs,
unsupported nesting, old versions and unsafe file metadata stop without repair.
Quoted scalars, comments, ordinary settings and a one-level aliases map work.

This guard matters: gh's old-config migration can query the current user, write
config/hosts files, and migrate keyring entries before command dispatch. The
supported schema-1 form avoids that migration. A configured global
http_unix_socket is refused instead of silently changing its routing. Config
must remain coordinated and stable through the child lookup; this is not an
atomic defense against a hostile same-user config writer.

Tagged source establishes token precedence: public GitHub GH_TOKEN then
GITHUB_TOKEN; ordinary GHES GH_ENTERPRISE_TOKEN then GITHUB_ENTERPRISE_TOKEN;
then the configured host token; then active-account/legacy keyring lookup.
The command uses TokenFromEnvOrConfig, not go-gh's separate GH_PATH command
fallback. Fixed builtins cannot be replaced by aliases or extensions in gh
2.86.0. Owned stdout/stderr pipes disable gh update checks independently of
GH_FORCE_TTY, without changing credential or security environment variables.

For a wrapped lookup the sole compiled child-environment addition is
GH_TELEMETRY=disabled. The selector must establish that this reaches gh unchanged;
the parent and HTTPS worker environment stay unchanged. In 2.100 this value
selects NoOpService before device-ID persistence or detached telemetry flushers.
Agent detection only inspects environment values. Its fixed token command uses
ActiveToken for the explicit host, not an API client: per-host api_host is not
consulted on that path. The HTTPS worker retains the independently confirmed,
compiled endpoint and does not parse hosts.yml or promise gh API-client routing.
Schema-1 config is still required to avoid startup migration, and owned pipes
still prevent update checks. The internal 2.100 keyring timeout is 60 seconds;
the outer shared 30-second deadline remains stricter.

Linux keyring fallback uses the inherited Secret Service session bus and may
request collection/item unlock, including an OS keyring prompt. No keyring or
credential call was executed in qualification. The gh process is bounded, but
system-daemon ownership, prompt cancellation, and descendant closure are not
promised. There is no application-level token refresh, login, scope repair or
credential-source substitution.

## HTTPS, routing and bounds

Only five compiled operations exist: named repository/viewer context, pinned
repository/viewer context, bounded Issue pages, pinned Issue-number read, and
createIssue(repositoryId, title, body). github.com maps to
https://api.github.com/graphql; ordinary GHES DNS names map to
https://HOST/api/graphql. Scheme, port, userinfo, paths and caller GraphQL are
not inputs. Subdomains of github.com, ghe.com tenancy and github.localhost
mappings are refused rather than guessed.

HTTPS_PROXY is inherited for HTTPS destinations; HTTP_PROXY is not a fallback.
The supported proxy is unauthenticated HTTP CONNECT. Upper/lower conflicts,
ALL_PROXY, TLS-to-proxy/authenticated URLs, unsupported NO_PROXY syntax, gh
debug logging and TLS key logging fail before credential lookup. DNS/suffix
NO_PROXY entries, optional literal ports, leading-dot/star-dot forms and * work.
Valid IPv4/IPv6/CIDR exclusions are accepted but do not match DNS endpoint
addresses, just as Go tests literal exclusions without resolving hostnames.
Port strings are compared literally, matching Go's behavior.

An HTTP proxy receives only CONNECT to the fixed API host on port 443.
Authorization follows verified TLS inside that tunnel; proxy failures never
cause direct fallback. ssl.create_default_context retains inherited default
CA paths and SSL_CERT_FILE/SSL_CERT_DIR. Explicit paths must be absolute and
exist; they are not silently cleared or replaced. The observed Linux default
OpenSSL CA file and Go's first existing CA bundle resolve to the same managed
PEM bundle, and their relevant CA directory is shared. This establishes the
supported managed roots, not universal Go/OpenSSL verification equivalence or
identical loading of every possible directory layout. Managed files are unchanged.

The isolated -I -S -B worker uses the fingerprinted interpreter, no site hooks or
bytecode writes. It qualifies only the runtime, not credential/package selection:
its credential already arrived over the owned pipe. It performs exactly one
HTTP/1.1 request and one response read.
All 3xx, non-200 responses, compression, ambiguous framing, oversized bodies
and truncated Content-Length responses fail. No redirect, refresh, connection
pool, HTTP retry or possible-create replay exists. TCP address attempts before
HTTP are bounded by the same deadline and are not mutation replays.

One 30-second deadline covers credential lookup and HTTPS work, including DNS,
CONNECT, TLS, transmission and response reads. Existing invocation/call/output
budgets and TERM/KILL/reap grace periods remain. Failures return fixed codes,
not provider bodies, credentials or raw exceptions. A failed possible submission
remains uncertain in the unchanged publication state machine.

## Binding and authority integration

PublicationPlan/Receipt remain version 2. The HTTPS provider is version 2,
fixed-https-graphql-v1; the legacy unavailable GraphQL provider stays version 1.
Nothing relabels or reopens old stores. The new https binding records interpreter,
runtime source/native-extension fingerprints, TLS version, credential selection
and complete fixed policy. Selection version fixed-code-v2 and credential policy
fixed-gh-auth-token-v2 additionally bind launcher, startup control and cwd;
old HTTPS stores cannot silently adopt these changed source/policy identities.
It does not store raw security environment values or
claim to freeze trust-store contents and operator configuration.

github-plan checks proposals/body budgets, preparation reference, exact host
confirmation and new private store location before adapter construction.
github-apply and github-reconcile validate source/profile/receipt and exact
digest/authority under lock, then the unchanged core reacquires and revalidates
the lock before constructing its lazy single-instance adapter. Competing intent
between locks stops. Actual client/runtime/source/selection/policy are compared
to the saved binding before the first credential lookup. Completed batches need
no backend construction.

CLI forms are github-plan --input ... --store ... --prepare-authority ...
--confirm-host ..., github-apply --store ... --plan-digest ...
--filing-authority ..., and github-reconcile --store ... --plan-digest ...
--reconciliation-authority .... Unsupported source profiles refuse before
input/store access. provider_available:true means admitted capability, not
create permission. Offline status remains non-probing.

## Evidence and remaining scope

Owned tests run the actual worker against ephemeral loopback TLS and CONNECT
listeners, generated test-only keys, and synthetic credential clients. The proxy
tunnels only to its owned TLS listener, never the requested remote hostname.
Coverage includes every redirect class, certificate/hostname failures, proxy
refusal, no replay/fallback, bounds, cleanup, config/source refusal,
authority-before-construction, changed durable intent, and the normal CLI's
lost-create/reconcile/resume sequence. Synthetic selectors exercise source and
artifact binding without private package examples. macOS transport fixtures
bypass only source admission, not request/TLS behavior. Exact tested revisions,
counts and commands belong in the associated QA evidence, not capability claims.
An owned synthetic wrapper also verifies fixed arguments, inherited cwd/config/
proxy/trust, child-only telemetry control, one lookup and pre-lookup drift refusal.
It does not certify installed wrapper, package-manager or sandbox behavior.

Tagged public source read: gh 2.86.0 cmd/gh/main.go; internal/ghcmd/cmd.go;
internal/config/config.go and migration/multi_account.go; internal/keyring/
keyring.go; internal/update/update.go; pkg/cmd/{auth/auth,auth/token/token,
factory/default,root/root,alias/shared/validations,extension/manager,
extension/extension,version/version,copilot/copilot}.go; go-gh 2.13.0
pkg/{auth/auth,config/config,api/http_client,api/client_options}.go and
internal/yamlmap/yaml_map.go; go-keyring 0.2.6 keyring_unix.go and
secret_service/secret_service.go; Cobra 1.10.2 command.go; Go 1.25.5
debug/buildinfo/buildinfo.go, crypto/x509/root_{linux,unix,darwin}.go and
vendor/golang.org/x/net/http/httpproxy/proxy.go. Exact source/tool receipts are
retained in the private qualification notes. Private package qualification is
separate and is not public source evidence for this implementation.

Additional token-path source: gh 2.100.0 cmd/gh/main.go, internal/{ghcmd/cmd,
config/config,config/migration/multi_account,keyring/keyring,update/update,
agents/detect,telemetry/telemetry}.go and pkg/cmd/{auth/auth,auth/token/token,
factory/default,root/root,alias/shared/validations,extension/manager,
extension/extension}.go; go-gh 2.16.0 pkg/{auth/auth,config/config,
api/client_options,api/http_client,api/host}.go; go-keyring 0.2.8 keyring_unix.go.
API mapping source explains the token/API separation, not a new endpoint input.

No actual GitHub request, real token lookup, config repair, deployment or
publication was performed. Owned fixtures establish transport/protocol behavior,
not a supported-version matrix for all GitHub/GHES servers or proof of create
permission. Strict fixed-schema validation remains fail-closed, and uncertain
creates retain durable intent. Whole-feature release still needs proportional
repository QA and independent review. Actual target/schema reads or filing
require their own operation authority. Native macOS trust support is follow-up
work, not a requirement to silently alter managed trust or block this Linux
subset.
