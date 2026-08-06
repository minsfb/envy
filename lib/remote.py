#!/usr/bin/env python3
"""Open, hand off, and inspect Envy remote development capabilities."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import select
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths


class LauncherError(RuntimeError):
    pass


SSH_SESSION_OPTIONS = (
    "-o",
    "ForwardAgent=no",
    "-o",
    "ForwardX11=no",
    "-o",
    "ForwardX11Trusted=no",
    "-o",
    "ClearAllForwardings=yes",
)
SSH_STREAMLOCAL_OPTIONS = (
    "-o",
    "StreamLocalBindMask=0177",
    "-o",
    "StreamLocalBindUnlink=yes",
)
TRANSPORT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_MESSAGE = 65536
EDITOR_STARTUP_TIMEOUT = 30.0
FORWARD_ATTEMPTS = 10
FORWARD_ATTEMPT_TIMEOUT = 2.0


def validate_host(value: str) -> str:
    if (
        not value
        or value.startswith("-")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise LauncherError("invalid SSH host")
    return value


def validate_transport(value: str) -> str:
    if not TRANSPORT_RE.fullmatch(value):
        raise LauncherError("invalid remote transport name")
    return value


def preferred_transport(explicit: str | None = None) -> str:
    if explicit is not None:
        return validate_transport(explicit)
    manifest = os.environ.get("ENVY_MANIFEST")
    if not manifest:
        return "ssh"
    try:
        value = json.loads(Path(manifest).read_text(encoding="utf-8"))
        configured = (
            value.get("settings", {})
            .get("remote_development", {})
            .get("preferred_transport", "ssh")
        )
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        raise LauncherError(f"cannot read remote transport policy: {error}") from error
    if not isinstance(configured, str):
        raise LauncherError("remote preferred_transport must be a string")
    return validate_transport(configured)


def origin_transport(explicit: str | None = None) -> str:
    """Select a return transport without trusting persistent tmux environment."""
    if explicit is not None:
        return validate_transport(explicit)
    if not (os.environ.get("TMUX") or os.environ.get("TMUX_PANE")):
        hinted = os.environ.get("ENVY_REMOTE_ORIGIN_TRANSPORT")
        if hinted is not None:
            return validate_transport(hinted)
    return preferred_transport()


def transport_adapter(name: str) -> Path:
    name = validate_transport(name)
    if name == "ssh":
        raise LauncherError("ssh is a built-in transport")
    root = paths.bin_dir()
    if not root.is_absolute() or any(
        ord(character) < 32 or ord(character) == 127 for character in str(root)
    ):
        raise LauncherError("ENVY_BIN_DIR must be an absolute path without controls")
    adapter = root / f"nv-remote-transport-{name}"
    try:
        mode = adapter.lstat().st_mode
    except OSError as error:
        raise LauncherError(
            f"remote transport adapter is unavailable: {adapter}; "
            "retry with --transport ssh to use built-in OpenSSH"
        ) from error
    if adapter.is_symlink() or not stat.S_ISREG(mode) or not os.access(adapter, os.X_OK):
        raise LauncherError(
            f"remote transport adapter is unavailable: {adapter}; "
            "retry with --transport ssh to use built-in OpenSSH"
        )
    return adapter


def ssh() -> str:
    command = shutil.which("ssh")
    if not command:
        raise LauncherError("system OpenSSH client is required")
    return command


def control_command(control: Path, host: str, operation: str) -> list[str]:
    return [ssh(), "-S", str(control), "-O", operation, host]


def session_command(*arguments: str) -> list[str]:
    """Build an SSH command that may create a connection or remote session."""
    return [ssh(), *SSH_SESSION_OPTIONS, *arguments]


def master_command(host: str, control: Path) -> list[str]:
    return session_command(
        "-M",
        "-S",
        str(control),
        *SSH_STREAMLOCAL_OPTIONS,
        "-o",
        "ControlPersist=no",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        host,
    )


def start_master(host: str, control: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        master_command(host, control),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )
    for _ in range(200):
        if process.poll() is not None:
            raise LauncherError(f"SSH connection to {host} failed")
        result = subprocess.run(
            control_command(control, host, "check"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return process
        time.sleep(0.05)
    process.terminate()
    raise LauncherError(f"SSH connection to {host} timed out")


def read_json_line(
    process: subprocess.Popen[bytes], timeout: float = EDITOR_STARTUP_TIMEOUT
) -> dict[str, Any]:
    if process.stdout is None:
        raise LauncherError("remote lease has no output channel")
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    if not ready:
        raise LauncherError("remote development server startup timed out")
    line = process.stdout.readline(65537)
    if not line or len(line) > 65536:
        raise LauncherError("remote development server returned no readiness data")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise LauncherError(
            "remote development server returned invalid readiness data"
        ) from error
    if (
        not isinstance(value, dict)
        or value.get("engine") != "code-server"
        or value.get("protocol") != "http"
        or value.get("transport") != "streamlocal"
        or not isinstance(value.get("socket"), str)
        or not isinstance(value.get("path"), str)
    ):
        raise LauncherError(
            "remote development server returned incomplete readiness data"
        )
    remote_socket = value["socket"]
    if (
        not remote_socket.startswith("/")
        or ":" in remote_socket
        or any(ord(character) < 32 for character in remote_socket)
    ):
        raise LauncherError("remote development server returned an unsafe socket path")
    return value


def free_port(requested: int | None = None) -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", requested or 0))
        return int(probe.getsockname()[1])


def add_forward(
    control: Path, host: str, remote_socket: str, requested: int | None
) -> int:
    attempts = 1 if requested else FORWARD_ATTEMPTS
    for _ in range(attempts):
        port = free_port(requested)
        specification = f"127.0.0.1:{port}:{remote_socket}"
        try:
            result = subprocess.run(
                [
                    ssh(),
                    "-S",
                    str(control),
                    "-O",
                    "forward",
                    "-L",
                    specification,
                    host,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=FORWARD_ATTEMPT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            if requested is not None:
                break
            continue
        if result.returncode == 0:
            return port
    raise LauncherError("could not allocate a local forwarding port")


def add_reverse_forward(
    control: Path, host: str, remote_socket: Path, local_socket: Path
) -> None:
    specification = f"{remote_socket}:{local_socket}"
    result = subprocess.run(
        [ssh(), "-S", str(control), "-O", "forward", "-R", specification, host],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise LauncherError("could not create the remote origin callback")


def cancel_reverse_forward(
    control: Path, host: str, remote_socket: Path, local_socket: Path
) -> None:
    specification = f"{remote_socket}:{local_socket}"
    subprocess.run(
        [ssh(), "-S", str(control), "-O", "cancel", "-R", specification, host],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def encoded_cwd(cwd: str | None) -> str:
    if cwd is None:
        return "-"
    if (
        not cwd
        or any(ord(character) < 32 or ord(character) == 127 for character in cwd)
    ):
        raise LauncherError("invalid remote shell working directory")
    try:
        raw = cwd.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LauncherError("invalid remote shell working directory") from error
    if len(raw) > 16384:
        raise LauncherError("remote shell working directory is too long")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def origin_prepare_command(control: Path, host: str, identifier: str) -> list[str]:
    if not REQUEST_ID_RE.fullmatch(identifier):
        raise LauncherError("invalid remote origin identifier")
    return session_command(
        "-S",
        str(control),
        "-o",
        "ControlMaster=no",
        host,
        f'exec "$HOME/.local/bin/envy" remote _origin-prepare {identifier}',
    )


def prepare_origin(control: Path, host: str, identifier: str) -> Path:
    result = subprocess.run(
        origin_prepare_command(control, host, identifier),
        check=False,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    if result.returncode != 0 or not result.stdout or len(result.stdout) > MAX_MESSAGE:
        raise LauncherError("remote origin callback preparation failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LauncherError("remote origin callback returned invalid readiness data") from error
    remote_socket = value.get("socket") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("type") != "origin-ready"
        or not isinstance(remote_socket, str)
        or not re.fullmatch(
            rf"/tmp/envy-origin-[0-9]+/{identifier}\.sock", remote_socket
        )
        or len(remote_socket) > 103
        or any(ord(character) < 32 for character in remote_socket)
    ):
        raise LauncherError("remote origin callback returned unsafe readiness data")
    return Path(remote_socket)


def cleanup_origin(control: Path, host: str, identifier: str) -> None:
    subprocess.run(
        session_command(
            "-S",
            str(control),
            "-o",
            "ControlMaster=no",
            host,
            f'exec "$HOME/.local/bin/envy" remote _origin-cleanup {identifier}',
        ),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def origin_shell_command(
    control: Path, host: str, identifier: str, cwd: str | None
) -> list[str]:
    remote_command = (
        'exec "$HOME/.local/bin/envy" remote _origin-shell '
        f"{identifier} {encoded_cwd(cwd)}"
    )
    return session_command(
        "-S",
        str(control),
        "-o",
        "ControlMaster=no",
        "-tt",
        host,
        remote_command,
    )


def open_browser(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(
            ["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return
    for command in ("wslview", "xdg-open"):
        executable = shutil.which(command)
        if executable:
            subprocess.Popen(
                [executable, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
    if not webbrowser.open(url):
        raise LauncherError("no browser opener found; retry with --no-browser")


def folder_url(port: int, project: str) -> str:
    if (
        not project.startswith("/")
        or len(project) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in project)
    ):
        raise LauncherError("remote editor returned an unsafe project path")
    query = urllib.parse.urlencode({"folder": project}, quote_via=urllib.parse.quote)
    return f"http://127.0.0.1:{port}/?{query}"


def terminate_process(process: subprocess.Popen[Any], timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class EditorSession:
    """A lazily-created editor lease and loopback forward on one SSH master."""

    def __init__(self, control: Path, host: str):
        self.control = control
        self.host = host
        self.lease: subprocess.Popen[bytes] | None = None
        self.port: int | None = None

    def ensure(self, project: str) -> str:
        folder_url(1, project)  # Validate before starting or changing remote state.
        if self.lease is None:
            try:
                lease_id = secrets.token_hex(16)
                lease = subprocess.Popen(
                    lease_command(self.control, self.host, lease_id),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=None,
                )
                self.lease = lease
                assert lease.stdin is not None
                lease.stdin.write((json.dumps({"path": project}) + "\n").encode())
                lease.stdin.flush()
                ready = read_json_line(lease)
                self.port = add_forward(
                    self.control, self.host, ready["socket"], requested=None
                )
            except Exception:
                self.close()
                raise
        if self.lease.poll() is not None or self.port is None:
            raise LauncherError("remote editor lease ended unexpectedly")
        return folder_url(self.port, project)

    def close(self) -> None:
        lease = self.lease
        self.lease = None
        if lease is None:
            return
        if lease.stdin is not None:
            try:
                lease.stdin.close()
            except OSError:
                pass
        try:
            lease.wait(timeout=5)
        except subprocess.TimeoutExpired:
            terminate_process(lease)


class OriginBroker:
    """Accept bounded editor requests arriving over an SSH reverse UDS forward."""

    def __init__(self, path: Path, editor: EditorSession):
        self.path = path
        self.editor = editor
        self.responses: dict[str, dict[str, Any]] = {}
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        os.chmod(path, 0o600)
        self.server.listen(8)

    def close(self) -> None:
        self.server.close()
        self.path.unlink(missing_ok=True)
        self.editor.close()

    def _response(self, request_id: str, project: str) -> dict[str, Any]:
        cached = self.responses.get(request_id)
        if cached is not None:
            return cached
        try:
            url = self.editor.ensure(project)
            print(url, flush=True)
            open_browser(url)
            response: dict[str, Any] = {
                "version": 1,
                "type": "editor-result",
                "request_id": request_id,
                "ok": True,
            }
        except (LauncherError, OSError, subprocess.SubprocessError) as error:
            response = {
                "version": 1,
                "type": "editor-result",
                "request_id": request_id,
                "ok": False,
                "error": str(error),
            }
        if len(self.responses) >= 128:
            self.responses.pop(next(iter(self.responses)))
        self.responses[request_id] = response
        return response

    def accept_once(self) -> None:
        connection, _address = self.server.accept()
        with connection:
            connection.settimeout(5)
            with connection.makefile("rb") as reader:
                line = reader.readline(MAX_MESSAGE + 1)
            # Server route discovery uses connect-and-close probes.
            if not line:
                return
            request_id = ""
            try:
                if len(line) > MAX_MESSAGE:
                    raise LauncherError("origin request is oversized")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise LauncherError("origin request must be an object")
                request_id = value.get("request_id", "")
                project = value.get("path")
                if (
                    value.get("version") != 1
                    or value.get("type") != "editor"
                    or not isinstance(request_id, str)
                    or not REQUEST_ID_RE.fullmatch(request_id)
                    or not isinstance(project, str)
                ):
                    raise LauncherError("invalid origin editor request")
                response = self._response(request_id, project)
            except (json.JSONDecodeError, LauncherError) as error:
                response = {
                    "version": 1,
                    "type": "editor-result",
                    "request_id": request_id,
                    "ok": False,
                    "error": str(error),
                }
            try:
                connection.sendall(
                    (json.dumps(response, separators=(",", ":")) + "\n").encode()
                )
            except OSError:
                pass

    def serve(self, shell: subprocess.Popen[Any], master: subprocess.Popen[Any]) -> int:
        while shell.poll() is None:
            if master.poll() is not None:
                raise LauncherError("SSH connection ended unexpectedly")
            readable, _, _ = select.select([self.server], [], [], 0.25)
            if readable:
                self.accept_once()
        return int(shell.returncode or 0)


def remote_command(host: str, action: str, verbose: bool = False) -> list[str]:
    validate_host(host)
    commands = {
        "status": 'exec "$HOME/.local/bin/envy" remote _status',
        "doctor": 'exec "$HOME/.local/bin/envy" remote _doctor',
    }
    try:
        command = commands[action]
    except KeyError as error:
        raise LauncherError("invalid remote inspection action") from error
    if action == "doctor" and verbose:
        command += " --verbose"
    return session_command(
        host,
        command,
    )


def inspect_remote(host: str, action: str, verbose: bool = False) -> int:
    if action == "doctor":
        if (
            sys.platform == "darwin"
            or shutil.which("wslview")
            or shutil.which("xdg-open")
        ):
            print("  OK local browser opener is available")
        else:
            print("  WARN no local browser opener found; use --no-browser")
        print(f"  OK system OpenSSH client: {ssh()}")
        if verbose:
            if sys.platform == "darwin":
                opener = shutil.which("open") or "open"
            else:
                opener = shutil.which("wslview") or shutil.which("xdg-open")
            print(f"  INFO local platform: {sys.platform}")
            print(f"  INFO browser opener: {opener or 'webbrowser fallback only'}")
            print(
                "  INFO SSH policy: agent, X11, trusted X11, and configured "
                "forwards are disabled"
            )
            print(
                "  INFO tunnel policy: one loopback-only local port forwards "
                "to the private remote Unix socket"
            )
    return subprocess.run(
        remote_command(host, action, verbose=verbose), check=False
    ).returncode


def lease_command(control: Path, host: str, lease_id: str) -> list[str]:
    return session_command(
        "-S",
        str(control),
        "-o",
        "ControlMaster=no",
        host,
        f'exec "$HOME/.local/bin/envy" remote _lease {lease_id}',
    )


def open_remote(
    host: str, project: str, no_browser: bool, requested_port: int | None, client: str
) -> int:
    if client != "browser":
        raise LauncherError("v1 supports only the browser client")
    host = validate_host(host)
    lease_id = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="envy-r-", dir="/tmp") as scratch:
        os.chmod(scratch, 0o700)
        control = Path(scratch) / "control"
        master: subprocess.Popen[bytes] | None = None
        lease: subprocess.Popen[bytes] | None = None
        try:
            master = start_master(host, control)
            lease = subprocess.Popen(
                lease_command(control, host, lease_id),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
            )
            assert lease.stdin is not None
            lease.stdin.write((json.dumps({"path": project}) + "\n").encode())
            lease.stdin.flush()
            ready = read_json_line(lease)
            port = add_forward(control, host, ready["socket"], requested_port)
            url = folder_url(port, ready["path"])
            print(url, flush=True)
            if not no_browser:
                open_browser(url)
            print(
                "Remote development session active; press Ctrl-C to close.",
                file=sys.stderr,
            )
            while lease.poll() is None and master.poll() is None:
                time.sleep(0.25)
            if lease.returncode not in {None, 0}:
                raise LauncherError("remote development lease ended unexpectedly")
            if master.returncode not in {None, 0}:
                raise LauncherError("SSH connection ended unexpectedly")
            return 0
        except KeyboardInterrupt:
            return 0
        finally:
            if lease is not None:
                if lease.stdin is not None:
                    try:
                        lease.stdin.close()
                    except OSError:
                        pass
                try:
                    lease.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_process(lease)
            if master is not None and master.poll() is None:
                subprocess.run(
                    control_command(control, host, "exit"),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    master.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_process(master)


def adapter_command(name: str, action: str, **values: Any) -> list[str]:
    command = [str(transport_adapter(name)), action]
    for key, value in values.items():
        if value is None or value is False:
            continue
        option = "--" + key.replace("_", "-")
        command.extend([option, str(value)])
    return command


def adapter_editor(
    transport: str,
    host: str,
    project: str,
    no_browser: bool,
    requested_port: int | None,
) -> int:
    process = subprocess.Popen(
        adapter_command(
            transport,
            "editor",
            host=validate_host(host),
            path=project,
            local_port=requested_port,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    try:
        if process.stdout is None:
            raise LauncherError("transport adapter has no readiness channel")
        ready, _, _ = select.select([process.stdout], [], [], 30)
        if not ready:
            raise LauncherError("transport adapter editor startup timed out")
        line = process.stdout.readline(MAX_MESSAGE + 1)
        if not line or len(line) > MAX_MESSAGE:
            raise LauncherError("transport adapter returned no readiness data")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise LauncherError("transport adapter returned invalid readiness data") from error
        local_port = value.get("local_port") if isinstance(value, dict) else None
        path = value.get("path") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("type") != "editor-ready"
            or not isinstance(local_port, int)
            or isinstance(local_port, bool)
            or not 1 <= local_port <= 65535
            or not isinstance(path, str)
        ):
            raise LauncherError("transport adapter returned incomplete readiness data")
        if requested_port is not None and local_port != requested_port:
            raise LauncherError(
                "transport adapter did not honor the requested local port"
            )
        if process.poll() is not None:
            raise LauncherError("transport adapter exited after reporting readiness")
        url = folder_url(local_port, path)
        process.stdout.close()
        print(url, flush=True)
        if not no_browser:
            open_browser(url)
        print("Remote development session active; press Ctrl-C to close.", file=sys.stderr)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            terminate_process(process)


def adapter_shell(transport: str, host: str, cwd: str | None) -> int:
    if cwd is not None:
        encoded_cwd(cwd)  # Apply the same data bounds as the built-in transport.
    return subprocess.run(
        adapter_command(
            transport, "shell", host=validate_host(host), cwd=cwd
        ),
        check=False,
    ).returncode


def installed_envy() -> Path:
    executable = paths.bin_dir() / "envy"
    if (
        executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise LauncherError(f"managed envy launcher is unavailable: {executable}")
    return executable


def origin_editor(transport: str, project: str | None, via: str | None) -> int:
    if transport != "ssh":
        return subprocess.run(
            adapter_command(transport, "origin", path=project, via=via),
            check=False,
        ).returncode
    command = [str(installed_envy()), "remote", "_origin-editor"]
    if via:
        command.extend(["--via", via])
    if project is not None:
        command.append(project)
    return subprocess.run(command, check=False).returncode


def ssh_shell(host: str, cwd: str | None) -> int:
    host = validate_host(host)
    identifier = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="envy-r-", dir="/tmp") as scratch:
        os.chmod(scratch, 0o700)
        control = Path(scratch) / "control"
        broker_path = Path(scratch) / "origin.sock"
        master: subprocess.Popen[bytes] | None = None
        shell: subprocess.Popen[Any] | None = None
        broker: OriginBroker | None = None
        remote_socket: Path | None = None
        try:
            editor = EditorSession(control, host)
            broker = OriginBroker(broker_path, editor)
            master = start_master(host, control)
            remote_socket = prepare_origin(control, host, identifier)
            add_reverse_forward(control, host, remote_socket, broker_path)
            shell = subprocess.Popen(origin_shell_command(control, host, identifier, cwd))
            return broker.serve(shell, master)
        except KeyboardInterrupt:
            return 130
        finally:
            try:
                if shell is not None:
                    terminate_process(shell)
            finally:
                try:
                    if broker is not None:
                        broker.close()
                finally:
                    if master is not None and master.poll() is None:
                        if remote_socket is not None:
                            cancel_reverse_forward(
                                control, host, remote_socket, broker_path
                            )
                            cleanup_origin(control, host, identifier)
                        subprocess.run(
                            control_command(control, host, "exit"),
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        try:
                            master.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            terminate_process(master)


def remote_shell(host: str, cwd: str | None, transport: str | None) -> int:
    selected = preferred_transport(transport)
    if selected == "ssh":
        return ssh_shell(host, cwd)
    return adapter_shell(selected, host, cwd)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="envy remote", description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    opener = commands.add_parser(
        "editor", help="open or hand off a remote folder in the browser editor"
    )
    opener.add_argument("target", nargs="?")
    opener.add_argument("path", nargs="?")
    opener.add_argument("--origin", action="store_true")
    opener.add_argument("--via", metavar="ROUTE")
    opener.add_argument("--transport")
    opener.add_argument("--no-browser", action="store_true")
    opener.add_argument(
        "--local-port",
        "--port",
        dest="port",
        type=int,
        choices=range(1, 65536),
        metavar="PORT",
    )
    shell = commands.add_parser("shell", help="open an origin-aware remote shell")
    shell.add_argument("host")
    shell.add_argument("--transport")
    shell.add_argument("--cwd")
    for action in ("status", "doctor"):
        command = commands.add_parser(action, help=f"inspect remote server {action}")
        command.add_argument("host")
        if action == "doctor":
            command.add_argument("--verbose", action="store_true")
    return result


def main() -> int:
    raw_arguments = sys.argv[1:]
    if raw_arguments[:1] == ["open"]:
        raw_arguments = ["editor", *raw_arguments[1:]]
    arguments = parser().parse_args(raw_arguments)
    try:
        if arguments.action == "editor":
            origin = arguments.origin or arguments.target is None
            if origin:
                selected = origin_transport(arguments.transport)
                if arguments.path is not None:
                    raise LauncherError("origin editor accepts at most one path")
                if arguments.no_browser or arguments.port is not None:
                    raise LauncherError(
                        "--no-browser and --local-port are unavailable with --origin"
                    )
                return origin_editor(selected, arguments.target, arguments.via)
            selected = preferred_transport(arguments.transport)
            if arguments.via:
                raise LauncherError("--via requires --origin")
            project = arguments.path if arguments.path is not None else "~"
            if selected == "ssh":
                return open_remote(
                    arguments.target,
                    project,
                    arguments.no_browser,
                    arguments.port,
                    "browser",
                )
            return adapter_editor(
                selected,
                arguments.target,
                project,
                arguments.no_browser,
                arguments.port,
            )
        if arguments.action == "shell":
            return remote_shell(arguments.host, arguments.cwd, arguments.transport)
        return inspect_remote(
            arguments.host,
            arguments.action,
            verbose=getattr(arguments, "verbose", False),
        )
    except (LauncherError, OSError, subprocess.SubprocessError) as error:
        print(f"envy remote: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
