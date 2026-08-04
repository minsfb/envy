#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import stat
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


launcher = load_module("envy_remote_launcher", ROOT / "lib" / "remote.py")


class RemoteLauncherTest(unittest.TestCase):
    def test_host_validation_accepts_ssh_aliases(self) -> None:
        self.assertEqual(launcher.validate_host("user@dev.example"), "user@dev.example")

    def test_host_validation_rejects_options_and_whitespace(self) -> None:
        for value in ("-oProxyCommand=bad", "host name", "host\ncommand", ""):
            with self.subTest(value=value), self.assertRaises(launcher.LauncherError):
                launcher.validate_host(value)

    def test_remote_helper_command_is_fixed(self) -> None:
        command = launcher.remote_command("dev", "status")
        self.assertEqual(command[-1], 'exec "$HOME/.local/bin/envy" remote _status')


class RemoteSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.previous = {
            key: os.environ.get(key)
            for key in ("XDG_STATE_HOME", "XDG_DATA_HOME", "ENVY_MANIFEST")
        }
        os.environ["XDG_STATE_HOME"] = str(self.base / "state")
        os.environ["XDG_DATA_HOME"] = str(self.base / "data")
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
        original_settings = self.feature.settings
        original_runtime = self.feature.runtime_dir
        original_fingerprint = self.feature.extension_fingerprint
        self.feature.provision.dependency = lambda _name: {"version": "1.2.3"}
        self.feature.provision.managed_command = lambda _item: executable
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
                self.feature.DATA
                / "extensions"
                / "example.extension"
                / "package.json"
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
