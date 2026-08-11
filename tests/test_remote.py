#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


launcher = load_module("envy_remote_launcher", ROOT / "lib" / "remote.py")


class RemoteLauncherTest(unittest.TestCase):
    def assert_session_hardening(self, command: list[str]) -> None:
        expected = list(launcher.SSH_SESSION_OPTIONS)
        self.assertEqual(command[1 : 1 + len(expected)], expected)
        for option in (
            "ForwardAgent=no",
            "ForwardX11=no",
            "ForwardX11Trusted=no",
            "ClearAllForwardings=yes",
        ):
            self.assertEqual(command.count(option), 1)

    def test_host_validation_accepts_ssh_aliases(self) -> None:
        self.assertEqual(launcher.validate_host("user@dev.example"), "user@dev.example")

    def test_host_validation_rejects_options_and_whitespace(self) -> None:
        for value in (
            "-oProxyCommand=bad",
            "host name",
            "host\ncommand",
            "host\x7fcommand",
            "",
        ):
            with self.subTest(value=value), self.assertRaises(launcher.LauncherError):
                launcher.validate_host(value)

    def test_remote_helper_command_is_fixed(self) -> None:
        with mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"):
            status = launcher.remote_command("dev", "status")
            doctor = launcher.remote_command("dev", "doctor", verbose=True)
        self.assertEqual(status[-1], 'exec "$HOME/.local/bin/envy" remote _status')
        self.assertEqual(
            doctor[-1], 'exec "$HOME/.local/bin/envy" remote _doctor --verbose'
        )

    def test_remote_helper_rejects_unknown_action(self) -> None:
        with self.assertRaisesRegex(
            launcher.LauncherError, "invalid remote inspection action"
        ):
            launcher.remote_command("dev", "doctor; unsafe")

    def test_connection_and_session_commands_have_exact_argv(self) -> None:
        control = Path("/tmp/control")
        with mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"):
            master = launcher.master_command("dev", control)
            lease = launcher.lease_command(control, "dev", "0123456789abcdef")
            status = launcher.remote_command("dev", "status")
            doctor = launcher.remote_command("dev", "doctor")
        prefix = ["/usr/bin/ssh", *launcher.SSH_SESSION_OPTIONS]
        self.assertEqual(
            master,
            [
                *prefix,
                "-M",
                "-S",
                "/tmp/control",
                *launcher.SSH_STREAMLOCAL_OPTIONS,
                "-o",
                "ControlPersist=no",
                "-o",
                "ExitOnForwardFailure=yes",
                "-N",
                "dev",
            ],
        )
        self.assertEqual(
            lease,
            [
                *prefix,
                "-S",
                "/tmp/control",
                "-o",
                "ControlMaster=no",
                "dev",
                'exec "$HOME/.local/bin/envy" remote _lease 0123456789abcdef',
            ],
        )
        self.assertEqual(
            status,
            [*prefix, "dev", 'exec "$HOME/.local/bin/envy" remote _status'],
        )
        self.assertEqual(
            doctor,
            [*prefix, "dev", 'exec "$HOME/.local/bin/envy" remote _doctor'],
        )
        for command in (master, lease, status, doctor):
            with self.subTest(command=command):
                self.assert_session_hardening(command)

    def test_control_check_and_exit_remain_mux_operations(self) -> None:
        with mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"):
            commands = (
                launcher.control_command(Path("/tmp/control"), "dev", "check"),
                launcher.control_command(Path("/tmp/control"), "dev", "exit"),
            )
        self.assertEqual(
            commands,
            (
                ["/usr/bin/ssh", "-S", "/tmp/control", "-O", "check", "dev"],
                ["/usr/bin/ssh", "-S", "/tmp/control", "-O", "exit", "dev"],
            ),
        )

    def test_forward_operation_is_loopback_only(self) -> None:
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"),
            mock.patch.object(launcher, "free_port", return_value=43127),
            mock.patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run,
        ):
            port = launcher.add_forward(
                Path("/tmp/control"), "dev", "/run/user/123/server.sock", None
            )
        self.assertEqual(port, 43127)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/ssh",
                "-S",
                "/tmp/control",
                "-O",
                "forward",
                "-L",
                "127.0.0.1:43127:/run/user/123/server.sock",
                "dev",
            ],
        )

    def test_verbose_doctor_reports_local_policy_and_forwards_flag(self) -> None:
        completed = mock.Mock(returncode=7)
        output = io.StringIO()
        with (
            mock.patch.object(launcher.sys, "platform", "darwin"),
            mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"),
            mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/open"),
            mock.patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run,
            redirect_stdout(output),
        ):
            result = launcher.inspect_remote("dev", "doctor", verbose=True)
        self.assertEqual(result, 7)
        self.assertIn("OK local browser opener is available", output.getvalue())
        self.assertIn("INFO local platform: darwin", output.getvalue())
        self.assertIn("INFO browser opener: /usr/bin/open", output.getvalue())
        self.assertIn("INFO SSH policy:", output.getvalue())
        self.assertIn("INFO tunnel policy:", output.getvalue())
        self.assertEqual(
            run.call_args.args[0][-1],
            'exec "$HOME/.local/bin/envy" remote _doctor --verbose',
        )

    def test_default_doctor_output_and_remote_command_are_unchanged(self) -> None:
        completed = mock.Mock(returncode=0)
        output = io.StringIO()
        with (
            mock.patch.object(launcher.sys, "platform", "darwin"),
            mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"),
            mock.patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run,
            redirect_stdout(output),
        ):
            result = launcher.inspect_remote("dev", "doctor")
        self.assertEqual(result, 0)
        self.assertNotIn("INFO", output.getvalue())
        self.assertEqual(
            run.call_args.args[0][-1], 'exec "$HOME/.local/bin/envy" remote _doctor'
        )

    def test_main_forwards_verbose_to_inspection(self) -> None:
        with (
            mock.patch.object(
                launcher.sys, "argv", ["envy remote", "doctor", "dev", "--verbose"]
            ),
            mock.patch.object(launcher, "inspect_remote", return_value=3) as inspect,
        ):
            self.assertEqual(launcher.main(), 3)
        inspect.assert_called_once_with("dev", "doctor", verbose=True)

    def test_preferred_transport_uses_composed_manifest_and_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "settings": {
                            "remote_development": {"preferred_transport": "ek"}
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"ENVY_MANIFEST": str(manifest)}):
                self.assertEqual(launcher.preferred_transport(), "ek")
                self.assertEqual(launcher.preferred_transport("ssh"), "ssh")
        for invalid in ("../ek", "EK", "ek shell", ""):
            with self.subTest(invalid=invalid), self.assertRaises(launcher.LauncherError):
                launcher.validate_transport(invalid)
        with self.assertRaises(launcher.LauncherError):
            launcher.preferred_transport("")

    def test_origin_transport_hint_is_scoped_and_never_trusted_in_tmux(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"ENVY_REMOTE_ORIGIN_TRANSPORT": "ssh"},
                clear=True,
            ),
            mock.patch.object(launcher, "preferred_transport", return_value="ek"),
        ):
            self.assertEqual(launcher.origin_transport(), "ssh")
            self.assertEqual(launcher.origin_transport("ek"), "ek")
        with (
            mock.patch.dict(
                os.environ,
                {"ENVY_REMOTE_ORIGIN_TRANSPORT": "ssh", "TMUX_PANE": "%1"},
                clear=True,
            ),
            mock.patch.object(
                launcher, "preferred_transport", return_value="ek"
            ) as preferred,
        ):
            self.assertEqual(launcher.origin_transport(), "ek")
        preferred.assert_called_once_with()

    def test_transport_adapter_is_exact_executable_and_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "nv-remote-transport-ek"
            adapter.write_text("#!/bin/sh\n", encoding="utf-8")
            adapter.chmod(0o755)
            with mock.patch.object(launcher.paths, "bin_dir", return_value=root):
                self.assertEqual(launcher.transport_adapter("ek"), adapter)
                adapter.unlink()
                with self.assertRaisesRegex(
                    launcher.LauncherError, r"unavailable.*--transport ssh"
                ):
                    launcher.transport_adapter("ek")
                target = root / "target"
                target.write_text("#!/bin/sh\n", encoding="utf-8")
                target.chmod(0o755)
                adapter.symlink_to(target)
                with self.assertRaisesRegex(
                    launcher.LauncherError, r"unavailable.*--transport ssh"
                ):
                    launcher.transport_adapter("ek")

    def test_primary_help_exposes_editor_but_hides_open_alias(self) -> None:
        help_text = launcher.parser().format_help()
        self.assertIn("{editor,shell,status,doctor}", help_text)
        self.assertNotIn("\n    open ", help_text)

    def test_reverse_forward_and_origin_shell_use_fixed_structured_values(self) -> None:
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"),
            mock.patch.object(launcher.subprocess, "run", return_value=completed) as run,
        ):
            launcher.add_reverse_forward(
                Path("/tmp/control"),
                "dev",
                Path("/tmp/envy-origin-1/a.sock"),
                Path("/tmp/private/broker.sock"),
            )
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/ssh",
                "-S",
                "/tmp/control",
                "-O",
                "forward",
                "-R",
                "/tmp/envy-origin-1/a.sock:/tmp/private/broker.sock",
                "dev",
            ],
        )
        with mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"):
            command = launcher.origin_shell_command(
                Path("/tmp/control"), "dev", "a" * 32, "/work/a $()"
            )
        self.assertIn("_origin-shell " + "a" * 32, command[-1])
        self.assertNotIn("/work/a $()", command[-1])
        self.assertIn("-tt", command)

    def test_prepare_origin_accepts_only_bounded_private_readiness_shape(self) -> None:
        ready = mock.Mock(
            returncode=0,
            stdout=(
                b'{"version":1,"type":"origin-ready",'
                b'"socket":"/tmp/envy-origin-501/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.sock"}\n'
            ),
        )
        with (
            mock.patch.object(launcher, "ssh", return_value="/usr/bin/ssh"),
            mock.patch.object(launcher.subprocess, "run", return_value=ready),
        ):
            path = launcher.prepare_origin(Path("/tmp/control"), "dev", "a" * 32)
        self.assertEqual(
            path, Path("/tmp/envy-origin-501/" + "a" * 32 + ".sock")
        )

    def test_origin_broker_ignores_probe_and_deduplicates_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "broker.sock"
            editor = mock.Mock()
            editor.ensure.return_value = "http://127.0.0.1:43127/?folder=%2Fwork"
            broker = launcher.OriginBroker(path, editor)
            request = {
                "version": 1,
                "type": "editor",
                "request_id": "b" * 32,
                "path": "/work",
            }
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.connect(str(path))
                probe.close()
                broker.accept_once()
                with mock.patch.object(launcher, "open_browser") as opener:
                    for _ in range(2):
                        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        client.connect(str(path))
                        client.sendall((json.dumps(request) + "\n").encode())
                        broker.accept_once()
                        response = json.loads(client.makefile("rb").readline())
                        client.close()
                        self.assertTrue(response["ok"])
                editor.ensure.assert_called_once_with("/work")
                opener.assert_called_once()
            finally:
                broker.close()

    def test_hidden_open_alias_and_zero_argument_origin_are_unambiguous(self) -> None:
        with (
            mock.patch.object(
                launcher.sys, "argv", ["envy remote", "open", "dev", "/work"]
            ),
            mock.patch.object(launcher, "preferred_transport", return_value="ssh"),
            mock.patch.object(launcher, "open_remote", return_value=7) as direct,
        ):
            self.assertEqual(launcher.main(), 7)
        direct.assert_called_once_with("dev", "/work", False, None, "browser")
        with (
            mock.patch.object(launcher.sys, "argv", ["envy remote", "editor"]),
            mock.patch.object(launcher, "origin_transport", return_value="ssh"),
            mock.patch.object(launcher, "origin_editor", return_value=8) as origin,
        ):
            self.assertEqual(launcher.main(), 8)
        origin.assert_called_once_with("ssh", None, None)

        with (
            mock.patch.object(
                launcher.sys,
                "argv",
                [
                    "envy remote",
                    "editor",
                    "--origin",
                    "--via",
                    "abc12345",
                    "/work",
                ],
            ),
            mock.patch.object(launcher, "origin_transport", return_value="ssh"),
            mock.patch.object(launcher, "origin_editor", return_value=9) as explicit,
        ):
            self.assertEqual(launcher.main(), 9)
        explicit.assert_called_once_with("ssh", "/work", "abc12345")

    def test_adapter_editor_returns_primitives_and_core_constructs_url(self) -> None:
        process = mock.Mock()
        process.stdout = io.BytesIO(
            b'{"version":1,"type":"editor-ready",'
            b'"local_port":43127,"path":"/work/a $()"}\n'
        )
        process.poll.return_value = None

        def wait(*_args, **_kwargs):
            process.poll.return_value = 0
            return 0

        process.wait.side_effect = wait
        with (
            mock.patch.object(
                launcher, "adapter_command", return_value=["/managed/adapter", "editor"]
            ),
            mock.patch.object(launcher.subprocess, "Popen", return_value=process),
            mock.patch.object(launcher.select, "select", return_value=([process.stdout], [], [])),
            mock.patch.object(launcher, "open_browser") as opener,
        ):
            self.assertEqual(
                launcher.adapter_editor("ek", "dev", "/work", False, None), 0
            )
        opener.assert_called_once_with(
            "http://127.0.0.1:43127/?folder=%2Fwork%2Fa%20%24%28%29"
        )

    def test_adapter_editor_rejects_wrong_requested_port_and_stops_adapter(self) -> None:
        process = mock.Mock()
        process.stdout = io.BytesIO(
            b'{"version":1,"type":"editor-ready",'
            b'"local_port":43128,"path":"/work"}\n'
        )
        process.poll.return_value = None
        with (
            mock.patch.object(
                launcher, "adapter_command", return_value=["/managed/adapter", "editor"]
            ),
            mock.patch.object(launcher.subprocess, "Popen", return_value=process),
            mock.patch.object(
                launcher.select, "select", return_value=([process.stdout], [], [])
            ),
            mock.patch.object(launcher, "open_browser") as opener,
            self.assertRaisesRegex(launcher.LauncherError, "requested local port"),
        ):
            launcher.adapter_editor("ek", "dev", "/work", False, 43127)
        opener.assert_not_called()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_editor_session_escalates_cleanup_for_a_stuck_lease(self) -> None:
        lease = mock.Mock()
        lease.stdin = mock.Mock()
        lease.wait.side_effect = [
            launcher.subprocess.TimeoutExpired("lease", 5),
            launcher.subprocess.TimeoutExpired("lease", 5),
            0,
        ]
        lease.poll.return_value = None
        editor = launcher.EditorSession(Path("/tmp/control"), "dev")
        editor.lease = lease

        editor.close()

        lease.stdin.close.assert_called_once_with()
        lease.terminate.assert_called_once_with()
        lease.kill.assert_called_once_with()
        self.assertEqual(lease.wait.call_count, 3)

    def test_remote_shell_cwd_is_bounded_for_every_transport(self) -> None:
        for cwd in ("bad\x7fpath", "x" * 16385):
            with (
                self.subTest(cwd=cwd[:20]),
                mock.patch.object(launcher, "adapter_command") as command,
                self.assertRaises(launcher.LauncherError),
            ):
                launcher.adapter_shell("ek", "dev", cwd)
            command.assert_not_called()


class RemoteSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.previous = {
            key: os.environ.get(key)
            for key in (
                "XDG_STATE_HOME",
                "XDG_DATA_HOME",
                "XDG_RUNTIME_DIR",
                "ENVY_MANIFEST",
            )
        }
        os.environ["XDG_STATE_HOME"] = str(self.base / "state")
        os.environ["XDG_DATA_HOME"] = str(self.base / "data")
        os.environ["XDG_RUNTIME_DIR"] = str(self.base / "runtime")
        os.environ.pop("ENVY_MANIFEST", None)
        self.feature = load_module(
            f"envy_remote_feature_{id(self)}",
            ROOT / "features" / "remote-development.py",
        )

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def manifest(self, setting: object) -> None:
        path = self.base / "manifest.json"
        path.write_text(
            json.dumps({"settings": {"remote_development": setting}}), encoding="utf-8"
        )
        os.environ["ENVY_MANIFEST"] = str(path)

    def test_origin_response_timeout_covers_full_cold_start_budget(self) -> None:
        self.assertEqual(
            self.feature.ORIGIN_EDITOR_STARTUP_TIMEOUT,
            launcher.EDITOR_STARTUP_TIMEOUT,
        )
        self.assertEqual(
            self.feature.ORIGIN_FORWARD_SETUP_TIMEOUT,
            launcher.FORWARD_ATTEMPTS * launcher.FORWARD_ATTEMPT_TIMEOUT,
        )
        self.assertGreater(
            self.feature.ORIGIN_TIMEOUT,
            self.feature.ORIGIN_EDITOR_STARTUP_TIMEOUT
            + self.feature.ORIGIN_FORWARD_SETUP_TIMEOUT,
        )

    def test_defaults_to_five_minute_grace_and_open_vsx(self) -> None:
        grace, registry = self.feature.settings()
        self.assertEqual(grace, 300)
        self.assertIn("open-vsx.org", registry["serviceUrl"])

    def test_bundle_can_replace_registry_and_grace(self) -> None:
        self.manifest(
            {
                "idle_grace_seconds": 45,
                "extension_registry": {
                    "service_url": "https://registry.example/gallery",
                    "item_url": "https://registry.example/item",
                    "resource_url_template": "https://registry.example/{publisher}/{name}",
                },
            }
        )
        grace, registry = self.feature.settings()
        self.assertEqual(grace, 45)
        self.assertEqual(registry["serviceUrl"], "https://registry.example/gallery")

    def test_invalid_grace_fails_closed(self) -> None:
        self.manifest({"idle_grace_seconds": 3601})
        with self.assertRaises(self.feature.RemoteError):
            self.feature.settings()

    def test_shared_engine_is_reused_and_stopped_by_recorded_identity(self) -> None:
        executable = self.base / "fake-code-server"
        executable.write_text(
            """#!/usr/bin/env python3
import signal,socket,sys,time
path=sys.argv[sys.argv.index('--socket')+1]
server=socket.socket(socket.AF_UNIX)
server.bind(path)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True: time.sleep(1)
""",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        original_dependency = self.feature.provision.dependency
        original_command = self.feature.provision.managed_command
        original_verify = self.feature.provision.verify
        original_settings = self.feature.settings
        original_runtime = self.feature.runtime_dir
        original_fingerprint = self.feature.extension_fingerprint
        self.feature.provision.dependency = lambda _name: {"version": "1.2.3"}
        self.feature.provision.managed_command = lambda _item: executable
        self.feature.provision.verify = lambda _name: 0
        self.feature.settings = lambda: (300, self.feature.DEFAULT_REGISTRY)
        self.feature.runtime_dir = lambda: self.base / "r"
        fingerprint = ["inventory-one"]
        self.feature.extension_fingerprint = lambda: fingerprint[0]

        def identity(pid: int):
            try:
                waited, _status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    return None
            except ChildProcessError:
                pass
            try:
                os.kill(pid, 0)
            except OSError:
                return None
            return f"identity-{pid}"

        self.feature.proc_identity = identity
        try:
            first = self.feature.start_engine()
            second = self.feature.start_engine()
            self.assertEqual(first["pid"], second["pid"])
            fingerprint[0] = "inventory-two"
            unchanged = self.feature.start_engine()
            self.assertEqual(first["pid"], unchanged["pid"])
            restarted = self.feature.start_engine(restart_if_changed=True)
            self.assertNotEqual(first["pid"], restarted["pid"])
            self.assertEqual(restarted["fingerprint"], "inventory-two")
            self.assertTrue(self.feature.socket_path().is_socket())
        finally:
            self.feature.stop_engine()
            self.feature.provision.dependency = original_dependency
            self.feature.provision.managed_command = original_command
            self.feature.provision.verify = original_verify
            self.feature.settings = original_settings
            self.feature.runtime_dir = original_runtime
            self.feature.extension_fingerprint = original_fingerprint
        self.assertFalse(self.feature.ENGINE_STATE.exists())

    def test_extension_cache_invalidation_is_scoped_and_idempotent(self) -> None:
        user_data = self.feature.DATA / "user-data" / "code-server"
        profile = user_data / "CachedProfilesData" / "__default__profile__"
        user = user_data / "User"
        profile.mkdir(parents=True)
        user.mkdir(parents=True)
        caches = [
            profile / "extensions.builtin.cache",
            profile / "extensions.user.cache",
            user / "customBuiltinExtensionsCache.json",
            user / "systemExtensionsCache.json",
        ]
        for cache in caches:
            cache.write_text("stale", encoding="utf-8")
        unrelated = profile / "workspace.json"
        unrelated.write_text("keep", encoding="utf-8")
        root_unrelated = profile.parent / "metadata.json"
        root_unrelated.write_text("keep", encoding="utf-8")

        self.feature.invalidate_extension_caches(user_data, "inventory-one")
        self.assertTrue(all(not cache.exists() for cache in caches))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
        self.assertEqual(root_unrelated.read_text(encoding="utf-8"), "keep")

        caches[0].write_text("new cache", encoding="utf-8")
        self.feature.invalidate_extension_caches(user_data, "inventory-one")
        self.assertTrue(caches[0].is_file())
        self.feature.invalidate_extension_caches(user_data, "inventory-two")
        self.assertFalse(caches[0].exists())

    def test_extension_fingerprint_tracks_tree_and_extension_manifests(self) -> None:
        tree = self.base / "engine"
        tree.mkdir()
        manifest = tree / self.feature.provision.TREE_MANIFEST
        manifest.write_text('{"artifact":"one"}\n', encoding="utf-8")
        with (
            mock.patch.object(
                self.feature.provision,
                "dependency",
                return_value={"version": "1.2.3"},
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=tree
            ),
        ):
            initial = self.feature.extension_fingerprint()
            package = (
                self.feature.DATA / "extensions" / "example.extension" / "package.json"
            )
            package.parent.mkdir(parents=True)
            package.write_text('{"version":"1.0.0"}\n', encoding="utf-8")
            with_extension = self.feature.extension_fingerprint()
            package.write_text('{"version":"1.0.1"}\n', encoding="utf-8")
            updated_extension = self.feature.extension_fingerprint()
        self.assertNotEqual(initial, with_extension)
        self.assertNotEqual(with_extension, updated_extension)

    def test_feature_lifecycle_retains_user_data_on_removal(self) -> None:
        version_root = self.base / "engine" / "1.2.3"
        version_root.mkdir(parents=True)
        item = {"version": "1.2.3"}
        retained = self.feature.DATA / "user-data" / "settings.json"
        retained.parent.mkdir(parents=True)
        retained.write_text("{}", encoding="utf-8")
        with (
            mock.patch.object(self.feature.sys, "platform", "linux"),
            mock.patch.object(
                self.feature,
                "settings",
                return_value=(300, self.feature.DEFAULT_REGISTRY),
            ),
            mock.patch.object(self.feature.provision, "install", return_value=0),
            mock.patch.object(self.feature.provision, "verify", return_value=0),
            mock.patch.object(self.feature.provision, "dependency", return_value=item),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=version_root
            ),
        ):
            self.assertEqual(self.feature.converge(), 0)
            self.assertEqual(self.feature.verify(), 0)
            self.assertTrue((version_root.parent / "current").is_symlink())
            self.assertEqual(self.feature.remove(), 0)
        self.assertTrue(retained.is_file())
        self.assertTrue(version_root.is_dir())
        self.assertFalse((version_root.parent / "current").exists())


if __name__ == "__main__":
    unittest.main()
