#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import tempfile
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


class RemoteProvisionLaunchTest(unittest.TestCase):
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
            f"envy_remote_provision_launch_{id(self)}",
            ROOT / "features" / "remote-development.py",
        )

    def tearDown(self) -> None:
        for process in self.feature._STARTED_PROCESSES.values():
            if isinstance(process, subprocess.Popen):
                process.terminate()
        self.feature._STARTED_PROCESSES.clear()
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def provisioned_tree(self) -> tuple[dict[str, object], Path, Path]:
        tree = self.base / "code-server" / "1.2.3"
        executable = tree / "bin" / "code-server"
        resource = tree / "lib" / "resource.txt"
        executable.parent.mkdir(parents=True)
        resource.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\necho 1.2.3\n", encoding="utf-8")
        executable.chmod(0o755)
        resource.write_text("safe\n", encoding="utf-8")
        resource.chmod(0o644)

        def entry(path: Path) -> dict[str, object]:
            relative = str(path.relative_to(tree))
            if path.is_dir():
                return {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(path.stat().st_mode),
                }
            return {
                "path": relative,
                "type": "file",
                "mode": stat.S_IMODE(path.stat().st_mode),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        manifest = tree / self.feature.provision.TREE_MANIFEST
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "artifact_sha256": "artifact-pin",
                    "excluded_paths": [],
                    "removed_json_fields": [],
                    "entries": [
                        entry(tree / "bin"),
                        entry(executable),
                        entry(tree / "lib"),
                        entry(resource),
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        marker = self.base / "code-server.installed"
        marker.write_text(
            hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n",
            encoding="utf-8",
        )
        item: dict[str, object] = {
            "name": "code-server",
            "version": "1.2.3",
            "provision": {
                "kind": "tree",
                "install_path": str(tree),
                "executable": "bin/code-server",
            },
        }
        return item, marker, resource

    def healthy_verification_state(self) -> tuple[dict[str, object], Path]:
        version_root = self.base / "managed" / "code-server" / "1.2.3"
        version_root.mkdir(parents=True)
        (version_root.parent / "current").symlink_to(version_root.name)
        self.feature.STATE.mkdir(parents=True, mode=0o700)
        self.feature.STATE.chmod(0o700)
        grace, registry = self.feature.settings()
        self.feature.MARKER.write_text(
            json.dumps(
                {
                    "version": 1,
                    "idle_grace_seconds": grace,
                    "extension_registry": registry,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.feature.MARKER.chmod(0o600)
        return {"version": "1.2.3"}, version_root

    def test_tree_drift_blocks_new_engine_even_when_executable_reports_pin(self) -> None:
        item, marker, resource = self.provisioned_tree()
        command = self.feature.provision.managed_command(item)
        assert command is not None
        self.assertEqual(
            subprocess.run(
                [str(command), "--version"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            "1.2.3",
        )
        resource.write_text("evil\n", encoding="utf-8")

        with (
            mock.patch.object(self.feature.provision, "dependency", return_value=item),
            mock.patch.object(
                self.feature.provision,
                "install_path",
                return_value=Path(item["provision"]["install_path"]),
            ),
            mock.patch.object(
                self.feature.provision, "marker", return_value=marker
            ),
            mock.patch.object(
                self.feature.provision,
                "artifact",
                return_value={"sha256": "artifact-pin"},
            ),
            mock.patch.object(
                self.feature.provision, "tree_exclusions", return_value=()
            ),
            mock.patch.object(
                self.feature.provision, "tree_json_field_removals", return_value=()
            ),
            mock.patch.object(self.feature.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                self.feature.RemoteError,
                r"envy upgrade --only remote-development --force",
            ):
                self.feature.start_engine()
        popen.assert_not_called()
        self.assertFalse(self.feature.ENGINE_STATE.exists())

    def test_failed_restart_verification_preserves_live_engine(self) -> None:
        current = {
            "pid": 123,
            "identity": "live",
            "socket": "/existing/socket",
            "fingerprint": "old",
        }
        self.feature.ENGINE_STATE.parent.mkdir(parents=True)
        self.feature.ENGINE_STATE.write_text(
            json.dumps(current) + "\n", encoding="utf-8"
        )
        with (
            mock.patch.object(
                self.feature, "extension_fingerprint", return_value="new"
            ),
            mock.patch.object(self.feature, "engine", return_value=current),
            mock.patch.object(
                self.feature.provision,
                "dependency",
                return_value={"version": "1.2.3"},
            ),
            mock.patch.object(
                self.feature.provision,
                "verify",
                side_effect=self.feature.provision.ProvisionError("tree drifted"),
            ),
            mock.patch.object(self.feature, "stop_engine") as stop_engine,
            mock.patch.object(self.feature.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                self.feature.RemoteError,
                r"envy upgrade --only remote-development --force",
            ):
                self.feature.start_engine(restart_if_changed=True)
        stop_engine.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(
            json.loads(self.feature.ENGINE_STATE.read_text(encoding="utf-8")), current
        )

    def test_extension_inventory_error_is_not_misdiagnosed_as_provision_drift(self) -> None:
        message = "extensions path has an unexpected type"
        with mock.patch.object(
            self.feature,
            "extension_fingerprint",
            side_effect=self.feature.RemoteError(message),
        ):
            with self.assertRaisesRegex(self.feature.RemoteError, message) as raised:
                self.feature.start_engine()
        self.assertNotIn("envy upgrade", str(raised.exception))

    def test_restart_verifies_before_stop_and_process_launch(self) -> None:
        events: list[str] = []
        item = {"version": "1.2.3"}
        executable = self.base / "code-server"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
        runtime = self.base / "runtime"
        current = {
            "pid": 123,
            "identity": "live",
            "socket": "/existing/socket",
            "fingerprint": "old",
        }
        server: socket.socket | None = None
        process = mock.Mock(pid=4242)
        process.poll.return_value = None

        def verify(_name: str) -> int:
            events.append("verify")
            return 0

        def stop() -> None:
            events.append("stop")

        def popen(*_args, **_kwargs):
            nonlocal server
            events.append("popen")
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(self.feature.socket_path()))
            return process

        try:
            with (
                mock.patch.object(
                    self.feature, "extension_fingerprint", return_value="new"
                ),
                mock.patch.object(self.feature, "engine", return_value=current),
                mock.patch.object(
                    self.feature.provision, "dependency", return_value=item
                ),
                mock.patch.object(self.feature.provision, "verify", side_effect=verify),
                mock.patch.object(
                    self.feature.provision,
                    "managed_command",
                    return_value=executable,
                ),
                mock.patch.object(self.feature, "stop_engine", side_effect=stop),
                mock.patch.object(self.feature.subprocess, "Popen", side_effect=popen),
                mock.patch.object(
                    self.feature, "proc_identity", return_value="identity-4242"
                ),
                mock.patch.object(self.feature, "runtime_dir", return_value=runtime),
            ):
                started = self.feature.start_engine(restart_if_changed=True)
            self.assertEqual(events[:3], ["verify", "stop", "popen"])
            self.assertEqual(started["pid"], 4242)
            self.assertEqual(started["fingerprint"], "new")
        finally:
            if server is not None:
                server.close()
            self.feature._STARTED_PROCESSES.clear()

    def test_unchanged_live_engine_reuse_skips_full_verification(self) -> None:
        current = {
            "pid": 123,
            "identity": "live",
            "socket": "/existing/socket",
            "fingerprint": "same",
        }
        with (
            mock.patch.object(
                self.feature, "extension_fingerprint", return_value="same"
            ),
            mock.patch.object(self.feature, "engine", return_value=current),
            mock.patch.object(self.feature.provision, "verify") as verify,
            mock.patch.object(self.feature, "stop_engine") as stop_engine,
            mock.patch.object(self.feature.subprocess, "Popen") as popen,
        ):
            self.assertEqual(
                self.feature.start_engine(restart_if_changed=True), current
            )
        verify.assert_not_called()
        stop_engine.assert_not_called()
        popen.assert_not_called()

    def test_rejected_lease_preserves_pending_and_stale_state(self) -> None:
        project = self.base / "project"
        project.mkdir()
        self.feature.LEASES.mkdir(parents=True)
        stale = self.feature.LEASES / "stale.json"
        stale.write_bytes(b'{"pid":999,"identity":"gone"}\n')
        self.feature.STOP.parent.mkdir(parents=True, exist_ok=True)
        self.feature.STOP.write_bytes(b'{"token":"pending","deadline":99}\n')
        self.feature.ENGINE_STATE.write_bytes(b'{"pid":123,"identity":"live"}\n')
        log = self.feature.LOG
        log.write_bytes(b"existing log\n")
        inventory = (
            self.feature.DATA
            / "user-data"
            / "code-server"
            / ".envy-extension-inventory.json"
        )
        inventory.parent.mkdir(parents=True)
        inventory.write_bytes(b'{"fingerprint":"old","version":1}\n')
        before = {
            path: path.read_bytes()
            for path in (
                stale,
                self.feature.STOP,
                self.feature.ENGINE_STATE,
                log,
                inventory,
            )
        }
        stdin = mock.Mock(
            buffer=io.BytesIO(
                json.dumps({"path": str(project)}).encode("utf-8") + b"\n"
            )
        )
        with (
            mock.patch.object(self.feature.sys, "stdin", stdin),
            mock.patch.object(self.feature, "active_leases", return_value=[]),
            mock.patch.object(
                self.feature,
                "start_engine",
                side_effect=self.feature.RemoteError(
                    "code-server provision verification failed; run "
                    "envy upgrade --only remote-development --force"
                ),
            ),
            mock.patch.object(self.feature, "clean_stale_leases") as clean,
        ):
            with self.assertRaises(self.feature.RemoteError):
                self.feature.lease("a1")
        clean.assert_not_called()
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.feature.LEASES / "a1.json").exists())

    def test_symlinked_lease_directory_fails_before_engine_actions(self) -> None:
        project = self.base / "project"
        project.mkdir()
        lease_target = self.base / "outside-leases"
        lease_target.mkdir()
        self.feature.STATE.mkdir(parents=True, mode=0o700)
        self.feature.LEASES.symlink_to(lease_target, target_is_directory=True)
        self.feature.LOCK.write_bytes(b"")
        sentinel = self.feature.STATE / "engine.json"
        sentinel.write_bytes(b'{"pid":123,"identity":"live"}\n')
        stdin = mock.Mock(
            buffer=io.BytesIO(
                json.dumps({"path": str(project)}).encode("utf-8") + b"\n"
            )
        )
        with (
            mock.patch.object(self.feature.sys, "stdin", stdin),
            mock.patch.object(self.feature, "start_engine") as start_engine,
            mock.patch.object(self.feature.provision, "verify") as verify,
            mock.patch.object(self.feature, "stop_engine") as stop_engine,
            mock.patch.object(self.feature.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                self.feature.RemoteError, "lease path has an unexpected type"
            ):
                self.feature.lease("a1")
        start_engine.assert_not_called()
        verify.assert_not_called()
        stop_engine.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(
            sentinel.read_bytes(), b'{"pid":123,"identity":"live"}\n'
        )
        self.assertEqual(list(lease_target.iterdir()), [])

    def test_verbose_doctor_adds_paths_without_changing_default_output(self) -> None:
        item = {"version": "1.2.3"}
        provision_root = self.base / "code-server" / "1.2.3"
        usage = mock.Mock(free=1024 * 1024)
        with (
            mock.patch.object(
                self.feature, "verification_diagnostics", return_value=[]
            ),
            mock.patch.object(self.feature.shutil, "disk_usage", return_value=usage),
            mock.patch.object(
                self.feature.provision, "dependency", return_value=item
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=provision_root
            ),
        ):
            normal = io.StringIO()
            with contextlib.redirect_stdout(normal):
                self.assertEqual(self.feature.doctor(), 0)
            detailed = io.StringIO()
            with contextlib.redirect_stdout(detailed):
                self.assertEqual(self.feature.doctor(verbose=True), 0)
        self.assertNotIn("provision root", normal.getvalue())
        self.assertIn(f"INFO provision root: {provision_root}", detailed.getvalue())
        self.assertIn(f"INFO state directory: {self.feature.STATE}", detailed.getvalue())
        self.assertIn(f"INFO engine socket: {self.feature.socket_path()}", detailed.getvalue())

    def test_doctor_reports_force_repair_without_mutating_state(self) -> None:
        self.feature.STATE.mkdir(parents=True)
        sentinel = self.feature.STATE / "sentinel"
        sentinel.write_bytes(b"preserve\n")
        usage = mock.Mock(free=1024 * 1024)
        with (
            mock.patch.object(
                self.feature,
                "verification_diagnostics",
                return_value=[
                    ("code-server does not match its pinned version", True)
                ],
            ),
            mock.patch.object(self.feature.shutil, "disk_usage", return_value=usage),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.feature.doctor(), 1)
        self.assertIn(
            "envy upgrade --only remote-development --force", output.getvalue()
        )
        self.assertEqual(sentinel.read_bytes(), b"preserve\n")

    def test_doctor_does_not_offer_upgrade_for_nonrepairable_failures(self) -> None:
        usage = mock.Mock(free=1024 * 1024)
        malformed = json.JSONDecodeError("bad manifest", "{", 1)
        with (
            mock.patch.object(
                self.feature,
                "verification_diagnostics",
                return_value=[
                    ("remote idle_grace_seconds must be an integer", False),
                    ("runtime directory is not private: /runtime", False),
                    ("engine socket is not private: /runtime/socket", False),
                ],
            ),
            mock.patch.object(self.feature.shutil, "disk_usage", return_value=usage),
            mock.patch.object(
                self.feature.provision, "dependency", side_effect=malformed
            ),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.feature.doctor(verbose=True), 1)
        self.assertNotIn("envy upgrade", output.getvalue())
        self.assertIn("remote idle_grace_seconds", output.getvalue())
        self.assertIn("runtime directory is not private", output.getvalue())
        self.assertIn("engine socket is not private", output.getvalue())
        self.assertIn("provision paths unavailable", output.getvalue())

    def test_current_regular_file_is_not_classified_as_upgrade_repairable(self) -> None:
        item, version_root = self.healthy_verification_state()
        current = version_root.parent / "current"
        current.unlink()
        current.write_text("unmanaged\n", encoding="utf-8")
        with (
            mock.patch.object(self.feature.provision, "verify", return_value=0),
            mock.patch.object(
                self.feature.provision, "dependency", return_value=item
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=version_root
            ),
        ):
            diagnostics = self.feature.verification_diagnostics()
        self.assertIn(
            (f"selected engine link has an unexpected type: {current}", False),
            diagnostics,
        )
        self.assertFalse(any(repairable for _problem, repairable in diagnostics))

    def test_marker_directory_and_runtime_drift_are_not_upgrade_repairable(self) -> None:
        item, version_root = self.healthy_verification_state()
        self.feature.MARKER.unlink()
        self.feature.MARKER.mkdir()
        runtime = self.feature.runtime_dir()
        runtime.mkdir(parents=True, mode=0o755)
        runtime.chmod(0o755)
        with (
            mock.patch.object(self.feature.provision, "verify", return_value=0),
            mock.patch.object(
                self.feature.provision, "dependency", return_value=item
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=version_root
            ),
        ):
            diagnostics = self.feature.verification_diagnostics()
        self.assertIn(
            (f"feature marker has an unexpected type: {self.feature.MARKER}", False),
            diagnostics,
        )
        self.assertIn(
            (f"runtime directory is not private: {runtime}", False), diagnostics
        )
        self.assertFalse(any(repairable for _problem, repairable in diagnostics))

    def test_dangling_marker_symlink_is_repairable_without_stat_failure(self) -> None:
        item, version_root = self.healthy_verification_state()
        self.feature.MARKER.unlink()
        self.feature.MARKER.symlink_to(self.base / "missing-marker-target")
        with (
            mock.patch.object(self.feature.provision, "verify", return_value=0),
            mock.patch.object(
                self.feature.provision, "dependency", return_value=item
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=version_root
            ),
        ):
            diagnostics = self.feature.verification_diagnostics()
        self.assertEqual(
            diagnostics,
            [(f"feature marker is not private: {self.feature.MARKER}", True)],
        )

    def test_state_symlink_does_not_trigger_nested_upgrade_repairs(self) -> None:
        item, version_root = self.healthy_verification_state()
        self.feature.shutil.rmtree(self.feature.STATE)
        outside = self.base / "outside-state"
        outside.mkdir()
        self.feature.STATE.symlink_to(outside, target_is_directory=True)
        with (
            mock.patch.object(self.feature.provision, "verify", return_value=0),
            mock.patch.object(
                self.feature.provision, "dependency", return_value=item
            ),
            mock.patch.object(
                self.feature.provision, "install_path", return_value=version_root
            ),
        ):
            diagnostics = self.feature.verification_diagnostics()
        self.assertEqual(
            diagnostics,
            [
                (
                    f"feature state directory has an unexpected type: "
                    f"{self.feature.STATE}",
                    False,
                )
            ],
        )

    def test_doctor_cli_accepts_only_optional_verbose_flag(self) -> None:
        with mock.patch.object(self.feature, "doctor", return_value=0) as doctor:
            with mock.patch.object(
                self.feature.sys,
                "argv",
                ["remote-development.py", "doctor", "--verbose"],
            ):
                self.assertEqual(self.feature.main(), 0)
            doctor.assert_called_once_with(verbose=True)

        with mock.patch.object(self.feature, "doctor", return_value=0) as doctor:
            with mock.patch.object(
                self.feature.sys,
                "argv",
                ["remote-development.py", "doctor", "--unknown"],
            ):
                self.assertEqual(self.feature.main(), 2)
            doctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
