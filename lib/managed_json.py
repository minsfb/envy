#!/usr/bin/env python3
"""Add managed JSON defaults while preserving machine-local choices."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


def merge(managed: Any, local: Any) -> Any:
    if isinstance(managed, dict) and isinstance(local, dict):
        result = deepcopy(local)
        for key, value in managed.items():
            result[key] = merge(value, local[key]) if key in local else deepcopy(value)
        return result
    if isinstance(managed, list) and isinstance(local, list):
        result = deepcopy(local)
        for value in managed:
            if value not in result:
                result.append(deepcopy(value))
        return result
    return deepcopy(local)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def desired(managed: Path, target: Path) -> Any:
    local = load(target) if target.exists() else {}
    return merge(load(managed), local)


def atomic_write(target: Path, value: Any) -> None:
    actual = target.resolve() if target.is_symlink() else target
    actual.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(actual.stat().st_mode) if actual.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{actual.name}.", dir=actual.parent
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, actual)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup(target: Path, directory: Path | None) -> Path | None:
    if directory is None or not target.exists():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = directory / f"{target.name}.{timestamp}.json"
    shutil.copy2(target.resolve() if target.is_symlink() else target, destination)
    return destination


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("apply", "verify"))
    result.add_argument("--managed", type=Path, required=True)
    result.add_argument("--target", type=Path, required=True)
    result.add_argument("--backup-dir", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    target = args.target.expanduser()
    managed = args.managed.expanduser()
    expected = desired(managed, target)
    current = load(target) if target.exists() else None
    if args.action == "verify":
        return 0 if current == expected else 1
    if current == expected:
        print(f"Managed JSON is current: {target}")
        return 0
    saved = backup(target, args.backup_dir.expanduser() if args.backup_dir else None)
    atomic_write(target, expected)
    print(f"Merged managed JSON: {target}")
    if saved:
        print(f"Backup: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
