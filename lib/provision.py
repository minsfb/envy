#!/usr/bin/env python3
"""Install and verify envy-owned tool binaries from pinned upstream releases.

Binaries are never vendored. Each provisioned dependency names an exact upstream
release artifact per platform and the SHA-256 digest that artifact must have.
An unrecorded or mismatched digest fails closed: envy refuses to install
rather than trust an unverified download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_TIMEOUT = 180
# Reviewed upstream release archives fit under this cap; it also prevents a
# redirected or hostile URL from exhausting the disk.
DOWNLOAD_LIMIT = 256 * 1024 * 1024
EXTRACT_LIMIT = 2 * 1024 * 1024 * 1024
MEMBER_LIMIT = 200_000
CHUNK = 1024 * 1024
VERSION_PATTERN = re.compile(r"\d+(?:\.\d+)+")
TREE_MANIFEST = ".envy-provision-tree.json"
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


class ProvisionError(RuntimeError):
    pass


def dependencies() -> list[dict[str, Any]]:
    value = json.loads((ROOT / "dependencies.json").read_text(encoding="utf-8"))
    return [item for item in value.get("dependencies", []) if isinstance(item, dict)]


def dependency(name: str) -> dict[str, Any]:
    for item in dependencies():
        if item.get("name") == name and isinstance(item.get("provision"), dict):
            return item
    raise ProvisionError(f"no provisioned dependency named '{name}'")


def platform_key() -> str:
    system = platform.system().lower()
    if system.startswith("linux"):
        operating_system = "linux"
    elif system == "darwin":
        operating_system = "darwin"
    elif system == "windows":
        operating_system = "windows"
    else:
        operating_system = system
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        architecture = "amd64"
    elif machine in {"aarch64", "arm64"}:
        architecture = "arm64"
    else:
        architecture = machine
    return f"{operating_system}-{architecture}"


def artifact(item: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    key = key or platform_key()
    artifacts = item["provision"].get("artifacts", {})
    value = artifacts.get(key)
    if not isinstance(value, dict):
        raise ProvisionError(
            f"{item['name']} {item['version']} has no recorded artifact for {key}"
        )
    return value


def state_root() -> Path:
    return paths.state_dir()


def marker(name: str) -> Path:
    return state_root() / "providers" / f"{name}.installed"


def install_path(item: dict[str, Any]) -> Path:
    raw = item["provision"]["install_path"]
    raw = raw.replace("${ENVY_DATA_DIR}", str(paths.data_dir()))
    raw = raw.replace("${ENVY_STATE_DIR}", str(paths.state_dir()))
    raw = raw.replace("${ENVY_BIN_DIR}", str(paths.bin_dir()))
    if raw.startswith("~/.local/bin/"):
        home = os.environ.get("ENVY_BIN_HOME")
        if home:
            return Path(home) / raw.removeprefix("~/.local/bin/")
    return Path(raw).expanduser()


def is_tree(item: dict[str, Any]) -> bool:
    return item["provision"].get("kind") == "tree"


def tree_exclusions(item: dict[str, Any]) -> tuple[PurePosixPath, ...]:
    raw = item["provision"].get("exclude_paths", [])
    if not isinstance(raw, list):
        raise ProvisionError(f"{item['name']} tree exclude_paths must be a list")
    exclusions: list[PurePosixPath] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ProvisionError(
                f"{item['name']} tree exclude_paths entries must be non-empty strings"
            )
        relative = _tree_relative(value, 0)
        if relative is None or str(relative) != value:
            raise ProvisionError(
                f"{item['name']} has unsafe or non-canonical exclusion: {value}"
            )
        if any(relative == other or other in relative.parents for other in exclusions):
            raise ProvisionError(
                f"{item['name']} has duplicate or overlapping exclusion: {value}"
            )
        if any(relative in other.parents for other in exclusions):
            raise ProvisionError(
                f"{item['name']} has overlapping exclusion: {value}"
            )
        exclusions.append(relative)
    return tuple(exclusions)


def managed_command(item: dict[str, Any]) -> Path | None:
    if not is_tree(item):
        return None
    executable = item["provision"].get("executable")
    if not isinstance(executable, str) or not executable:
        raise ProvisionError(f"{item['name']} tree provision requires executable")
    relative = PurePosixPath(executable)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProvisionError(f"{item['name']} has unsafe executable path: {executable}")
    return install_path(item).joinpath(*relative.parts)


def digest_of(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            value.update(block)
    return value.hexdigest()


def external_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(source if source is not None else os.environ)
    proxy = environment.get("ENVY_NETWORK_PROXY")
    if proxy and not any(environment.get(key) for key in PROXY_VARIABLES):
        for key in PROXY_VARIABLES:
            environment[key] = proxy
    return environment


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "envy"})
    environment = external_environment()
    proxies = {
        scheme: environment.get(lower) or environment.get(upper)
        for scheme, lower, upper in (
            ("http", "http_proxy", "HTTP_PROXY"),
            ("https", "https_proxy", "HTTPS_PROXY"),
        )
        if environment.get(lower) or environment.get(upper)
    }
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        if proxies
        else urllib.request.build_opener()
    )
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            total = 0
            with destination.open("wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    total += len(block)
                    if total > DOWNLOAD_LIMIT:
                        raise ProvisionError(f"download exceeded size limit: {url}")
                    handle.write(block)
    except urllib.error.HTTPError as error:
        raise ProvisionError(f"HTTP {error.code} fetching {url}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProvisionError(f"cannot fetch {url}: {error}") from error


def extract_member(archive: Path, member: str | None, destination: Path) -> None:
    """Copy one known member out of the archive without honoring archive paths."""
    name = archive.name
    if member is None:
        shutil.copyfile(archive, destination)
        return
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            try:
                info = bundle.getinfo(member)
            except KeyError as error:
                raise ProvisionError(f"{member} is not in {name}") from error
            with bundle.open(info) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
        return
    try:
        with tarfile.open(archive) as bundle:
            try:
                info = bundle.getmember(member)
            except KeyError as error:
                raise ProvisionError(f"{member} is not in {name}") from error
            if not info.isfile():
                raise ProvisionError(f"{member} in {name} is not a regular file")
            source = bundle.extractfile(info)
            if source is None:
                raise ProvisionError(f"cannot read {member} from {name}")
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    except tarfile.TarError as error:
        raise ProvisionError(f"cannot read {name}: {error}") from error


def _tree_relative(name: str, strip_components: int) -> PurePosixPath | None:
    raw = PurePosixPath(name)
    if raw.is_absolute() or ".." in raw.parts:
        raise ProvisionError(f"unsafe archive path: {name}")
    parts = tuple(part for part in raw.parts if part not in {"", "."})
    if len(parts) <= strip_components:
        return None
    relative = PurePosixPath(*parts[strip_components:])
    if relative.is_absolute() or ".." in relative.parts:
        raise ProvisionError(f"unsafe stripped archive path: {name}")
    return relative


def validate_tree_archive(
    archive: Path,
    strip_components: int,
    exclusions: tuple[PurePosixPath, ...] = (),
) -> list[tuple[tarfile.TarInfo, PurePosixPath]]:
    if strip_components < 0:
        raise ProvisionError("strip_components cannot be negative")
    try:
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
    except tarfile.TarError as error:
        raise ProvisionError(f"cannot read {archive.name}: {error}") from error
    if len(members) > MEMBER_LIMIT:
        raise ProvisionError(
            f"archive contains {len(members)} members; limit is {MEMBER_LIMIT}"
        )

    selected: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    seen: set[PurePosixPath] = set()
    symlinks: set[PurePosixPath] = set()
    total = 0
    for info in members:
        relative = _tree_relative(info.name, strip_components)
        if relative is None:
            if not info.isdir():
                raise ProvisionError(
                    f"archive member disappears after stripping: {info.name}"
                )
            continue
        if relative in seen:
            raise ProvisionError(f"duplicate archive path: {relative}")
        seen.add(relative)
        if info.isfile():
            total += info.size
            if total > EXTRACT_LIMIT:
                raise ProvisionError(f"extracted content exceeds {EXTRACT_LIMIT} bytes")
        elif info.isdir():
            pass
        elif info.issym():
            target = PurePosixPath(info.linkname)
            if target.is_absolute():
                raise ProvisionError(
                    f"absolute symlink target for {relative}: {info.linkname}"
                )
            resolved = posixpath.normpath(str(relative.parent / target))
            if resolved == ".." or resolved.startswith("../"):
                raise ProvisionError(
                    f"escaping symlink target for {relative}: {info.linkname}"
                )
            symlinks.add(relative)
        else:
            raise ProvisionError(f"unsupported archive member type for {relative}")
        selected.append((info, relative))

    for _info, relative in selected:
        if any(parent in symlinks for parent in relative.parents):
            raise ProvisionError(f"archive member traverses symlink parent: {relative}")

    matched: set[PurePosixPath] = set()
    retained: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    for info, relative in selected:
        exclusion = next(
            (
                candidate
                for candidate in exclusions
                if relative == candidate or candidate in relative.parents
            ),
            None,
        )
        if exclusion is None:
            retained.append((info, relative))
            continue
        if relative == exclusion and not info.isdir():
            raise ProvisionError(f"tree exclusion is not a directory: {exclusion}")
        matched.add(exclusion)
    missing = set(exclusions) - matched
    if missing:
        detail = ", ".join(str(path) for path in sorted(missing))
        raise ProvisionError(f"tree exclusions are absent from archive: {detail}")
    return retained


def extract_tree(
    archive: Path,
    destination: Path,
    strip_components: int,
    artifact_sha256: str,
    exclusions: tuple[PurePosixPath, ...] = (),
) -> None:
    selected = validate_tree_archive(archive, strip_components, exclusions)
    destination.mkdir(parents=True, exist_ok=False)
    manifest_entries: list[dict[str, Any]] = []
    try:
        with tarfile.open(archive) as bundle:
            for info, relative in sorted(
                (item for item in selected if item[0].isdir()),
                key=lambda item: len(item[1].parts),
            ):
                target = destination.joinpath(*relative.parts)
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(info.mode & 0o777)

            for info, relative in (item for item in selected if item[0].isfile()):
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(info)
                if source is None:
                    raise ProvisionError(f"cannot read archive member: {info.name}")
                digest = hashlib.sha256()
                size = 0
                with source, target.open("xb") as output:
                    while True:
                        block = source.read(CHUNK)
                        if not block:
                            break
                        size += len(block)
                        digest.update(block)
                        output.write(block)
                if size != info.size:
                    raise ProvisionError(
                        f"size mismatch extracting {relative}: {size} != {info.size}"
                    )
                mode = info.mode & 0o777
                target.chmod(mode)
                manifest_entries.append(
                    {
                        "path": str(relative),
                        "type": "file",
                        "mode": mode,
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )

            for info, relative in (item for item in selected if item[0].issym()):
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(info.linkname)
                manifest_entries.append(
                    {
                        "path": str(relative),
                        "type": "symlink",
                        "target": info.linkname,
                    }
                )
        for path in destination.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                manifest_entries.append(
                    {
                        "path": str(path.relative_to(destination)),
                        "type": "directory",
                        "mode": stat.S_IMODE(path.stat().st_mode),
                    }
                )
    except (OSError, tarfile.TarError) as error:
        raise ProvisionError(f"cannot extract {archive.name}: {error}") from error

    manifest = {
        "version": 1,
        "artifact_sha256": artifact_sha256,
        "excluded_paths": [str(path) for path in exclusions],
        "entries": sorted(manifest_entries, key=lambda entry: entry["path"]),
    }
    manifest_path = destination / TREE_MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)


def verify_tree(item: dict[str, Any]) -> None:
    target = install_path(item)
    manifest_path = target / TREE_MANIFEST
    if not target.is_dir() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise ProvisionError(f"{item['name']} tree is missing: {target}")
    ownership = marker(item["name"])
    if not ownership.is_file() or ownership.read_text(
        encoding="utf-8"
    ).strip() != digest_of(manifest_path):
        raise ProvisionError(f"{item['name']} tree ownership manifest drifted")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProvisionError(f"invalid tree manifest: {manifest_path}") from error
    expected_artifact = artifact(item).get("sha256")
    expected_exclusions = [str(path) for path in tree_exclusions(item)]
    if (
        manifest.get("version") != 1
        or manifest.get("artifact_sha256") != expected_artifact
        or manifest.get("excluded_paths", []) != expected_exclusions
    ):
        raise ProvisionError(f"{item['name']} tree manifest does not match its pin")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ProvisionError(f"invalid tree entries in {manifest_path}")

    expected_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ProvisionError(f"invalid tree entry in {manifest_path}")
        relative = _tree_relative(entry["path"], 0)
        if relative is None:
            raise ProvisionError(f"invalid empty tree entry in {manifest_path}")
        path = target.joinpath(*relative.parts)
        expected_paths.add(str(relative))
        if entry.get("type") == "directory":
            if not path.is_dir() or path.is_symlink():
                raise ProvisionError(
                    f"tree directory is missing or changed type: {path}"
                )
            if stat.S_IMODE(path.stat().st_mode) != entry.get("mode"):
                raise ProvisionError(f"tree directory mode drifted: {path}")
            continue
        if entry.get("type") == "symlink":
            if not path.is_symlink() or os.readlink(path) != entry.get("target"):
                raise ProvisionError(f"tree symlink drifted: {path}")
            continue
        if entry.get("type") != "file" or not path.is_file() or path.is_symlink():
            raise ProvisionError(f"tree file is missing or changed type: {path}")
        current = path.stat()
        if current.st_size != entry.get("size"):
            raise ProvisionError(f"tree file size drifted: {path}")
        if stat.S_IMODE(current.st_mode) != entry.get("mode"):
            raise ProvisionError(f"tree file mode drifted: {path}")
        if digest_of(path) != entry.get("sha256"):
            raise ProvisionError(f"tree file content drifted: {path}")

    actual_paths = {
        str(path.relative_to(target))
        for path in target.rglob("*")
        if path != manifest_path
    }
    extras = actual_paths - expected_paths
    missing = expected_paths - actual_paths
    if extras or missing:
        detail = ", ".join(sorted(extras or missing)[:5])
        kind = "extra" if extras else "missing"
        raise ProvisionError(f"{item['name']} tree has {kind} paths: {detail}")


def install_tree_atomically(
    item: dict[str, Any], archive: Path, entry: dict[str, Any]
) -> Path:
    target = install_path(item)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.envy-", dir=target.parent)
    )
    extracted = temporary / "tree"
    backup = target.parent / f".{target.name}.envy-old-{os.getpid()}"
    moved_old = False
    installed_new = False
    try:
        extract_tree(
            archive,
            extracted,
            int(entry.get("strip_components", 0)),
            str(entry["sha256"]),
            tree_exclusions(item),
        )
        command = managed_command(item)
        assert command is not None
        staged_command = extracted / command.relative_to(target)
        if not staged_command.is_file() or not os.access(staged_command, os.X_OK):
            raise ProvisionError(
                f"tree executable is missing or not executable: {staged_command}"
            )
        if target.exists() or target.is_symlink():
            if not marker(item["name"]).is_file():
                raise ProvisionError(f"refusing to replace unowned tree: {target}")
            if backup.exists() or backup.is_symlink():
                raise ProvisionError(f"stale tree backup blocks install: {backup}")
            os.replace(target, backup)
            moved_old = True
        os.replace(extracted, target)
        installed_new = True
        if backup.exists():
            shutil.rmtree(backup)
            moved_old = False
        return target
    except OSError as error:
        if moved_old and not installed_new and backup.exists() and not target.exists():
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise ProvisionError(f"cannot install tree at {target}: {error}") from error
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def install_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = source.stat().st_mode
    source.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    temporary = target.parent / f".{target.name}.envy-{os.getpid()}"
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(source.stat().st_mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def brew_available(item: dict[str, Any]) -> bool:
    return (
        platform.system() == "Darwin"
        and bool(item["provision"].get("brew_formula"))
        and shutil.which("brew") is not None
    )


def can_install(item: dict[str, Any]) -> tuple[bool, str]:
    if brew_available(item):
        return True, "brew"
    try:
        entry = artifact(item)
        if is_tree(item):
            tree_exclusions(item)
    except ProvisionError as error:
        return False, str(error)
    if not entry.get("sha256"):
        return False, (
            f"{item['name']} {item['version']} has no recorded sha256 for "
            f"{platform_key()}; run: python3 lib/provision.py record {item['name']}"
        )
    return True, "verified upstream release"


def installed_version(item: dict[str, Any], command: str | None = None) -> str | None:
    managed = managed_command(item)
    command = command or (str(managed) if managed is not None else None)
    command = command or shutil.which(item["provision"]["command"])
    if not command:
        return None
    probe = item["provision"].get("version_probe", ["--version"])
    try:
        result = subprocess.run(
            [command, *probe],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw_pattern = item["provision"].get("version_pattern")
    pattern = (
        re.compile(raw_pattern) if isinstance(raw_pattern, str) else VERSION_PATTERN
    )
    found = pattern.search(result.stdout)
    if not found:
        return None
    return found.group(1) if found.lastindex else found.group(0)


def install(name: str, force: bool = False) -> int:
    try:
        item = dependency(name)
    except ProvisionError:
        # Not a provisioned tool; pkg_ensure falls back to its other sources.
        return 2
    pin = item["version"]
    if is_tree(item):
        existing = managed_command(item)
        if existing is not None and not force:
            try:
                verify_tree(item)
            except ProvisionError:
                pass
            else:
                current = installed_version(item, str(existing))
                if current == pin:
                    print(f"  OK {name} {pin} found at {existing}")
                    return 0
    else:
        existing = shutil.which(item["provision"]["command"])
        if existing and not force:
            current = installed_version(item, existing)
            if current == pin:
                print(f"  OK {name} {pin} found at {existing}")
                return 0
    usable, reason = can_install(item)
    if not usable:
        print(f"  cannot install {name} {pin}: {reason}", file=sys.stderr)
        return 1
    if brew_available(item):
        formula = item["provision"]["brew_formula"]
        result = subprocess.run(["brew", "install", formula], check=False)
        if result.returncode != 0:
            return result.returncode
        located = shutil.which(item["provision"]["command"])
        if located:
            print(f"  OK {name} installed at {located}")
            marker(name).parent.mkdir(parents=True, exist_ok=True)
            marker(name).touch()
            return 0
        return 1
    entry = artifact(item)
    target = install_path(item)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "artifact"
        download(entry["url"], archive)
        actual = digest_of(archive)
        if actual != entry["sha256"]:
            print(
                f"  refusing to install {name}: sha256 mismatch for {entry['url']}\n"
                f"    expected {entry['sha256']}\n    actual   {actual}",
                file=sys.stderr,
            )
            return 1
        if is_tree(item):
            install_tree_atomically(item, archive, entry)
        else:
            binary = Path(scratch) / "binary"
            extract_member(archive, entry.get("member"), binary)
            install_atomically(binary, target)
    marker(name).parent.mkdir(parents=True, exist_ok=True)
    if is_tree(item):
        marker(name).write_text(
            digest_of(target / TREE_MANIFEST) + "\n", encoding="utf-8"
        )
        marker(name).chmod(0o600)
    else:
        marker(name).touch()
    command = managed_command(item) if is_tree(item) else target
    current = installed_version(item, str(command))
    if current != pin:
        print(
            f"  installed {name} at {target} but it reports {current or 'unknown'}, "
            f"not {pin}",
            file=sys.stderr,
        )
        return 1
    print(f"  OK {name} {pin} installed at {target}")
    return 0


def verify(name: str) -> int:
    item = dependency(name)
    pin = item["version"]
    if is_tree(item):
        verify_tree(item)
    current = installed_version(item)
    if current is None:
        print(f"envy provision: {name} is not installed", file=sys.stderr)
        return 1
    if current != pin:
        print(f"envy provision: {name} is {current}; expected {pin}", file=sys.stderr)
        return 1
    return 0


def record(name: str, platforms: list[str]) -> int:
    """Maintainer action: fetch each artifact and store its digest. Needs network."""
    path = ROOT / "dependencies.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = [
        item
        for item in document.get("dependencies", [])
        if item.get("name") == name and isinstance(item.get("provision"), dict)
    ]
    if not entries:
        print(f"envy provision: no provisioned dependency '{name}'", file=sys.stderr)
        return 1
    item = entries[0]
    artifacts = item["provision"].get("artifacts", {})
    selected = platforms or sorted(artifacts)
    failures = 0
    recorded = 0
    for key in selected:
        entry = artifacts.get(key)
        if not isinstance(entry, dict):
            print(f"  {key}: no artifact declared", file=sys.stderr)
            failures += 1
            continue
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "artifact"
            try:
                download(entry["url"], archive)
            except ProvisionError as error:
                print(f"  {key}: {error}", file=sys.stderr)
                failures += 1
                continue
            value = digest_of(archive)
            member = entry.get("member")
            if is_tree(item):
                try:
                    validate_tree_archive(
                        archive,
                        int(entry.get("strip_components", 0)),
                        tree_exclusions(item),
                    )
                except ProvisionError as error:
                    print(f"  {key}: {error}", file=sys.stderr)
                    failures += 1
                    continue
            elif member is not None:
                try:
                    extract_member(archive, member, Path(scratch) / "binary")
                except ProvisionError as error:
                    print(f"  {key}: {error}", file=sys.stderr)
                    failures += 1
                    continue
        if entry.get("sha256") != value:
            entry["sha256"] = value
            recorded += 1
        print(f"  {key}: {value}")
    # Leave the manifest untouched when nothing was recorded, so a run that
    # fails outright cannot churn or truncate it.
    if recorded:
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 1 if failures else 0


def remove(name: str) -> int:
    item = dependency(name)
    if not is_tree(item):
        raise ProvisionError("remove is only supported for managed trees")
    target = install_path(item)
    try:
        target.resolve().relative_to(paths.data_dir().resolve())
    except ValueError as error:
        raise ProvisionError(
            f"refusing to remove tree outside envy data: {target}"
        ) from error
    if not marker(name).is_file():
        raise ProvisionError(f"refusing to remove unowned tree: {target}")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise ProvisionError(f"managed tree has unexpected type: {target}")
        shutil.rmtree(target)
    marker(name).unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    for action in ("install", "verify", "remove", "can-install", "declared"):
        current = subcommands.add_parser(action)
        current.add_argument("name")
        if action == "install":
            current.add_argument("--force", action="store_true")
    recorder = subcommands.add_parser("record")
    recorder.add_argument("name")
    recorder.add_argument("--platform", action="append", default=[])
    arguments = parser.parse_args()
    try:
        if arguments.action == "install":
            return install(arguments.name, force=arguments.force)
        if arguments.action == "verify":
            return verify(arguments.name)
        if arguments.action == "remove":
            return remove(arguments.name)
        if arguments.action == "record":
            return record(arguments.name, arguments.platform)
        if arguments.action == "declared":
            try:
                dependency(arguments.name)
            except ProvisionError:
                return 1
            return 0
        usable, reason = can_install(dependency(arguments.name))
        if not usable:
            print(reason, file=sys.stderr)
            return 1
        print(reason)
        return 0
    except (ProvisionError, OSError, json.JSONDecodeError) as error:
        print(f"envy provision: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
