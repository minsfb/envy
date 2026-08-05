#!/usr/bin/env python3
"""Manage the shared per-user remote development engine and its leases."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import paths  # noqa: E402
import provision  # noqa: E402

DEPENDENCY = "code-server"
DEFAULT_GRACE = 300
DEFAULT_REGISTRY = {
    "serviceUrl": "https://open-vsx.org/vscode/gallery",
    "itemUrl": "https://open-vsx.org/vscode/item",
    "resourceUrlTemplate": (
        "https://open-vsx.org/vscode/unpkg/" "{publisher}/{name}/{version}/{path}"
    ),
}
STATE = paths.state_dir() / "remote-development"
DATA = paths.data_dir() / "remote-development"
ENGINE_STATE = STATE / "engine.json"
LOCK = STATE / "broker.lock"
LEASES = STATE / "leases"
LOG = STATE / "code-server.log"
STOP = STATE / "stop.json"
MARKER = STATE / "configured.json"
_STARTED_PROCESSES: dict[int, subprocess.Popen[bytes]] = {}


class RemoteError(RuntimeError):
    pass


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RemoteError(f"private path has an unexpected type: {path}")
    path.chmod(0o700)


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return (Path(base) / "envy" / "remote-development") if base else STATE / "runtime"


def socket_path() -> Path:
    return runtime_dir() / "code-server.sock"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RemoteError(f"expected an object at {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def settings() -> tuple[int, dict[str, str]]:
    manifest_path = os.environ.get("ENVY_MANIFEST")
    value: dict[str, Any] = {}
    if manifest_path:
        value = (
            load_json(Path(manifest_path))
            .get("settings", {})
            .get("remote_development", {})
        )
    if not isinstance(value, dict):
        raise RemoteError("settings.remote_development must be an object")
    grace = value.get("idle_grace_seconds", DEFAULT_GRACE)
    if not isinstance(grace, int) or isinstance(grace, bool) or not 0 <= grace <= 3600:
        raise RemoteError("remote idle_grace_seconds must be an integer from 0 to 3600")
    raw_registry = value.get("extension_registry", DEFAULT_REGISTRY)
    if not isinstance(raw_registry, dict):
        raise RemoteError("remote extension_registry must be an object")
    aliases = {
        "service_url": "serviceUrl",
        "item_url": "itemUrl",
        "resource_url_template": "resourceUrlTemplate",
    }
    registry: dict[str, str] = {}
    for key, raw in raw_registry.items():
        mapped = aliases.get(key, key)
        parsed = urllib.parse.urlsplit(raw) if isinstance(raw, str) else None
        if (
            mapped not in DEFAULT_REGISTRY
            or parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise RemoteError(f"invalid remote extension registry field: {key}")
        registry[mapped] = raw
    if set(registry) != set(DEFAULT_REGISTRY):
        raise RemoteError("remote extension_registry must provide all registry URLs")
    return grace, registry


def proc_identity(pid: int) -> str | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = value[value.rfind(")") + 2 :].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (FileNotFoundError, IndexError, OSError):
        return None


def live(pid: Any, identity: Any) -> bool:
    return (
        isinstance(pid, int)
        and isinstance(identity, str)
        and proc_identity(pid) == identity
    )


@contextmanager
def locked() -> Iterator[None]:
    private_dir(STATE)
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def engine() -> dict[str, Any]:
    value = load_json(ENGINE_STATE)
    return value if live(value.get("pid"), value.get("identity")) else {}


def clean_stale_leases() -> list[Path]:
    private_dir(LEASES)
    active: list[Path] = []
    for path in LEASES.glob("*.json"):
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError, RemoteError):
            value = {}
        if live(value.get("pid"), value.get("identity")):
            active.append(path)
        else:
            path.unlink(missing_ok=True)
    return active


def active_leases() -> list[Path]:
    if not LEASES.exists() and not LEASES.is_symlink():
        return []
    if LEASES.is_symlink() or not LEASES.is_dir():
        raise RemoteError(f"lease path has an unexpected type: {LEASES}")
    active: list[Path] = []
    for path in LEASES.glob("*.json"):
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError, RemoteError):
            continue
        if live(value.get("pid"), value.get("identity")):
            active.append(path)
    return active


def extension_fingerprint() -> str:
    item = provision.dependency(DEPENDENCY)
    tree_manifest = provision.install_path(item) / provision.TREE_MANIFEST
    try:
        valid_manifest = tree_manifest.is_file() and not tree_manifest.is_symlink()
        manifest_bytes = tree_manifest.read_bytes() if valid_manifest else None
    except OSError as error:
        raise RemoteError(
            f"cannot read code-server provision manifest {tree_manifest}: {error}; "
            "run envy upgrade --only remote-development --force"
        ) from error
    if manifest_bytes is None:
        raise RemoteError(
            f"code-server provision manifest is missing: {tree_manifest}; "
            "run envy upgrade --only remote-development --force"
        )
    digest = hashlib.sha256()
    digest.update(b"code-server-tree\0")
    digest.update(manifest_bytes)
    extensions = DATA / "extensions"
    if extensions.exists() and (extensions.is_symlink() or not extensions.is_dir()):
        raise RemoteError(f"extensions path has an unexpected type: {extensions}")
    if extensions.is_dir():
        for package in sorted(extensions.glob("*/package.json")):
            if not package.is_file() or package.is_symlink():
                raise RemoteError(f"extension manifest has an unexpected type: {package}")
            digest.update(b"extension\0")
            digest.update(str(package.relative_to(extensions)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(package.read_bytes())
    return digest.hexdigest()


def invalidate_extension_caches(user_data: Path, fingerprint: str) -> None:
    inventory = user_data / ".envy-extension-inventory.json"
    try:
        current = load_json(inventory)
    except (OSError, json.JSONDecodeError, RemoteError):
        current = {}
    expected = {"version": 1, "fingerprint": fingerprint}
    if current == expected:
        return
    profiles = user_data / "CachedProfilesData"
    if profiles.exists():
        if profiles.is_symlink() or not profiles.is_dir():
            raise RemoteError(f"extension cache path has an unexpected type: {profiles}")
        for profile in profiles.iterdir():
            if profile.is_symlink():
                raise RemoteError(
                    f"extension cache profile has an unexpected type: {profile}"
                )
            if not profile.is_dir():
                continue
            for name in ("extensions.builtin.cache", "extensions.user.cache"):
                cache = profile / name
                if cache.exists() or cache.is_symlink():
                    if cache.is_dir() and not cache.is_symlink():
                        raise RemoteError(
                            f"extension cache has an unexpected type: {cache}"
                        )
                    cache.unlink()
    user = user_data / "User"
    if user.exists():
        if user.is_symlink() or not user.is_dir():
            raise RemoteError(f"extension cache path has an unexpected type: {user}")
        for name in ("customBuiltinExtensionsCache.json", "systemExtensionsCache.json"):
            cache = user / name
            if cache.exists() or cache.is_symlink():
                if cache.is_dir() and not cache.is_symlink():
                    raise RemoteError(f"extension cache has an unexpected type: {cache}")
                cache.unlink()
    atomic_json(inventory, expected)


def verified_engine_dependency() -> dict[str, Any]:
    try:
        item = provision.dependency(DEPENDENCY)
        result = provision.verify(DEPENDENCY)
    except (provision.ProvisionError, OSError) as error:
        raise RemoteError(
            f"code-server provision verification failed: {error}; "
            "run envy upgrade --only remote-development --force"
        ) from error
    if result != 0:
        raise RemoteError(
            "code-server provision verification failed; run "
            "envy upgrade --only remote-development --force"
        )
    return item


def launch_fingerprint() -> str:
    try:
        return extension_fingerprint()
    except provision.ProvisionError as error:
        raise RemoteError(
            f"code-server provision verification failed: {error}; "
            "run envy upgrade --only remote-development --force"
        ) from error


def start_engine(restart_if_changed: bool = False) -> dict[str, Any]:
    current = engine()
    if current:
        if not restart_if_changed:
            return current
        fingerprint = launch_fingerprint()
        if current.get("fingerprint") == fingerprint:
            return current
    else:
        fingerprint = launch_fingerprint()
    item = verified_engine_dependency()
    command = provision.managed_command(item)
    if command is None or not os.access(command, os.X_OK):
        raise RemoteError(
            "code-server is not provisioned; run "
            "envy upgrade --only remote-development --force"
        )
    _grace, registry = settings()
    if current:
        stop_engine()
    ENGINE_STATE.unlink(missing_ok=True)
    sock = socket_path()
    private_dir(sock.parent)
    sock.unlink(missing_ok=True)
    user_data = DATA / "user-data" / "code-server"
    extensions = DATA / "extensions"
    private_dir(DATA)
    private_dir(DATA / "user-data")
    private_dir(user_data)
    private_dir(extensions)
    private_dir(STATE)
    invalidate_extension_caches(user_data, fingerprint)
    log_handle = LOG.open("ab", buffering=0)
    os.chmod(LOG, 0o600)
    environment = provision.external_environment()
    environment["EXTENSIONS_GALLERY"] = json.dumps(registry, separators=(",", ":"))
    process = subprocess.Popen(
        [
            str(command),
            "--socket",
            str(sock),
            "--socket-mode",
            "0600",
            "--auth",
            "none",
            "--disable-telemetry",
            "--disable-update-check",
            "--disable-getting-started-override",
            "--user-data-dir",
            str(user_data),
            "--extensions-dir",
            str(extensions),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    _STARTED_PROCESSES[process.pid] = process
    log_handle.close()
    identity = None
    for _ in range(200):
        identity = proc_identity(process.pid)
        if process.poll() is not None:
            _STARTED_PROCESSES.pop(process.pid, None)
            raise RemoteError(f"code-server exited during startup; see {LOG}")
        if identity and sock.is_socket():
            break
        time.sleep(0.05)
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        _STARTED_PROCESSES.pop(process.pid, None)
        raise RemoteError(f"code-server did not create its socket; see {LOG}")
    value = {
        "pid": process.pid,
        "identity": identity,
        "socket": str(sock),
        "fingerprint": fingerprint,
    }
    atomic_json(ENGINE_STATE, value)
    return value


def stop_engine() -> None:
    current = load_json(ENGINE_STATE)
    if live(current.get("pid"), current.get("identity")):
        pid = int(current["pid"])
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not live(pid, current["identity"]):
                break
            time.sleep(0.1)
        if live(pid, current["identity"]):
            os.kill(pid, signal.SIGKILL)
        child = _STARTED_PROCESSES.pop(pid, None)
        if child is not None:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    ENGINE_STATE.unlink(missing_ok=True)
    socket_path().unlink(missing_ok=True)


def schedule_stop() -> None:
    grace, _registry = settings()
    token = os.urandom(16).hex()
    atomic_json(STOP, {"token": token, "deadline": time.time() + grace})
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "reap", token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )


def reap(token: str) -> int:
    value = load_json(STOP)
    if value.get("token") != token:
        return 0
    delay = max(0.0, float(value.get("deadline", 0)) - time.time())
    time.sleep(delay)
    with locked():
        value = load_json(STOP)
        if value.get("token") == token and not clean_stale_leases():
            stop_engine()
            STOP.unlink(missing_ok=True)
    return 0


def converge() -> int:
    if sys.platform != "linux":
        raise RemoteError("remote-development hosts require Linux or WSL")
    settings()
    if provision.install(DEPENDENCY) != 0:
        raise RemoteError("could not provision code-server")
    item = provision.dependency(DEPENDENCY)
    version_root = provision.install_path(item)
    current = version_root.parent / "current"
    if current.is_symlink() and os.readlink(current) == version_root.name:
        pass
    elif current.is_symlink():
        replacement = current.with_name(f".current.{os.getpid()}")
        replacement.symlink_to(version_root.name)
        os.replace(replacement, current)
    elif current.exists():
        raise RemoteError(f"refusing to replace unmanaged engine link: {current}")
    else:
        current.symlink_to(version_root.name)
    grace, registry = settings()
    atomic_json(
        MARKER,
        {"version": 1, "idle_grace_seconds": grace, "extension_registry": registry},
    )
    with locked():
        if not clean_stale_leases() and engine():
            schedule_stop()
    print("remote development server is configured")
    return 0


def verification_diagnostics() -> list[tuple[str, bool]]:
    diagnostics: list[tuple[str, bool]] = []
    configured: tuple[int, dict[str, str]] | None = None
    try:
        configured = settings()
    except (
        RemoteError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        diagnostics.append((str(error), False))

    try:
        if provision.verify(DEPENDENCY) != 0:
            diagnostics.append(
                ("code-server does not match its pinned version", True)
            )
    except provision.ProvisionError as error:
        diagnostics.append((str(error), True))
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
        diagnostics.append((str(error), False))

    try:
        version_root = provision.install_path(provision.dependency(DEPENDENCY))
        current_link = version_root.parent / "current"
        if current_link.is_symlink():
            if os.readlink(current_link) != version_root.name:
                diagnostics.append(
                    (f"selected engine link is stale: {current_link}", True)
                )
        elif current_link.exists():
            diagnostics.append(
                (f"selected engine link has an unexpected type: {current_link}", False)
            )
        else:
            diagnostics.append((f"selected engine link is stale: {current_link}", True))
    except (provision.ProvisionError, OSError, json.JSONDecodeError, ValueError) as error:
        diagnostics.append((str(error), False))

    state_blocks_converge = False
    try:
        if STATE.is_symlink() or (STATE.exists() and not STATE.is_dir()):
            diagnostics.append(
                (f"feature state directory has an unexpected type: {STATE}", False)
            )
            state_blocks_converge = True
        elif not STATE.exists():
            diagnostics.append((f"feature state directory is missing: {STATE}", True))
        elif stat.S_IMODE(STATE.stat().st_mode) != 0o700:
            diagnostics.append(
                (f"feature state directory is not private: {STATE}", True)
            )
    except OSError as error:
        diagnostics.append((f"cannot inspect feature state directory: {error}", False))
        state_blocks_converge = True

    if not state_blocks_converge:
        try:
            if not MARKER.exists() and not MARKER.is_symlink():
                diagnostics.append((f"feature marker is missing: {MARKER}", True))
            elif MARKER.is_symlink():
                diagnostics.append((f"feature marker is not private: {MARKER}", True))
            elif MARKER.is_dir():
                diagnostics.append(
                    (f"feature marker has an unexpected type: {MARKER}", False)
                )
            else:
                if configured is not None:
                    grace, registry = configured
                    try:
                        marker_value = load_json(MARKER)
                    except (OSError, json.JSONDecodeError, RemoteError) as error:
                        diagnostics.append(
                            (f"feature marker cannot be read: {error}", True)
                        )
                    else:
                        if marker_value != {
                            "version": 1,
                            "idle_grace_seconds": grace,
                            "extension_registry": registry,
                        }:
                            diagnostics.append(
                                (f"feature marker is stale: {MARKER}", True)
                            )
                if stat.S_IMODE(MARKER.stat().st_mode) != 0o600:
                    diagnostics.append(
                        (f"feature marker is not private: {MARKER}", True)
                    )
        except OSError as error:
            diagnostics.append((f"cannot inspect feature marker: {error}", False))

    runtime = runtime_dir()
    inspect_runtime = not state_blocks_converge or bool(
        os.environ.get("XDG_RUNTIME_DIR")
    )
    if inspect_runtime:
        try:
            if runtime.exists() or runtime.is_symlink():
                if (
                    runtime.is_symlink()
                    or not runtime.is_dir()
                    or stat.S_IMODE(runtime.stat().st_mode) != 0o700
                ):
                    diagnostics.append(
                        (f"runtime directory is not private: {runtime}", False)
                    )
        except OSError as error:
            diagnostics.append((f"cannot inspect runtime directory: {error}", False))
    sock = socket_path()
    if inspect_runtime:
        try:
            if sock.exists() or sock.is_symlink():
                if (
                    sock.is_symlink()
                    or not sock.is_socket()
                    or stat.S_IMODE(sock.stat().st_mode) != 0o600
                ):
                    diagnostics.append(
                        (f"engine socket is not private: {sock}", False)
                    )
        except OSError as error:
            diagnostics.append((f"cannot inspect engine socket: {error}", False))
    return diagnostics


def report_verification(
    diagnostics: list[tuple[str, bool]], verbose: bool = False
) -> int:
    if diagnostics:
        if verbose:
            for problem, _repairable in diagnostics:
                print(f"  FAIL {problem}")
        return 1
    if verbose:
        current = engine()
        state = f"running (pid {current['pid']})" if current else "idle"
        print(
            f"  OK code-server {provision.dependency(DEPENDENCY)['version']}; engine {state}"
        )
    return 0


def verify(verbose: bool = False) -> int:
    return report_verification(verification_diagnostics(), verbose=verbose)


def doctor(verbose: bool = False) -> int:
    diagnostics = verification_diagnostics()
    result = report_verification(diagnostics, verbose=True)
    stale: list[str] = []
    if LEASES.is_dir():
        for path in LEASES.glob("*.json"):
            try:
                value = load_json(path)
            except (OSError, json.JSONDecodeError, RemoteError):
                value = {}
            if not live(value.get("pid"), value.get("identity")):
                stale.append(path.stem)
    free = (
        shutil.disk_usage(paths.data_dir()).free
        if paths.data_dir().exists()
        else shutil.disk_usage(paths.data_dir().parent).free
    )
    print(f"  OK data filesystem has {free // (1024 * 1024)} MiB free")
    if stale:
        print(
            f"  WARN {len(stale)} stale lease record(s); the next open or convergence will clean them"
        )
    if any(repairable for _problem, repairable in diagnostics):
        print(
            "  REPAIR managed drift: run "
            "envy upgrade --only remote-development --force"
        )
    if verbose:
        try:
            item = provision.dependency(DEPENDENCY)
            version_root = provision.install_path(item)
        except (
            provision.ProvisionError,
            OSError,
            json.JSONDecodeError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            print(f"  INFO provision paths unavailable: {error}")
        else:
            print(f"  INFO provision root: {version_root}")
            print(f"  INFO selected engine link: {version_root.parent / 'current'}")
        print(f"  INFO state directory: {STATE}")
        print(f"  INFO runtime directory: {runtime_dir()}")
        print(f"  INFO engine socket: {socket_path()}")
    return result


def status() -> int:
    current = engine()
    leases: list[str] = []
    stale: list[str] = []
    if LEASES.is_dir():
        for path in LEASES.glob("*.json"):
            try:
                value = load_json(path)
            except (OSError, json.JSONDecodeError, RemoteError):
                value = {}
            (leases if live(value.get("pid"), value.get("identity")) else stale).append(
                path.stem
            )
    pending = load_json(STOP)
    print(
        json.dumps(
            {
                "configured": MARKER.is_file(),
                "engine_version": provision.dependency(DEPENDENCY)["version"],
                "running": bool(current),
                "pid": current.get("pid"),
                "socket": current.get("socket"),
                "lease_count": len(leases),
                "leases": sorted(leases),
                "stale_leases": sorted(stale),
                "shutdown_deadline": pending.get("deadline"),
            }
        )
    )
    return 0


def lease(identifier: str) -> int:
    if (
        not identifier
        or any(character not in "0123456789abcdef" for character in identifier)
        or len(identifier) > 64
    ):
        raise RemoteError("invalid lease identifier")
    line = sys.stdin.buffer.readline(65537)
    if not line or len(line) > 65536:
        raise RemoteError("missing or oversized remote open request")
    value = json.loads(line)
    raw_path = value.get("path") if isinstance(value, dict) else None
    if not isinstance(raw_path, str) or "\0" in raw_path:
        raise RemoteError("remote open request requires a path")
    project = Path(raw_path).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise RemoteError(f"remote path is not a directory: {project}")
    lease_path = LEASES / f"{identifier}.json"
    with locked():
        active = active_leases()
        current = start_engine(restart_if_changed=not active)
        clean_stale_leases()
        STOP.unlink(missing_ok=True)
        atomic_json(
            lease_path,
            {
                "pid": os.getpid(),
                "identity": proc_identity(os.getpid()),
                "project": str(project),
            },
        )
    print(
        json.dumps(
            {
                "engine": "code-server",
                "protocol": "http",
                "transport": "streamlocal",
                "socket": current["socket"],
                "path": str(project),
            }
        ),
        flush=True,
    )
    try:
        while sys.stdin.buffer.read(65536):
            pass
    finally:
        with locked():
            lease_path.unlink(missing_ok=True)
            if not clean_stale_leases() and engine():
                schedule_stop()
    return 0


def remove() -> int:
    with locked():
        for lease_path in clean_stale_leases():
            value = load_json(lease_path)
            if live(value.get("pid"), value.get("identity")):
                os.kill(int(value["pid"]), signal.SIGTERM)
        stop_engine()
        shutil.rmtree(LEASES, ignore_errors=True)
        STOP.unlink(missing_ok=True)
    MARKER.unlink(missing_ok=True)
    current = (
        provision.install_path(provision.dependency(DEPENDENCY)).parent / "current"
    )
    if current.is_symlink():
        current.unlink()
    if os.environ.get("ENVY_PURGE") == "true":
        if provision.marker(DEPENDENCY).is_file():
            provision.remove(DEPENDENCY)
    shutil.rmtree(STATE, ignore_errors=True)
    print("remote development server removed; user data retained")
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "help"
    try:
        if action == "converge":
            return converge()
        if action == "verify":
            return verify()
        if action == "doctor":
            arguments = sys.argv[2:]
            if arguments not in ([], ["--verbose"]):
                print(
                    "usage: remote-development.py doctor [--verbose]",
                    file=sys.stderr,
                )
                return 2
            return doctor(verbose=bool(arguments))
        if action == "remove":
            return remove()
        if action == "status":
            return status()
        if action == "lease" and len(sys.argv) == 3:
            return lease(sys.argv[2])
        if action == "reap" and len(sys.argv) == 3:
            return reap(sys.argv[2])
        print(
            "usage: remote-development.py "
            "{converge|verify|doctor [--verbose]|remove|status|lease ID}",
            file=sys.stderr,
        )
        return 2
    except (
        RemoteError,
        provision.ProvisionError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        print(f"remote-development: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
