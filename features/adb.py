#!/usr/bin/env python3
"""Select and record an existing Android SDK without installing SDK content."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path


class AdbError(RuntimeError):
    pass


def adb_path(root: Path) -> Path | None:
    for name in ("adb", "adb.exe"):
        candidate = root / "platform-tools" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def state_root() -> Path | None:
    state_file = os.environ.get("ENVY_STATE_FILE")
    if not state_file or not Path(state_file).is_file():
        return None
    try:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = state.get("features", {}).get("adb", {}).get("data", {}).get("sdk_root")
    return Path(raw).expanduser() if isinstance(raw, str) and raw else None


def candidates() -> list[Path]:
    home = Path.home()
    raw_values = [
        os.environ.get("ENVY_ADB_SDK_ROOT"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.environ.get("ANDROID_HOME"),
    ]
    current = state_root()
    if current:
        raw_values.append(str(current))
    if platform.system() == "Darwin":
        raw_values.append(str(home / "Library" / "Android" / "sdk"))
    if os.name == "nt" or platform.system().startswith(("MSYS", "MINGW", "CYGWIN")):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            raw_values.append(str(Path(local_app_data) / "Android" / "Sdk"))
    raw_values.extend((str(home / "Android" / "Sdk"), str(home / ".android" / "sdk")))
    result: list[Path] = []
    for raw in raw_values:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if adb_path(path) and path not in result:
            result.append(path)
    return result


def choose(values: list[Path]) -> Path:
    if not values:
        raise AdbError(
            "no Android SDK with platform-tools/adb was found; install one or set "
            "ENVY_ADB_SDK_ROOT"
        )
    explicit = os.environ.get("ENVY_ADB_SDK_ROOT")
    if explicit:
        requested = Path(explicit).expanduser().resolve()
        if requested not in values:
            raise AdbError(f"ENVY_ADB_SDK_ROOT is not a usable SDK: {requested}")
        return requested
    if len(values) == 1 or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return values[0]
    print("Detected Android SDKs:")
    for index, value in enumerate(values, 1):
        version = subprocess.run(
            [str(adb_path(value)), "version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout.splitlines()
        label = version[0] if version else "version unavailable"
        print(f"  [{index}] {value} ({label})")
    while True:
        reply = input("Select canonical Android SDK [1]: ").strip() or "1"
        if reply.isdigit() and 1 <= int(reply) <= len(values):
            return values[int(reply) - 1]
        print("Enter one of the listed numbers.")


def record(root: Path) -> None:
    state_file = os.environ.get("ENVY_STATE_FILE")
    env_root = os.environ.get("ENVY_ROOT")
    if not state_file or not env_root:
        raise AdbError("envy state is unavailable")
    subprocess.run(
        [
            sys.executable,
            str(Path(env_root) / "lib" / "state.py"),
            state_file,
            "set",
            "features.adb.data.sdk_root",
            json.dumps(str(root)),
        ],
        check=True,
    )


def converge() -> int:
    root = choose(candidates())
    if os.environ.get("ENVY_DRY_RUN", "false").lower() in {"1", "true", "yes"}:
        print(f"Would select Android SDK: {root}")
        return 0
    record(root)
    print(f"Selected Android SDK: {root}")
    return 0


def verify(verbose: bool) -> int:
    root = state_root()
    if root is None or adb_path(root) is None:
        raise AdbError("recorded Android SDK is missing or has no executable adb")
    if verbose:
        print(f"adb: canonical SDK is current: {root}")
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
            print("Retaining the Android SDK and recorded selection")
            return 0
        print("usage: adb.py {converge|verify|doctor|remove}", file=sys.stderr)
        return 2
    except (AdbError, OSError, subprocess.SubprocessError) as error:
        print(f"adb: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
