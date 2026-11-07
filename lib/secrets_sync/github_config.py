"""Public GitHub sync policy validation, without credential reads or execution.

Resolved commands are candidates, not certified native capabilities. The caller
must pass the native compatibility gate and recheck identities before using them.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from .errors import ProviderUnavailable, SyncError, UsageError
from .files import safe_directory
from .inventory import TOKEN_OVERRIDES, validate_host_account


def public_text(value):
    if (not isinstance(value, str) or not value
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise UsageError("invalid public GitHub setting")
    return value


def absolute_path(value):
    path = Path(public_text(value)).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise UsageError("GitHub path must be absolute without traversal")
    return path


def command_spec(raw):
    """Validate command syntax without resolving PATH or requiring installation."""
    raw = public_text(raw)
    if "/" in raw:
        return str(absolute_path(raw))
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", raw):
        raise UsageError("invalid provider command name")
    return raw


def bind_network(prefix, ca_file):
    helper = Executable.resolve(prefix[0]) if prefix else None
    if helper:
        prefix = (str(helper.path), *prefix[1:])
    signature = None
    if ca_file:
        try:
            info = ca_file.stat()
        except FileNotFoundError:
            raise ProviderUnavailable("configured CA file is missing; provision it before syncing this provider") from None
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or info.st_uid not in {0, os.getuid()}):
            raise UsageError("unsafe provider CA file")
        signature = Executable._signature(info)
    return prefix, helper, signature


def private_directory(path):
    """Inspect metadata only, following no links. An absent suffix stays absent."""
    current = Path(path.anchor)
    missing = False
    for component in path.parts[1:]:
        current /= component
        if missing:
            continue
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        safe_directory(info)
        if current == path and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise UsageError("GitHub configuration directory must be private")


@dataclass(frozen=True)
class Executable:
    path: Path
    signature: tuple

    @classmethod
    def resolve(cls, raw):
        raw = command_spec(raw)
        if "/" not in raw:
            found = shutil.which(raw)
            if not found:
                raise ProviderUnavailable("configured command is missing; install this provider's tooling and retry")
            candidate = Path(found)
        else:
            candidate = absolute_path(raw)
        try:
            path = candidate.resolve(strict=True)
        except FileNotFoundError:
            raise ProviderUnavailable("configured command is missing; install this provider's tooling and retry") from None
        # Executables may be installed via symlinks. Validate the resolved file
        # and its ancestors, then retain the resolved identity for later rechecks.
        current = path.parent
        while current != current.parent:
            safe_directory(current.lstat())
            current = current.parent
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
            raise UsageError("unsafe GitHub executable")
        return cls(path, cls._signature(info))

    @staticmethod
    def _signature(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def recheck(self):
        if self._signature(self.path.lstat()) != self.signature:
            raise SyncError("GitHub executable changed during sync")


@dataclass(frozen=True)
class GitHubConfig:
    executable: Executable
    directory: Path
    accounts: tuple[tuple[str, str], ...]
    network_prefix: tuple[str, ...]
    network_executable: Executable | None
    ca_file: Path | None
    ca_signature: tuple | None = None

    @property
    def blocked_names(self):
        return TOKEN_OVERRIDES


@dataclass(frozen=True)
class GitHubPolicy:
    executable: str
    directory: Path
    accounts: tuple[tuple[str, str], ...]
    network_prefix: tuple[str, ...]
    ca_file: Path | None

    @property
    def blocked_names(self):
        return TOKEN_OVERRIDES

    def bind(self):
        """Metadata only; native capability checks still belong to the backend."""
        private_directory(self.directory)
        executable = Executable.resolve(self.executable)
        prefix, helper, signature = bind_network(self.network_prefix, self.ca_file)
        return GitHubConfig(executable, self.directory, self.accounts, prefix, helper,
                            self.ca_file, signature)


def parse_github(raw, *, repository, local, protected=()):
    allowed = {"executable", "config_directory", "accounts", "network_command_prefix", "ca_file",
               "platforms"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise UsageError("invalid GitHub sync configuration")
    overrides = raw.get("platforms", {})
    if not isinstance(overrides, dict) or set(overrides) - {"darwin", "linux"}:
        raise UsageError("invalid GitHub platform policy")
    for values in overrides.values():
        if not isinstance(values, dict) or set(values) - {"executable", "network_command_prefix", "ca_file"}:
            raise UsageError("invalid GitHub platform settings")
        if "executable" in values:
            command_spec(values["executable"])
        if "ca_file" in values:
            absolute_path(values["ca_file"])
        if "network_command_prefix" in values:
            prefix = values["network_command_prefix"]
            if not isinstance(prefix, list) or len(prefix) > 32:
                raise UsageError("invalid platform GitHub command prefix")
            for part in prefix:
                public_text(part)
            if prefix:
                command_spec(prefix[0])
    raw = {**raw, **overrides.get(sys.platform, {})}
    accounts = raw.get("accounts")
    if not isinstance(accounts, list) or len(accounts) > 32:
        raise UsageError("GitHub accounts must be an explicit bounded list")
    pairs = []
    for record in accounts:
        if not isinstance(record, dict) or set(record) != {"host", "account"}:
            raise UsageError("invalid GitHub account configuration")
        try:
            validate_host_account(record["host"], record["account"])
        except SyncError:
            raise UsageError("invalid canonical GitHub host/account") from None
        pair = record["host"], record["account"]
        if pair in pairs:
            raise UsageError("duplicate GitHub account configuration")
        pairs.append(pair)
    directory = absolute_path(raw.get("config_directory"))
    # Reject ancestor overlap in either direction, including declared recovery
    # locations; do not open or resolve those protected files.
    for other in (repository, local, *protected):
        other = Path(other).expanduser()
        if directory.is_relative_to(other) or other.is_relative_to(directory):
            raise UsageError("GitHub configuration overlaps a protected destination")
    private_directory(directory)
    executable = command_spec(raw.get("executable", "gh"))
    prefix = raw.get("network_command_prefix", [])
    if not isinstance(prefix, list) or len(prefix) > 32:
        raise UsageError("invalid GitHub network command prefix")
    prefix = tuple(public_text(part) for part in prefix)
    if prefix:
        command_spec(prefix[0])
    ca_file = absolute_path(raw["ca_file"]) if "ca_file" in raw else None
    return GitHubPolicy(executable, directory, tuple(sorted(pairs)), prefix, ca_file)
