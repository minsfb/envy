"""Saved gh credentials for an explicitly unlocked sync session.

Construction does not read credentials. The transaction must call discover only
after manual age authorization and hold its gh-config lock through completion.
Only commit performs a native mutation; preflight validates without installing.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile

from .errors import Cancelled, CommittedError, SyncError
from .files import SecureFile, Snapshot
from .github_config import Executable, private_directory
from .github_metadata import MAX_CONFIG, Metadata
from .github_process import run
from .inventory import Ref, validate_value

ORPHAN_WARNING = "No registered login; adding may replace an inaccessible orphaned credential for this account."
NATIVE_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


@dataclass(frozen=True, repr=False)
class AuthSnapshot:
    general: Snapshot
    hosts: Snapshot
    metadata: Metadata


@dataclass(frozen=True, repr=False)
class Saved:
    state: str
    token: str | None = None
    reason: str = ""


def environment(root, directory, *, native=True, ca_file=None):
    root = Path(root)
    home = pwd.getpwuid(os.getuid()).pw_dir if native else str(root)
    env = {"HOME": home, "GH_CONFIG_DIR": str(directory),
           "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
           "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
           "TMPDIR": str(root), "PATH": "/usr/bin:/bin", "LANG": "C",
           "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
           "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1", "GH_PAGER": "cat",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    if ca_file is not None:
        env["SSL_CERT_FILE"] = str(ca_file)
    if native and sys.platform == "linux":
        # Preserve the actual credential-store transport. A private HOME/XDG
        # tree must not silently turn a user's native keyring into file fallback.
        for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def linux_storage_gate(env, root):
    """Bound support to the verified Linux native-fallback configuration.

    No login/default collection exists on either canary. Do not mask the bus,
    unlock/create collections, enumerate items, or assume a bus failure is empty.
    An existing keyring requires its own native integration evidence.
    """
    executable = Executable.resolve("/usr/bin/busctl")
    def query(verb, *args):
        executable.recheck()
        result = run([str(executable.path), "--user", "--timeout=5", "--json=short", verb,
                      "org.freedesktop.secrets", "/org/freedesktop/secrets",
                      "org.freedesktop.Secret.Service", *args], b"", environment=env, cwd=root, limit=8192)
        if result.status:
            raise SyncError("Linux credential-store availability could not be verified")
        try:
            data = json.loads(result.output)
        except (ValueError, UnicodeError):
            raise SyncError("invalid Linux credential-store metadata") from None
        if not isinstance(data, dict):
            raise SyncError("invalid Linux credential-store metadata")
        return data
    result = query("get-property", "Collections")
    if result.get("type") != "ao" or not isinstance(result.get("data"), list):
        raise SyncError("invalid Linux collection metadata")
    if "/org/freedesktop/secrets/collection/login" in result["data"]:
        raise SyncError("Linux keyring-backed sync requires separate native verification")
    result = query("call", "ReadAlias", "s", "default")
    if result != {"type": "o", "data": ["/"]}:
        raise SyncError("Linux keyring-backed sync requires separate native verification")


def snapshot(directory):
    private_directory(directory)
    if not directory.exists():
        empty = Snapshot(None, b"")
        return AuthSnapshot(empty, empty, Metadata.parse(b"", b""))
    snapshots = []
    for name in ("config.yml", "hosts.yml"):
        handle = SecureFile(directory / name, MAX_CONFIG)
        try:
            current = handle.snapshot()
            if current.signature is not None and stat.S_IMODE(current.signature[2]) != 0o600:
                raise SyncError("gh configuration files must have permissions 0600")
            snapshots.append(current)
        finally:
            handle.close()
    return AuthSnapshot(*snapshots, Metadata.parse(*(s.content for s in snapshots)))


class Backend:
    def __init__(self, config):
        self.config = config
        self.before = None
        self.saved = {}
        self.prepared = {}
        self.mutation_started = False
        self.mutation_ref = None

    def _same_files(self):
        current = snapshot(self.config.directory)
        if current != self.before:
            raise SyncError("gh authentication state changed; restart selection")

    def _call(self, args, payload=b"", *, network=False, worker=False, version_probe=False, limit=8192):
        self.config.executable.recheck()
        if self.config.ca_file is not None:
            if Executable._signature(self.config.ca_file.stat()) != self.config.ca_signature:
                raise SyncError("GitHub CA configuration changed during sync")
        prefix = self.config.network_prefix if network else ()
        if prefix:
            self.config.network_executable.recheck()
        if worker:
            command = [str(Path(sys.executable).resolve()), "-I", "-B",
                       str(Path(__file__).with_name("github_validate.py"))]
        else:
            command = [str(self.config.executable.path), *args]
        with tempfile.TemporaryDirectory(prefix="envy-gh-") as temporary:
            root = Path(temporary).resolve()
            directory = root / "gh" if worker or version_probe else self.config.directory
            if version_probe:
                # A candidate version may have different startup migrations.
                # Probe only disposable schema-1 config, with no registered hosts.
                directory.mkdir(mode=0o700)
                path = directory / "config.yml"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write("version: 1\n")
            env = environment(root, directory, native=not worker, ca_file=self.config.ca_file)
            if sys.platform == "linux" and not worker and not version_probe:
                linux_storage_gate(env, root)
            return run([*prefix, *command], payload, environment=env, cwd=root, limit=limit)

    def _native_gate(self):
        # Refuse shells/shims before executing --version: a wrapper could install
        # tools or source credentials. Other versions/platforms need native proof.
        if sys.platform not in {"darwin", "linux"}:
            raise SyncError("native GitHub sync on this platform is not yet verified")
        self.config.executable.recheck()
        with self.config.executable.path.open("rb") as binary:
            magic = binary.read(4)
        allowed = NATIVE_MAGIC if sys.platform == "darwin" else {b"\x7fELF"}
        if magic not in allowed:
            raise SyncError("GitHub sync requires the verified native executable, not a shim or wrapper")
        result = self._call(["--version"], version_probe=True, limit=4096)
        version = "2.83.2" if sys.platform == "darwin" else "2.100.0"
        if result.status or not result.output.startswith(f"gh version {version} ".encode()):
            raise SyncError(f"GitHub sync requires verified native gh {version}")
        self._same_files()

    def _lookup(self, ref, metadata):
        registered = ref.account in metadata.users(ref.host)
        try:
            result = self._call(["auth", "token", "--hostname", ref.host, "--user", ref.account])
        except Cancelled:
            raise
        except SyncError:
            return Saved("blocked", reason="Saved credential lookup unavailable")
        if result.status not in (0, 1):
            return Saved("blocked", reason="Saved credential lookup failed")
        if result.status or not result.output:
            return (Saved("blocked", reason="Registered credential unavailable") if registered
                    else Saved("absent", reason=ORPHAN_WARNING))
        try:
            token = result.output.removesuffix(b"\n").decode("ascii")
            validate_value(ref, token)
        except (UnicodeError, SyncError):
            return Saved("blocked", reason="Saved credential encoding unsupported")
        if not registered:
            return Saved("blocked", reason="Readable orphaned credential; reconcile gh separately")
        return Saved("available", token)

    def discover(self):
        if self.before is not None:
            raise SyncError("GitHub discovery already initialized")
        self.before = snapshot(self.config.directory)
        self._native_gate()
        for host, account in self.config.accounts:
            ref = Ref("github", host=host, account=account)
            self.saved[ref] = self._lookup(ref, self.before.metadata)
            self._same_files()
        return dict(self.saved)

    def recheck(self):
        if self.before is None:
            raise SyncError("GitHub discovery required before use")
        self._same_files()
        for ref, expected in self.saved.items():
            if self._lookup(ref, self.before.metadata) != expected:
                raise SyncError("saved GitHub credential changed; restart selection")
        self._same_files()

    def _eligible_import(self, ref, token, action):
        if self.before is None or ref not in self.saved:
            raise SyncError("GitHub account is outside this unlocked session")
        if action not in {"add", "overwrite"}:
            raise SyncError("local GitHub deletion is unsupported")
        saved = self.saved[ref]
        if saved.state == "blocked" or (action == "add") != (saved.state == "absent"):
            raise SyncError("GitHub action does not match destination state")
        users = self.before.metadata.users(ref.host)
        active = self.before.metadata.active(ref.host)
        if users and active != ref.account:
            raise SyncError("another GitHub account is active; switch separately and restart")
        validate_value(ref, token)
        if saved.state == "available" and saved.token == token:
            raise SyncError("GitHub credential already matches; keep it unchanged")

    def preflight(self, ref, token, action):
        self._eligible_import(ref, token, action)
        self.recheck()
        request = json.dumps({"host": ref.host, "account": ref.account, "token": token,
                              "ca_file": str(self.config.ca_file) if self.config.ca_file else None}).encode()
        result = self._call([], request, network=True, worker=True, limit=4096)
        if result.status or result.output != b"verified\n":
            raise SyncError("GitHub identity/capability validation unavailable or refused")
        self._same_files()
        self.prepared[ref] = (token, action)

    def commit(self, ref, token, action):
        self._eligible_import(ref, token, action)
        if self.prepared.get(ref) != (token, action):
            raise SyncError("GitHub import requires successful selected-token preflight")
        self.recheck()
        before = self.before
        self.mutation_ref = ref
        self.mutation_started = True
        try:
            result = self._call(["auth", "login", "--hostname", ref.host, "--with-token"],
                                token.encode("ascii") + b"\n", network=True)
            if result.status:
                raise SyncError("native GitHub login failed")
            after = snapshot(self.config.directory)
            if (after.metadata.active(ref.host) != ref.account
                    or not before.metadata.preserved_except(after.metadata, ref.host, ref.account)):
                raise SyncError("native GitHub preservation check failed")
            observed = self._lookup(ref, after.metadata)
            if observed.state != "available" or observed.token != token:
                raise SyncError("native GitHub write could not be verified")
            for other, expected in self.saved.items():
                if other != ref and self._lookup(other, after.metadata) != expected:
                    raise SyncError("unselected GitHub credential changed")
            self.before = after
            self.saved[ref] = observed
            del self.prepared[ref]
            self._same_files()
        except BaseException:
            raise CommittedError("GitHub write outcome uncertain; restart comparison, do not blindly retry") from None
