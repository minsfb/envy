#!/usr/bin/env python3
"""Move a legacy mins-env layout onto the configured envy roots.

The order is always migrate, verify, then remove. A legacy root is only deleted
once its replacement has been confirmed byte-for-byte, so an interrupted or
failed run leaves the original in place and can simply be retried.

Repository paths recorded in state are deliberately not rewritten. The core
records its own manifest and root on every run, so those self-correct; only
paths that point into the directories being moved are rewritten here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

LEGACY_NAME = "mins-env"
MARKER_NAME = "migrated-from-mins-env"
TEXT_SUFFIXES = {".json", ".env", ".sh", ".list", ".txt", ".toml", ".log", ""}
MAX_REWRITE_BYTES = 4 * 1024 * 1024


class MigrationError(RuntimeError):
    pass


def legacy_roots() -> list[tuple[Path, Path]]:
    """(legacy, current) pairs, most specific first."""
    return [
        (paths.xdg_config_home() / LEGACY_NAME, paths.config_dir()),
        (paths.xdg_state_home() / LEGACY_NAME, paths.state_dir()),
        (paths.xdg_data_home() / LEGACY_NAME, paths.data_dir()),
        (paths.xdg_data_home() / "mins-zsh", paths.data_dir() / "zsh"),
        (paths.xdg_data_home() / "mins-tmux", paths.data_dir() / "tmux"),
    ]


def legacy_commands() -> list[Path]:
    """Superseded launcher and managed helpers, now installed under envy names."""
    names = (
        "mins-env",
        "mins-agent-notify",
        "mins-agent-register",
        "mins-agent-resume",
        "mins-terminal-notify",
        "mins-tmux-bootstrap",
        "mins-tmux-restore",
        "mins-tmux-save",
        "mins-tmux-snapshot",
    )
    return [paths.bin_dir() / name for name in names]


def marker() -> Path:
    return paths.state_dir() / MARKER_NAME


def digest_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = "link:" + os.readlink(path)
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def copy_verified(source: Path, destination: Path) -> None:
    expected = digest_tree(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    actual = digest_tree(destination)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        differing = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
        raise MigrationError(
            f"copy of {source} did not verify; missing={missing[:5]}, "
            f"differing={differing[:5]}"
        )


def rewrite_paths(root: Path, replacements: list[tuple[str, str]]) -> int:
    """Repoint references that name a directory this migration moved."""
    changed = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > MAX_REWRITE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def plan() -> list[tuple[Path, Path]]:
    pending = []
    for legacy, current in legacy_roots():
        if not legacy.is_dir() or legacy.is_symlink():
            continue
        if current.exists() and any(current.iterdir()):
            # Already populated; the legacy copy is stale, not authoritative.
            continue
        pending.append((legacy, current))
    return pending


def migrate(dry_run: bool = False) -> int:
    if marker().is_file():
        return 0
    pending = plan()
    stale = [legacy for legacy, _ in legacy_roots() if legacy.is_dir()]
    orphans = [p for p in legacy_commands() if p.is_file() or p.is_symlink()]
    if not pending and not stale and not orphans:
        return 0
    if dry_run:
        for legacy, current in pending:
            print(f"  would migrate {legacy} -> {current}")
        for legacy in stale:
            if legacy not in [item[0] for item in pending]:
                print(f"  would remove superseded {legacy}")
        for orphan in legacy_commands():
            if orphan.is_file() or orphan.is_symlink():
                print(f"  would remove superseded {orphan}")
        return 0

    replacements = [(str(legacy), str(current)) for legacy, current in legacy_roots()]
    migrated: list[Path] = []
    for legacy, current in pending:
        print(f"  migrating {legacy} -> {current}")
        copy_verified(legacy, current)
        rewrite_paths(current, replacements)
        migrated.append(legacy)

    # Only now that every replacement verified is the legacy layout given back.
    for legacy in stale:
        shutil.rmtree(legacy)
        print(f"  removed {legacy}")
    for orphan in legacy_commands():
        if orphan.is_file() or orphan.is_symlink():
            orphan.unlink()
            print(f"  removed {orphan}")

    marker().parent.mkdir(parents=True, exist_ok=True)
    marker().write_text(
        json.dumps({"migrated": [str(p) for p in migrated]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    try:
        return migrate(dry_run=dry_run)
    except (MigrationError, OSError) as error:
        print(f"envy migrate: {error}", file=sys.stderr)
        print("envy migrate: the legacy layout was left in place", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
