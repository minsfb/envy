#!/usr/bin/env python3
"""Deployed helpers must resolve envy's roots the way lib/paths.py does.

The helpers in chezmoi/dot_local/bin are launched by tmux and by terminal
hooks, not by the launcher, so they inherit none of the roots lib/paths.sh
exports. Each one has to resolve them itself, and it has to honour the same
ENVY_* overrides. When only the writing side of a marker honoured an override
the two halves silently disagreed, so these tests pin the invariant rather
than the symptom.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = sorted((ROOT / "chezmoi" / "dot_local" / "bin").glob("executable_*"))

# XDG base -> the override that must accompany it when deriving an envy root.
ROOTS = {
    "XDG_CONFIG_HOME": "ENVY_CONFIG_DIR",
    "XDG_STATE_HOME": "ENVY_STATE_DIR",
    "XDG_DATA_HOME": "ENVY_DATA_DIR",
}

# Fully hardcoded roots bypass the XDG base entirely, so they need their own rule.
HARDCODED = re.compile(r"\.config/envy|\.local/state/envy|\.local/share/envy")


class HelpersAreDiscovered(unittest.TestCase):
    def test_helpers_exist(self) -> None:
        # A glob that silently matches nothing would make every test below pass.
        self.assertTrue(HELPERS, "no deployed helpers found to check")


class OverridesAreHonoured(unittest.TestCase):
    def test_envy_roots_derived_from_xdg_also_honour_the_override(self) -> None:
        problems = []
        for helper in HELPERS:
            for number, line in enumerate(
                helper.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "envy" not in line.lower():
                    # Not an envy-owned root; XDG bases for other tools are fine.
                    continue
                for base, override in ROOTS.items():
                    if base in line and override not in line:
                        problems.append(f"{helper.name}:{number}: {base} without {override}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_helper_hardcodes_an_envy_root(self) -> None:
        problems = []
        for helper in HELPERS:
            for number, line in enumerate(
                helper.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if HARDCODED.search(line):
                    problems.append(f"{helper.name}:{number}: hardcoded envy root")
        self.assertEqual(problems, [], "\n".join(problems))


class RegisterResolvesOverrides(unittest.TestCase):
    """nv-agent-register is Python, so its resolution is directly observable."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.helper = ROOT / "chezmoi" / "dot_local" / "bin" / "executable_nv-agent-register"

    def resolve(self, environment: dict[str, str]) -> tuple[str, str]:
        script = (
            "import runpy,sys;"
            f"m=runpy.run_path({str(self.helper)!r});"
            "print(m['config_dir']());print(m['state_dir']())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **environment},
        )
        config, state = result.stdout.strip().splitlines()[:2]
        return config, state

    def test_explicit_override_wins(self) -> None:
        config, state = self.resolve(
            {
                "ENVY_CONFIG_DIR": str(self.base / "cfg"),
                "ENVY_STATE_DIR": str(self.base / "st"),
            }
        )
        self.assertEqual(config, str(self.base / "cfg"))
        self.assertEqual(state, str(self.base / "st"))

    def test_xdg_base_is_used_when_no_override(self) -> None:
        environment = {
            "XDG_CONFIG_HOME": str(self.base / "x-config"),
            "XDG_STATE_HOME": str(self.base / "x-state"),
        }
        for key in ("ENVY_CONFIG_DIR", "ENVY_STATE_DIR"):
            environment.pop(key, None)
        os.environ.pop("ENVY_CONFIG_DIR", None)
        os.environ.pop("ENVY_STATE_DIR", None)
        config, state = self.resolve(environment)
        self.assertEqual(config, str(self.base / "x-config" / "envy"))
        self.assertEqual(state, str(self.base / "x-state" / "envy"))

    def test_marker_written_by_the_feature_is_found_by_the_helper(self) -> None:
        # The regression: the feature wrote through paths.config_dir() while the
        # helper read a literal, so a non-default root split the pair.
        config_dir = self.base / "custom-config"
        config_dir.mkdir(parents=True)
        (config_dir / "tmux-persistence.enabled").write_text("version=2\n")

        sys.path.insert(0, str(ROOT / "lib"))
        try:
            environment = {**os.environ, "ENVY_CONFIG_DIR": str(config_dir)}
            written = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.path.insert(0,%r);import paths;print(paths.config_dir())"
                    % str(ROOT / "lib"),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.strip()
        finally:
            sys.path.remove(str(ROOT / "lib"))

        read, _ = self.resolve({"ENVY_CONFIG_DIR": str(config_dir)})
        self.assertEqual(written, read)


if __name__ == "__main__":
    unittest.main()
