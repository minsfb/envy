"""Explicit native Git store policy; no helper-chain execution or credentials."""
from __future__ import annotations

from dataclasses import dataclass
import sys

from .errors import ProviderUnavailable, SyncError, UsageError
from .github_config import Executable, absolute_path, bind_network, command_spec, public_text
from .inventory import validate_host_account

MAC_HELPER = "/Library/Developer/CommandLineTools/usr/libexec/git-core/git-credential-osxkeychain"


@dataclass(frozen=True)
class GitConfig:
    backend: str
    accounts: tuple
    executable: Executable | None = None
    network_prefix: tuple = ()
    network_executable: Executable | None = None
    ca_file: object = None
    ca_signature: tuple | None = None


@dataclass(frozen=True)
class GitPolicy:
    backend: str
    accounts: tuple
    executable: str | None = None
    network_prefix: tuple = ()
    ca_file: object = None

    def bind(self):
        if self.backend == "gh":
            return GitConfig(self.backend, self.accounts)
        if sys.platform != "darwin":
            raise ProviderUnavailable("native Git osxkeychain sync is verified only on macOS")
        helper = Executable.resolve(self.executable)
        prefix, network, signature = bind_network(self.network_prefix, self.ca_file)
        return GitConfig(self.backend, self.accounts, helper, prefix, network, self.ca_file, signature)


def parse_git(raw):
    if not isinstance(raw, dict) or set(raw) - {"backend", "accounts", "network_command_prefix", "ca_file"}:
        raise UsageError("invalid Git credential sync policy")
    backend = raw.get("backend")
    if not isinstance(backend, str) or backend not in {"gh", "osxkeychain"}:
        raise UsageError("unsupported Git credential backend; configure gh or osxkeychain explicitly")
    accounts = raw.get("accounts")
    if not isinstance(accounts, list) or len(accounts) > 32:
        raise UsageError("Git credential accounts must be an explicit bounded list")
    pairs = []
    for account in accounts:
        if not isinstance(account, dict) or set(account) != {"host", "account"}:
            raise UsageError("invalid Git credential context")
        try:
            validate_host_account(account["host"], account["account"])
        except SyncError:
            raise UsageError("invalid Git host/account") from None
        pair = account["host"], account["account"]
        if pair in pairs:
            raise UsageError("duplicate Git credential context")
        pairs.append(pair)
    if backend == "gh":
        if set(raw) - {"backend", "accounts"}:
            raise UsageError("gh-delegated Git credentials use the saved-gh provider policy")
        return GitPolicy(backend, tuple(sorted(pairs)))
    raw_prefix = raw.get("network_command_prefix", [])
    if not isinstance(raw_prefix, list) or len(raw_prefix) > 32:
        raise UsageError("invalid Git validation network prefix")
    prefix = tuple(public_text(part) for part in raw_prefix)
    if prefix:
        command_spec(prefix[0])
    ca = absolute_path(raw["ca_file"]) if "ca_file" in raw else None
    return GitPolicy(backend, tuple(sorted(pairs)), MAC_HELPER, prefix, ca)
