#!/usr/bin/env python3
"""Converge portable tmux persistence and agent lifecycle adapters."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import paths  # noqa: E402

# XDG bases, for locations other tools own (editor, terminal, agents).
CONFIG_HOME = paths.xdg_config_home()
STATE_HOME = paths.xdg_state_home()
MARKER = paths.config_dir() / "tmux-persistence.enabled"
STATE_ROOT = paths.state_dir() / "tmux-persistence"
BACKUP_ROOT = paths.state_dir() / "backups" / "tmux-persistence"
OWNERSHIP = STATE_ROOT / "ownership.json"
HOOK_TOKEN = "nv-agent-register"

HOOK_FILES = {
    "claude": HOME / ".claude" / "settings.json",
    "codex": HOME / ".codex" / "hooks.json",
}
HELPERS = (
    "nv-agent-register",
    "nv-agent-resume",
    "nv-tmux-bootstrap",
    "nv-tmux-restore",
    "nv-tmux-save",
    "nv-tmux-snapshot",
)
PLUGIN_FILES = (
    paths.data_dir() / "tmux" / "tmux-resurrect" / "resurrect.tmux",
    paths.data_dir() / "tmux" / "tmux-continuum" / "continuum.tmux",
)
PI_EXTENSION = HOME / ".pi" / "agent" / "extensions" / "nv-tmux-session.ts"
OWNERSHIP_VERSION = 2


class FeatureError(RuntimeError):
    pass


def selected_adapters() -> set[str]:
    manifest_path = os.environ.get("ENVY_MANIFEST")
    if not manifest_path:
        return set()
    manifest = load_json(Path(manifest_path))
    raw = (
        manifest.get("settings", {})
        .get("tmux_persistence", {})
        .get("agent_adapters", [])
    )
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise FeatureError("settings.tmux_persistence.agent_adapters must be an array")
    unknown = set(raw) - {*HOOK_FILES, "pi"}
    if unknown:
        raise FeatureError(f"unknown tmux agent adapters: {', '.join(sorted(unknown))}")
    return set(raw)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as error:
        raise FeatureError(f"invalid JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise FeatureError(f"expected a JSON object at {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def canonical_bin_dir() -> Path:
    value = str(paths.bin_dir())
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise FeatureError("ENVY_BIN_DIR must not contain control characters")
    path = Path(value)
    if not path.is_absolute():
        raise FeatureError(f"ENVY_BIN_DIR must be an absolute path: {value}")
    return Path(os.path.normpath(value))


def helper_sources() -> dict[str, Path]:
    return {
        helper: ROOT / "chezmoi" / "dot_local" / "bin" / f"executable_{helper}"
        for helper in HELPERS
    }


def ownership() -> dict[str, Any]:
    value = load_json(OWNERSHIP)
    if not value:
        return {
            "version": OWNERSHIP_VERSION,
            "files": {},
            "helpers": {},
            "bin_dir": None,
        }
    if value.get("version") == 1 and isinstance(value.get("files"), dict):
        return {
            "version": OWNERSHIP_VERSION,
            "files": value["files"],
            "helpers": {},
            "bin_dir": None,
        }
    if (
        value.get("version") != OWNERSHIP_VERSION
        or not isinstance(value.get("files"), dict)
        or not isinstance(value.get("helpers"), dict)
        or (
            value.get("bin_dir") is not None
            and not isinstance(value.get("bin_dir"), str)
        )
    ):
        raise FeatureError(f"unsupported ownership state at {OWNERSHIP}")
    for target, entry in value["helpers"].items():
        if (
            not isinstance(target, str)
            or not isinstance(entry, dict)
            or not isinstance(entry.get("created"), bool)
            or not isinstance(entry.get("digest"), str)
        ):
            raise FeatureError(f"invalid helper ownership state at {OWNERSHIP}")
    return value


def atomic_helper(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with source.open("rb") as source_handle, os.fdopen(fd, "wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def converge_helpers(owner: dict[str, Any], bin_dir: Path) -> None:
    previous = owner["helpers"]
    sources = helper_sources()
    desired: dict[str, tuple[Path, Path, str, bool, bool]] = {}

    # Validate every destination before changing any of them. A pre-existing
    # helper is accepted only when it is already the exact executable asset;
    # a helper previously created by this feature is replaceable only while it
    # still has the digest recorded when it was written.
    for helper, source in sources.items():
        if not source.is_file():
            raise FeatureError(f"tmux persistence helper source is missing: {source}")
        source_digest = digest(source)
        target = bin_dir / helper
        entry = previous.get(str(target))
        created = bool(isinstance(entry, dict) and entry.get("created") is True)
        exists = os.path.lexists(target)
        replace = not exists
        if exists:
            if target.is_symlink() or not target.is_file():
                raise FeatureError(
                    f"tmux persistence helper target is not a regular file: {target}"
                )
            current_digest = digest(target)
            if created:
                if current_digest != entry["digest"]:
                    raise FeatureError(
                        f"tmux persistence helper was modified; refusing to overwrite: {target}"
                    )
                replace = current_digest != source_digest or not os.access(target, os.X_OK)
            elif current_digest != source_digest or not os.access(target, os.X_OK):
                raise FeatureError(
                    f"tmux persistence helper conflicts with the managed asset: {target}"
                )
        desired[str(target)] = (
            source,
            target,
            source_digest,
            created or not exists,
            replace,
        )

    for source, target, _source_digest, _created, replace in desired.values():
        if replace:
            atomic_helper(source, target)

    desired_paths = set(desired)
    for target_value, entry in previous.items():
        if target_value in desired_paths or entry.get("created") is not True:
            continue
        target = Path(target_value)
        if (
            not target.is_symlink()
            and target.is_file()
            and digest(target) == entry["digest"]
        ):
            target.unlink()

    owner["helpers"] = {
        target_value: {"created": created, "digest": source_digest}
        for target_value, (_source, _target, source_digest, created, _replace) in desired.items()
    }
    owner["bin_dir"] = str(bin_dir)


def helper_problems(bin_dir: Path) -> list[str]:
    problems: list[str] = []
    for helper, source in helper_sources().items():
        target = bin_dir / helper
        if os.path.lexists(target) and (target.is_symlink() or not target.is_file()):
            problems.append(f"asset invalid (expected a regular file): {target}")
        elif not target.is_file() or not os.access(target, os.X_OK):
            problems.append(f"asset missing: {target}")
        elif not source.is_file() or digest(target) != digest(source):
            problems.append(f"asset stale: {target}")
    return problems


def backup(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = BACKUP_ROOT / f"{label}.{timestamp}.json"
    shutil.copy2(path, destination)
    return destination


def managed_handler(
    agent: str, action: str, bin_dir: Path | None = None
) -> dict[str, Any]:
    register = (bin_dir or canonical_bin_dir()) / "nv-agent-register"
    return {
        "type": "command",
        "command": f"{shlex.quote(str(register))} {agent} {action}",
        "timeout": 3,
    }


def remove_managed_hooks(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        retained_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                retained_groups.append(group)
                continue
            retained_handlers = [
                handler
                for handler in group["hooks"]
                if not (
                    isinstance(handler, dict)
                    and isinstance(handler.get("command"), str)
                    and HOOK_TOKEN in handler["command"]
                )
            ]
            if retained_handlers:
                updated = copy.deepcopy(group)
                updated["hooks"] = retained_handlers
                retained_groups.append(updated)
        if retained_groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result


def desired_hooks(agent: str, current: dict[str, Any], bin_dir: Path) -> dict[str, Any]:
    result = remove_managed_hooks(current)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise FeatureError(f"hooks must be an object in {HOOK_FILES[agent]}")
    for event, action in (("SessionStart", "start"), ("SessionEnd", "end")):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise FeatureError(f"hooks.{event} must be an array in {HOOK_FILES[agent]}")
        groups.append({"hooks": [managed_handler(agent, action, bin_dir)]})
    return result


def hook_present(
    agent: str, action: str, value: dict[str, Any], bin_dir: Path
) -> bool:
    event = "SessionStart" if action == "start" else "SessionEnd"
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        return False
    expected = managed_handler(agent, action, bin_dir)["command"]
    return any(
        isinstance(group, dict)
        and isinstance(group.get("hooks"), list)
        and any(
            isinstance(handler, dict) and handler.get("command") == expected
            for handler in group.get("hooks", [])
        )
        for group in groups
    )


def assets_missing(adapters: set[str]) -> list[Path]:
    missing = [path for path in PLUGIN_FILES if not os.access(path, os.X_OK)]
    if "pi" in adapters and not PI_EXTENSION.is_file():
        missing.append(PI_EXTENSION)
    return missing


def source_running_tmux() -> None:
    if not shutil.which("tmux") or not (HOME / ".tmux.conf").is_file():
        return
    probe = subprocess.run(
        ["tmux", "list-sessions"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        subprocess.run(
            ["tmux", "source-file", str(HOME / ".tmux.conf")],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def converge() -> int:
    adapters = selected_adapters()
    bin_dir = canonical_bin_dir()
    missing = assets_missing(adapters)
    if missing:
        raise FeatureError(
            "tmux persistence assets are missing; reconverge chezmoi first: "
            + ", ".join(str(path) for path in missing)
        )

    owner = ownership()
    converge_helpers(owner, bin_dir)
    # Persist helper ownership before touching third-party hook files. If a
    # malformed hook blocks the rest of convergence, a later removal must
    # still know which helper copies this feature created.
    atomic_json(OWNERSHIP, owner)
    for agent, path in HOOK_FILES.items():
        current = load_json(path)
        desired = (
            desired_hooks(agent, current, bin_dir)
            if agent in adapters
            else remove_managed_hooks(current)
        )
        if current == desired:
            continue
        entry = owner["files"].setdefault(str(path), {"created": not path.exists()})
        saved = backup(path, f"{agent}-hooks")
        if saved:
            entry.setdefault("backups", []).append(str(saved))
        atomic_json(path, desired)

    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(
        "version=3\nbin_dir="
        + str(bin_dir)
        + "\nadapters="
        + ",".join(sorted(adapters))
        + "\n",
        encoding="utf-8",
    )
    os.chmod(MARKER, 0o600)
    atomic_json(OWNERSHIP, owner)
    source_running_tmux()
    print("tmux persistence and agent session adapters are configured")
    return 0


def verify(verbose: bool = False) -> int:
    problems: list[str] = []
    adapters = selected_adapters()
    bin_dir = canonical_bin_dir()
    if not MARKER.is_file():
        problems.append(f"feature marker missing: {MARKER}")
    else:
        expected_marker = (
            "version=3\nbin_dir="
            + str(bin_dir)
            + "\nadapters="
            + ",".join(sorted(adapters))
            + "\n"
        )
        if MARKER.read_text(encoding="utf-8") != expected_marker:
            problems.append(f"feature marker is stale: {MARKER}")
    for path in assets_missing(adapters):
        problems.append(f"asset missing: {path}")
    problems.extend(helper_problems(bin_dir))
    for agent, path in HOOK_FILES.items():
        try:
            value = load_json(path)
        except FeatureError as error:
            problems.append(str(error))
            continue
        if agent not in adapters:
            if value != remove_managed_hooks(value):
                problems.append(f"disabled {agent} adapter hook remains in {path}")
            continue
        for action in ("start", "end"):
            if not hook_present(agent, action, value, bin_dir):
                problems.append(f"{agent} {action} hook missing from {path}")

    if verbose:
        if problems:
            for problem in problems:
                print(f"tmux-persistence: {problem}", file=sys.stderr)
        else:
            print(
                "tmux-persistence: plugins, helpers, and selected agent adapters are current"
            )
            if "codex" in adapters:
                print(
                    "tmux-persistence: Codex may require one-time hook review with /hooks"
                )
    return 1 if problems else 0


def remove() -> int:
    had_ownership = OWNERSHIP.exists()
    owner = ownership()
    for _agent, path in HOOK_FILES.items():
        if not path.exists():
            continue
        current = load_json(path)
        desired = remove_managed_hooks(current)
        entry = owner.get("files", {}).get(str(path), {})
        if not desired and entry.get("created") is True:
            path.unlink()
        elif desired != current:
            backup(path, f"remove-{path.parent.name}")
            atomic_json(path, desired)
    if MARKER.exists():
        MARKER.unlink()
    for target_value, entry in owner["helpers"].items():
        if entry.get("created") is not True:
            continue
        target = Path(target_value)
        if (
            not target.is_symlink()
            and target.is_file()
            and digest(target) == entry["digest"]
        ):
            target.unlink()
    owner["helpers"] = {}
    owner["bin_dir"] = None
    if had_ownership:
        atomic_json(OWNERSHIP, owner)
    print(
        "Removed tmux persistence activation and agent hooks; retained snapshots and plugins"
    )
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "converge"
    try:
        if action == "converge":
            return converge()
        if action == "verify":
            return verify(False)
        if action == "doctor":
            return verify(True)
        if action == "remove":
            return remove()
        print(
            "usage: tmux-persistence.py {converge|verify|doctor|remove}",
            file=sys.stderr,
        )
        return 2
    except (FeatureError, OSError) as error:
        print(f"tmux-persistence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
