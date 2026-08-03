#!/usr/bin/env python3
"""Converge portable tmux persistence and agent lifecycle adapters."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


HOME = Path.home()
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


def ownership() -> dict[str, Any]:
    value = load_json(OWNERSHIP)
    if not value:
        return {"version": 1, "files": {}}
    if value.get("version") != 1 or not isinstance(value.get("files"), dict):
        raise FeatureError(f"unsupported ownership state at {OWNERSHIP}")
    return value


def backup(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = BACKUP_ROOT / f"{label}.{timestamp}.json"
    shutil.copy2(path, destination)
    return destination


def managed_handler(agent: str, action: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": f'"$HOME/.local/bin/nv-agent-register" {agent} {action}',
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


def desired_hooks(agent: str, current: dict[str, Any]) -> dict[str, Any]:
    result = remove_managed_hooks(current)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise FeatureError(f"hooks must be an object in {HOOK_FILES[agent]}")
    for event, action in (("SessionStart", "start"), ("SessionEnd", "end")):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise FeatureError(f"hooks.{event} must be an array in {HOOK_FILES[agent]}")
        groups.append({"hooks": [managed_handler(agent, action)]})
    return result


def hook_present(agent: str, action: str, value: dict[str, Any]) -> bool:
    event = "SessionStart" if action == "start" else "SessionEnd"
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        return False
    expected = managed_handler(agent, action)["command"]
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
    missing = [
        HOME / ".local" / "bin" / helper
        for helper in HELPERS
        if not os.access(HOME / ".local" / "bin" / helper, os.X_OK)
    ]
    missing.extend(path for path in PLUGIN_FILES if not os.access(path, os.X_OK))
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
    missing = assets_missing(adapters)
    if missing:
        raise FeatureError(
            "tmux persistence assets are missing; reconverge chezmoi first: "
            + ", ".join(str(path) for path in missing)
        )

    owner = ownership()
    for agent, path in HOOK_FILES.items():
        current = load_json(path)
        desired = (
            desired_hooks(agent, current)
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
        "version=2\nadapters=" + ",".join(sorted(adapters)) + "\n",
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
    if not MARKER.is_file():
        problems.append(f"feature marker missing: {MARKER}")
    else:
        expected_marker = "version=2\nadapters=" + ",".join(sorted(adapters)) + "\n"
        if MARKER.read_text(encoding="utf-8") != expected_marker:
            problems.append(f"feature marker is stale: {MARKER}")
    for path in assets_missing(adapters):
        problems.append(f"asset missing: {path}")
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
            if not hook_present(agent, action, value):
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
