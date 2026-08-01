#!/usr/bin/env python3
"""Assemble non-overlapping chezmoi sources into one owned working tree."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


class CompositionError(RuntimeError):
    pass


def source_files(root: Path):
    if not root.is_dir() or root.is_symlink():
        raise CompositionError(f"chezmoi source is not a regular directory: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CompositionError(f"source symlinks are not supported: {path}")
        if path.is_file():
            yield path.relative_to(root), path


def compose(destination: Path, sources: list[Path]) -> None:
    if not sources:
        raise CompositionError("at least one chezmoi source is required")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    previous = destination.with_name(f".{destination.name}.previous-{os.getpid()}")
    owners: dict[str, str] = {}
    try:
        for source in sources:
            source = source.expanduser().resolve()
            for relative, path in source_files(source):
                key = relative.as_posix()
                if key in owners:
                    raise CompositionError(
                        f"chezmoi source collision for {key}: {owners[key]} and {source}"
                    )
                owners[key] = str(source)
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        ledger = destination.parent / f"{destination.name}.owners.json"
        ledger_temp = ledger.with_name(f".{ledger.name}.{os.getpid()}")
        ledger_temp.write_text(
            json.dumps({"version": 1, "files": owners}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.chmod(ledger_temp, 0o600)
        if previous.exists():
            shutil.rmtree(previous)
        if destination.exists():
            destination.rename(previous)
        temporary.rename(destination)
        os.replace(ledger_temp, ledger)
        if previous.exists():
            shutil.rmtree(previous)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        if previous.exists() and not destination.exists():
            previous.rename(destination)
        raise


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: compose_chezmoi.py DESTINATION SOURCE...", file=sys.stderr)
        return 2
    try:
        compose(Path(sys.argv[1]), [Path(value) for value in sys.argv[2:]])
        return 0
    except (CompositionError, OSError) as error:
        print(f"chezmoi composition: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
