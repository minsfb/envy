"""Direct, exact native Git HTTPS store adapter; never runs a helper chain."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

from .errors import Cancelled, CommittedError, SyncError
from .github import NATIVE_MAGIC, Saved, environment
from .github_process import run
from .github_config import Executable
from .inventory import Ref, validate_value


def decode_reply(output, ref):
    fields = {}
    for line in output.splitlines():
        if not line:
            continue
        key, separator, value = line.partition(b"=")
        if not separator or not key or key in fields:
            raise SyncError("invalid native Git credential reply")
        fields[key] = value
    # Modern osxkeychain advertises read state. It is not a credential, and
    # echoing seen=1 into store would deliberately suppress an overwrite.
    if b"capability[]" in fields or b"state[]" in fields:
        if (fields.pop(b"capability[]", None) != b"state"
                or fields.pop(b"state[]", None) != b"osxkeychain:seen=1"):
            raise SyncError("unsupported native Git helper state")
    expected = {b"protocol": b"https", b"host": (ref.host + ":0").encode(), b"username": ref.account.encode(), b"path": b""}
    if set(fields) - {*expected, b"password"} or any(fields.get(key, value) != value for key, value in expected.items()):
        raise SyncError("native Git reply changed credential context")
    if not fields.get(b"password"):
        return None
    try:
        value = fields[b"password"].decode("ascii")
        validate_value(ref, value)
        return value
    except (UnicodeError, SyncError):
        raise SyncError("unsupported native Git credential encoding") from None


class Backend:
    def __init__(self, config):
        self.config = config
        self.saved, self.prepared = {}, {}
        self.initialized = False
        self.mutation_started, self.mutation_ref = False, None

    def _call(self, command, payload=b"", *, network=False):
        self.config.executable.recheck()
        if self.config.ca_file is not None and Executable._signature(self.config.ca_file.stat()) != self.config.ca_signature:
            raise SyncError("Git validation CA file changed")
        prefix = self.config.network_prefix if network else ()
        if prefix:
            self.config.network_executable.recheck()
        with tempfile.TemporaryDirectory(prefix="envy-git-credential-") as temporary:
            root = Path(temporary).resolve()
            env = environment(root, root / "gh", native=not network, ca_file=self.config.ca_file)
            return run([*prefix, *command], payload, environment=env, cwd=root, limit=16384)

    def _helper(self, ref, action, value=None):
        # Native Keychain's unspecified/default port is stored as zero. Omitting
        # port in a query can also match another explicit port; bind it to zero.
        query = f"protocol=https\nhost={ref.host}:0\nusername={ref.account}\npath=\n".encode()
        if value is not None:
            validate_value(ref, value)
            query += b"password=" + value.encode() + b"\n"
        return self._call([str(self.config.executable.path), action], query + b"\n")

    def _lookup(self, ref):
        try:
            metadata = self._call(["/usr/bin/security", "find-internet-password", "-s", ref.host,
                                   "-a", ref.account, "-r", "htps", "-p", "", "-P", "0"])
            if metadata.status not in {0, 44}:
                return Saved("blocked", reason="native Git Keychain unavailable")
            result = self._helper(ref, "get")
            if result.status:
                return Saved("blocked", reason="native Git credential lookup failed")
            value = decode_reply(result.output, ref)
            if value is not None:
                return (Saved("available", value) if metadata.status == 0 else
                        Saved("blocked", reason="native Git credential metadata disagrees"))
            return (Saved("absent") if metadata.status == 44 else
                    Saved("blocked", reason="existing Git credential is unreadable"))
        except Cancelled:
            raise
        except (OSError, SyncError):
            return Saved("blocked", reason="native Git credential lookup unavailable")

    def discover(self):
        if self.initialized:
            raise SyncError("Git credential discovery already initialized")
        if self.config.backend != "osxkeychain" or sys.platform != "darwin":
            raise SyncError("unsupported native Git credential backend")
        self.config.executable.recheck()
        with self.config.executable.path.open("rb") as binary:
            if binary.read(4) not in NATIVE_MAGIC:
                raise SyncError("Git credential sync requires the verified native helper")
        self.initialized = True
        for host, account in self.config.accounts:
            ref = Ref("git", host=host, account=account)
            self.saved[ref] = self._lookup(ref)
        return dict(self.saved)

    def recheck(self):
        if not self.initialized:
            raise SyncError("Git credential discovery required")
        for ref, expected in self.saved.items():
            if self._lookup(ref) != expected:
                raise SyncError("Git credential state changed; restart selection")

    def _eligible(self, ref, value, action):
        if ref not in self.saved or action not in {"add", "overwrite"}:
            raise SyncError("Git credential target/action unavailable; local deletion unsupported")
        validate_value(ref, value)
        state = self.saved[ref]
        if state.state == "blocked" or (action == "add") != (state.state == "absent") or state.token == value:
            raise SyncError("Git credential action does not match destination")

    def preflight(self, ref, value, action):
        self._eligible(ref, value, action)
        self.recheck()
        request = {"host": ref.host, "account": ref.account, "token": value, "profile": "git-https",
                   "ca_file": str(self.config.ca_file) if self.config.ca_file else None}
        result = self._call([str(Path(sys.executable).resolve()), "-I", "-B",
                             str(Path(__file__).with_name("github_validate.py"))],
                            json.dumps(request).encode(), network=True)
        if result.status or result.output != b"verified\n":
            raise SyncError("Git credential identity validation refused or unavailable")
        self.recheck()
        self.prepared[ref] = value, action

    def commit(self, ref, value, action):
        self._eligible(ref, value, action)
        if self.prepared.get(ref) != (value, action):
            raise SyncError("Git write requires selected credential validation")
        self.recheck()
        self.mutation_started, self.mutation_ref = True, ref
        try:
            result = self._helper(ref, "store", value)
            if result.status or self._lookup(ref) != Saved("available", value):
                raise SyncError("Git credential write verification failed")
            for other, expected in self.saved.items():
                if other != ref and self._lookup(other) != expected:
                    raise SyncError("unselected Git credential changed")
            self.saved[ref] = Saved("available", value)
            del self.prepared[ref]
        except BaseException:
            raise CommittedError("native Git write uncertain; restart comparison before retrying") from None
