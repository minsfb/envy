#!/usr/bin/env python3
"""Emit the bounded native runtime environment selected by nv-node."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def runtime_selection(value: object) -> tuple[str, str]:
    """Validate feature-owned selection metadata before rendering shell code."""
    if not isinstance(value, dict) or set(value) != {"backend", "bin"}:
        raise ValueError("invalid node_runtime fields")
    backend, directory = value["backend"], value["bin"]
    if not isinstance(backend, str) or backend not in {"nvm", "system"} or not isinstance(directory, str):
        raise ValueError("invalid node_runtime selection")
    if (not directory.startswith("/") or ":" in directory
            or any(part in {".", ".."} for part in directory.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in directory)):
        raise ValueError("node_runtime bin must be an absolute PATH directory")
    return backend, str(Path(directory))


def record(backend: str, directory: str) -> dict:
    backend, directory = runtime_selection({"backend": backend, "bin": directory})
    runtime = Path(directory)
    # nvm can replace a previously selected bin in place, after utility paths.
    # Controlled launchers require the selected native bin first. Remove only
    # repetitions of that bin, preserving unrelated and empty PATH components.
    inherited = os.environ.get("PATH", os.defpath).split(":")
    environment = {"PATH": ":".join([directory, *(entry for entry in inherited if entry != directory)])}
    result = {"version": 1, "backend": backend}
    for command in ("node", "npm"):
        executable = runtime / command
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"selected runtime has no executable {command}")
        # Check exported command resolution. Keep the native executable path
        # rather than resolving npm's link into node_modules.
        selected = shutil.which(command, path=environment["PATH"])
        if selected is None or Path(selected).absolute() != executable:
            raise ValueError(f"selected runtime does not resolve native {command}")
        probe = subprocess.run([str(executable), "--version"],
                               env={**os.environ, **environment},
                               capture_output=True, text=True, timeout=15, check=True)
        version = probe.stdout.strip()
        pattern = r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?" if command == "node" else r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?"
        if not re.fullmatch(pattern, version):
            raise ValueError(f"selected runtime returned an invalid {command} version")
        result[command] = str(executable)
        result[f"{command}_version"] = version
    if backend == "nvm":
        nvm_dir = os.environ.get("NVM_DIR", "")
        if not Path(nvm_dir).is_absolute() or os.environ.get("NVM_BIN") != str(runtime):
            raise ValueError("selected nvm runtime environment is inconsistent")
        environment.update(NVM_DIR=nvm_dir, NVM_BIN=str(runtime))
    result["env"] = environment
    return result


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    try:
        value = record(*sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError):
        # Version probes can include private configuration in their output.
        # Report the failed boundary without copying arbitrary subprocess text.
        print("nv-node: selected native Node/npm environment is unavailable or invalid", file=sys.stderr)
        return 1
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
