#!/usr/bin/env python3
"""Select, link, verify, and diagnose user-level agent assets."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # v1 remains usable on non-Unix hosts.
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import paths  # noqa: E402


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CONTROL_LIMIT = 16 * 1024 * 1024
HARNESS_PAIR = ("claude", "codex")
PLATFORM_HIERARCHY = {
    "darwin": ("darwin", "unix", "global"),
    "linux": ("linux", "unix", "global"),
    "wsl": ("wsl", "linux", "unix", "global"),
    "git-bash": ("git-bash", "windows", "global"),
    "windows": ("windows", "global"),
    "unix": ("unix", "global"),
    "global": ("global",),
}


class AssetError(RuntimeError):
    pass


class V2CapabilityError(AssetError):
    """V2 selection cannot run without Unix locking."""


class OperationEntryChanged(AssetError):
    def __init__(self, target: Path) -> None:
        self.target = str(target)
        super().__init__(f"ordinary entry changed during observation: {target}")


class OwnershipDriftError(AssetError):
    def __init__(self, target: Path, absent: bool) -> None:
        self.target = str(target)
        self.state = "missing" if absent else "changed"
        super().__init__(f"recorded target is {self.state}: {target}; inspect agent-assets status "
                         "and the recorded link before reconciling it; preserve foreign entries "
                         "and do not delete the ownership ledger")


def object_fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise AssetError("invalid or unknown object fields")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssetError("duplicate JSON key")
        result[key] = value
    return result


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def absolute_path(value: str | Path) -> Path:
    raw = os.path.expanduser(str(value))
    if os.name != "posix":
        return Path(raw)  # New handoff semantics are refused before use there.
    if not raw.startswith("/") or any(part in {"", ".", ".."} for part in raw.split("/")[1:]):
        raise AssetError("path must be absolute without traversal or ambiguous components")
    return Path(raw)


def namespace_key(path: Path) -> tuple[str, ...]:
    """Conservative canonical-caseless components, without filesystem discovery."""
    return tuple(unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())
                 for part in Path(os.path.abspath(path)).parts)


def same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def directory_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


def unix_flags() -> int:
    if fcntl is None or any(not hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")):
        raise AssetError("safe ownership locking is unsupported on this platform")
    return os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


@contextmanager
def parent_descriptor(path: Path, *, create: bool = False, created: list[str] | None = None,
                      inspection: dict[str, Any] | None = None, expected: dict[str, Any] | None = None):
    """Walk the exact parent chain; creation is only an explicitly selected prefix."""
    path = absolute_path(path)
    flags = unix_flags() | os.O_RDONLY | os.O_DIRECTORY
    fd = os.open("/", flags)
    current = Path("/")
    missing = False
    try:
        root_identity = directory_identity(os.fstat(fd))
        if inspection is not None:
            inspection.update({"path": str(path), "ancestors": [root_identity], "missing": []})
        if expected is not None and (expected.get("path") != str(path) or
                not expected.get("ancestors") or expected["ancestors"][0] != root_identity or
                expected.get("missing") != list(path.parts[len(expected["ancestors"]):-1])):
            raise AssetError("ownership parent inspection no longer matches its intended chain")
        for index, part in enumerate(path.parts[1:-1]):
            parent_stat = os.fstat(fd)
            current = current / part
            expected_absent = expected is not None and index + 1 >= len(expected["ancestors"])
            try:
                before = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if missing or expected_absent:
                    raise AssetError("scaffolding path appeared during creation")
            except FileNotFoundError:
                if inspection is not None:
                    inspection["missing"] = list(path.parts[index + 1:-1])
                if expected is not None and not expected_absent:
                    raise AssetError("previously inspected ownership ancestor disappeared")
                if not create:
                    raise
                if parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022:
                    raise AssetError("scaffolding requires an existing private owned ancestor")
                missing = True
                os.mkdir(part, mode=0o700, dir_fd=fd)  # EEXIST is a refusal, never adoption.
                if created is not None:
                    created.append(str(current))
                before = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if stat.S_IMODE(before.st_mode) != 0o700:
                    raise AssetError("created scaffolding has unexpected permissions")
            if (expected is not None and not expected_absent and
                    directory_identity(before) != expected["ancestors"][index + 1]):
                raise AssetError("previously inspected ownership ancestor changed")
            if not stat.S_ISDIR(before.st_mode) or before.st_uid not in {0, os.geteuid()}:
                raise AssetError("invalid ownership path directory")
            if before.st_mode & 0o022 and not (before.st_uid == 0 and before.st_mode & stat.S_ISVTX):
                raise AssetError("ownership path directory is writable by others")
            next_fd = os.open(part, flags, dir_fd=fd)
            after = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if not same_inode(before, os.fstat(next_fd)) or not same_inode(before, after):
                os.close(next_fd)
                raise AssetError("ownership path changed during inspection")
            if inspection is not None:
                inspection["ancestors"].append(directory_identity(after))
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def regular_stat(info: os.stat_result, *, control: bool, lock: bool = False) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AssetError("expected a single-link regular file")
    if control and (info.st_uid != os.geteuid() or info.st_mode & 0o022 or not info.st_mode & 0o400):
        raise AssetError("invalid ownership control file permissions")
    if lock and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0):
        raise AssetError("ownership lock must be empty and mode 0600")


@contextmanager
def regular_descriptor(path: Path, *, control: bool = True, lock: bool = False,
                       create: bool = False, write: bool = False, created: list[str] | None = None,
                       inspection: dict[str, Any] | None = None):
    with parent_descriptor(path, inspection=inspection) as parent:
        parent_stat = os.fstat(parent)
        if control and (parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022):
            raise AssetError("control files require a non-writable-by-others owned parent")
        try:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                raise
            try:
                fd = os.open(path.name, unix_flags() | os.O_RDWR | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=parent)
            except FileExistsError:
                fd = None
            else:
                before = os.fstat(fd)
                if created is not None:
                    created.append(str(path))
        else:
            fd = None
        if fd is None:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            regular_stat(before, control=control, lock=lock)
            fd = os.open(path.name, unix_flags() | (os.O_RDWR if write else os.O_RDONLY), dir_fd=parent)
        try:
            actual = os.fstat(fd)
            regular_stat(actual, control=control, lock=lock)
            if not same_inode(before, actual) or not same_inode(actual, os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
                raise AssetError("regular file changed during inspection")
            yield fd
        finally:
            os.close(fd)


def regular_bytes(path: Path, *, control: bool = True,
                  inspection: dict[str, Any] | None = None) -> tuple[bytes, os.stat_result]:
    with regular_descriptor(path, control=control, inspection=inspection) as fd:
        info = os.fstat(fd)
        if info.st_size > CONTROL_LIMIT:
            raise AssetError("input exceeds ownership operation size limit")
        chunks = []
        remaining = CONTROL_LIMIT + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not remaining:
            raise AssetError("input exceeds ownership operation size limit")
        return b"".join(chunks), info


@contextmanager
def ownership_lock(path: Path, *, write: bool, created: list[str] | None = None):
    if fcntl is None:
        yield
        return
    lock_path = path.with_name(path.name + ".lock")
    if not write:
        try:
            with parent_descriptor(lock_path) as parent:
                os.stat(lock_path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            yield
            return
    with regular_descriptor(lock_path, lock=True, create=write, write=write, created=created) as fd:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AssetError("ownership-store-busy") from error
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Asset:
    kind: str
    name: str
    source: Path
    group: str
    harness: str
    target: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "source": str(self.source),
            "group": self.group,
            "harness": self.harness,
            "target": str(self.target),
        }


def checkpoint_bytes(value: dict[str, Any]) -> bytes:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > CONTROL_LIMIT:
        raise AssetError("ownership checkpoint exceeds size limit")
    return data


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    data = checkpoint_bytes(value)
    if fcntl is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return
    with parent_descriptor(path) as parent:
        temporary = f".{path.name}.{os.urandom(16).hex()}"
        fd = os.open(temporary, unix_flags() | os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o600, dir_fd=parent)
        owned = os.fstat(fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                current = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if same_inode(owned, current):
                    os.unlink(temporary, dir_fd=parent)


def load_json(path: Path, label: str, *, strict: bool = False) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=unique_object if strict else dict)
    except FileNotFoundError as error:
        raise AssetError(f"{label} not found: {path}") from error
    except json.JSONDecodeError as error:
        raise AssetError(f"invalid {label} JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise AssetError(f"{label} must contain a JSON object: {path}")
    return value


def launcher_script(manager: str, script: Path) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"# managed by {manager} agent-assets\n"
        f'exec {shlex.quote(str(script))} "$@"\n'
    )


def available_plugin_versions(claude_root: Path) -> dict[str, str]:
    registry_path = claude_root / "plugins" / "known_marketplaces.json"
    if not registry_path.is_file():
        return {}
    try:
        marketplaces = load_json(registry_path, "Claude marketplace registry")
    except AssetError:
        return {}
    available: dict[str, str] = {}
    for marketplace_name, definition in marketplaces.items():
        if not isinstance(marketplace_name, str) or not isinstance(definition, dict):
            continue
        raw_location = definition.get("installLocation")
        if not isinstance(raw_location, str):
            continue
        location = Path(raw_location).expanduser()
        if not location.is_absolute():
            location = claude_root / location
        manifest_path = location / ".claude-plugin" / "marketplace.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = load_json(manifest_path, f"{marketplace_name} manifest")
        except AssetError:
            continue
        plugins = manifest.get("plugins", [])
        if not isinstance(plugins, list):
            continue
        for item in plugins:
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("version"), str)
            ):
                available[f"{item['name']}@{marketplace_name}"] = item["version"]
    return available


def configured_manifests(args: argparse.Namespace) -> list[Path]:
    explicit = args.manifest or os.environ.get("ENVY_AGENT_ASSETS_MANIFEST")
    if explicit:
        return [Path(explicit).expanduser().resolve()]
    manifest_path = os.environ.get("ENVY_MANIFEST")
    if not manifest_path:
        raise AssetError("no agent asset manifest is configured")
    resolved = load_json(Path(manifest_path), "resolved envy manifest")
    resources = resolved.get("resources", {}).get("agent_asset_manifests", [])
    if not isinstance(resources, list) or not resources:
        raise AssetError("no agent_asset_manifests resources are configured")
    return [Path(value).expanduser().resolve() for value in resources]


def names(value: Any, *, nonempty: bool = False) -> list[str]:
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) or not NAME_RE.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        raise AssetError("expected unique names")
    return value


def validate_v2_manifest(value: dict[str, Any]) -> None:
    object_fields(value, {"version"}, {"distribution_feature", "destinations", "audit_roots",
                  "skill_groups", "agent_groups", "launcher_predecessors", "plugin_audit",
                  "skill_contracts", "skill_handoffs"})
    if value.get("distribution_feature", "agent-assets") != "agent-assets":
        raise AssetError("unsupported distribution feature")
    if type(value.get("plugin_audit", False)) is not bool:
        raise AssetError("plugin_audit must be boolean")
    for key in ("skill_groups", "agent_groups", "audit_roots", "launcher_predecessors",
                "skill_contracts", "skill_handoffs"):
        if not isinstance(value.get(key, []), list):
            raise AssetError(f"{key} must be an array")
    groups = {}
    for key in ("skill_groups", "agent_groups"):
        for group in value.get(key, []):
            optional = {"platforms", "features", "any_features"}
            if key == "skill_groups":
                optional.add("exclude_names")
            object_fields(group, {"name", "path", "targets"}, optional)
            names([group["name"]], nonempty=True)
            names(group["targets"], nonempty=True)
            if not isinstance(group["path"], str) or not group["path"]:
                raise AssetError("invalid source group path")
            for field in optional:
                if field in group:
                    names(group[field])
            if key == "skill_groups":
                groups[group["name"]] = group
    destinations = value.get("destinations", {})
    if not isinstance(destinations, dict):
        raise AssetError("destinations must be an object")
    for harness, kinds in destinations.items():
        names([harness])
        object_fields(kinds, set(), {"skills", "agents"})
        for path in kinds.values():
            if not isinstance(path, str):
                raise AssetError("invalid destination path")
            absolute_path(path)
    for root in value.get("audit_roots", []):
        object_fields(root, {"path"}, {"name", "owner", "read_only", "alias_of", "flat_markdown"})
        for field, item in root.items():
            if field in {"read_only", "flat_markdown"}:
                if type(item) is not bool:
                    raise AssetError("invalid audit root flag")
            elif not isinstance(item, str):
                raise AssetError("invalid audit root field")
    for item in value.get("launcher_predecessors", []):
        object_fields(item, {"manager", "script"})
    seen = set()
    for contract in value.get("skill_contracts", []):
        object_fields(contract, {"group", "name", "version"})
        names([contract["group"]])
        names([contract["name"]])
        if type(contract["version"]) is not int or contract["version"] != 1 or contract["group"] not in groups:
            raise AssetError("unsupported skill contract")
        identity = (contract["group"], contract["name"])
        if identity in seen:
            raise AssetError("duplicate skill contract")
        seen.add(identity)
    for handoff in value.get("skill_handoffs", []):
        object_fields(handoff, {"group", "name", "replacement", "adapter", "legacy_files"})
        for field in ("group", "name", "adapter"):
            names([handoff[field]])
        object_fields(handoff["replacement"], {"group", "version"})
        names([handoff["replacement"]["group"]])
        if type(handoff["replacement"]["version"]) is not int or handoff["replacement"]["version"] != 1:
            raise AssetError("unsupported replacement contract")
        if handoff["group"] not in groups or handoff["name"] == handoff["adapter"]:
            raise AssetError("invalid handoff group or adapter")
        if handoff["name"] not in groups[handoff["group"]].get("exclude_names", []):
            raise AssetError("handoff predecessor must be excluded")
        inventory = handoff["legacy_files"]
        if (not isinstance(inventory, dict) or set(inventory) != {"SKILL.md"}
                or not isinstance(inventory["SKILL.md"], str) or not HASH_RE.fullmatch(inventory["SKILL.md"])):
            raise AssetError("handoff requires one pinned regular SKILL.md")


def compose_manifests(paths: list[Path]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": 1,
        "distribution_feature": "agent-assets",
        "destinations": {},
        "audit_roots": [],
        "skill_groups": [],
        "agent_groups": [],
        "launcher_predecessors": [],
        "plugin_audit": False,
        "skill_contracts": [],
        "skill_handoffs": [],
    }
    group_names: set[tuple[str, str]] = set()
    for path in paths:
        value = load_json(path, "agent asset manifest", strict=True)
        version = value.get("version")
        if type(version) is not int or version not in {1, 2}:
            raise AssetError(f"unsupported agent asset manifest version: {path}")
        if version == 2:
            value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            validate_v2_manifest(value)
            result["version"] = 2
            for key in ("skill_contracts", "skill_handoffs"):
                result[key].extend(value.get(key, []))
        destinations = value.get("destinations", {})
        if not isinstance(destinations, dict):
            raise AssetError(f"destinations must be an object: {path}")
        for harness, definitions in destinations.items():
            existing = result["destinations"].setdefault(harness, {})
            if not isinstance(definitions, dict):
                raise AssetError(f"destination '{harness}' must be an object")
            for kind, destination in definitions.items():
                if kind in existing and existing[kind] != destination:
                    raise AssetError(f"conflicting destination {harness}.{kind}")
                existing[kind] = destination
        for key in ("skill_groups", "agent_groups"):
            groups = value.get(key, [])
            if not isinstance(groups, list):
                raise AssetError(f"{key} must be an array: {path}")
            for raw_group in groups:
                if not isinstance(raw_group, dict):
                    raise AssetError(f"invalid group in {path}")
                group = dict(raw_group)
                if version == 1:
                    group.pop("exclude_names", None)
                group["_version"] = version
                name = group.get("name")
                identity = (key, str(name))
                if identity in group_names:
                    raise AssetError(f"duplicate agent asset group: {name}")
                group_names.add(identity)
                raw_source = group.get("path")
                if isinstance(raw_source, str):
                    source = Path(raw_source).expanduser()
                    if not source.is_absolute():
                        source = path.parent / source
                    group["path"] = str(absolute_path(source) if version == 2 else source.resolve())
                result[key].append(group)
        audit_roots = value.get("audit_roots", [])
        if not isinstance(audit_roots, list):
            raise AssetError(f"audit_roots must be an array: {path}")
        result["audit_roots"].extend(audit_roots)
        predecessors = value.get("launcher_predecessors", [])
        if not isinstance(predecessors, list):
            raise AssetError(f"launcher_predecessors must be an array: {path}")
        for predecessor in predecessors:
            if not isinstance(predecessor, dict):
                raise AssetError(f"invalid launcher predecessor in {path}")
            manager = predecessor.get("manager")
            raw_script = predecessor.get("script")
            if not isinstance(manager, str) or not NAME_RE.fullmatch(manager):
                raise AssetError(f"invalid launcher predecessor manager in {path}")
            if not isinstance(raw_script, str) or not raw_script:
                raise AssetError(f"invalid launcher predecessor script in {path}")
            script = Path(raw_script).expanduser()
            if not script.is_absolute():
                script = path.parent / script
            result["launcher_predecessors"].append(
                {"manager": manager, "script": str(script.resolve())}
            )
        result["plugin_audit"] = bool(
            result["plugin_audit"] or value.get("plugin_audit", False)
        )
    if len(result["skill_handoffs"]) > 1:
        raise AssetError("only one two-harness handoff is supported")
    return result


def lexical_target(link: Path) -> Path:
    raw = Path(os.readlink(link))
    if not raw.is_absolute():
        raw = link.parent / raw
    return Path(os.path.abspath(raw))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_inventory(source: Path) -> list[dict[str, Any]]:
    # Opening a dummy child validates the root as a real directory component.
    with parent_descriptor(source / "SKILL.md"):
        pass
    inventory = []
    def walk_error(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(source, followlinks=False, onerror=walk_error):
        for name in sorted(directories + files):
            path = Path(current) / name
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISDIR(info.st_mode):
                inventory.append({"path": relative, "type": "directory"})
            elif stat.S_ISREG(info.st_mode):
                content, info = regular_bytes(path, control=False)
                inventory.append({"path": relative, "type": "file",
                                  "sha256": hashlib.sha256(content).hexdigest(),
                                  "executable": bool(info.st_mode & 0o111)})
            else:
                raise AssetError("skill inventory contains a link or special entry")
    if not any(item["path"] == "SKILL.md" and item["type"] == "file" for item in inventory):
        raise AssetError("skill inventory is missing SKILL.md")
    return sorted(inventory, key=lambda item: item["path"])


def link_observation(path: Path) -> dict[str, Any]:
    try:
        with parent_descriptor(path) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            result = {"mode": info.st_mode, "size": info.st_size, "mtime_ns": info.st_mtime_ns}
            if stat.S_ISLNK(info.st_mode):
                result["link"] = os.readlink(path.name, dir_fd=parent)
            return result
    except FileNotFoundError:
        return {"absent": True}


def exact_link(path: Path, source: str | Path) -> bool:
    observed = link_observation(path)
    return observed.get("link") == str(source)


def entry_identity(info: os.stat_result) -> str:
    # Equality witness, not protection from inode reuse/hostile same-user writers.
    # The final POSIX check/rename and check/unlink gaps also remain.
    return digest_json([info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)])


def operation_observation(target: Path, file_limit: int = 0) -> dict[str, Any]:
    """No-follow leaf observation; regular launcher reads have a planned bound."""
    if type(file_limit) is not int or not 0 <= file_limit <= CONTROL_LIMIT:
        raise AssetError("invalid ordinary launcher read bound")
    try:
        with parent_descriptor(target) as parent:
            before = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            observed = {"kind": "other", "identity": entry_identity(before)}
            if stat.S_ISLNK(before.st_mode):
                observed.update(kind="link", source=os.readlink(target.name, dir_fd=parent))
            elif stat.S_ISREG(before.st_mode) and file_limit and before.st_size <= file_limit:
                try:
                    regular_stat(before, control=True)
                except AssetError:
                    return observed
                fd = os.open(target.name, unix_flags() | os.O_RDONLY, dir_fd=parent)
                try:
                    opened = os.fstat(fd)
                    regular_stat(opened, control=True)
                    if not same_inode(before, opened):
                        raise OperationEntryChanged(target)
                    data = b""
                    while len(data) <= file_limit:
                        part = os.read(fd, file_limit + 1 - len(data))
                        if not part:
                            break
                        data += part
                    if len(data) > file_limit:
                        raise AssetError("ordinary launcher exceeds planned read bound")
                    observed.update(kind="file", bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                    mode=stat.S_IMODE(opened.st_mode))
                finally:
                    os.close(fd)
            named = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if not same_inode(before, named):
                raise OperationEntryChanged(target)
            return observed
    except FileNotFoundError:
        return {"kind": "absent"}


def validate_operation_observation(value: Any) -> None:
    if not isinstance(value, dict):
        raise AssetError("invalid ordinary preimage")
    kind = value.get("kind")
    if kind == "absent":
        object_fields(value, {"kind"})
        return
    fields = {"kind", "identity"}
    if kind == "link":
        fields.add("source")
        if not isinstance(value.get("source"), str):
            raise AssetError("invalid ordinary link preimage")
    elif kind == "file":
        fields.update({"sha256", "bytes", "mode"})
        if (not isinstance(value.get("sha256"), str) or not HASH_RE.fullmatch(value["sha256"])
                or type(value.get("bytes")) is not int or not 0 <= value["bytes"] <= CONTROL_LIMIT
                or type(value.get("mode")) is not int or not 0 <= value["mode"] <= 0o7777):
            raise AssetError("invalid ordinary file preimage")
    elif kind != "other":
        raise AssetError("unsupported ordinary preimage kind")
    object_fields(value, fields)
    if not isinstance(value["identity"], str) or not HASH_RE.fullmatch(value["identity"]):
        raise AssetError("invalid ordinary preimage identity")


def operation_baseline(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "ordinary_operation"}


def operation_checkpoint(base: dict[str, Any], plan: dict[str, Any], cursor: int = 0,
                         prepared: str | None = None) -> dict[str, Any]:
    return {**base, "ordinary_operation": {"version": 1, "plan": plan,
            "plan_sha256": digest_json(plan), "cursor": cursor, "prepared": prepared}}


def operation_complete(base: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    links = dict(base["links"])
    for step in plan["steps"]:
        if step["kind"] == "link":
            if step["after"] is None:
                links.pop(step["target"], None)
            else:
                links[step["target"]] = step["after"]
    return {**base, "links": links, "manifests": plan["manifests"]}


def validate_ordinary_operation(value: dict[str, Any]) -> None:
    record = value["ordinary_operation"]
    object_fields(record, {"version", "plan", "plan_sha256", "cursor", "prepared"})
    if type(record["version"]) is not int or record["version"] != 1:
        raise AssetError("unsupported ordinary operation version")
    plan = record["plan"]
    object_fields(plan, {"action", "baseline_sha256", "selection_sha256", "manifests", "steps", "preserved"},
                  {"handoff_preparation"})
    if not isinstance(plan["action"], str) or plan["action"] not in {"converge", "remove"}:
        raise AssetError("invalid ordinary operation action")
    if "handoff_preparation" in plan and (
            plan["action"] != "converge" or value["handoff"] is not None
            or not isinstance(plan["handoff_preparation"], str)
            or not HASH_RE.fullmatch(plan["handoff_preparation"])):
        raise AssetError("invalid handoff preparation fingerprint")
    if (record["plan_sha256"] != digest_json(plan)
            or plan["baseline_sha256"] != digest_json(operation_baseline(value))):
        raise AssetError("ordinary operation fingerprint mismatch")
    selection = plan["selection_sha256"]
    if plan["action"] == "remove":
        if selection is not None or plan["manifests"] != value["manifests"]:
            raise AssetError("removal must retain its recorded selection")
    elif not isinstance(selection, str) or not HASH_RE.fullmatch(selection):
        raise AssetError("invalid ordinary selection fingerprint")
    if not isinstance(plan["manifests"], list):
        raise AssetError("invalid ordinary manifests")
    for path in plan["manifests"]:
        if not isinstance(path, str):
            raise AssetError("invalid ordinary manifest path")
        absolute_path(path)
    if not isinstance(plan["steps"], list) or not isinstance(plan["preserved"], list):
        raise AssetError("invalid ordinary operation lists")
    targets = set()
    for step in plan["steps"]:
        object_fields(step, {"kind", "target", "before", "after", "stage"})
        if not isinstance(step["target"], str):
            raise AssetError("invalid ordinary target")
        target = absolute_path(step["target"])
        if not isinstance(step["kind"], str) or step["kind"] not in {"link", "launcher"} or str(target) in targets:
            raise AssetError("invalid or duplicate ordinary target")
        targets.add(str(target))
        validate_operation_observation(step["before"])
        if step["kind"] == "launcher":
            if str(target) != value["launcher"]:
                raise AssetError("ordinary launcher differs from recorded path")
            if step["after"] is not None:
                object_fields(step["after"], {"sha256", "bytes"})
                if (not isinstance(step["after"]["sha256"], str)
                        or not HASH_RE.fullmatch(step["after"]["sha256"])
                        or type(step["after"]["bytes"]) is not int
                        or not 0 < step["after"]["bytes"] <= CONTROL_LIMIT):
                    raise AssetError("invalid ordinary launcher postimage")
        elif step["after"] is not None:
            if not isinstance(step["after"], str):
                raise AssetError("invalid ordinary source")
            absolute_path(step["after"])
        if step["after"] is None:
            if step["stage"] is not None or (step["kind"] == "link" and str(target) not in value["links"]):
                raise AssetError("unrecorded ordinary deletion")
        else:
            prefix = f".{target.name}.agent-assets-ordinary-"
            if (plan["action"] != "converge" or not isinstance(step["stage"], str)
                    or not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{24}", step["stage"])):
                raise AssetError("invalid ordinary staging name")
        if (step["before"]["kind"] not in ({"absent", "link"} if step["kind"] == "link" else {"absent", "file"})
                or (step["kind"] == "link" and step["before"]["kind"] == "link"
                    and value["links"].get(str(target)) != step["before"]["source"])):
            raise AssetError("ordinary preimage is not recorded ownership")
    for item in plan["preserved"]:
        object_fields(item, {"kind", "target", "before"})
        if not isinstance(item["target"], str):
            raise AssetError("invalid preserved target")
        target = str(absolute_path(item["target"]))
        if (plan["action"] != "remove" or target in targets or not isinstance(item["kind"], str)
                or item["kind"] not in {"link", "launcher"}
                or (item["kind"] == "link" and target not in value["links"])
                or (item["kind"] == "launcher" and target != value["launcher"])):
            raise AssetError("invalid preserved removal target")
        targets.add(target)
        validate_operation_observation(item["before"])
    cursor = record["cursor"]
    if type(cursor) is not int or not 0 <= cursor <= len(plan["steps"]):
        raise AssetError("invalid ordinary operation cursor")
    if record["prepared"] is not None and (
            cursor == len(plan["steps"]) or plan["steps"][cursor]["after"] is None
            or not isinstance(record["prepared"], str) or not HASH_RE.fullmatch(record["prepared"])):
        raise AssetError("invalid ordinary prepared identity")
    if value["handoff"] is not None and value["handoff"]["state"] == "pending":
        raise AssetError("handoff and ordinary operations cannot both be pending")


def admit_operation(base: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int]:
    initial = operation_checkpoint(base, plan)
    complete = operation_complete(base, plan)
    # Only cursor decimal width and null/64-character identity vary. These
    # schema-valid maxima cover every write, without N copies of a 16 MiB plan.
    # The capacity placeholder is never written as a prepared identity.
    samples = [initial, complete, operation_checkpoint(base, plan, len(plan["steps"]))]
    replacements = [index for index, step in enumerate(plan["steps"]) if step["after"] is not None]
    if replacements:
        samples.append(operation_checkpoint(base, plan, replacements[-1], "0" * 64))
    maximum = 0
    for checkpoint in samples:
        validate_ownership(checkpoint)
        maximum = max(maximum, len(checkpoint_bytes(checkpoint)))
    return initial, complete, maximum


def validate_handoff_record(record: Any) -> None:
    object_fields(record, {"version", "state", "plan_sha256", "plan"})
    if (type(record["version"]) is not int or record["version"] != 1 or
            not isinstance(record["state"], str) or record["state"] not in {"pending", "complete"}):
        raise AssetError("unsupported handoff record")
    if not isinstance(record["plan_sha256"], str) or not HASH_RE.fullmatch(record["plan_sha256"]):
        raise AssetError("invalid handoff plan fingerprint")
    plan = record["plan"]
    object_fields(plan, {"manifest_inputs", "selection_sha256", "prior_ownership", "contract",
                         "links", "adapter_links", "sources", "untouched_sha256"})
    if record["plan_sha256"] != digest_json(plan):
        raise AssetError("handoff plan digest mismatch")
    for field in ("selection_sha256", "untouched_sha256"):
        if not isinstance(plan[field], str) or not HASH_RE.fullmatch(plan[field]):
            raise AssetError("invalid handoff fingerprint")
    prior = plan["prior_ownership"]
    # Preparation promotes the ledger without transferring protected ownership.
    # No nesting of handoffs or pending ordinary operations is admissible here.
    if not isinstance(prior, dict) or prior.get("handoff") is not None or "ordinary_operation" in prior:
        raise AssetError("handoff prior ownership must have no transfer or pending operation")
    validate_ownership(prior)
    object_fields(plan["contract"], {"group", "name", "version"})
    for field in ("group", "name"):
        names([plan["contract"][field]])
    if type(plan["contract"]["version"]) is not int or plan["contract"]["version"] != 1:
        raise AssetError("invalid recorded contract")
    targets = []
    for field in ("links", "adapter_links"):
        records = plan[field]
        if not isinstance(records, list) or len(records) != 2:
            raise AssetError("handoff must bind two harnesses")
        for harness, item in zip(HARNESS_PAIR, records):
            object_fields(item, {"harness", "target", "old_source", "new_source"})
            if item["harness"] != harness:
                raise AssetError("invalid handoff harness order")
            for key in ("target", "new_source"):
                if not isinstance(item[key], str):
                    raise AssetError("invalid handoff path")
                absolute_path(item[key])
            if item["old_source"] is not None or field == "links":
                if not isinstance(item["old_source"], str):
                    raise AssetError("invalid predecessor path")
                absolute_path(item["old_source"])
            targets.append(item["target"])
    if len(set(targets)) != 4:
        raise AssetError("handoff destinations overlap")
    if not isinstance(plan["manifest_inputs"], list) or not plan["manifest_inputs"]:
        raise AssetError("missing manifest bindings")
    for item in plan["manifest_inputs"]:
        object_fields(item, {"path", "sha256"})
        absolute_path(item["path"])
        if not isinstance(item["sha256"], str) or not HASH_RE.fullmatch(item["sha256"]):
            raise AssetError("invalid manifest fingerprint")
    if not isinstance(plan["sources"], list) or len(plan["sources"]) != 3:
        raise AssetError("handoff must bind three source inventories")
    for source in plan["sources"]:
        object_fields(source, {"path", "inventory"})
        absolute_path(source["path"])
        if not isinstance(source["inventory"], list) or not source["inventory"]:
            raise AssetError("invalid recorded source inventory")
        seen = set()
        for entry in source["inventory"]:
            if not isinstance(entry, dict):
                raise AssetError("invalid inventory entry")
            kind = entry.get("type")
            object_fields(entry, {"path", "type"} | ({"sha256", "executable"} if kind == "file" else set()))
            path = entry["path"]
            if not isinstance(path, str) or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")) or path in seen:
                raise AssetError("invalid inventory relative path")
            seen.add(path)
            if kind == "file":
                if not isinstance(entry["sha256"], str) or not HASH_RE.fullmatch(entry["sha256"]) or type(entry["executable"]) is not bool:
                    raise AssetError("invalid file inventory")
            elif kind != "directory":
                raise AssetError("invalid inventory type")
    if len({source["path"] for source in plan["sources"]}) != 3:
        raise AssetError("handoff source identities must be distinct")
    for item in plan["links"]:
        if (item["old_source"] != plan["sources"][0]["path"] or item["new_source"] != plan["sources"][1]["path"]
                or plan["prior_ownership"]["links"].get(item["target"]) != item["old_source"]
                or Path(item["target"]).name != plan["contract"]["name"]):
            raise AssetError("handoff predecessor binding mismatch")
    for item in plan["adapter_links"]:
        if (item["new_source"] != plan["sources"][2]["path"] or item["old_source"] not in {None, item["new_source"]}
                or plan["prior_ownership"]["links"].get(item["target"]) != item["old_source"]):
            raise AssetError("handoff adapter binding mismatch")


def validate_ownership(value: Any, *, legacy_only: bool = False) -> None:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in ({1} if legacy_only else {1, 2}):
        raise AssetError("unsupported ownership version")
    if not isinstance(value.get("links"), dict):
        raise AssetError("ownership links must be an object")
    for target, source in value["links"].items():
        if not isinstance(target, str) or not isinstance(source, str):
            raise AssetError("invalid ownership link")
        absolute_path(target)
        absolute_path(source)
    if "manifests" in value and (not isinstance(value["manifests"], list) or any(not isinstance(item, str) for item in value["manifests"])):
        raise AssetError("invalid recorded manifests")
    if "launcher" in value:
        absolute_path(value["launcher"])
    if value["version"] == 2:
        object_fields(value, {"version", "manifests", "links", "launcher", "handoff"}, {"ordinary_operation"})
        for path in value["manifests"]:
            absolute_path(path)
        if value["handoff"] is not None:
            validate_handoff_record(value["handoff"])
            if value["handoff"]["state"] == "pending" and value["links"] != value["handoff"]["plan"]["prior_ownership"]["links"]:
                raise AssetError("pending ownership provenance differs from plan")
        if "ordinary_operation" in value:
            validate_ordinary_operation(value)


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value[1:-1].replace("''", "'")
    return value


def frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    """Parse the small YAML subset used by skill metadata without PyYAML."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return {}, [f"cannot read {path}: {error}"]
    if not lines or lines[0].strip() != "---":
        return {}, [f"missing YAML frontmatter in {path}"]
    try:
        end = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration:
        return {}, [f"unterminated YAML frontmatter in {path}"]

    metadata: dict[str, str] = {}
    issues: list[str] = []
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace():
            # Nested metadata belongs to keys the doctor does not interpret.
            index += 1
            continue
        if ":" not in line:
            issues.append(f"unsupported frontmatter line {index + 1} in {path}")
            index += 1
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if raw in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < end and (
                not lines[index].strip() or lines[index][:1].isspace()
            ):
                block.append(lines[index].strip())
                index += 1
            metadata[key] = " ".join(part for part in block if part)
            continue
        metadata[key] = unquote_yaml_scalar(raw)
        index += 1
    return metadata, issues


def issue(severity: str, code: str, message: str, **context: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    result.update(context)
    return result


def transition_issue(error: Exception) -> dict[str, Any]:
    if isinstance(error, OwnershipDriftError):
        return issue("error", "ownership-drift", str(error), target=error.target, state=error.state)
    return issue("error", "handoff-preflight", str(error))


class AgentAssets:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        script = Path(__file__).resolve()
        self.env_dir = Path(
            os.environ.get("ENVY_DIR", script.parent.parent)
        ).resolve()
        config_home = paths.xdg_config_home()
        state_home = Path(
            paths.xdg_state_home()
        )
        self.state_path = Path(
            args.state
            or os.environ.get(
                "ENVY_STATE_FILE", config_home / "envy" / "state.json"
            )
        ).expanduser()
        ownership_selection = args.ownership or os.environ.get("ENVY_AGENT_ASSETS_OWNERSHIP")
        if ownership_selection is None:
            # Validate the selected raw base before Path can erase dot/empty components.
            absolute_path(os.environ.get("XDG_STATE_HOME") or os.environ.get("HOME") or str(Path.home()))
            ownership_selection = state_home / "envy" / "agent-assets.json"
        self.ownership_path = absolute_path(ownership_selection)
        self.launcher = Path.home() / ".local" / "bin" / "agent-assets"
        self.reject_control_paths([self.launcher])
        self.manifest_paths: list[Path] = []
        self.manifest: dict[str, Any] = {}
        self.issues: list[dict[str, Any]] = []
        self.state: dict[str, Any] | None = None
        self.ownership = self._load_ownership()
        self.groups: list[dict[str, Any]] = []
        self.all_assets: list[Asset] = []
        self.expected: list[Asset] = []
        self.source_roots: set[Path] = set()
        self.destinations: dict[str, dict[str, Path]] = {}
        # A pending operation is validated ledger state, not a new selection
        # to discover. Only its recorded action may proceed to resume checks.
        operation = self.ordinary_record()
        recorded_payloads = {Path(target) for target in self.ownership["links"]}
        recorded_targets = set(recorded_payloads)
        if self.ownership.get("launcher"):
            recorded_targets.add(Path(self.ownership["launcher"]))
        if operation:
            recorded_targets.update(Path(item["target"]) for item in
                                    operation["plan"]["steps"] + operation["plan"]["preserved"])
            recorded_targets.update(Path(step["target"]).with_name(step["stage"])
                                    for step in operation["plan"]["steps"] if step["stage"] is not None)
            recorded_payloads.update(Path(item["target"]) for item in
                                     operation["plan"]["steps"] + operation["plan"]["preserved"]
                                     if item["kind"] == "link")
            recorded_payloads.update(Path(step["target"]).with_name(step["stage"])
                                     for step in operation["plan"]["steps"]
                                     if step["kind"] == "link" and step["stage"] is not None)
        handoff = self.handoff_record()
        if handoff:
            protected = {Path(item["target"]) for item in
                         handoff["plan"]["links"] + handoff["plan"]["adapter_links"]}
            recorded_targets.update(protected)
            recorded_payloads.update(protected)
        # Ledger-only removal and pending-operation refusal must not depend on
        # current manifests or source discovery. Never place a control in a
        # payload namespace, even when that payload has not been written yet.
        self.reject_control_paths(recorded_targets)
        # The launcher's own typed ledger entry is not an asset collision;
        # a link or retained protected target at that same path still is.
        self.reject_launcher_paths(recorded_payloads)
        self.recorded_payloads = recorded_payloads
        # Even manifest-free removal must not mutate two recorded spellings of
        # one leaf, or a recorded leaf containing another pending payload.
        self.reject_payload_namespace(recorded_payloads, ())
        if operation and args.action != operation["plan"]["action"]:
            raise AssetError(self.ordinary_pending_message())
        # Removal chooses the validated store first; v2 never needs current discovery.
        if args.action != "remove":
            self._load_selection()

    def reject_control_paths(self, targets: Iterable[Path], *, directories: Iterable[Path] = ()) -> None:
        # Compare lexical component keys without inspecting payload referents.
        # Unix descriptor walks separately reject symlinked ancestors.
        leaves = {Path(os.path.abspath(path)) for path in targets}
        parents = {Path(os.path.abspath(path)) for path in directories}
        for control in (self.ownership_path, self.ownership_path.with_name(self.ownership_path.name + ".lock")):
            control = Path(os.path.abspath(control))
            control_key = namespace_key(control)
            for target in sorted(leaves | parents, key=str):
                target_key = namespace_key(target)
                if (control_key == target_key[:len(control_key)]
                        or (target in leaves and target_key == control_key[:len(target_key)])):
                    raise AssetError(f"ownership control path overlaps managed destination: {control} and {target}; "
                                     "select a disjoint ownership path and lock sidecar; preserve existing evidence")

    def reject_launcher_paths(self, targets: Iterable[Path]) -> None:
        # A generated file cannot also be an asset or a required destination
        # directory, including either ancestry direction. Do not follow links.
        launcher = Path(os.path.abspath(self.launcher))
        launcher_key = namespace_key(launcher)
        for target in sorted({Path(os.path.abspath(path)) for path in targets}, key=str):
            target_key = namespace_key(target)
            if (launcher_key == target_key[:len(launcher_key)]
                    or target_key == launcher_key[:len(target_key)]):
                raise AssetError(f"launcher path overlaps managed asset destination: {launcher} and {target}; "
                                 "select disjoint asset destinations; preserve existing evidence")

    def reject_asset_namespace(self, directories: Iterable[Path]) -> None:
        # Equal raw leaves may be shared only when all selected claims agree
        # on the already-resolved source, including claims across harnesses.
        # Existing selection errors already block convergence; retain their
        # provenance diagnostics and the recorded v1 removal path.
        if not self.selected_source_errors():
            source_by_target: dict[Path, Path] = {}
            for asset in self.expected:
                target = Path(os.path.abspath(asset.target))
                previous = source_by_target.get(target)
                if previous is not None and previous != asset.source:
                    raise AssetError(f"managed asset destination has conflicting sources: {target}; "
                                     "select one source per asset leaf; preserve existing evidence")
                source_by_target[target] = asset.source
        # A managed leaf will be a symlink, never a parent directory. Preserve
        # existing handoff restrictions on otherwise valid sharing.
        self.reject_payload_namespace(
            {asset.target for asset in self.expected} | self.protected_targets() | self.recorded_payloads,
            directories)

    @staticmethod
    def reject_payload_namespace(payloads: Iterable[Path], directories: Iterable[Path]) -> None:
        leaves = {Path(os.path.abspath(path)) for path in payloads}
        roots = {Path(os.path.abspath(path)) for path in directories}
        keyed_leaves: dict[tuple[str, ...], Path] = {}
        for leaf in sorted(leaves, key=str):
            key = namespace_key(leaf)
            if key in keyed_leaves:
                raise AssetError(f"managed asset destinations overlap: {keyed_leaves[key]} and {leaf}; "
                                 "select disjoint asset leaves; preserve existing evidence")
            keyed_leaves[key] = leaf
        for key, leaf in keyed_leaves.items():
            for length in range(1, len(key)):
                if key[:length] in keyed_leaves:
                    raise AssetError(f"managed asset destinations overlap: {keyed_leaves[key[:length]]} and {leaf}; "
                                     "select disjoint asset leaves; preserve existing evidence")
        for key, leaf in keyed_leaves.items():
            for directory in sorted(roots, key=str):
                directory_key = namespace_key(directory)
                if key == directory_key[:len(key)]:
                    raise AssetError(f"managed asset destination overlaps required directory: {leaf} and {directory}; "
                                     "keep required directories outside asset leaves; preserve existing evidence")

    def _load_selection(self) -> None:
        self.manifest_paths = configured_manifests(self.args)
        self.manifest_path = self.manifest_paths[0]
        self.manifest = compose_manifests(self.manifest_paths)
        if self.state_path.is_file():
            self.state = load_json(self.state_path, "envy state")
        self.source_roots = {path.parent for path in self.manifest_paths}
        self.destinations = self._destinations()
        self.platform = str(
            os.environ.get("ENVY_PLATFORM")
            or (self.state or {}).get("platform")
            or self._detect_platform()
        )
        # A control cannot replace a required destination directory. A regular
        # sibling inside it is harmless unless it overlaps an actual leaf.
        destination_roots = {path for definitions in self.destinations.values() for path in definitions.values()}
        self.reject_control_paths(self.protected_targets(), directories=destination_roots)
        self.reject_launcher_paths(destination_roots | self.protected_targets())
        self._load_assets()
        self.reject_control_paths(asset.target for asset in self.expected)
        self.reject_launcher_paths(asset.target for asset in self.expected)
        self.reject_asset_namespace(destination_roots)

    def _load_ownership(self) -> dict[str, Any]:
        self.ownership_parent_inspection: dict[str, Any] = {}
        try:
            if fcntl is None:
                if not self.ownership_path.exists():
                    self.ownership_present = False
                    return {"version": 1, "links": {}}
                value = load_json(self.ownership_path, "agent asset ownership")
                info = self.ownership_path.stat()
            else:
                data, info = regular_bytes(self.ownership_path, inspection=self.ownership_parent_inspection)
                value = json.loads(data, object_pairs_hook=unique_object)
            validate_ownership(value)
            if value["version"] == 2 and fcntl is None:
                raise AssetError("v2 ownership requires Unix locking")
            if value["version"] == 2 and stat.S_IMODE(info.st_mode) != 0o600:
                raise AssetError("v2 ownership must have mode 0600")
            self.ownership_present = True
            return value
        except FileNotFoundError:
            self.ownership_present = False
            return {"version": 1, "links": {}}
        except (AssetError, OSError, ValueError) as error:
            self.ownership_present = True
            self.issues.append(issue("error", "ownership-invalid", str(error)))
            return {"version": 1, "links": {}}

    @staticmethod
    def _detect_platform() -> str:
        if sys.platform == "darwin":
            return "darwin"
        if sys.platform.startswith("linux"):
            return "linux"
        if os.name == "nt":
            return "windows"
        return sys.platform

    def _destinations(self) -> dict[str, dict[str, Path]]:
        raw = self.manifest.get("destinations")
        if not isinstance(raw, dict) or not raw:
            raise AssetError("agent asset manifest requires destinations")
        result: dict[str, dict[str, Path]] = {}
        for harness, definitions in raw.items():
            if not isinstance(definitions, dict):
                raise AssetError(f"destination '{harness}' must be an object")
            result[harness] = {}
            for kind, path in definitions.items():
                if kind not in {"skills", "agents"} or not isinstance(path, str):
                    raise AssetError(f"invalid destination {harness}.{kind}")
                result[harness][kind] = Path(path).expanduser()
        return result

    def feature_enabled(self, name: str) -> bool:
        if self.state is None:
            return False
        feature = self.state.get("features", {}).get(name, {})
        return isinstance(feature, dict) and feature.get("enabled") is True

    def distribution_enabled(self) -> bool:
        name = self.manifest.get("distribution_feature")
        return isinstance(name, str) and self.feature_enabled(name)

    def platform_matches(self, targets: Iterable[str]) -> bool:
        override = os.environ.get("ENVY_PLATFORM_HIERARCHY_OVERRIDE", "").split()
        hierarchy = tuple(override) or PLATFORM_HIERARCHY.get(
            self.platform, (self.platform, "global")
        )
        return any(target in hierarchy for target in targets)

    def group_active(self, group: dict[str, Any]) -> tuple[bool, str]:
        if not self.distribution_enabled():
            return False, "agent asset distribution feature is disabled"
        platforms = group.get("platforms", ["global"])
        if not isinstance(platforms, list) or not all(
            isinstance(item, str) for item in platforms
        ):
            return False, "invalid platforms"
        if not self.platform_matches(platforms):
            return False, f"platform {self.platform} not selected"
        required = group.get("features", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            return False, "invalid features"
        disabled = [name for name in required if not self.feature_enabled(name)]
        if disabled:
            return False, f"disabled features: {', '.join(disabled)}"
        any_features = group.get("any_features", [])
        if not isinstance(any_features, list) or not all(
            isinstance(item, str) for item in any_features
        ):
            return False, "invalid any_features"
        if any_features and not any(
            self.feature_enabled(name) for name in any_features
        ):
            return False, f"none enabled: {', '.join(any_features)}"
        return True, "selected"

    def _load_assets(self) -> None:
        if self.manifest.get("version") not in {1, 2}:
            raise AssetError("unsupported agent asset manifest version")
        if self.manifest.get("version") == 2 and fcntl is None:
            raise V2CapabilityError("v2 assets require Unix locking")
        for kind, key in (("skill", "skill_groups"), ("agent", "agent_groups")):
            definitions = self.manifest.get(key, [])
            if not isinstance(definitions, list):
                raise AssetError(f"{key} must be an array")
            for definition in definitions:
                self._load_group(kind, definition)
        self._check_source_duplicates()

    def _load_group(self, kind: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise AssetError(f"{kind} group must be an object")
        name = definition.get("name")
        rel_path = definition.get("path")
        targets = definition.get("targets")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise AssetError(f"invalid {kind} group name: {name!r}")
        if (
            not isinstance(rel_path, str)
            or not isinstance(targets, list)
            or not targets
        ):
            raise AssetError(f"group '{name}' requires path and targets")
        active, reason = self.group_active(definition)
        group_info = {
            "kind": kind,
            "name": name,
            "path": str(Path(rel_path).expanduser().resolve()),
            "targets": targets,
            "selected": active,
            "reason": reason,
        }
        self.groups.append(group_info)
        source_root = Path(rel_path).expanduser().resolve()
        if definition.get("_version") == 2:
            source_root = absolute_path(rel_path)
        self.source_roots.add(source_root)
        if not source_root.is_dir():
            self.issues.append(
                issue(
                    "error" if active else "warning",
                    "source-group-missing",
                    f"source group does not exist: {source_root}",
                    group=name,
                )
            )
            return
        unknown_targets = [
            target for target in targets if target not in self.destinations
        ]
        if unknown_targets:
            self.issues.append(
                issue(
                    "error",
                    "target-unknown",
                    f"group '{name}' has unknown targets: {', '.join(unknown_targets)}",
                    group=name,
                )
            )
            return

        if kind == "skill":
            candidates = sorted(path for path in source_root.iterdir() if path.is_dir())
        else:
            candidates = sorted(
                path
                for path in source_root.glob("*.md")
                if path.name.lower() != "readme.md"
            )
        for source in candidates:
            if kind == "skill" and source.name in definition.get("exclude_names", []):
                continue
            if definition.get("_version") == 2:
                with parent_descriptor(source / "SKILL.md" if kind == "skill" else source):
                    pass
                if source.is_symlink():
                    raise AssetError("v2 source must not be a symlink")
            metadata_path = source / "SKILL.md" if kind == "skill" else source
            asset_name = source.name if kind == "skill" else source.stem
            if definition.get("_version") == 2 and metadata_path.exists():
                with regular_descriptor(metadata_path, control=False):
                    pass
            if kind == "skill" and not metadata_path.is_file():
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "skill-definition-missing",
                        f"skill directory has no SKILL.md: {source}",
                        group=name,
                        name=asset_name,
                    )
                )
                continue
            metadata, parse_issues = frontmatter(metadata_path)
            for message in parse_issues:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "frontmatter-invalid",
                        message,
                        group=name,
                        name=asset_name,
                    )
                )
            canonical_name = metadata.get("name")
            description = metadata.get("description")
            if canonical_name != asset_name:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "name-mismatch",
                        f"metadata name {canonical_name!r} does not match {asset_name!r}",
                        group=name,
                        name=asset_name,
                    )
                )
            if not description:
                self.issues.append(
                    issue(
                        "error" if active else "warning",
                        "description-missing",
                        f"description is missing: {metadata_path}",
                        group=name,
                        name=asset_name,
                    )
                )
            if kind == "skill":
                extras = sorted(set(metadata) - {"name", "description"})
                if extras:
                    self.issues.append(
                        issue(
                            "warning",
                            "frontmatter-extra",
                            f"skill has noncanonical metadata fields: {', '.join(extras)}",
                            group=name,
                            name=asset_name,
                        )
                    )
            for harness in targets:
                destination_kind = "skills" if kind == "skill" else "agents"
                destination = self.destinations[harness].get(destination_kind)
                if destination is None:
                    self.issues.append(
                        issue(
                            "error",
                            "target-kind-unsupported",
                            f"target '{harness}' has no {destination_kind} destination",
                            group=name,
                            name=asset_name,
                        )
                    )
                    continue
                target_name = asset_name if kind == "skill" else source.name
                asset = Asset(
                    kind,
                    asset_name,
                    source.resolve(),
                    name,
                    harness,
                    destination / target_name,
                )
                self.all_assets.append(asset)
                if active:
                    self.expected.append(asset)

    def _check_source_duplicates(self) -> None:
        by_identity: dict[tuple[str, str, str], list[Asset]] = {}
        for asset in self.all_assets:
            by_identity.setdefault((asset.kind, asset.harness, asset.name), []).append(
                asset
            )
        for (kind, harness, name), assets in sorted(by_identity.items()):
            sources = sorted({str(asset.source) for asset in assets})
            if len(sources) < 2:
                continue
            selected = [asset for asset in self.expected if asset in assets]
            self.issues.append(
                issue(
                    "error" if len(selected) > 1 else "warning",
                    "source-duplicate",
                    f"{kind} '{name}' is defined more than once for {harness}",
                    name=name,
                    harness=harness,
                    sources=sources,
                )
            )

    def selected_source_errors(self) -> list[dict[str, Any]]:
        selected_groups = {group["name"] for group in self.groups if group["selected"]}
        return [
            item
            for item in self.issues
            if item["severity"] == "error"
            and (not item.get("group") or item.get("group") in selected_groups)
        ]

    def previous_links(self) -> dict[Path, Path]:
        raw = self.ownership.get("links", {})
        result: dict[Path, Path] = {}
        if isinstance(raw, dict):
            for target, source in raw.items():
                if isinstance(target, str) and isinstance(source, str):
                    result[Path(target)] = Path(source)
        return result

    def strict_ownership(self) -> bool:
        return self.manifest.get("version") == 2 or self.ownership.get("version") == 2

    def handoff_record(self) -> dict[str, Any] | None:
        return self.ownership.get("handoff")

    def assets_for(self, group: str, name: str) -> list[Asset]:
        return sorted((asset for asset in self.expected if asset.kind == "skill"
                       and asset.group == group and asset.name == name), key=lambda asset: asset.harness)

    def handoff_context(self) -> dict[str, Any] | None:
        declarations = self.manifest.get("skill_handoffs", [])
        if not declarations:
            return None
        declaration = declarations[0]
        group = next(group for group in self.manifest["skill_groups"] if group["name"] == declaration["group"])
        if not self.group_active(group)[0] or set(group["targets"]) != set(HARNESS_PAIR):
            raise AssetError("handoff group must select both supported harnesses")
        contract = {"group": declaration["replacement"]["group"], "name": declaration["name"],
                    "version": declaration["replacement"]["version"]}
        if contract not in self.manifest.get("skill_contracts", []):
            raise AssetError("handoff selected contract is missing")
        assets = self.assets_for(contract["group"], contract["name"])
        adapters = self.assets_for(group["name"], declaration["adapter"])
        for selected in (assets, adapters):
            if [asset.harness for asset in selected] != list(HARNESS_PAIR) or len({asset.source for asset in selected}) != 1:
                raise AssetError("handoff provider and adapter must select both harnesses")
        old_source = absolute_path(Path(group["path"]) / declaration["name"])
        targets = [asset.target for asset in assets + adapters]
        if len(set(targets)) != 4 or any(is_within(left, right) for left in targets for right in targets if left != right):
            raise AssetError("handoff target paths overlap")
        return {"declaration": declaration, "contract": contract, "old_source": old_source,
                "assets": assets, "adapters": adapters}

    def completed_selection(self, record: dict[str, Any]) -> None:
        if self.manifest["version"] != 2:
            raise AssetError(f"ownership-v2-selection-incompatible: {self.ownership_path} requires a compatible "
                             "reviewed v2 selection; restore that selection, do not delete/rewrite the ledger to downgrade")
        plan = record["plan"]
        if plan["contract"] not in self.manifest.get("skill_contracts", []):
            raise AssetError("completed handoff requires its selected public contract")
        expected = {str(asset.target): asset for asset in self.expected}
        for item in plan["links"] + plan["adapter_links"]:
            asset = expected.get(item["target"])
            if asset is None or str(asset.source) != item["new_source"] or asset.harness != item["harness"]:
                raise AssetError("completed handoff provider or adapter is absent or incompatible; retained "
                                 "handoff provenance pins canonical source paths even after removal; restore the "
                                 "reviewed source/installer prefix, do not rewrite the ledger for relocation")
        previous = self.ownership["links"]
        for pair in (plan["links"], plan["adapter_links"]):
            fresh = all(item["target"] not in previous and link_observation(Path(item["target"])).get("absent")
                        for item in pair)
            current = all(previous.get(item["target"]) == item["new_source"]
                          and exact_link(Path(item["target"]), item["new_source"]) for item in pair)
            if not fresh and not current:
                raise AssetError("completed handoff targets require wholly fresh or exactly owned pairs")
        selected = self.assets_for(plan["contract"]["group"], plan["contract"]["name"])
        if [str(asset.target) for asset in selected] != [item["target"] for item in plan["links"]]:
            raise AssetError("completed provider identity changed")
        context = self.handoff_context()
        if context is not None:
            if (context["contract"] != plan["contract"] or
                    str(context["old_source"]) != plan["sources"][0]["path"] or
                    [(str(asset.target), str(asset.source)) for asset in context["adapters"]] !=
                    [(item["target"], item["new_source"]) for item in plan["adapter_links"]] or
                    context["declaration"]["legacy_files"] != {
                        entry["path"]: entry["sha256"] for entry in plan["sources"][0]["inventory"]
                        if entry["type"] == "file"}):
                raise AssetError("handoff declaration differs from retained provenance")

    def untouched_digest(self, participating: set[Path]) -> str:
        excluded = participating | {self.ownership_path, self.ownership_path.with_name(self.ownership_path.name + ".lock")}
        mutation_parents = {path.parent for path in participating} | {self.ownership_path.parent}
        targets = set(self.previous_links()) | {asset.target for asset in self.expected}
        roots = {self.ownership_path.parent}
        for definitions in self.destinations.values():
            roots.update(definitions.values())
        for root in roots:
            with parent_descriptor(root / "entry"):
                pass
            targets.update(root.iterdir())
        result = {}
        for path in sorted(targets - excluded, key=str):
            if path in mutation_parents:
                # A scanned root can itself be another root's child. Its own
                # authorized entry writes change size/mtime, not identity;
                # unrelated children remain separately enumerated and observed.
                with parent_descriptor(path / "entry") as directory:
                    result[str(path)] = {"directory": directory_identity(os.fstat(directory))}
            else:
                result[str(path)] = link_observation(path)
        result["launcher"] = {"path": str(self.launcher), "sha256": sha256(self.launcher)}
        return digest_json(result)

    def handoff_sources(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        old_source = context["old_source"]
        old_inventory = source_inventory(old_source)
        if (len(old_inventory) != 1 or old_inventory[0]["type"] != "file" or
                {old_inventory[0]["path"]: old_inventory[0]["sha256"]} != context["declaration"]["legacy_files"]):
            raise AssetError("predecessor inventory does not match the declared release")
        return [{"path": str(old_source), "inventory": old_inventory}] + [
            {"path": str(source), "inventory": source_inventory(source)}
            for source in (context["assets"][0].source, context["adapters"][0].source)]

    def make_handoff_plan(self, context: dict[str, Any], prior: dict[str, Any], *,
                          preparing: bool = False) -> dict[str, Any]:
        assets, adapters = context["assets"], context["adapters"]
        participating = {asset.target for asset in assets + adapters}
        previous = {Path(target): source for target, source in prior["links"].items()}
        expected = {asset.target: asset for asset in self.expected}
        for target, asset in expected.items():
            if not preparing and target not in participating and (previous.get(target) != str(asset.source) or not exact_link(target, asset.source)):
                raise AssetError(f"ordinary target must converge before handoff: {target}")
        if not preparing and any(target not in expected for target in previous if target not in participating):
            raise AssetError("unrelated recorded stale target blocks handoff")
        for definitions in self.destinations.values():
            for root in definitions.values():
                with parent_descriptor(root / "entry"):
                    pass
                for path in root.iterdir():
                    if path in participating or path in previous or not path.is_symlink():
                        continue
                    if any(is_within(lexical_target(path), source) for source in self.source_roots):
                        raise AssetError("unrecorded would-be managed target blocks handoff")
        if self.launcher_status() != "current":
            raise AssetError("launcher must already be current before handoff")
        old_source = context["old_source"]
        sources = self.handoff_sources(context)
        inputs = []
        for path in self.manifest_paths:
            data, _ = regular_bytes(path, control=False)
            inputs.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
        selection = {"destinations": {h: {k: str(v) for k, v in definitions.items()}
                                       for h, definitions in self.destinations.items()},
                     "platform": self.platform, "hierarchy": os.environ.get("ENVY_PLATFORM_HIERARCHY_OVERRIDE", ""),
                     "groups": self.groups, "features": (self.state or {}).get("features", {}),
                     "expected": [asset.as_dict() for asset in sorted(self.expected, key=lambda a: str(a.target))],
                     "launcher": {"path": str(self.launcher), "sha256": hashlib.sha256(self.launcher_content().encode()).hexdigest()},
                     "contract": context["contract"], "handoff": context["declaration"]}
        return {"manifest_inputs": inputs, "selection_sha256": digest_json(selection),
                "prior_ownership": prior, "contract": context["contract"],
                "links": [{"harness": asset.harness, "target": str(asset.target),
                           "old_source": str(old_source), "new_source": str(asset.source)} for asset in assets],
                "adapter_links": [{"harness": asset.harness, "target": str(asset.target),
                                   "old_source": previous.get(asset.target), "new_source": str(asset.source)} for asset in adapters],
                "sources": sources, "untouched_sha256": self.untouched_digest(participating)}

    def check_pending(self, record: dict[str, Any], context: dict[str, Any] | None) -> None:
        if context is None:
            raise AssetError("pending handoff declaration is missing")
        current = AgentAssets(self.args)
        if current.ownership_path != self.ownership_path or current.handoff_record() != record:
            raise AssetError("pending ownership changed during operation")
        context = current.handoff_context()
        if context is None:
            raise AssetError("pending handoff declaration is missing")
        plan = record["plan"]
        computed = current.make_handoff_plan(context, plan["prior_ownership"])
        if digest_json(computed) != record["plan_sha256"]:
            raise AssetError("pending handoff plan, source, or unrelated state changed")
        for item in plan["links"] + plan["adapter_links"]:
            actual = link_observation(Path(item["target"]))
            allowed = {item["new_source"]}
            if item["old_source"] is not None:
                allowed.add(item["old_source"])
            if not (actual.get("link") in allowed or (item["old_source"] is None and actual.get("absent"))):
                raise AssetError("pending target is missing or in an unrecorded state")

    def protected_targets(self) -> set[Path]:
        protected = {(contract["group"], contract["name"])
                     for contract in self.manifest.get("skill_contracts", [])}
        protected.update((handoff["group"], handoff["adapter"])
                         for handoff in self.manifest.get("skill_handoffs", []))
        groups = {group["name"]: group for group in self.manifest.get("skill_groups", [])}
        targets = set()
        for group_name, name in protected:
            group = groups[group_name]
            if not self.group_active(group)[0]:
                continue
            # A declared destination stays protected when source discovery fails.
            for harness in group["targets"]:
                destination = self.destinations.get(harness, {}).get("skills")
                if destination is not None:
                    targets.add(destination / name)
        record = self.handoff_record()
        if record:
            targets.update(Path(item["target"]) for item in
                           record["plan"]["links"] + record["plan"]["adapter_links"])
        return targets

    def reject_protected_staging(self) -> None:
        prefixes: dict[Path, set[str]] = {}
        for target in self.protected_targets():
            prefixes.setdefault(target.parent, set()).add(f".{target.name}.agent-assets-")
        for parent, names in sorted(prefixes.items(), key=lambda item: str(item[0])):
            try:
                with parent_descriptor(parent / "entry") as directory:
                    for name in sorted(os.listdir(directory)):
                        if name.startswith(tuple(names)):
                            # The reserved name is ambiguous evidence, not ownership.
                            # Do not follow, adopt, or clean up an unexplained entry.
                            raise AssetError(f"unexplained staging entry requires reconciliation: {parent / name}")
            except FileNotFoundError:
                continue

    def transition_preflight(self) -> tuple[str, dict[str, Any] | None]:
        record = self.handoff_record()
        if self.ownership["version"] == 2 and self.manifest["version"] != 2:
            raise AssetError(f"ownership-v2-selection-incompatible: {self.ownership_path} requires a compatible "
                             "reviewed v2 selection; restore that selection, do not delete/rewrite the ledger to downgrade")
        self.reject_protected_staging()
        context = self.handoff_context()
        if record and record["state"] == "complete":
            self.completed_selection(record)
        if record and record["state"] == "pending":
            self.check_pending(record, context)
            return "pending", record["plan"]
        previous = self.previous_links()
        mode = "ordinary"
        plan = None
        for contract in self.manifest.get("skill_contracts", []):
            assets = self.assets_for(contract["group"], contract["name"])
            group = next(group for group in self.manifest["skill_groups"] if group["name"] == contract["group"])
            if not self.group_active(group)[0]:
                continue
            if {asset.harness for asset in assets} != set(group["targets"]):
                raise AssetError("declared contract has no selected provider")
            if all(asset.target not in previous and link_observation(asset.target).get("absent") for asset in assets):
                continue
            if all(previous.get(asset.target) == asset.source and exact_link(asset.target, asset.source) for asset in assets):
                continue
            if (context is not None and assets == context["assets"] and not record
                    and all(previous.get(asset.target) == context["old_source"] and exact_link(asset.target, context["old_source"]) for asset in assets)):
                mode = "legacy"
            else:
                unrecorded = [str(asset.target) for asset in assets if asset.target not in previous
                              and not link_observation(asset.target).get("absent")]
                if unrecorded:
                    raise AssetError("protected skill requires a fresh or exactly owned complete pair; "
                                     f"unrecorded targets for {contract['group']}/{contract['name']}: {', '.join(unrecorded)}. "
                                     "These may be interrupted fresh-installation or foreign entries; managed remove "
                                     "preserves unrecorded entries. Preserve target/staging evidence and reconcile "
                                     "provenance before separately authorized cleanup; do not infer ownership or "
                                     "delete/rewrite the ledger.")
                raise AssetError("protected skill requires a fresh or exactly owned complete pair; "
                                 f"contract {contract['group']}/{contract['name']} at "
                                 f"{', '.join(str(asset.target) for asset in assets)}; "
                                 "source relocation is not automatic and deleting links alone does not clear ownership. "
                                 "Restore trusted installer/sources at the recorded canonical prefix where supported; "
                                 "do not delete/rewrite the ledger or adopt a foreign launcher. Only a nonpending "
                                 "null-handoff selection with an unchanged recognizable launcher supports explicitly "
                                 "authorized feature-wide remove then converge; this removes all owned assets and "
                                 "the owned launcher. A moved installer may preserve its old launcher and remove "
                                 "other links partially; inspect remove --dry-run first.")
        if context:
            adapters = context["adapters"]
            fresh = all(asset.target not in previous and link_observation(asset.target).get("absent") for asset in adapters)
            current = all(previous.get(asset.target) == asset.source and exact_link(asset.target, asset.source) for asset in adapters)
            if not fresh and not current:
                raise AssetError("adapter targets must be a fresh or exactly owned pair")
            if mode == "legacy":
                plan = self.make_handoff_plan(context, self.ownership, preparing=True)
        if self.strict_ownership():
            participating = {Path(item["target"]) for item in plan["links"] + plan["adapter_links"]} if plan else set()
            ordinary = {asset.target for asset in self.expected} - self.protected_targets()
            for target, source in previous.items():
                if target in participating:
                    continue
                observed = link_observation(target)
                if observed.get("link") == str(source):
                    continue
                # Only absence is repairable. Legacy/pending transitions, retained
                # pairs, deselected records and every extant foreign entry stay strict.
                if mode == "ordinary" and target in ordinary and observed.get("absent"):
                    continue
                raise OwnershipDriftError(target, bool(observed.get("absent")))
        if mode == "legacy":
            preparation = self.make_handoff_preparation(context, plan)
            if preparation is not None:
                return "legacy-prepare", preparation
        return mode, plan

    def preparation_expected(self, context: dict[str, Any] | None) -> dict[str, str]:
        if context is None or self.handoff_record():
            raise AssetError("handoff preparation requires its untransferred declaration")
        previous = self.ownership["links"]
        for asset in context["assets"]:
            if (previous.get(str(asset.target)) != str(context["old_source"])
                    or not exact_link(asset.target, context["old_source"])):
                raise AssetError(f"handoff preparation predecessor changed: {asset.target}")
        adapters = context["adapters"]
        fresh = all(str(asset.target) not in previous and link_observation(asset.target).get("absent")
                    for asset in adapters)
        current = all(previous.get(str(asset.target)) == str(asset.source) and exact_link(asset.target, asset.source)
                      for asset in adapters)
        if not fresh and not current:
            raise AssetError("handoff preparation adapter pair changed")
        expected = {str(asset.target): str(asset.source) for asset in self.expected}
        for asset in context["assets"] + adapters:
            target = str(asset.target)
            if target in previous:
                expected[target] = previous[target]
            else:
                expected.pop(target, None)
        return expected

    def make_handoff_preparation(self, context: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any] | None:
        base = {"version": 2, "manifests": self.ownership.get("manifests", []),
                "links": dict(self.ownership["links"]), "launcher": str(self.launcher), "handoff": None}
        plan = self.make_ordinary_plan(base, "converge")
        complete = operation_complete(base, plan)
        if complete["links"] != self.preparation_expected(context):
            raise AssetError("handoff preparation cannot install other protected pairs or adopt unrecorded links")
        if not plan["steps"]:
            return None
        if any(step["kind"] != "link" for step in plan["steps"]):
            raise AssetError("handoff preparation cannot update the launcher")
        plan["handoff_preparation"] = digest_json(handoff["sources"])
        admit_operation(base, plan)
        # Admit the subsequent handoff too, before any ordinary mutation. Its
        # unrelated-state fingerprint is recomputed after preparation; its size
        # is fixed. Never persist this prospective pre-preparation snapshot.
        self.handoff_checkpoints({**handoff, "prior_ownership": complete})
        return plan

    def prepare_handoff(self, plan: dict[str, Any]) -> int:
        base = {"version": 2, "manifests": self.ownership.get("manifests", []),
                "links": dict(self.ownership["links"]), "launcher": str(self.launcher), "handoff": None}
        initial = admit_operation(base, plan)[0]
        if self.args.dry_run:
            print(f"Dry run: would prepare {len(plan['steps'])} ordinary link updates before handoff; "
                  "then revalidate and transfer the protected pair")
            for step in plan["steps"]:
                print(f"  {'remove' if step['after'] is None else 'link'}: {step['target']}")
            return 0
        if not self.args.quiet:
            print(f"Preparing {len(plan['steps'])} ordinary link updates before protected handoff")
        self.write_ownership(initial)
        return self.finish_preparation()

    def finish_preparation(self) -> int:
        result = self.advance_ordinary()
        if result or self.args.dry_run:
            return result
        # Still under the same writer lock, rebuild the handoff against the
        # completed ordinary state, including its unrelated-state fingerprint.
        current = AgentAssets(self.args)
        if current.ownership_path != self.ownership_path:
            raise AssetError("ownership path changed after handoff preparation")
        return current._converge_locked()

    def ordinary_record(self) -> dict[str, Any] | None:
        return self.ownership.get("ordinary_operation") if self.ownership["version"] == 2 else None

    def ordinary_pending_message(self) -> str:
        record = self.ordinary_record()
        if record is None:
            return "no pending ordinary operation"
        action = record["plan"]["action"]
        return (f"ordinary {action} is pending; resume agent-assets {action} with the original "
                "ownership path and selection; preserve staging and do not rewrite the ledger")

    def ordinary_selection(self) -> str:
        inputs = []
        for path in self.manifest_paths:
            data, _ = regular_bytes(path, control=False)
            inputs.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
        return digest_json({"inputs": inputs, "platform": self.platform, "groups": self.groups,
                            "features": (self.state or {}).get("features", {}),
                            "hierarchy": os.environ.get("ENVY_PLATFORM_HIERARCHY_OVERRIDE", ""),
                            "expected": [asset.as_dict() for asset in sorted(self.expected, key=lambda item: str(item.target))],
                            "launcher": {"path": str(self.launcher),
                                         "sha256": hashlib.sha256(self.launcher_content().encode()).hexdigest()}})

    @staticmethod
    def ordinary_post(observed: dict[str, Any], step: dict[str, Any]) -> bool:
        if step["after"] is None:
            return observed["kind"] == "absent"
        if step["kind"] == "link":
            return observed.get("kind") == "link" and observed.get("source") == step["after"]
        return (observed.get("kind") == "file" and observed.get("sha256") == step["after"]["sha256"]
                and observed.get("bytes") == step["after"]["bytes"] and observed.get("mode") == 0o755)

    @staticmethod
    def ordinary_limit(step: dict[str, Any]) -> int:
        return max(step["before"].get("bytes", 0),
                   step["after"].get("bytes", 0) if isinstance(step["after"], dict) else 0)

    def reject_ordinary_staging(self) -> None:
        if not self.strict_ownership():
            return
        record = self.ordinary_record()
        active = None
        if record and record["prepared"] is not None:
            active = record["plan"]["steps"][record["cursor"]]
        targets = set(self.previous_links()) | {asset.target for asset in self.expected} | {self.launcher}
        if record:
            targets.update(Path(step["target"]) for step in record["plan"]["steps"])
        prefixes: dict[Path, dict[str, Path]] = {}
        for target in targets:
            prefixes.setdefault(target.parent, {})[f".{target.name}.agent-assets-ordinary-"] = target
        for parent, names in sorted(prefixes.items(), key=lambda item: str(item[0])):
            try:
                with parent_descriptor(parent / "entry") as directory:
                    for name in sorted(os.listdir(directory)):
                        if not name.startswith(tuple(names)):
                            continue
                        leaf = parent / name
                        if active and leaf == Path(active["target"]).with_name(active["stage"]):
                            observed = operation_observation(leaf, self.ordinary_limit(active))
                            if observed.get("identity") == record["prepared"] and self.ordinary_post(observed, active):
                                continue
                        # A basename/prefix/other cursor is not durable ownership.
                        raise AssetError(f"unexplained ordinary staging requires reconciliation: {leaf}")
            except FileNotFoundError:
                continue

    def ordinary_step_state(self, step: dict[str, Any], prepared: str | None) -> str:
        target = Path(step["target"])
        observed = operation_observation(target, self.ordinary_limit(step))
        if step["after"] is None:
            if observed["kind"] == "absent":
                return "after"
            if observed == step["before"]:
                return "before"
        elif prepared is None:
            if observed == step["before"]:
                return "before"
        else:
            staged = operation_observation(target.with_name(step["stage"]), self.ordinary_limit(step))
            if (staged.get("identity") == prepared and self.ordinary_post(staged, step)
                    and observed == step["before"]):
                return "prepared"
            if (staged["kind"] == "absent" and observed.get("identity") == prepared
                    and self.ordinary_post(observed, step)):
                return "after"
        raise AssetError(f"ordinary operation preimage or prepared object changed: {target}")

    def check_ordinary(self) -> None:
        record = self.ordinary_record()
        if not record or record["plan"]["action"] != self.args.action:
            raise AssetError(self.ordinary_pending_message())
        current = AgentAssets(self.args)
        if current.ownership_path != self.ownership_path or current.ownership != self.ownership:
            raise AssetError("ordinary ownership changed during operation")
        plan = record["plan"]
        if plan["action"] == "converge":
            current.reject_protected_staging()
            current.handoff_context()
            if current.selected_source_errors() or current.ordinary_selection() != plan["selection_sha256"]:
                raise AssetError("ordinary selection changed; restore the original reviewed selection")
            if current.handoff_record():
                current.completed_selection(current.handoff_record())
            expected = {str(asset.target): str(asset.source) for asset in current.expected}
            if "handoff_preparation" in plan:
                context = current.handoff_context()
                expected = current.preparation_expected(context)
                if digest_json(current.handoff_sources(context)) != plan["handoff_preparation"]:
                    raise AssetError("handoff preparation source inventories changed")
                if current.launcher_status() != "current":
                    raise AssetError("handoff preparation launcher changed")
            protected = current.protected_targets()
            for step in plan["steps"]:
                if step["kind"] == "link" and (Path(step["target"]) in protected
                        or (step["after"] is None and step["target"] in expected)
                        or (step["after"] is not None and expected.get(step["target"]) != step["after"])):
                    raise AssetError("ordinary intent no longer matches selected unprotected targets")
                if step["kind"] == "launcher":
                    data = current.launcher_content().encode()
                    if step["after"] != {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}:
                        raise AssetError("ordinary launcher selection changed")
            if operation_complete(operation_baseline(self.ownership), plan)["links"] != expected:
                raise AssetError("ordinary completion differs from selected targets")
        else:
            described = {step["target"] for step in plan["steps"] if step["kind"] == "link"}
            described.update(item["target"] for item in plan["preserved"] if item["kind"] == "link")
            if described != set(self.ownership["links"]):
                raise AssetError("removal intent must account for every recorded link")
        current.reject_ordinary_staging()
        changed = {step["target"] for step in plan["steps"]}
        preserved = {item["target"] for item in plan["preserved"]}
        for target, source in self.ownership["links"].items():
            if target not in changed | preserved and not exact_link(Path(target), source):
                raise AssetError(f"ordinary untouched recorded link changed: {target}")
        for item in plan["preserved"]:
            if operation_observation(Path(item["target"]), item["before"].get("bytes", 0)) != item["before"]:
                raise AssetError(f"preserved removal entry changed: {item['target']}")
        if self.ownership["launcher"] not in changed | preserved:
            launcher = operation_observation(Path(self.ownership["launcher"]), len(self.launcher_content().encode()))
            if plan["action"] == "remove":
                valid = launcher["kind"] == "absent"
            else:
                content = self.launcher_content().encode()
                valid = self.ordinary_post(launcher, {"kind": "launcher", "after": {
                    "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}})
            if not valid:
                raise AssetError("ordinary untouched launcher changed")
        for index, step in enumerate(plan["steps"]):
            observed = operation_observation(Path(step["target"]), self.ordinary_limit(step))
            if index < record["cursor"]:
                if not self.ordinary_post(observed, step):
                    raise AssetError(f"completed ordinary target changed: {step['target']}")
            elif index > record["cursor"]:
                if observed != step["before"]:
                    raise AssetError(f"future ordinary preimage changed: {step['target']}")
            else:
                self.ordinary_step_state(step, record["prepared"])

    def make_ordinary_plan(self, base: dict[str, Any], action: str) -> dict[str, Any]:
        steps, preserved = [], []
        expected = {str(asset.target): str(asset.source) for asset in self.expected}
        protected = self.protected_targets() if action == "converge" else set()
        targets = set(base["links"]) | (set(expected) if action == "converge" else set())
        for target in sorted(targets):
            before = operation_observation(Path(target))
            if action == "converge" and Path(target) in protected:
                continue
            after = expected.get(target) if action == "converge" else None
            if action == "converge" and before.get("kind") == "link" and before.get("source") == after:
                continue
            owned = before.get("kind") == "link" and before.get("source") == base["links"].get(target)
            if not owned and before["kind"] != "absent":
                if action == "converge":
                    raise OwnershipDriftError(Path(target), False)
                preserved.append({"kind": "link", "target": target, "before": before})
                continue
            steps.append({"kind": "link", "target": target, "before": before, "after": after,
                          "stage": f".{Path(target).name}.agent-assets-ordinary-{os.urandom(12).hex()}" if after is not None else None})
        launcher = Path(base["launcher"])
        content = self.launcher_content().encode()
        recognized = {self.launcher_content()} | (self.predecessor_launcher_contents() if action == "converge" else set())
        limit = max(len(item.encode()) for item in recognized)
        before = operation_observation(launcher, limit if launcher == self.launcher else 0)
        current = self.ordinary_post(before, {"kind": "launcher", "after": {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}})
        if not (action == "converge" and current) and not (action == "remove" and before["kind"] == "absent"):
            hashes = {hashlib.sha256(item.encode()).hexdigest() for item in recognized}
            if before["kind"] != "absent" and (launcher != self.launcher or before.get("sha256") not in hashes):
                if action == "converge":
                    raise AssetError("refusing to replace unmanaged launcher")
                preserved.append({"kind": "launcher", "target": str(launcher), "before": before})
            else:
                steps.append({"kind": "launcher", "target": str(launcher), "before": before,
                              "after": {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)} if action == "converge" else None,
                              "stage": f".{launcher.name}.agent-assets-ordinary-{os.urandom(12).hex()}" if action == "converge" else None})
        return {"action": action, "baseline_sha256": digest_json(base),
                "selection_sha256": self.ordinary_selection() if action == "converge" else None,
                "manifests": [str(path) for path in self.manifest_paths] if action == "converge" else base["manifests"],
                "steps": steps, "preserved": preserved}

    @staticmethod
    def ordinary_errors(plan: dict[str, Any]) -> list[dict[str, Any]]:
        # Diagnostics are derived from bounded typed plan entries, not saved text.
        return [issue("error", "remove-preserved-launcher" if item["kind"] == "launcher" else "remove-preserved-drift",
                      "preserved changed recorded entry", target=item["target"]) for item in plan["preserved"]]

    def print_removal_plan(self, plan: dict[str, Any], cursor: int = 0) -> None:
        if self.args.quiet:
            return
        remaining = [step for index, step in enumerate(plan["steps"]) if index >= cursor
                     and step["before"]["kind"] != "absent"]
        record = self.ordinary_record()
        if record and cursor < len(plan["steps"]):
            active = plan["steps"][cursor]
            if active in remaining and self.ordinary_step_state(active, record["prepared"]) == "after":
                remaining.remove(active)
        print(f"Dry run: would remove {len(remaining)} recorded entries; "
              f"preserve {len(plan['preserved'])} changed entries")
        for step in remaining:
            print(f"  remove {step['kind']}: {step['target']}")
        for item in plan["preserved"]:
            print(f"  preserve {item['kind']}: {item['target']}")

    def advance_ordinary(self) -> int:
        record = self.ordinary_record()
        base, plan = operation_baseline(self.ownership), record["plan"]
        _, complete, maximum = admit_operation(base, plan)
        self.check_ordinary()
        if self.args.dry_run:
            if not self.args.quiet:
                print(f"Dry run: would resume pending ordinary {plan['action']}; no checkpoint or payload changed")
            if plan["action"] == "remove":
                self.print_removal_plan(plan, record["cursor"])
            self.print_issues(self.ordinary_errors(plan))
            return bool(plan["preserved"])

        def checkpoint(cursor: int, prepared: str | None = None) -> None:
            value = operation_checkpoint(base, plan, cursor, prepared)
            validate_ownership(value)
            if len(checkpoint_bytes(value)) > maximum:
                raise AssetError("ordinary progress exceeds its admitted checkpoint envelope")
            self.write_ownership(value)

        while self.ordinary_record()["cursor"] < len(plan["steps"]):
            self.check_ordinary()
            record = self.ordinary_record()
            cursor = record["cursor"]
            step = plan["steps"][cursor]
            target = Path(step["target"])
            state = self.ordinary_step_state(step, record["prepared"])
            if state == "before" and step["after"] is not None:
                with parent_descriptor(target, create=True) as parent:
                    if operation_observation(target, self.ordinary_limit(step)) != step["before"]:
                        raise AssetError("ordinary destination changed before staging")
                    if step["kind"] == "link":
                        os.symlink(step["after"], step["stage"], dir_fd=parent)
                        created = os.stat(step["stage"], dir_fd=parent, follow_symlinks=False)
                    else:
                        data = self.launcher_content().encode()
                        fd = os.open(step["stage"], unix_flags() | os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700, dir_fd=parent)
                        try:
                            with os.fdopen(fd, "wb", closefd=False) as stream:
                                stream.write(data)
                                stream.flush()
                            os.fchmod(fd, 0o755)
                            created = os.fstat(fd)
                        finally:
                            os.close(fd)
                    staged = operation_observation(target.with_name(step["stage"]), self.ordinary_limit(step))
                    if staged.get("identity") != entry_identity(created) or not self.ordinary_post(staged, step):
                        raise AssetError("ordinary staging postimage mismatch")
                # A crash before this durable identity leaves unexplained staging.
                # Never clean or adopt it merely because the planned name matches.
                checkpoint(cursor, staged["identity"])
                self.check_ordinary()
                state = self.ordinary_step_state(step, staged["identity"])
            if state in {"before", "prepared"}:
                with parent_descriptor(target) as parent:
                    self.check_ordinary()
                    state = self.ordinary_step_state(step, self.ordinary_record()["prepared"])
                    try:
                        if step["after"] is None:
                            if state == "before" and step["before"]["kind"] != "absent":
                                os.unlink(target.name, dir_fd=parent)
                        elif state == "prepared":
                            os.replace(step["stage"], target.name, src_dir_fd=parent, dst_dir_fd=parent)
                    except OSError:
                        # Only this exact locked operation/postimage can resolve
                        # a lost response. Do not blindly repeat the mutation.
                        if self.ordinary_step_state(step, self.ordinary_record()["prepared"]) != "after":
                            raise
            if self.ordinary_step_state(step, self.ordinary_record()["prepared"]) != "after":
                raise AssetError("ordinary operation did not reach its postcondition")
            checkpoint(cursor + 1)
        self.check_ordinary()
        self.write_ownership(complete)
        self.print_issues(self.ordinary_errors(plan))
        if not self.args.quiet and plan["action"] == "converge":
            print("Agent assets converged")
        return bool(plan["preserved"])

    def converge_ordinary_v2(self) -> int:
        base = {"version": 2, "manifests": self.ownership.get("manifests", []),
                "links": dict(self.ownership["links"]), "launcher": str(self.launcher),
                "handoff": self.handoff_record()}
        declared = {(contract["group"], contract["name"])
                    for contract in self.manifest.get("skill_contracts", [])}
        declared.update((handoff["group"], handoff["adapter"])
                        for handoff in self.manifest.get("skill_handoffs", []))
        selected_units = [self.assets_for(group, name) for group, name in sorted(declared)]
        record = self.handoff_record()
        if record and record["state"] == "complete":
            # Retained provenance still protects the adapter pair when its
            # completed declaration is omitted. Reinstall only current assets
            # whose selection and canonical identities match that provenance.
            self.completed_selection(record)
            current = {str(asset.target): asset for asset in self.expected}
            selected_units.append([current[item["target"]] for item in record["plan"]["adapter_links"]])
        units: list[dict[Path, Asset]] = []
        for selected in selected_units:
            unit = {asset.target: asset for asset in selected}
            if not unit:
                continue
            # Units come from full declarations or the exact retained pair,
            # not arbitrary sharing claims.
            # Previously accumulated units are disjoint; merge every overlap
            # before checkpointing and write each shared destination only once.
            merged: dict[Path, Asset] = {}
            separate = []
            for prior in units:
                if prior.keys() & unit.keys():
                    merged.update(prior)
                else:
                    separate.append(prior)
            merged.update(unit)
            units = separate + [merged]
        fresh = [list(unit.values()) for unit in units if all(
            str(asset.target) not in base["links"] and operation_observation(asset.target)["kind"] == "absent"
            for asset in unit.values())]
        checkpoints = []
        for pair in fresh:
            base = {**base, "links": {**base["links"], **{str(asset.target): str(asset.source) for asset in pair}}}
            checkpoints.append(base)
        plan = self.make_ordinary_plan(base, "converge")
        complete = operation_complete(base, plan)
        if not fresh and not plan["steps"] and complete == self.ownership:
            if not self.args.quiet:
                print("Agent assets already current")
            return 0  # No-op does not require journal headroom, staging or a write.
        for value in checkpoints + [complete]:
            validate_ownership(value)
            checkpoint_bytes(value)
        initial = admit_operation(base, plan)[0] if plan["steps"] else None
        # All intermediates and downstream intent/progress/final capacity are now
        # admitted before the first protected staging or ordinary payload change.
        for pair, value in zip(fresh, checkpoints):
            current = AgentAssets(self.args)
            if current.ownership != self.ownership or current.ordinary_selection() != plan["selection_sha256"]:
                raise AssetError("selection or ownership changed before fresh protected installation")
            _, errors = current.preflight()
            if errors:
                raise AssetError("fresh protected preflight changed")
            for asset in pair:
                with parent_descriptor(asset.target, create=True):
                    pass
                self.atomic_symlink(asset.source, asset.target)
            self.write_ownership(value)
        if initial is None:
            if complete != self.ownership:
                self.write_ownership(complete)
            return 0
        self.write_ownership(initial)
        return self.advance_ordinary()

    def write_ownership(self, value: dict[str, Any]) -> None:
        validate_ownership(value)
        if fcntl is None:
            atomic_json(self.ownership_path, value)
            self.ownership = value
            return
        try:
            atomic_json(self.ownership_path, value)
        except OSError:
            # A lost replace response is not authority to repeat a mutation.
            data, _ = regular_bytes(self.ownership_path)
            if json.loads(data, object_pairs_hook=unique_object) != value:
                raise
        data, _ = regular_bytes(self.ownership_path)
        if json.loads(data, object_pairs_hook=unique_object) != value:
            raise AssetError("ownership checkpoint readback mismatch")
        self.ownership = value

    def handoff_checkpoints(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        prior = plan["prior_ownership"]
        pending = {"version": 2, "links": dict(prior["links"]),
                       "manifests": prior.get("manifests", []),
                       "launcher": prior.get("launcher", str(self.launcher)),
                       "handoff": {"version": 1, "state": "pending",
                                   "plan_sha256": digest_json(plan), "plan": plan}}
        record = pending["handoff"]
        links = dict(plan["prior_ownership"]["links"])
        links.update({item["target"]: item["new_source"] for item in plan["links"] + plan["adapter_links"]})
        complete = {"version": 2, "manifests": [str(path) for path in self.manifest_paths],
                    "links": links, "launcher": str(self.launcher),
                    "handoff": {**record, "state": "complete"}}
        # Admission must reserve capacity for completion, including on resume.
        # Use these same objects for writes; never discover an oversized final
        # checkpoint only after pending ownership or destination links changed.
        for checkpoint in (pending, complete):
            validate_ownership(checkpoint)
            checkpoint_bytes(checkpoint)
        return pending, complete

    def advance_handoff(self, mode: str, plan: dict[str, Any]) -> int:
        pending, complete = self.handoff_checkpoints(plan)
        record = pending["handoff"]
        if mode == "legacy":
            self.write_ownership(pending)
        for item in plan["adapter_links"] + plan["links"]:
            self.check_pending(record, self.handoff_context())
            target = Path(item["target"])
            if not exact_link(target, item["new_source"]):
                self.atomic_symlink(Path(item["new_source"]), target)
        self.check_pending(record, self.handoff_context())
        if any(not exact_link(Path(item["target"]), item["new_source"]) for item in plan["links"] + plan["adapter_links"]):
            raise AssetError("handoff links did not reach their prepared destinations")
        self.write_ownership(complete)
        if not self.args.quiet:
            print("Agent asset handoff completed; predecessor source retained")
        return 0

    def link_is_managed(self, link: Path, previous: dict[Path, Path]) -> bool:
        if self.strict_ownership():
            return link in previous and exact_link(link, previous[link])
        if not link.is_symlink():
            return False
        actual = lexical_target(link)
        recorded = previous.get(link)
        if recorded is not None and actual == recorded:
            return True
        return any(is_within(actual, root) for root in self.source_roots)

    def link_report(self, include_unmanaged: bool = False) -> dict[str, Any]:
        expected_by_target = {asset.target: asset for asset in self.expected}
        previous = self.previous_links()
        current: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        unmanaged: list[dict[str, Any]] = []
        broken: list[dict[str, Any]] = []

        for target, asset in sorted(
            expected_by_target.items(), key=lambda item: str(item[0])
        ):
            if target.is_symlink():
                actual = lexical_target(target)
                if actual == asset.source and (not self.strict_ownership() or
                        (previous.get(target) == asset.source and exact_link(target, asset.source))):
                    current.append(asset.as_dict())
                elif self.link_is_managed(target, previous):
                    stale.append(
                        {
                            **asset.as_dict(),
                            "actual": str(actual),
                            "reason": "wrong managed target",
                        }
                    )
                else:
                    conflicts.append(
                        {
                            **asset.as_dict(),
                            "actual": str(actual),
                            "reason": "unmanaged symlink",
                        }
                    )
            elif target.exists():
                conflicts.append(
                    {**asset.as_dict(), "reason": "unmanaged filesystem entry"}
                )
            else:
                missing.append(asset.as_dict())

        roots: set[Path] = set()
        for definitions in self.destinations.values():
            roots.update(definitions.values())
        for root in sorted(roots, key=str):
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if entry in expected_by_target:
                    continue
                if entry.is_symlink():
                    actual = lexical_target(entry)
                    if not entry.exists():
                        if self.link_is_managed(entry, previous):
                            stale.append(
                                {
                                    "path": str(entry),
                                    "actual": str(actual),
                                    "reason": "not selected",
                                }
                            )
                        elif include_unmanaged:
                            broken.append({"path": str(entry), "target": str(actual)})
                    elif self.link_is_managed(entry, previous):
                        stale.append(
                            {
                                "path": str(entry),
                                "actual": str(actual),
                                "reason": "not selected",
                            }
                        )
                    elif include_unmanaged:
                        unmanaged.append(
                            {
                                "path": str(entry),
                                "target": str(actual),
                                "type": "symlink",
                            }
                        )
                elif include_unmanaged:
                    unmanaged.append(
                        {
                            "path": str(entry),
                            "type": "directory" if entry.is_dir() else "file",
                        }
                    )
        return {
            "current": current,
            "missing": missing,
            "conflicts": conflicts,
            "stale": stale,
            "broken": broken,
            "unmanaged": unmanaged,
        }

    def launcher_content(self) -> str:
        return launcher_script("envy", Path(__file__).resolve())

    def predecessor_launcher_contents(self) -> set[str]:
        return {
            launcher_script(item["manager"], Path(item["script"]))
            for item in self.manifest.get("launcher_predecessors", [])
        }

    def launcher_status(self) -> str:
        if not self.launcher.exists() and not self.launcher.is_symlink():
            return "missing"
        if not self.launcher.is_file() or self.launcher.is_symlink():
            return "conflict"
        try:
            content = self.launcher.read_text()
            if content == self.launcher_content():
                if fcntl is not None:
                    info = self.launcher.lstat()
                    regular_stat(info, control=True)
                    if stat.S_IMODE(info.st_mode) != 0o755:
                        return "mode-drift"
                return "current"
            if content in self.predecessor_launcher_contents():
                return "adoptable"
            return "conflict"
        except (OSError, AssetError):
            return "conflict"

    def remove_launcher(self) -> str:
        """Unix exact-content removal; the final identity-check/unlink gap remains."""
        expected = self.launcher_content().encode("utf-8")
        try:
            with parent_descriptor(self.launcher) as parent:
                before = os.stat(self.launcher.name, dir_fd=parent, follow_symlinks=False)
                try:
                    regular_stat(before, control=True)
                except AssetError:
                    return "foreign"
                fd = os.open(self.launcher.name, unix_flags() | os.O_RDONLY, dir_fd=parent)
                try:
                    opened = os.fstat(fd)
                    regular_stat(opened, control=True)
                    if not same_inode(before, opened):
                        return "conflict"
                    if opened.st_size != len(expected):
                        return "foreign"
                    content = b""
                    while len(content) <= len(expected):
                        chunk = os.read(fd, len(expected) + 1 - len(content))
                        if not chunk:
                            break
                        content += chunk
                    named = os.stat(self.launcher.name, dir_fd=parent, follow_symlinks=False)
                    if not same_inode(opened, named):
                        return "conflict"
                    regular_stat(named, control=True)
                    if content != expected:
                        return "foreign"
                    if not self.args.dry_run:
                        os.unlink(self.launcher.name, dir_fd=parent)
                    return "removed"
                finally:
                    os.close(fd)
        except FileNotFoundError:
            return "missing"
        except (AssetError, OSError):
            return "conflict"

    def preflight(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self.reject_protected_staging()
        self.handoff_context()
        self.reject_ordinary_staging()
        if self.ordinary_record():
            self.check_ordinary()
            self.transition_mode = "ordinary-pending"
            self.transition_plan = self.ordinary_record()["plan"]
            return {"current": [], "stale": []}, []
        links = self.link_report()
        errors = list(self.selected_source_errors())
        self.transition_mode, self.transition_plan = "ordinary", None
        authorized_pending = set()
        try:
            self.transition_mode, self.transition_plan = self.transition_preflight()
            if self.transition_mode == "pending":
                authorized_pending = {item["target"] for item in self.transition_plan["links"] + self.transition_plan["adapter_links"]}
        except (AssetError, OSError, ValueError) as error:
            errors.append(transition_issue(error))
        for conflict in links["conflicts"]:
            if conflict["target"] in authorized_pending:
                continue
            errors.append(
                issue(
                    "error",
                    "target-conflict",
                    f"refusing to replace unmanaged target: {conflict['target']}",
                    target=conflict["target"],
                )
            )
        if self.launcher_status() == "conflict":
            errors.append(
                issue(
                    "error",
                    "launcher-conflict",
                    f"refusing to replace unmanaged launcher: {self.launcher}",
                )
            )
        return links, errors

    @staticmethod
    def atomic_symlink(source: Path, target: Path) -> None:
        if fcntl is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.{os.urandom(16).hex()}"
            os.symlink(source, temporary)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return
        with parent_descriptor(target) as parent:
            for _ in range(8):
                temporary = f".{target.name}.agent-assets-{os.urandom(16).hex()}"
                try:
                    os.symlink(str(source), temporary, dir_fd=parent)
                except FileExistsError:
                    continue
                break
            else:
                raise AssetError("could not allocate an exclusive symlink staging entry")
            owned = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            try:
                try:
                    os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                except OSError:
                    # Resolve an uncertain replace response under the held writer lock.
                    actual = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISLNK(actual.st_mode) or os.readlink(target.name, dir_fd=parent) != str(source):
                        raise
            finally:
                try:
                    current = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if same_inode(owned, current):
                        os.unlink(temporary, dir_fd=parent)

    def write_launcher(self) -> None:
        self.launcher.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".agent-assets.", dir=self.launcher.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self.launcher_content())
            os.chmod(temp_name, 0o755)
            os.replace(temp_name, self.launcher)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def converge(self) -> int:
        if self.args.dry_run:
            with ownership_lock(self.ownership_path, write=False):
                current = AgentAssets(self.args)
                if current.ownership_path != self.ownership_path:
                    raise AssetError("ownership path changed before dry-run preflight")
                return current._converge_locked()
        if self.state is None or not self.distribution_enabled():
            return self._converge_locked()
        _, errors = self.preflight()
        if errors:
            self.print_issues(errors)
            return 1
        created: list[str] = []
        controls: list[str] = []
        try:
            if fcntl is not None:
                with parent_descriptor(self.ownership_path, create=not self.ownership_present, created=created,
                                       expected=self.ownership_parent_inspection):
                    pass
            with ownership_lock(self.ownership_path, write=True, created=controls):
                current = AgentAssets(self.args)
                if current.ownership_path != self.ownership_path:
                    raise AssetError("ownership path changed before locked preflight")
                return current._converge_locked()
        finally:
            if created or controls:
                print(json.dumps({"created_ownership_directories": created,
                                  "created_ownership_controls": controls,
                                  "preserved": True}), file=sys.stderr)

    def _converge_locked(self) -> int:
        if self.state is None:
            print(
                f"agent-assets: envy state not found: {self.state_path}",
                file=sys.stderr,
            )
            return 2
        if not self.distribution_enabled():
            print(
                "agent-assets: distribution feature is disabled; run envy install --reconfigure"
            )
            return 2
        links, errors = self.preflight()
        if errors:
            self.print_issues(errors)
            return 1
        if self.ordinary_record():
            if "handoff_preparation" in self.ordinary_record()["plan"]:
                return self.finish_preparation()
            return self.advance_ordinary()
        expected_by_target = {asset.target: asset for asset in self.expected}
        if self.transition_mode == "legacy-prepare":
            return self.prepare_handoff(self.transition_plan)
        if self.args.dry_run:
            if not self.args.quiet:
                if self.transition_mode in {"legacy", "pending"}:
                    plan = self.transition_plan
                    adapters = sum(not exact_link(Path(item["target"]), item["new_source"])
                                   for item in plan["adapter_links"])
                    generic = sum(not exact_link(Path(item["target"]), item["new_source"])
                                  for item in plan["links"])
                    action = "start" if self.transition_mode == "legacy" else "resume"
                    print(f"Dry run: would {action} handoff: create {adapters} adapter links "
                          f"and atomically replace {generic} generic links; "
                          "preserve launcher and predecessor source")
                else:
                    to_link = len(self.expected) - len(links["current"])
                    print(
                        "Dry run: "
                        f"would link {to_link} assets, "
                        f"remove {len(links['stale'])} stale links, and manage {self.launcher}"
                    )
            return 0

        if self.transition_mode in {"legacy", "pending"}:
            return self.advance_handoff(self.transition_mode, self.transition_plan)

        if self.strict_ownership():
            return self.converge_ordinary_v2()

        previous = self.previous_links()
        for item in links["stale"]:
            raw_target = item.get("target") or item.get("path")
            if not raw_target:
                continue
            target = Path(raw_target)
            if target.is_symlink() and self.link_is_managed(target, previous):
                target.unlink()
        for target, asset in expected_by_target.items():
            if target.is_symlink() and lexical_target(target) == asset.source:
                continue
            if fcntl is not None:
                with parent_descriptor(target, create=True):
                    pass
            self.atomic_symlink(asset.source, target)
        self.write_launcher()
        checkpoint = {
                "version": 2 if self.strict_ownership() else 1,
                "manifests": [str(path) for path in self.manifest_paths],
                "links": {
                    str(asset.target): str(asset.source)
                    for asset in sorted(
                        self.expected, key=lambda item: str(item.target)
                    )
                },
                "launcher": str(self.launcher),
            }
        if checkpoint["version"] == 2:
            checkpoint["handoff"] = self.handoff_record()
        self.write_ownership(checkpoint)
        if not self.args.quiet:
            counts: dict[str, int] = {}
            for asset in self.expected:
                key = f"{asset.harness} {asset.kind}s"
                counts[key] = counts.get(key, 0) + 1
            print("Agent assets converged")
            for key, count in sorted(counts.items()):
                print(f"  {key:<20} {count}")
        return 0

    def load_removal_selection(self) -> bool:
        try:
            self._load_selection()
        except V2CapabilityError:
            if fcntl is not None or self.ownership["version"] != 1 or self.manifest["version"] != 2:
                raise
            # Validate the composed declaration, including any v1 contributors,
            # without discovering assets or treating other selection errors as permission.
            declaration = dict(self.manifest)
            for key in ("skill_groups", "agent_groups"):
                declaration[key] = [{name: value for name, value in group.items() if name != "_version"}
                                    for group in self.manifest[key]]
            validate_v2_manifest(declaration)
            return True
        return False

    def remove(self) -> int:
        if any(item["code"] == "ownership-invalid" for item in self.issues):
            self.print_issues(self.issues)
            return 1
        if not self.ownership_present:
            if not self.args.quiet:
                print("No recorded agent asset entries; unrecorded entries preserved")
            return 0
        if self.ordinary_record() and self.ordinary_record()["plan"]["action"] != "remove":
            raise AssetError(self.ordinary_pending_message())
        if self.handoff_record() and self.handoff_record()["state"] == "pending":
            raise AssetError("remove refuses pending ownership handoff")
        if self.ownership["version"] == 1:
            self.load_removal_selection()
        controls: list[str] = []
        try:
            with ownership_lock(self.ownership_path, write=not self.args.dry_run, created=controls):
                current = AgentAssets(self.args)
                if current.ownership_path != self.ownership_path:
                    raise AssetError("ownership path changed before locked removal")
                if any(item["code"] == "ownership-invalid" for item in current.issues):
                    raise AssetError("invalid ownership prevents removal")
                if self.ownership["version"] == 2 and current.ownership["version"] != 2:
                    raise AssetError("v2 ownership changed before locked removal")
                if not current.ownership_present:
                    return 0
                if current.handoff_record() and current.handoff_record()["state"] == "pending":
                    raise AssetError("remove refuses pending ownership handoff")
                if current.ownership["version"] == 1:
                    if current.load_removal_selection():
                        return current._remove_legacy_recorded()
                return current._remove_locked()
        finally:
            if controls:
                print(json.dumps({"created_ownership_directories": [],
                                  "created_ownership_controls": controls,
                                  "preserved": True}), file=sys.stderr)

    def _remove_locked(self) -> int:
        if self.ownership["version"] == 2:
            return self._remove_v2()
        previous = self.previous_links()
        links = self.link_report()
        candidates: set[Path] = set(previous)
        for section in ("current", "stale"):
            for item in links[section]:
                raw = item.get("target") or item.get("path")
                if raw:
                    candidates.add(Path(raw))
        removed = 0
        for target in sorted(candidates, key=str):
            if target.is_symlink() and self.link_is_managed(target, previous):
                if not self.args.dry_run:
                    target.unlink()
                removed += 1
        if fcntl is None:
            if self.launcher_status() == "current":
                if not self.args.dry_run:
                    self.launcher.unlink()
                removed += 1
        else:
            launcher = self.remove_launcher()
            if launcher == "conflict":
                self.print_issues([issue("error", "remove-preserved-launcher",
                                        "preserved changed recorded launcher", target=str(self.launcher))])
                return 1
            removed += launcher == "removed"
        if not self.args.dry_run:
            self.ownership_path.unlink(missing_ok=True)
        if not self.args.quiet:
            prefix = "Would remove" if self.args.dry_run else "Removed"
            print(f"{prefix} {removed} managed agent asset entries")
        return 0

    def _remove_legacy_recorded(self) -> int:
        """Capability-only v1 cleanup: no discovery, root adoption or v2 writes."""
        remaining = dict(self.ownership["links"])
        errors = []
        for raw_target, source in sorted(remaining.copy().items()):
            target = Path(raw_target)
            try:
                info = target.lstat()
            except FileNotFoundError:
                remaining.pop(raw_target)
                continue
            if stat.S_ISLNK(info.st_mode) and os.readlink(target) == source:
                if not self.args.dry_run:
                    target.unlink()
                remaining.pop(raw_target)
            else:
                errors.append(issue("error", "remove-preserved-drift",
                                    "preserved changed recorded entry", target=raw_target))
        recorded_launcher = self.ownership.get("launcher")
        if recorded_launcher:
            launcher = Path(recorded_launcher)
            if launcher == self.launcher and self.launcher_status() == "current":
                if not self.args.dry_run:
                    launcher.unlink()
            elif launcher.exists() or launcher.is_symlink():
                errors.append(issue("error", "remove-preserved-launcher",
                                    "preserved changed recorded launcher", target=recorded_launcher))
        if not self.args.dry_run:
            if errors:
                self.write_ownership({**self.ownership, "links": remaining})
            else:
                self.ownership_path.unlink()
        self.print_issues(errors)
        return 1 if errors else 0

    def _remove_v2(self) -> int:
        if self.ordinary_record():
            return self.advance_ordinary()
        self.reject_ordinary_staging()
        base = operation_baseline(self.ownership)
        try:
            plan = self.make_ordinary_plan(base, "remove")
        except OperationEntryChanged as error:
            self.print_issues([issue("error", "remove-preserved-drift", str(error), target=error.target)])
            return 1
        if not plan["steps"]:
            if self.args.dry_run:
                self.print_removal_plan(plan)
            self.print_issues(self.ordinary_errors(plan))
            return bool(plan["preserved"])
        initial, _, _ = admit_operation(base, plan)
        if self.args.dry_run:
            self.print_removal_plan(plan)
            self.print_issues(self.ordinary_errors(plan))
            return bool(plan["preserved"])
        self.write_ownership(initial)
        return self.advance_ordinary()

    def base_report(self, include_unmanaged: bool) -> dict[str, Any]:
        # Propagate refusal before any link/launcher scan or doctor extensions.
        self.reject_protected_staging()
        self.handoff_context()
        self.reject_ordinary_staging()
        if self.ordinary_record():
            raise AssetError(self.ordinary_pending_message())
        links = self.link_report(include_unmanaged=include_unmanaged)
        issues = list(self.issues)
        if self.strict_ownership() or self.manifest.get("skill_handoffs"):
            try:
                self.transition_preflight()
            except (AssetError, OSError, ValueError) as error:
                issues.append(transition_issue(error))
        record = self.handoff_record()
        if record and record["state"] == "pending":
            issues.append(issue("error", "handoff-pending", "ownership handoff is pending; convergence must reconcile it"))
        for item in links["missing"]:
            issues.append(
                issue(
                    "error",
                    "link-missing",
                    f"managed link is missing: {item['target']}",
                    **item,
                )
            )
        for item in links["conflicts"]:
            issues.append(
                issue(
                    "error",
                    "link-conflict",
                    f"managed target conflicts: {item['target']}",
                    **item,
                )
            )
        for item in links["stale"]:
            path = item.get("target") or item.get("path")
            issues.append(
                issue("error", "link-stale", f"managed link is stale: {path}", **item)
            )
        for item in links["broken"]:
            issues.append(
                issue(
                    "warning",
                    "link-broken",
                    f"unmanaged destination has a broken link: {item['path']}",
                    **item,
                )
            )
        launcher = self.launcher_status() if self.distribution_enabled() else "disabled"
        if self.distribution_enabled() and launcher != "current":
            issues.append(
                issue(
                    "error",
                    f"launcher-{launcher}",
                    f"agent-assets launcher is {launcher}: {self.launcher}",
                )
            )
        selected_groups = [group["name"] for group in self.groups if group["selected"]]
        report: dict[str, Any] = {
            "manifests": [str(path) for path in self.manifest_paths],
            "state": str(self.state_path),
            "ownership": str(self.ownership_path),
            "platform": self.platform,
            "selected_groups": selected_groups,
            "groups": self.groups,
            "expected": [asset.as_dict() for asset in self.expected],
            "links": links,
            "launcher": {"path": str(self.launcher), "status": launcher},
            "issues": issues,
        }
        return report

    def plugin_report(self) -> dict[str, Any]:
        claude_root = Path.home() / ".claude"
        settings_path = claude_root / "settings.json"
        registry_path = claude_root / "plugins" / "installed_plugins.json"
        result: dict[str, Any] = {
            "settings": str(settings_path),
            "registry": str(registry_path),
            "enabled": [],
            "skill_collisions": [],
            "surface_collisions": [],
        }
        if not settings_path.is_file():
            return result
        try:
            settings = load_json(settings_path, "Claude settings")
        except AssetError as error:
            result["error"] = str(error)
            return result
        enabled = settings.get("enabledPlugins", {})
        if not isinstance(enabled, dict):
            result["error"] = "enabledPlugins is not an object"
            return result
        registry: dict[str, Any] = {}
        if registry_path.is_file():
            try:
                registry = load_json(registry_path, "Claude plugin registry").get(
                    "plugins", {}
                )
            except AssetError as error:
                result["registry_error"] = str(error)
        available = available_plugin_versions(claude_root)

        skills: dict[str, list[dict[str, str]]] = {}
        for plugin_name in sorted(
            name for name, value in enabled.items() if value is True
        ):
            records = (
                registry.get(plugin_name, []) if isinstance(registry, dict) else []
            )
            record: dict[str, Any] = {}
            if isinstance(records, list) and records:
                record = next(
                    (
                        item
                        for item in records
                        if isinstance(item, dict)
                        and isinstance(item.get("installPath"), str)
                        and Path(item["installPath"]).is_dir()
                    ),
                    records[0] if isinstance(records[0], dict) else {},
                )
            raw_install_path = (
                record.get("installPath") if isinstance(record, dict) else None
            )
            install_path = (
                Path(raw_install_path) if isinstance(raw_install_path, str) else None
            )
            cache_exists = install_path is not None and install_path.is_dir()
            plugin: dict[str, Any] = {
                "name": plugin_name,
                "version": record.get("version") if isinstance(record, dict) else None,
                "available_version": available.get(plugin_name),
                "install_path": str(install_path) if install_path is not None else None,
                "cache_exists": cache_exists,
                "skills": [],
            }
            if cache_exists:
                for definition in sorted(install_path.rglob("SKILL.md")):
                    metadata, _ = frontmatter(definition)
                    skill_name = metadata.get("name") or definition.parent.name
                    entry = {
                        "name": skill_name,
                        "plugin": plugin_name,
                        "path": str(definition),
                        "sha256": sha256(definition),
                    }
                    plugin["skills"].append(entry)
                    skills.setdefault(skill_name, []).append(entry)
            result["enabled"].append(plugin)
        for name, entries in sorted(skills.items()):
            if len(entries) < 2:
                continue
            result["skill_collisions"].append(
                {
                    "name": name,
                    "identical": len({entry["sha256"] for entry in entries}) == 1,
                    "sources": [
                        {"plugin": entry["plugin"], "path": entry["path"]}
                        for entry in entries
                    ],
                }
            )
        claude_root = self.destinations.get("claude", {}).get("skills")
        if claude_root is not None and claude_root.is_dir():
            direct: dict[str, str] = {}
            for entry in sorted(claude_root.iterdir()):
                if entry.is_symlink() and not entry.exists():
                    continue
                definition = entry / "SKILL.md"
                if not definition.is_file():
                    continue
                metadata, _ = frontmatter(definition)
                direct[metadata.get("name") or entry.name] = str(entry)
            for name in sorted(set(direct) & set(skills)):
                result["surface_collisions"].append(
                    {
                        "name": name,
                        "direct_path": direct[name],
                        "plugins": sorted({entry["plugin"] for entry in skills[name]}),
                    }
                )
        return result

    def audit_roots_report(self) -> list[dict[str, Any]]:
        definitions = self.manifest.get("audit_roots", [])
        if not isinstance(definitions, list):
            return [{"error": "audit_roots must be an array"}]
        roots: list[dict[str, Any]] = []
        for definition in definitions:
            if not isinstance(definition, dict) or not isinstance(
                definition.get("path"), str
            ):
                roots.append({"error": "invalid audit root definition"})
                continue
            root = Path(definition["path"]).expanduser()
            record: dict[str, Any] = {
                "name": definition.get("name", root.name),
                "path": str(root),
                "owner": definition.get("owner"),
                "read_only": definition.get("read_only", False),
                "alias_of": definition.get("alias_of"),
                "exists": root.is_dir(),
                "assets": [],
            }
            if root.is_dir():
                if definition.get("flat_markdown"):
                    candidates = sorted(
                        path for path in root.glob("*.md") if path.is_file()
                    )
                    for path in candidates:
                        metadata, _ = frontmatter(path)
                        record["assets"].append(
                            {
                                "name": metadata.get("name") or path.stem,
                                "path": str(path),
                                "type": "legacy-markdown",
                            }
                        )
                else:
                    for path in sorted(root.iterdir()):
                        if not path.is_dir():
                            continue
                        definition_path = path / "SKILL.md"
                        if not definition_path.is_file():
                            continue
                        metadata, _ = frontmatter(definition_path)
                        record["assets"].append(
                            {
                                "name": metadata.get("name") or path.name,
                                "path": str(path),
                                "target": (
                                    str(lexical_target(path))
                                    if path.is_symlink()
                                    else None
                                ),
                                "type": "skill",
                            }
                        )
            roots.append(record)
        return roots

    def destination_metadata_issues(
        self, report: dict[str, Any]
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        seen: set[Path] = set()
        previous = self.previous_links()
        for definitions in self.destinations.values():
            root = definitions.get("skills")
            if root is None or root in seen or not root.is_dir():
                continue
            seen.add(root)
            for entry in sorted(root.iterdir()):
                if entry.is_symlink() and not entry.exists():
                    continue
                if entry.is_symlink() and self.link_is_managed(entry, previous):
                    continue
                if not entry.is_dir():
                    continue
                definition = entry / "SKILL.md"
                if not definition.is_file():
                    issues.append(
                        issue(
                            "warning",
                            "destination-skill-missing",
                            f"skill directory has no SKILL.md: {entry}",
                            path=str(entry),
                        )
                    )
                    continue
                metadata, parse_issues = frontmatter(definition)
                for message in parse_issues:
                    issues.append(
                        issue("warning", "destination-frontmatter-invalid", message)
                    )
                if metadata.get("name") != entry.name:
                    issues.append(
                        issue(
                            "warning",
                            "destination-name-mismatch",
                            f"skill name does not match destination directory: {entry}",
                            path=str(entry),
                            name=metadata.get("name"),
                        )
                    )
                if not metadata.get("description"):
                    issues.append(
                        issue(
                            "warning",
                            "destination-description-missing",
                            f"skill description is missing: {definition}",
                            path=str(definition),
                        )
                    )
        return issues

    def doctor(self) -> int:
        report = self.base_report(include_unmanaged=True)
        report["issues"].extend(self.destination_metadata_issues(report))
        report["audit_roots"] = self.audit_roots_report()
        for root in report["audit_roots"]:
            if root.get("error"):
                report["issues"].append(
                    issue("error", "audit-root-invalid", root["error"])
                )
            elif not root.get("exists") and not root.get("alias_of"):
                report["issues"].append(
                    issue(
                        "warning",
                        "audit-root-missing",
                        f"configured audit root is absent: {root['path']}",
                        path=root["path"],
                    )
                )
            for asset in root.get("assets", []):
                if asset.get("type") == "legacy-markdown":
                    report["issues"].append(
                        issue(
                            "warning",
                            "legacy-skill-file",
                            f"legacy standalone skill file remains: {asset['path']}",
                            path=asset["path"],
                        )
                    )
        if self.manifest.get("plugin_audit") is True:
            plugins = self.plugin_report()
            report["plugins"] = plugins
            if plugins.get("error"):
                report["issues"].append(
                    issue("error", "plugins-invalid", plugins["error"])
                )
            if plugins.get("registry_error"):
                report["issues"].append(
                    issue("error", "plugin-registry-invalid", plugins["registry_error"])
                )
            for plugin in plugins.get("enabled", []):
                if not plugin["cache_exists"]:
                    report["issues"].append(
                        issue(
                            "error",
                            "plugin-cache-missing",
                            f"enabled plugin cache is missing: {plugin['name']}",
                            plugin=plugin["name"],
                            path=plugin["install_path"],
                        )
                    )
                elif plugin.get("available_version") and plugin.get(
                    "version"
                ) != plugin.get("available_version"):
                    report["issues"].append(
                        issue(
                            "warning",
                            "plugin-outdated",
                            f"plugin {plugin['name']} is {plugin['version']}; available {plugin['available_version']}",
                            plugin=plugin["name"],
                        )
                    )
            for collision in plugins.get("skill_collisions", []):
                report["issues"].append(
                    issue(
                        "warning",
                        "plugin-skill-duplicate",
                        f"enabled plugins duplicate skill '{collision['name']}'",
                        **collision,
                    )
                )
            for collision in plugins.get("surface_collisions", []):
                report["issues"].append(
                    issue(
                        "warning",
                        "claude-skill-collision",
                        f"a direct Claude skill and enabled plugin share '{collision['name']}'",
                        **collision,
                    )
                )
        self.finish_report(report, title="Agent asset doctor")
        return 1 if any(item["severity"] == "error" for item in report["issues"]) else 0

    def status_or_verify(self, verify: bool) -> int:
        report = self.base_report(include_unmanaged=False)
        errors = [item for item in report["issues"] if item["severity"] == "error"]
        if verify:
            if errors:
                self.print_issues(errors)
            return 1 if errors else 0
        self.finish_report(report, title="Agent asset status")
        return 1 if errors else 0

    def finish_report(self, report: dict[str, Any], title: str) -> None:
        severities = {"error": 0, "warning": 0, "info": 0}
        for item in report["issues"]:
            severities[item["severity"]] = severities.get(item["severity"], 0) + 1
        report["summary"] = {
            "expected": len(report["expected"]),
            "current": len(report["links"]["current"]),
            "errors": severities.get("error", 0),
            "warnings": severities.get("warning", 0),
        }
        if self.args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
            return
        if self.args.quiet:
            return
        print(title)
        print(f"  manifests         {', '.join(report['manifests'])}")
        print(f"  platform          {report['platform']}")
        print(f"  selected groups   {', '.join(report['selected_groups']) or '(none)'}")
        print(
            f"  managed links     {report['summary']['current']}/{report['summary']['expected']} current"
        )
        if "plugins" in report:
            enabled = report["plugins"].get("enabled", [])
            missing = sum(not item["cache_exists"] for item in enabled)
            outdated = sum(
                bool(item.get("available_version"))
                and item.get("version") != item.get("available_version")
                for item in enabled
            )
            collisions = len(report["plugins"].get("skill_collisions", []))
            surface_collisions = len(report["plugins"].get("surface_collisions", []))
            print(
                f"  Claude plugins    {len(enabled)} enabled, {missing} missing, "
                f"{outdated} outdated, {collisions + surface_collisions} skill collisions"
            )
        print(
            f"  issues            {report['summary']['errors']} errors, "
            f"{report['summary']['warnings']} warnings"
        )
        self.print_issues(report["issues"])

    @staticmethod
    def print_issues(items: Iterable[dict[str, Any]]) -> None:
        items = list(items)
        if not items:
            return
        print("Issues:", file=sys.stderr)
        for item in items:
            target = item.get("target")
            context = f" [target: {target}]" if isinstance(target, str) and target not in item["message"] else ""
            print(
                f"  {item['severity'].upper():<7} {item['code']}: {item['message']}{context}",
                file=sys.stderr,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("converge", "status", "verify", "doctor", "remove")
    )
    parser.add_argument("--manifest")
    parser.add_argument("--state")
    parser.add_argument("--ownership")
    parser.add_argument("--dry-run", "-n", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if os.environ.get("ENVY_DRY_RUN", "false").lower() in {"1", "true", "yes"}:
        args.dry_run = True
    return args


def main() -> int:
    args = parse_args()
    try:
        manager = AgentAssets(args)
        if args.action == "converge":
            return manager.converge()
        if args.action == "remove":
            return manager.remove()
        with ownership_lock(manager.ownership_path, write=False):
            manager = AgentAssets(args)
            if args.action == "doctor":
                return manager.doctor()
            return manager.status_or_verify(verify=args.action == "verify")
    except (AssetError, OSError, ValueError) as error:
        if args.json:
            print(
                json.dumps({"summary": {"errors": 1}, "issues": [str(error)]}, indent=2)
            )
        else:
            print(f"agent-assets: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
