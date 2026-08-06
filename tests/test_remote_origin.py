#!/usr/bin/env python3
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class OriginServer:
    def __init__(
        self,
        path: Path,
        accepts: int,
        respond: bool = False,
        raw_response: bytes | None = None,
    ) -> None:
        self.path = path
        self.accepts = accepts
        self.respond = respond
        self.raw_response = raw_response
        self.requests: list[dict[str, object]] = []
        self.errors: list[BaseException] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        path.chmod(0o600)
        self.server.listen(8)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        try:
            for _ in range(self.accepts):
                connection, _address = self.server.accept()
                with connection:
                    chunks = bytearray()
                    while b"\n" not in chunks:
                        block = connection.recv(4096)
                        if not block:
                            break
                        chunks.extend(block)
                    if not chunks:
                        continue
                    request = json.loads(bytes(chunks).split(b"\n", 1)[0])
                    self.requests.append(request)
                    if self.respond:
                        if self.raw_response is not None:
                            connection.sendall(self.raw_response)
                        else:
                            reply = {
                                "version": 1,
                                "type": "editor-result",
                                "request_id": request["request_id"],
                                "ok": True,
                            }
                            connection.sendall(json.dumps(reply).encode() + b"\n")
        except BaseException as error:  # surfaced by close()
            self.errors.append(error)

    def close(self) -> None:
        self.thread.join(timeout=3)
        self.server.close()
        if self.thread.is_alive():
            self.fail("origin test server did not finish")
        if self.errors:
            raise self.errors[0]

    def fail(self, message: str) -> None:
        raise AssertionError(message)


class RemoteOriginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.routes = Path(tempfile.mkdtemp(prefix="nv-or-", dir="/tmp"))
        self.routes.chmod(0o700)
        self.addCleanup(shutil.rmtree, self.routes, True)
        self.previous = {
            key: os.environ.get(key)
            for key in (
                "XDG_STATE_HOME",
                "XDG_DATA_HOME",
                "ENVY_REMOTE_ORIGIN_SOCKET",
                "ENVY_REMOTE_ORIGIN_TOKEN",
                "ENVY_REMOTE_ORIGIN_TRANSPORT",
                "TMUX",
                "TMUX_PANE",
                "SSH_CONNECTION",
                "SSH_TTY",
                "ENKAKU_RUN_CALLER_SESSION_ID",
            )
        }
        os.environ["XDG_STATE_HOME"] = str(self.base / "state")
        os.environ["XDG_DATA_HOME"] = str(self.base / "data")
        for key in self.previous:
            if key not in {"XDG_STATE_HOME", "XDG_DATA_HOME"}:
                os.environ.pop(key, None)
        self.feature = load_module(
            f"envy_remote_origin_{id(self)}",
            ROOT / "features" / "remote-development.py",
        )
        self.feature.ORIGIN_ROOT = self.routes

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def route(self, token: str) -> Path:
        return self.feature.origin_socket_path(token)

    def test_exact_private_route_sends_bounded_editor_request(self) -> None:
        project = self.base / "project with spaces"
        project.mkdir()
        token = "a" * 32
        route = self.route(token)
        server = OriginServer(route, accepts=2, respond=True)
        os.environ["ENVY_REMOTE_ORIGIN_SOCKET"] = str(route)
        os.environ["ENVY_REMOTE_ORIGIN_TOKEN"] = token

        self.assertEqual(self.feature.origin_editor(str(project)), 0)

        server.close()
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual(request["version"], 1)
        self.assertEqual(request["type"], "editor")
        self.assertRegex(str(request["request_id"]), r"^[0-9a-f]{32}$")
        self.assertEqual(request["path"], str(project.resolve()))

    def test_prepare_creates_private_directory_and_reports_owned_socket(self) -> None:
        shutil.rmtree(self.routes)
        token = "2" * 32
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.assertEqual(self.feature.prepare_origin(token), 0)

        self.assertEqual(self.routes.stat().st_mode & 0o777, 0o700)
        self.assertFalse(self.routes.is_symlink())
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "version": 1,
                "type": "origin-ready",
                "socket": str(self.routes / f"{token}.sock"),
            },
        )

    def test_prepare_never_repairs_unsafe_directory_or_replaces_symlink(self) -> None:
        token = "6" * 32
        self.routes.chmod(0o755)
        with self.assertRaisesRegex(self.feature.RemoteError, "directory is not private"):
            self.feature.prepare_origin(token)
        self.assertEqual(self.routes.stat().st_mode & 0o777, 0o755)

        self.routes.chmod(0o700)
        route = self.route(token)
        target = self.base / "do-not-touch"
        target.write_text("owned elsewhere\n", encoding="utf-8")
        route.symlink_to(target)
        with self.assertRaisesRegex(self.feature.RemoteError, "unsafe origin route"):
            self.feature.prepare_origin(token)
        self.assertTrue(route.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "owned elsewhere\n")

    def test_prepare_cleans_only_inactive_owned_token_sockets(self) -> None:
        stale = self.route("8" * 32)
        stale_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale_listener.bind(str(stale))
        stale.chmod(0o600)
        stale_listener.close()
        old = self.feature.time.time() - self.feature.ORIGIN_STALE_AGE - 1
        os.utime(stale, (old, old))

        fresh = self.route("7" * 32)
        fresh_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        fresh_listener.bind(str(fresh))
        fresh.chmod(0o600)
        fresh_listener.close()

        live = OriginServer(self.route("9" * 32), accepts=1)
        os.utime(live.path, (old, old))

        wrong_mode = self.route("a" * 32)
        wrong_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wrong_listener.bind(str(wrong_mode))
        wrong_mode.chmod(0o660)
        wrong_listener.close()

        regular = self.route("b" * 32)
        regular.write_text("preserve me\n", encoding="utf-8")
        unrelated = self.routes / "not-a-token.sock"
        unrelated.write_text("preserve me too\n", encoding="utf-8")

        token = "c" * 32
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.feature.prepare_origin(token), 0)

        live.close()
        self.assertFalse(os.path.lexists(stale))
        self.assertTrue(os.path.lexists(fresh))
        self.assertTrue(os.path.lexists(live.path))
        self.assertTrue(os.path.lexists(wrong_mode))
        self.assertTrue(regular.is_file())
        self.assertTrue(unrelated.is_file())

    def test_cleanup_retains_live_route_then_removes_inactive_owned_socket(self) -> None:
        token = "5" * 32
        route = self.route(token)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(route))
        route.chmod(0o600)
        listener.listen(1)

        with self.assertRaisesRegex(self.feature.RemoteError, "active origin route"):
            self.feature.cleanup_origin(token)
        self.assertTrue(route.exists())
        listener.close()

        self.assertEqual(self.feature.cleanup_origin(token), 0)
        self.assertFalse(os.path.lexists(route))
        self.assertFalse(self.routes.exists())

    def test_stale_explicit_route_never_falls_back_to_another_live_route(self) -> None:
        stale_token = "b" * 32
        live_token = "c" * 32
        os.environ["ENVY_REMOTE_ORIGIN_SOCKET"] = str(self.route(stale_token))
        os.environ["ENVY_REMOTE_ORIGIN_TOKEN"] = stale_token
        live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        live.bind(str(self.route(live_token)))
        self.route(live_token).chmod(0o600)
        live.listen(1)

        try:
            with self.assertRaisesRegex(self.feature.RemoteError, "no longer active"):
                self.feature.selected_origin_socket()
        finally:
            live.close()

        # The unrelated live route remains untouched: explicit origin is fail-closed.

    def test_mismatched_broker_response_is_rejected(self) -> None:
        project = self.base / "project"
        project.mkdir()
        token = "7" * 32
        route = self.route(token)
        response = {
            "version": 1,
            "type": "editor-result",
            "request_id": "0" * 32,
            "ok": True,
        }
        server = OriginServer(
            route,
            accepts=2,
            respond=True,
            raw_response=json.dumps(response).encode() + b"\n",
        )
        os.environ["ENVY_REMOTE_ORIGIN_SOCKET"] = str(route)
        os.environ["ENVY_REMOTE_ORIGIN_TOKEN"] = token

        with self.assertRaisesRegex(self.feature.RemoteError, "mismatched response"):
            self.feature.origin_editor(str(project))
        server.close()

    def test_unique_live_route_is_selected_and_multiple_routes_are_ambiguous(self) -> None:
        first = OriginServer(self.route("d" * 32), accepts=1)
        self.assertEqual(self.feature.selected_origin_socket(), first.path)
        first.close()

        left = OriginServer(self.route("e" * 32), accepts=1)
        right = OriginServer(self.route("f" * 32), accepts=1)
        with self.assertRaisesRegex(self.feature.RemoteError, "multiple active"):
            self.feature.selected_origin_socket()
        left.close()
        right.close()

    def test_via_accepts_only_a_unique_hex_prefix_of_at_least_eight(self) -> None:
        left_token = "abcdef001" + "1" * 23
        right_token = "abcdef002" + "2" * 23
        left = OriginServer(self.route(left_token), accepts=2)
        right = OriginServer(self.route(right_token), accepts=1)

        with self.assertRaisesRegex(self.feature.RemoteError, "multiple active") as raised:
            self.feature.selected_origin_socket()
        self.assertIn("abcdef001", str(raised.exception))
        self.assertIn("abcdef002", str(raised.exception))
        self.assertEqual(self.feature.selected_origin_socket("abcdef001"), left.path)
        with self.assertRaisesRegex(self.feature.RemoteError, "8 to 32"):
            self.feature.selected_origin_socket("abcdef0")

        left.close()
        right.close()

    def test_tmux_never_treats_inherited_origin_environment_as_authoritative(self) -> None:
        first = OriginServer(self.route("3" * 32), accepts=1)
        second = OriginServer(self.route("4" * 32), accepts=1)
        os.environ["TMUX"] = "/tmp/tmux,1,0"
        os.environ["TMUX_PANE"] = "%8"
        os.environ["ENVY_REMOTE_ORIGIN_SOCKET"] = str(first.path)
        os.environ["ENVY_REMOTE_ORIGIN_TOKEN"] = "3" * 32

        with self.assertRaisesRegex(self.feature.RemoteError, "multiple active"):
            self.feature.selected_origin_socket()

        first.close()
        second.close()

    def test_managed_tmux_workspace_precedes_pane_cwd(self) -> None:
        workspace = self.base / "workspace"
        pane_cwd = self.base / "pane-cwd"
        workspace.mkdir()
        pane_cwd.mkdir()
        os.environ["TMUX"] = "/tmp/tmux,1,0"
        os.environ["TMUX_PANE"] = "%7"

        def run(arguments, **_kwargs):
            final = arguments[-1]
            values = {
                "#{session_id}": "$4",
                "@envy_remote_workspace_schema": "1",
                "@envy_remote_workspace": str(workspace),
                "#{pane_current_path}": str(pane_cwd),
            }
            return mock.Mock(returncode=0, stdout=values[final] + "\n")

        with (
            mock.patch.object(self.feature.shutil, "which", return_value="/tmux"),
            mock.patch.object(self.feature.subprocess, "run", side_effect=run),
        ):
            self.assertEqual(self.feature.editor_directory(None), workspace.resolve())

    def test_invalid_managed_tmux_metadata_fails_without_cwd_fallback(self) -> None:
        pane_cwd = self.base / "pane-cwd"
        pane_cwd.mkdir()
        os.environ["TMUX_PANE"] = "%7"

        def run(arguments, **_kwargs):
            final = arguments[-1]
            values = {
                "#{session_id}": "$4",
                "@envy_remote_workspace_schema": "2",
                "@envy_remote_workspace": str(pane_cwd),
                "#{pane_current_path}": str(pane_cwd),
            }
            return mock.Mock(returncode=0, stdout=values[final] + "\n")

        with (
            mock.patch.object(self.feature.shutil, "which", return_value="/tmux"),
            mock.patch.object(self.feature.subprocess, "run", side_effect=run),
            self.assertRaisesRegex(self.feature.RemoteError, "metadata is invalid"),
        ):
            self.feature.editor_directory(None)

    def test_unmanaged_tmux_uses_pane_cwd_and_ignores_transport_environment(self) -> None:
        pane_cwd = self.base / "pane-cwd"
        pane_cwd.mkdir()
        os.environ["TMUX"] = "/tmp/tmux,1,0"
        os.environ["TMUX_PANE"] = "%7"
        os.environ["SSH_CONNECTION"] = "client 1 server 2"
        os.environ["SSH_TTY"] = "/dev/pts/1"
        os.environ["ENKAKU_RUN_CALLER_SESSION_ID"] = "do-not-trust"

        def run(arguments, **_kwargs):
            final = arguments[-1]
            if final == "#{session_id}":
                return mock.Mock(returncode=0, stdout="$4\n")
            if final == "#{pane_current_path}":
                return mock.Mock(returncode=0, stdout=str(pane_cwd) + "\n")
            return mock.Mock(returncode=1, stdout="")

        with (
            mock.patch.object(self.feature.shutil, "which", return_value="/tmux"),
            mock.patch.object(self.feature.subprocess, "run", side_effect=run),
        ):
            self.assertEqual(self.feature.editor_directory(None), pane_cwd.resolve())
        with self.assertRaisesRegex(self.feature.RemoteError, "no active remote origin"):
            self.feature.selected_origin_socket()

    def test_origin_shell_decodes_cwd_and_exports_only_explicit_route(self) -> None:
        cwd = self.base / "shell cwd"
        cwd.mkdir()
        token = "1" * 32
        route = self.route(token)
        server = OriginServer(route, accepts=1)
        encoded = base64.urlsafe_b64encode(str(cwd).encode()).decode().rstrip("=")
        fake_shell = self.base / "shell"
        fake_shell.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_shell.chmod(0o755)
        account = mock.Mock(pw_shell=str(fake_shell))
        previous_cwd = Path.cwd()
        self.addCleanup(os.chdir, previous_cwd)

        with (
            mock.patch.object(self.feature.pwd, "getpwuid", return_value=account),
            mock.patch.object(self.feature.os, "execvpe") as execute,
        ):
            self.assertEqual(self.feature.origin_shell(token, encoded), 0)
        server.close()
        arguments = execute.call_args.args
        self.assertEqual(arguments[0], str(fake_shell))
        self.assertEqual(arguments[2]["ENVY_REMOTE_ORIGIN_SOCKET"], str(route))
        self.assertEqual(arguments[2]["ENVY_REMOTE_ORIGIN_TOKEN"], token)
        self.assertEqual(arguments[2]["ENVY_REMOTE_ORIGIN_TRANSPORT"], "ssh")
        self.assertEqual(Path.cwd(), cwd.resolve())


if __name__ == "__main__":
    unittest.main()
