#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    specification = importlib.util.spec_from_loader(name, loader)
    assert specification is not None
    module = importlib.util.module_from_spec(specification)
    loader.exec_module(module)
    return module


helper = load_module("envy_remote_terminal", ROOT / "bin" / "nv-remote-terminal")


class RemoteTerminalHelperTest(unittest.TestCase):
    def test_session_name_is_stable_bounded_and_path_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "one" / "A Project"
            second = root / "two" / "A Project"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            first_name = helper.session_name(first.resolve())
            self.assertEqual(first_name, helper.session_name(first.resolve()))
            self.assertNotEqual(first_name, helper.session_name(second.resolve()))
            self.assertRegex(first_name, r"^envy-rd-a-project-[0-9a-f]{12}$")
            self.assertLessEqual(len(first_name), 45)
            self.assertNotIn(str(root), first_name)

    def test_command_attaches_or_creates_in_workspace(self) -> None:
        workspace = Path("/tmp/example workspace")
        command = helper.tmux_command(workspace, "/test/tmux")
        self.assertEqual(
            command,
            [
                "/test/tmux",
                "new-session",
                "-A",
                "-s",
                helper.session_name(workspace),
                "-c",
                str(workspace),
            ],
        )

    def test_executable_passes_structured_arguments_to_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "project with spaces"
            workspace.mkdir()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            arguments = root / "arguments"
            tmux = fake_bin / "tmux"
            tmux.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$NV_TEST_TMUX_ARGUMENTS"\n',
                encoding="utf-8",
            )
            tmux.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["NV_TEST_TMUX_ARGUMENTS"] = str(arguments)
            subprocess.run(
                [str(ROOT / "bin" / "nv-remote-terminal"), str(workspace)],
                check=True,
                env=environment,
            )
            values = arguments.read_text(encoding="utf-8").splitlines()
            self.assertEqual(values[:3], ["new-session", "-A", "-s"])
            self.assertEqual(values[-2:], ["-c", str(workspace.resolve())])


class RemoteTmuxFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.previous = {
            key: os.environ.get(key)
            for key in ("XDG_STATE_HOME", "XDG_DATA_HOME", "PATH")
        }
        os.environ["XDG_STATE_HOME"] = str(self.base / "state")
        os.environ["XDG_DATA_HOME"] = str(self.base / "data")
        fake_bin = self.base / "bin"
        fake_bin.mkdir()
        tmux = fake_bin / "tmux"
        tmux.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        tmux.chmod(0o755)
        os.environ["PATH"] = f"{fake_bin}{os.pathsep}{self.previous['PATH'] or ''}"
        self.feature = load_module(
            f"envy_remote_tmux_feature_{id(self)}",
            ROOT / "features" / "remote-development-tmux.py",
        )
        self.feature.REMOTE_MARKER.parent.mkdir(parents=True)
        self.feature.REMOTE_MARKER.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def test_lifecycle_installs_and_removes_owned_extension(self) -> None:
        self.assertEqual(self.feature.converge(), 0)
        installed = json.loads(self.feature.PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(installed, self.feature.package())
        defaults = installed["contributes"]["configurationDefaults"]
        self.assertEqual(
            defaults["terminal.integrated.defaultProfile.linux"],
            self.feature.PROFILE_NAME,
        )
        self.assertEqual(self.feature.verify(), 0)
        self.assertEqual(self.feature.remove(), 0)
        self.assertFalse(self.feature.EXTENSION.exists())
        self.assertFalse(self.feature.MARKER.exists())

    def test_user_settings_are_never_read_or_rewritten(self) -> None:
        settings = (
            self.base
            / "data/envy/remote-development/user-data/code-server/User/settings.json"
        )
        settings.parent.mkdir(parents=True)
        original = '{\n  // User-owned JSONC\n  "editor.fontSize": 15,\n}\n'
        settings.write_text(original, encoding="utf-8")
        self.assertEqual(self.feature.converge(), 0)
        self.assertEqual(settings.read_text(encoding="utf-8"), original)
        self.assertEqual(self.feature.remove(), 0)
        self.assertEqual(settings.read_text(encoding="utf-8"), original)

    def test_unowned_extension_directory_fails_closed(self) -> None:
        self.feature.EXTENSION.mkdir(parents=True)
        self.feature.PACKAGE.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(self.feature.FeatureError, "unowned extension"):
            self.feature.converge()

    def test_modified_extension_survives_removal(self) -> None:
        self.assertEqual(self.feature.converge(), 0)
        modified = self.feature.package()
        modified["description"] = "User modified"
        self.feature.PACKAGE.write_text(
            json.dumps(modified, indent=2) + "\n", encoding="utf-8"
        )
        self.assertEqual(self.feature.remove(), 0)
        self.assertTrue(self.feature.EXTENSION.is_dir())
        self.assertEqual(
            json.loads(self.feature.PACKAGE.read_text(encoding="utf-8")), modified
        )


if __name__ == "__main__":
    unittest.main()
