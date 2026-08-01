#!/usr/bin/env python3
"""Configure portable terminal-bell notifications for interactive agents."""

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
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state"))
MARKER = CONFIG_HOME / "envy" / "agent-notifications.enabled"
STATE_ROOT = STATE_HOME / "envy" / "agent-notifications"
BACKUP_ROOT = STATE_HOME / "envy" / "backups" / "agent-notifications"
OWNERSHIP = STATE_ROOT / "ownership.json"
HOOK_TOKEN = "nv-agent-notify"
SUPPORTED_ADAPTERS = {"claude", "codex", "metacode", "pi"}

HOOK_FILES = {
    "claude": HOME / ".claude" / "settings.json",
    "codex": HOME / ".codex" / "hooks.json",
}
HELPERS = ("nv-agent-notify", "nv-terminal-notify")
METACODE_PLUGIN = (
    CONFIG_HOME / "opencode" / "plugins" / "nv-agent-notifications.js"
)
PI_EXTENSION = HOME / ".pi" / "agent" / "extensions" / "nv-tmux-session.ts"
ALACRITTY_CONFIG = CONFIG_HOME / "alacritty" / "alacritty.toml"


class FeatureError(RuntimeError):
    pass


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


def settings() -> tuple[set[str], str]:
    manifest_path = os.environ.get("ENVY_MANIFEST")
    if not manifest_path:
        raise FeatureError("ENVY_MANIFEST is required")
    manifest = load_json(Path(manifest_path))
    value = manifest.get("settings", {}).get("agent_notifications", {})
    if not isinstance(value, dict):
        raise FeatureError("settings.agent_notifications must be an object")
    raw = value.get("adapters", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise FeatureError("settings.agent_notifications.adapters must be an array")
    unknown = set(raw) - SUPPORTED_ADAPTERS
    if unknown:
        raise FeatureError(
            f"unknown agent notification adapters: {', '.join(sorted(unknown))}"
        )
    condition = value.get("condition", "unfocused")
    if condition not in {"always", "unfocused"}:
        raise FeatureError(
            "settings.agent_notifications.condition must be always or unfocused"
        )
    return set(raw), condition


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
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
    shutil.copy2(path.resolve() if path.is_symlink() else path, destination)
    return destination


def handler(*adapters: str) -> dict[str, Any]:
    arguments = " ".join(adapters)
    return {
        "type": "command",
        "command": f'"$HOME/.local/bin/nv-agent-notify" {arguments}',
        "timeout": 3,
    }


def hook_specs(agent: str) -> tuple[tuple[str, str | None], ...]:
    if agent == "claude":
        return (
            ("Stop", None),
            ("PermissionRequest", None),
            ("Notification", "elicitation_dialog"),
        )
    return (("Stop", None), ("PermissionRequest", None))


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
                item
                for item in group["hooks"]
                if not (
                    isinstance(item, dict)
                    and isinstance(item.get("command"), str)
                    and HOOK_TOKEN in item["command"]
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
    for event, matcher in hook_specs(agent):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise FeatureError(f"hooks.{event} must be an array in {HOOK_FILES[agent]}")
        group: dict[str, Any] = {"hooks": [handler(agent)]}
        if matcher is not None:
            group["matcher"] = matcher
        groups.append(group)
    return result


def hook_present(
    agent: str, event: str, matcher: str | None, value: dict[str, Any]
) -> bool:
    hooks = value.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get(event), list):
        return False
    expected = handler(agent)["command"]
    return any(
        isinstance(group, dict)
        and group.get("matcher") == matcher
        and isinstance(group.get("hooks"), list)
        and any(
            isinstance(item, dict) and item.get("command") == expected
            for item in group["hooks"]
        )
        for group in hooks[event]
    )


def assets_missing(adapters: set[str]) -> list[Path]:
    missing = [
        HOME / ".local" / "bin" / name
        for name in HELPERS
        if not os.access(HOME / ".local" / "bin" / name, os.X_OK)
    ]
    if "metacode" in adapters and not METACODE_PLUGIN.is_file():
        missing.append(METACODE_PLUGIN)
    if "pi" in adapters and not PI_EXTENSION.is_file():
        missing.append(PI_EXTENSION)
    if sys.platform == "darwin" and (
        not ALACRITTY_CONFIG.is_file()
        or "nv-terminal-notify"
        not in ALACRITTY_CONFIG.read_text(encoding="utf-8")
    ):
        missing.append(ALACRITTY_CONFIG)
    return missing


def marker(adapters: set[str], condition: str) -> str:
    selected = ",".join(sorted(adapters))
    return f"version=1\nadapters={selected}\ncondition={condition}\n"


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
    adapters, condition = settings()
    missing = assets_missing(adapters)
    if missing:
        raise FeatureError(
            "agent notification assets are missing; reconverge chezmoi first: "
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
    MARKER.write_text(marker(adapters, condition), encoding="utf-8")
    os.chmod(MARKER, 0o600)
    atomic_json(OWNERSHIP, owner)
    source_running_tmux()
    print("agent terminal-bell notifications are configured")
    return 0


def verify(verbose: bool = False) -> int:
    adapters, condition = settings()
    problems: list[str] = []
    expected_marker = marker(adapters, condition)
    if not MARKER.is_file():
        problems.append(f"feature marker missing: {MARKER}")
    elif MARKER.read_text(encoding="utf-8") != expected_marker:
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
                problems.append(f"disabled {agent} notification hook remains in {path}")
            continue
        for event, matcher in hook_specs(agent):
            if not hook_present(agent, event, matcher, value):
                problems.append(f"{agent} {event} notification hook missing from {path}")

    if verbose:
        if problems:
            for problem in problems:
                print(f"agent-notifications: {problem}", file=sys.stderr)
        else:
            print(
                "agent-notifications: selected adapters and terminal bell assets are current"
            )
            if "codex" in adapters:
                print(
                    "agent-notifications: Codex may require one-time hook review with /hooks"
                )
            if sys.platform == "darwin":
                print(
                    "agent-notifications: macOS attributes osascript alerts to Script Editor"
                )
    return 1 if problems else 0


def remove() -> int:
    owner = ownership()
    for agent, path in HOOK_FILES.items():
        if not path.exists():
            continue
        current = load_json(path)
        desired = remove_managed_hooks(current)
        entry = owner.get("files", {}).get(str(path), {})
        if not desired and entry.get("created") is True:
            path.unlink()
        elif desired != current:
            backup(path, f"remove-{agent}-hooks")
            atomic_json(path, desired)
    if MARKER.exists():
        MARKER.unlink()
    print("Removed agent notification activation and hooks; retained terminal assets")
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
            "usage: agent-notifications.py {converge|verify|doctor|remove}",
            file=sys.stderr,
        )
        return 2
    except (FeatureError, OSError) as error:
        print(f"agent-notifications: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
