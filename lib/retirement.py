#!/usr/bin/env python3
"""Independent non-purging retirement under envy's existing lifecycle lock."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import manifest as manifest_module
import state as state_module


def identity(definition: dict) -> str:
    root = Path(definition["_root"])
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode())
    paths = {manifest_module.resolved_run(definition[action], root)
             for action in ("retire", "verify_retired")}
    paths.update((root / raw).resolve() for raw in definition.get("retirement_inputs", []))
    for path in sorted(paths, key=str):
        manifest_module.require_contained(path, root, "retirement implementation")
        if not path.is_file():
            raise ValueError("retirement implementation unavailable")
        digest.update(str(path).encode())
        digest.update(str(path.stat().st_mode & 0o777).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_handler(manifest: dict, name: str, action: str) -> int:
    argv = manifest_module.handler_argv(manifest, name, action)
    path = Path(argv[0])
    if path.is_file() and not os.access(path, os.X_OK):
        argv.insert(0, sys.executable if path.suffix == ".py" else "bash")
    root = manifest["features"][name]["_root"]
    environment = dict(os.environ, ENVY_FEATURE=name, ENVY_FEATURE_ROOT=root,
                       ENVY_PURGE="false", ENVY_DRY_RUN="0", PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen(argv, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        return child.wait(timeout=60)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def retire(manifest_path: Path, state_path: Path, name: str) -> int:
    manifest = state_module.load_manifest(manifest_path)
    definition = manifest["features"][name]
    machine = state_module.load(state_path)
    feature = machine["features"][name]
    if definition.get("lifecycle") != "deprecated" or not feature.get("retirement"):
        raise ValueError("feature is not eligible for retirement")
    progress = feature["retirement"]
    current = identity(definition)
    recorded = progress.get("identity")
    if recorded and recorded != current and progress.get("status") != "retired":
        print(f"  FAILED {name}: retirement implementation changed; restore the recorded revision before retrying.", file=sys.stderr)
        return 1
    revision = definition["retirement_revision"]
    if progress.get("status") == "retired" and recorded == current:
        try:
            if run_handler(manifest, name, "verify_retired") == 0:
                print(f"  OK {name} - retired")
                return 0
        except (OSError, subprocess.TimeoutExpired):
            # An unavailable verification is not evidence of completed cleanup.
            progress.update(status="failed", detail="Retirement verification unavailable.")
            feature.update(status="retirement-failed", verification="failed")
            state_module.save(state_path, machine)
            raise
    progress.update(status="running", identity=current, revision=revision,
                    started_at=state_module.now())
    feature.update(enabled=False, status="retirement-pending")
    state_module.save(state_path, machine)
    try:
        if identity(definition) != current:
            raise ValueError("implementation changed")
        if run_handler(manifest, name, "retire") != 0:
            raise ValueError("retire failed")
        if run_handler(manifest, name, "verify_retired") != 0:
            raise ValueError("verification failed")
        if identity(definition) != current:
            raise ValueError("implementation changed")
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt):
        progress.update(status="failed", detail="Retirement incomplete; retry the same implementation.")
        feature.update(status="retirement-failed", verification="failed")
        state_module.save(state_path, machine)
        print(f"  FAILED {name}: retirement incomplete; resources may be partially cleaned. Retry upgrade.", file=sys.stderr)
        return 1
    progress.update(status="retired", completed_at=state_module.now(), detail="")
    feature.update(enabled=False, status="retired", verification="current", detail="")
    state_module.save(state_path, machine)
    print(f"  OK {name} retired")
    return 0


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return retire(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, KeyError, manifest_module.ManifestError, subprocess.TimeoutExpired):
        print("Retirement refused: invalid state or unavailable implementation.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
