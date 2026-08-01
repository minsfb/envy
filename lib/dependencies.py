#!/usr/bin/env python3
"""Verify or select immutable commits for envy-owned Git externals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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
        raise DependencyError(f"{item['name']} checkout is missing: {path}")
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
        return 0
    except (DependencyError, OSError, json.JSONDecodeError) as error:
        print(f"envy dependencies: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
