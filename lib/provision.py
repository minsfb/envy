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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_TIMEOUT = 180
# Upstream release archives for these tools are single-digit megabytes; the cap
# only exists so a redirected or hostile URL cannot exhaust the disk.
DOWNLOAD_LIMIT = 256 * 1024 * 1024
CHUNK = 1024 * 1024
VERSION_PATTERN = re.compile(r"\d+(?:\.\d+)+")


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
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "envy"


def marker(name: str) -> Path:
    return state_root() / "providers" / f"{name}.installed"


def install_path(item: dict[str, Any]) -> Path:
    raw = item["provision"]["install_path"]
    if raw.startswith("~/.local/bin/"):
        home = os.environ.get("ENVY_BIN_HOME")
        if home:
            return Path(home) / raw.removeprefix("~/.local/bin/")
    return Path(raw).expanduser()


def digest_of(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            value.update(block)
    return value.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "envy"})
    try:
        # urlopen honors the proxy environment that pkg_use_external_proxy exports.
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
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
    except ProvisionError as error:
        return False, str(error)
    if not entry.get("sha256"):
        return False, (
            f"{item['name']} {item['version']} has no recorded sha256 for "
            f"{platform_key()}; run: python3 lib/provision.py record {item['name']}"
        )
    return True, "verified upstream release"


def installed_version(item: dict[str, Any], command: str | None = None) -> str | None:
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
    found = VERSION_PATTERN.search(result.stdout)
    return found.group(0) if found else None


def install(name: str, force: bool = False) -> int:
    try:
        item = dependency(name)
    except ProvisionError:
        # Not a provisioned tool; pkg_ensure falls back to its other sources.
        return 2
    pin = item["version"]
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
        binary = Path(scratch) / "binary"
        extract_member(archive, entry.get("member"), binary)
        install_atomically(binary, target)
    marker(name).parent.mkdir(parents=True, exist_ok=True)
    marker(name).touch()
    current = installed_version(item, str(target))
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
    current = installed_version(item)
    if current is None:
        print(f"envy provision: {name} is not installed", file=sys.stderr)
        return 1
    if current != pin:
        print(
            f"envy provision: {name} is {current}; expected {pin}", file=sys.stderr
        )
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
            if member is not None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    for action in ("install", "verify", "can-install", "declared"):
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
