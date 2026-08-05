#!/usr/bin/env python3
"""Tmux persistence keeps one canonical, safely rendered helper directory."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "chezmoi"
HELPER_SOURCE = SOURCE / "dot_local" / "bin"


def render(source: Path, destination: Path, bin_dir: Path) -> str:
    environment = {
        **os.environ,
        "HOME": str(destination),
        "ENVY_BIN_DIR": str(bin_dir),
    }
    return subprocess.run(
        [
            "chezmoi",
            "execute-template",
            "--source",
            str(SOURCE),
            "--destination",
            str(destination),
        ],
        input=source.read_text(encoding="utf-8"),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout


class CanonicalTmuxHelperPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.bin_dir = (
            self.base
            / "bin space"
            / "$cash"
            / "`tick`"
            / 'double"'
            / "single'"
            / "$(touch injected)"
            / ";&hash#"
        )
        self.home.mkdir()
        self.bin_dir.mkdir(parents=True)

    @unittest.skipUnless(shutil.which("chezmoi"), "chezmoi is not installed")
    def test_templates_preserve_metacharacters_without_execution(self) -> None:
        marker = self.base / "bootstrap-ran"
        bootstrap = self.bin_dir / "nv-tmux-bootstrap"
        bootstrap.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$0\" >\"$TMUX_HELPER_MARKER\"\n",
            encoding="utf-8",
        )
        bootstrap.chmod(0o755)

        tmux_config = render(SOURCE / "dot_tmux.conf.tmpl", self.home, self.bin_dir)
        pi_extension = render(
            SOURCE / "dot_pi/agent/extensions/nv-tmux-session.ts.tmpl",
            self.home,
            self.bin_dir,
        )
        register_line = next(
            line for line in pi_extension.splitlines() if line.startswith("const registerHelper = ")
        )
        encoded = register_line.removeprefix("const registerHelper = ").removesuffix(";")
        self.assertEqual(json.loads(encoded), str(self.bin_dir / "nv-agent-register"))
        self.assertIn("nv-tmux-bootstrap", tmux_config)
        self.assertIn(r"\$(touch injected)", tmux_config)

        if not shutil.which("tmux"):
            return
        socket = f"nv-template-{uuid.uuid4().hex}"
        config = self.base / "tmux.conf"
        config.write_text(tmux_config, encoding="utf-8")
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "TMUX_HELPER_MARKER": str(marker),
        }
        try:
            subprocess.run(
                [
                    "tmux",
                    "-L",
                    socket,
                    "-f",
                    str(config),
                    "new-session",
                    "-d",
                    "-c",
                    str(self.base),
                    "-s",
                    "test",
                ],
                check=True,
                env=environment,
            )
            for _ in range(50):
                if marker.exists():
                    break
                time.sleep(0.02)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), str(bootstrap))
            self.assertFalse((self.base / "injected").exists())
        finally:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def deploy(self, name: str) -> Path:
        source = HELPER_SOURCE / f"executable_{name}"
        target = self.bin_dir / name
        shutil.copy2(source, target)
        target.chmod(0o755)
        return target

    def test_register_resolves_save_as_an_invocation_sibling(self) -> None:
        register = self.deploy("nv-agent-register")
        save = self.bin_dir / "nv-tmux-save"
        save.touch()
        module = runpy.run_path(str(register))
        with mock.patch.object(module["subprocess"], "Popen") as popen:
            module["request_snapshot"]()
        self.assertEqual(popen.call_args.args[0], [str(save), "quiet"])

    def test_legacy_and_old_absolute_resume_paths_normalize_to_current_sibling(self) -> None:
        snapshot = self.deploy("nv-tmux-snapshot")
        current = self.bin_dir / "nv-agent-resume"
        current.touch(mode=0o755)
        module = runpy.run_path(str(snapshot))
        state = self.base / "tmux_resurrect_20260101T000000.txt"
        state.write_text(
            "pane\twork\t0\t1\t:*\t0\tagent\t:/tmp\t1\tcodex\t"
            ":nv-agent-resume v1 codex legacy-1\n"
            "pane\twork\t0\t1\t:*\t1\tagent\t:/tmp\t1\tclaude\t"
            ":'/old bin/nv-agent-resume' v1 claude old-2\n",
            encoding="utf-8",
        )
        self.assertIn("[2 agents]", module["snapshot_label"](state, ""))
        module["normalize_resume_paths"](state)
        fields = [line.split("\t")[10] for line in state.read_text().splitlines()]
        for field in fields:
            arguments = module["resume_arguments"](field)
            self.assertIsNotNone(arguments)
            self.assertEqual(arguments[0], str(current))

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_bootstrap_records_shell_escaped_absolute_resume_process(self) -> None:
        for name in (
            "nv-agent-resume",
            "nv-tmux-bootstrap",
            "nv-tmux-restore",
            "nv-tmux-save",
            "nv-tmux-snapshot",
        ):
            self.deploy(name)
        config = self.home / ".config/envy"
        data = self.home / ".local/share/envy/tmux"
        config.mkdir(parents=True)
        (config / "tmux-persistence.enabled").write_text("version=3\n", encoding="utf-8")
        for plugin, executable in (
            ("tmux-resurrect", "resurrect.tmux"),
            ("tmux-continuum", "continuum.tmux"),
        ):
            path = data / plugin / executable
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

        socket = f"nv-bootstrap-{uuid.uuid4().hex}"
        environment = {**os.environ, "HOME": str(self.home)}
        try:
            subprocess.run(
                ["tmux", "-L", socket, "-f", "/dev/null", "new-session", "-d", "-s", "test"],
                check=True,
                env=environment,
            )
            socket_path = subprocess.run(
                ["tmux", "-L", socket, "display-message", "-p", "#{socket_path}"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.strip()
            server_pid = subprocess.run(
                ["tmux", "-L", socket, "display-message", "-p", "#{pid}"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.strip()
            subprocess.run(
                [str(self.bin_dir / "nv-tmux-bootstrap")],
                check=True,
                env={**environment, "TMUX": f"{socket_path},{server_pid},0"},
            )
            specification = subprocess.run(
                ["tmux", "-L", socket, "show-option", "-gqv", "@resurrect-processes"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.strip()
            parsed = subprocess.run(
                ["bash", "-c", 'eval "set -- $SPEC"; printf "%s\\n" "$#" "$1"'],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "SPEC": specification},
            ).stdout.splitlines()
            self.assertEqual(
                parsed,
                ["1", str(self.bin_dir / "nv-agent-resume")],
            )
        finally:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    unittest.main()
