#!/usr/bin/env python3
"""Verify pinned envy externals: Git checkouts and provisioned binaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provision  # noqa: E402


class DependencyError(RuntimeError):
    pass


def run(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def entries(root: Path) -> list[dict[str, Any]]:
    value = json.loads((root / "dependencies.json").read_text(encoding="utf-8"))
    return [
        item
        for item in value.get("dependencies", [])
        if isinstance(item, dict) and item.get("installed_path") and item.get("commit")
    ]


def provisioned(root: Path) -> list[dict[str, Any]]:
    value = json.loads((root / "dependencies.json").read_text(encoding="utf-8"))
    return [
        item
        for item in value.get("dependencies", [])
        if isinstance(item, dict) and isinstance(item.get("provision"), dict)
    ]


def verify_binary(item: dict[str, Any], converge: bool) -> None:
    """Hold envy-owned binaries to the pin; only warn about foreign ones."""
    name = item["name"]
    expected = item["version"]
    actual = provision.installed_version(item)
    if actual is None:
        # Absent tools are reported by the owning feature's pkg_ensure.
        return
    if actual == expected:
        return
    owned = provision.marker(name).is_file()
    if not owned:
        print(
            f"envy dependencies: {name} is {actual}; pinned at {expected} "
            "(not installed by envy, leaving it alone)",
            file=sys.stderr,
        )
        return
    if not converge:
        raise DependencyError(f"{name} is {actual}; expected {expected}")
    usable, reason = provision.can_install(item)
    if not usable:
        raise DependencyError(f"cannot repin {name} to {expected}: {reason}")
    if provision.install(name, force=True) != 0:
        raise DependencyError(f"could not repin {name} to {expected}")


def verify_one(item: dict[str, Any], converge: bool) -> None:
    raw_path = item["installed_path"]
    if raw_path.startswith("~/.local/share/") and os.environ.get("XDG_DATA_HOME"):
        path = Path(os.environ["XDG_DATA_HOME"]) / raw_path.removeprefix(
            "~/.local/share/"
        )
    else:
        path = Path(raw_path).expanduser()
    expected = item["commit"]
    if not (path / ".git").exists():
        # A missing checkout is drift, so verification must report it and let
        # the feature reconverge and re-clone. Convergence itself only warns:
        # .chezmoiexternal.toml states that a host without upstream access
        # simply skips the clone and the shell guards its sourcing, so an
        # unreachable external must not fail chezmoi and everything behind it.
        if not converge:
            raise DependencyError(f"{item['name']} checkout is missing: {path}")
        print(
            f"envy dependencies: {item['name']} checkout is missing: {path}",
            file=sys.stderr,
        )
        return
    actual = run(path, "rev-parse", "HEAD")
    if actual.returncode != 0:
        raise DependencyError(f"cannot inspect {item['name']}: {actual.stderr.strip()}")
    if actual.stdout.strip() == expected:
        return
    if not converge:
        raise DependencyError(
            f"{item['name']} is at {actual.stdout.strip()}; expected {expected}"
        )
    status = run(path, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise DependencyError(f"refusing to change modified dependency: {path}")
    present = run(path, "cat-file", "-e", f"{expected}^{{commit}}")
    if present.returncode != 0:
        raise DependencyError(
            f"immutable commit for {item['name']} is unavailable locally; recreate {path}"
        )
    checkout = run(path, "checkout", "--detach", expected)
    if checkout.returncode != 0:
        raise DependencyError(
            f"could not pin {item['name']}: {checkout.stderr.strip()}"
        )


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if action not in {"converge", "verify"}:
        return 2
    root = Path(__file__).resolve().parent.parent
    try:
        for item in entries(root):
            verify_one(item, converge=action == "converge")
        for item in provisioned(root):
            verify_binary(item, converge=action == "converge")
        return 0
    except (DependencyError, provision.ProvisionError, OSError, json.JSONDecodeError) as error:
        print(f"envy dependencies: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
