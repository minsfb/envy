"""Bounded public SSH policy; never discovers keys or creates directories."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .errors import SyncError, UsageError
from .files import safe_directory
from .ssh_format import validate_basename


def check_ssh_directory(path):
    """Validate ownership and write safety without managing existing modes."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return  # Never create directories during policy/discovery checks.
        safe_directory(info)
        if current == path and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise UsageError("SSH directory must be owned by this user and not writable by others")


@dataclass(frozen=True)
class SSHConfig:
    directory: Path
    filenames: tuple[str, ...]


def parse_ssh(raw, *, repository, local, protected=()):
    if not isinstance(raw, dict) or set(raw) != {"directory", "filenames"}:
        raise UsageError("SSH policy requires a directory and explicit filenames")
    directory = raw["directory"]
    if (not isinstance(directory, str) or not directory
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in directory)):
        raise UsageError("invalid SSH directory")
    directory = Path(directory).expanduser()
    if not directory.is_absolute() or ".." in directory.parts:
        raise UsageError("SSH directory must be absolute without traversal")
    names = raw["filenames"]
    if not isinstance(names, list) or len(names) > 32:
        raise UsageError("SSH filenames must be an explicit bounded list")
    try:
        for name in names:
            validate_basename(name)
    except SyncError:
        raise UsageError("SSH filenames must be safe nonreserved basenames") from None
    if len(set(names)) != len(names):
        raise UsageError("duplicate SSH filename")
    for other in (repository, local, *protected):
        other = Path(other).expanduser()
        if directory.is_relative_to(other) or other.is_relative_to(directory):
            raise UsageError("SSH directory overlaps another credential or protected destination")
    check_ssh_directory(directory)
    return SSHConfig(directory, tuple(sorted(names)))
