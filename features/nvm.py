#!/usr/bin/env python3
"""Own the pinned nvm scripts and default Node, retaining user runtimes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import paths
import provision
import state


def pin():
    return next(x for x in provision.dependencies() if x["name"] == "nvm")


def directory():
    return Path(os.environ.get("ENVY_NVM_DIR", str(paths.home() / ".nvm"))).expanduser()


def selected():
    try:
        return bool(json.loads(paths.state_file().read_text())["features"]["nvm"]["enabled"])
    except (OSError, KeyError):
        return False


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".envy-nvm-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_nvm(script, *args, download=False):
    env = dict(os.environ, NVM_DIR=str(directory()))
    if download and env.get("ENVY_NETWORK_PROXY"):
        # nvm uses curl/wget. Keep proxy configuration within this subprocess.
        env.setdefault("ALL_PROXY", env["ENVY_NETWORK_PROXY"])
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.setdefault(key, env["ENVY_NETWORK_PROXY"])
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c",
         'set -e; . "$NVM_DIR/nvm.sh" --no-use; ' + script, "nvm", *args],
        env=env, cwd=directory(), check=True,
    )


def verify():
    item = pin()
    for name, sha in item["files"].items():
        path = directory() / name
        if path.is_symlink() or not path.is_file() or digest(path) != sha:
            raise RuntimeError(f"nvm source drift: {path}")
    version = item["node_version"]
    run_nvm('test "$(nvm --version)" = "$1"; '
            'test "$(nvm version default)" = "v$2"; '
            'nvm use --silent "$2"; test "$(node --version)" = "v$2"; npm --version',
            item["version"], version)


def converge():
    if os.environ.get("ENVY_DRY_RUN") == "true":
        return
    item = pin()
    target = directory()
    if not target.is_absolute() or target == paths.home() or target == Path("/"):
        raise RuntimeError("ENVY_NVM_DIR must be an absolute dedicated directory")
    version = item["node_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Node default must be an exact version")
    ledger_path = paths.state_dir() / "providers/nvm.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {
        "directory": str(target), "files": {}}
    if ledger["directory"] != str(target):
        raise RuntimeError("nvm directory changed; remove the previous feature before relocating")
    backups = Path(ledger.setdefault("backups", str(paths.state_dir() / "backups/nvm" / uuid.uuid4().hex)))
    # Verify all downloads before touching the installation.
    with tempfile.TemporaryDirectory(prefix="envy-nvm-") as temporary:
        downloads = {}
        for name, sha in item["files"].items():
            current = target / name
            if current.is_file() and not current.is_symlink() and digest(current) == sha:
                downloads[name] = current.read_bytes()
                continue
            downloaded = Path(temporary) / name
            provision.download(f'{item["url"]}/v{item["version"]}/{name}', downloaded)
            if digest(downloaded) != sha:
                raise RuntimeError(f"nvm checksum mismatch: {name}")
            downloads[name] = downloaded.read_bytes()
        for name in [*item["files"], "alias/default"]:
            if name not in ledger["files"]:
                path = target / name
                original = path.exists() or path.is_symlink()
                if original:
                    backup = backups / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if not backup.exists() and not backup.is_symlink():
                        shutil.copy2(path, backup, follow_symlinks=False)
                ledger["files"][name] = {"original": original}
        # Journal backups before any replacement so a retry cannot lose them.
        atomic(ledger_path, (json.dumps(ledger) + "\n").encode())
        for name, content in downloads.items():
            atomic(target / name, content, 0o644 if name == "LICENSE.md" else 0o755)
            ledger["files"][name]["sha256"] = item["files"][name]
        atomic(ledger_path, (json.dumps(ledger) + "\n").encode())
    run_nvm('nvm install -b --no-progress --skip-default-packages "$1"; nvm alias default "$1"',
            version, download=True)
    ledger["files"]["alias/default"]["sha256"] = digest(target / "alias/default")
    atomic(ledger_path, (json.dumps(ledger) + "\n").encode())
    verify()
    if paths.state_file().exists():
        current = state.load(paths.state_file())
        state.nested_set(current, "features.nvm.data.directory", str(target))
        state.nested_set(current, "features.nvm.data.node_runtime", {
            "backend": "nvm", "bin": str(target / "versions/node" / f"v{version}" / "bin")})
        state.save(paths.state_file(), current)


def remove():
    ledger_path = paths.state_dir() / "providers/nvm.json"
    if not ledger_path.exists() or os.environ.get("ENVY_DRY_RUN") == "true":
        return
    ledger = json.loads(ledger_path.read_text())
    target = Path(ledger["directory"])
    for name, record in ledger["files"].items():
        path = target / name
        if name not in {*pin()["files"], "alias/default"}:
            raise RuntimeError("invalid nvm ownership entry")
        if not path.is_file() or path.is_symlink() or digest(path) != record.get("sha256"):
            print(f"Retaining changed nvm file: {path}")
            continue
        if record["original"]:
            backup = Path(ledger["backups"]) / name
            path.unlink()
            shutil.copy2(backup, path, follow_symlinks=False)
        else:
            path.unlink()
    ledger_path.unlink()
    print("Retaining installed Node versions, global packages, caches, and nvm backups")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "verify"
    try:
        if action == "selected":
            print("true" if selected() else "false")
        elif action == "converge":
            converge()
        elif action in {"verify", "doctor"}:
            verify()
        elif action == "remove":
            remove()
        else:
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            provision.ProvisionError) as error:
        print(f"nvm: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
