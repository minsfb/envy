#!/usr/bin/env python3
"""Open and inspect Envy remote development sessions through system OpenSSH."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any


class LauncherError(RuntimeError):
    pass


def validate_host(value: str) -> str:
    if (
        not value
        or value.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise LauncherError("invalid SSH host")
    return value


def ssh() -> str:
    command = shutil.which("ssh")
    if not command:
        raise LauncherError("system OpenSSH client is required")
    return command


def control_command(control: Path, host: str, operation: str) -> list[str]:
    return [ssh(), "-S", str(control), "-O", operation, host]


def start_master(host: str, control: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [
            ssh(),
            "-M",
            "-S",
            str(control),
            "-o",
            "ControlPersist=no",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            host,
        ],
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
    process: subprocess.Popen[bytes], timeout: float = 30.0
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
    attempts = 1 if requested else 10
    for _ in range(attempts):
        port = free_port(requested)
        specification = f"127.0.0.1:{port}:{remote_socket}"
        result = subprocess.run(
            [ssh(), "-S", str(control), "-O", "forward", "-L", specification, host],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return port
    raise LauncherError("could not allocate a local forwarding port")


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


def remote_command(host: str, action: str) -> list[str]:
    validate_host(host)
    return [
        ssh(),
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        host,
        f'exec "$HOME/.local/bin/envy" remote _{action}',
    ]


def inspect_remote(host: str, action: str) -> int:
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
    return subprocess.run(remote_command(host, action), check=False).returncode


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
                [
                    ssh(),
                    "-S",
                    str(control),
                    "-o",
                    "ControlMaster=no",
                    "-o",
                    "ForwardAgent=no",
                    "-o",
                    "ClearAllForwardings=yes",
                    host,
                    f'exec "$HOME/.local/bin/envy" remote _lease {lease_id}',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
            )
            assert lease.stdin is not None
            lease.stdin.write((json.dumps({"path": project}) + "\n").encode())
            lease.stdin.flush()
            ready = read_json_line(lease)
            port = add_forward(control, host, ready["socket"], requested_port)
            query = urllib.parse.urlencode(
                {"folder": ready["path"]}, quote_via=urllib.parse.quote
            )
            url = f"http://127.0.0.1:{port}/?{query}"
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
                    lease.terminate()
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
                    master.terminate()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="envy remote", description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    opener = commands.add_parser(
        "open", help="open a remote folder in the browser editor"
    )
    opener.add_argument("host")
    opener.add_argument("path", nargs="?", default="~")
    opener.add_argument("--no-browser", action="store_true")
    opener.add_argument(
        "--local-port",
        "--port",
        dest="port",
        type=int,
        choices=range(1, 65536),
        metavar="PORT",
    )
    for action in ("status", "doctor"):
        command = commands.add_parser(action, help=f"inspect remote server {action}")
        command.add_argument("host")
        if action == "doctor":
            command.add_argument("--verbose", action="store_true")
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.action == "open":
            return open_remote(
                arguments.host,
                arguments.path,
                arguments.no_browser,
                arguments.port,
                "browser",
            )
        return inspect_remote(arguments.host, arguments.action)
    except (LauncherError, OSError, subprocess.SubprocessError) as error:
        print(f"envy remote: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
