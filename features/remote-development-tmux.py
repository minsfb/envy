#!/usr/bin/env python3
"""Converge the optional tmux profile for remote Code OSS terminals."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import paths  # noqa: E402

PROFILE_NAME = "Envy Remote (tmux)"
EXTENSION_ID = "envy.remote-development-tmux"
EXTENSION_VERSION = "1.0.0"
HELPER = ROOT / "bin" / "nv-remote-terminal"
REMOTE_MARKER = paths.state_dir() / "remote-development" / "configured.json"
EXTENSIONS = paths.data_dir() / "remote-development" / "extensions"
EXTENSION = EXTENSIONS / f"{EXTENSION_ID}-{EXTENSION_VERSION}"
PACKAGE = EXTENSION / "package.json"
STATE = paths.state_dir() / "remote-development-tmux"
MARKER = STATE / "ownership.json"


class FeatureError(RuntimeError):
    pass


def package() -> dict[str, Any]:
    return {
        "name": "remote-development-tmux",
        "displayName": PROFILE_NAME,
        "description": "Workspace-scoped persistent tmux terminals managed by Envy",
        "version": EXTENSION_VERSION,
        "publisher": "envy",
        "license": "MIT",
        "engines": {"vscode": "*"},
        "extensionKind": ["workspace"],
        "contributes": {
            "configurationDefaults": {
                "terminal.integrated.profiles.linux": {
                    PROFILE_NAME: {
                        "path": str(HELPER),
                        "args": ["${workspaceFolder}"],
                        "icon": "terminal-tmux",
                    }
                },
                "terminal.integrated.defaultProfile.linux": PROFILE_NAME,
            }
        },
    }


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise FeatureError(f"private path has an unexpected type: {path}")
    path.chmod(0o700)


def ensure_extension_parent() -> None:
    current = paths.data_dir()
    for part in ("remote-development", "extensions"):
        current /= part
        private_dir(current)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file() or path.is_symlink():
        raise FeatureError(f"expected a regular JSON file at {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FeatureError(f"invalid JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise FeatureError(f"expected a JSON object at {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def marker() -> dict[str, Any]:
    value = load_json(MARKER)
    if not value:
        return {}
    if value != {
        "version": 1,
        "extension_id": EXTENSION_ID,
        "package_sha256": digest(package()),
    }:
        raise FeatureError(f"unsupported or stale ownership state at {MARKER}")
    return value


def prerequisites() -> list[str]:
    problems: list[str] = []
    if not REMOTE_MARKER.is_file():
        problems.append(f"remote-development is not configured: {REMOTE_MARKER}")
    if not shutil.which("tmux"):
        problems.append("tmux is unavailable")
    if not os.access(HELPER, os.X_OK):
        problems.append(f"terminal helper is not executable: {HELPER}")
    return problems


def converge() -> int:
    problems = prerequisites()
    if problems:
        raise FeatureError("; ".join(problems))
    expected = package()
    owner = marker()
    if EXTENSION.exists() and not owner:
        raise FeatureError(
            f"refusing to replace unowned extension directory: {EXTENSION}"
        )
    ensure_extension_parent()
    private_dir(EXTENSION)
    atomic_json(PACKAGE, expected)
    atomic_json(
        MARKER,
        {
            "version": 1,
            "extension_id": EXTENSION_ID,
            "package_sha256": digest(expected),
        },
    )
    print("remote Code OSS tmux terminal profile is configured")
    return 0


def verify(verbose: bool = False) -> int:
    problems = prerequisites()
    try:
        if not marker():
            problems.append(f"feature ownership marker is missing: {MARKER}")
        if load_json(PACKAGE) != package():
            problems.append(
                f"terminal profile extension is missing or drifted: {PACKAGE}"
            )
    except (FeatureError, OSError) as error:
        problems.append(str(error))
    if verbose:
        if problems:
            for problem in problems:
                print(f"remote-development-tmux: {problem}", file=sys.stderr)
        else:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            sessions = [
                name
                for name in result.stdout.splitlines()
                if name.startswith("envy-rd-")
            ]
            print(
                "remote-development-tmux: profile is current; "
                f"{len(sessions)} workspace session(s)"
            )
    return 1 if problems else 0


def remove() -> int:
    owner = marker()
    if owner and load_json(PACKAGE) == package():
        shutil.rmtree(EXTENSION)
    MARKER.unlink(missing_ok=True)
    try:
        STATE.rmdir()
    except OSError:
        pass
    print("Removed remote tmux terminal profile; retained tmux sessions")
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
            "usage: remote-development-tmux.py {converge|verify|doctor|remove}",
            file=sys.stderr,
        )
        return 2
    except (FeatureError, OSError) as error:
        print(f"remote-development-tmux: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
