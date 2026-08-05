#!/usr/bin/env python3
"""Ownership and path contracts for the tmux-persistence feature handler."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FEATURE = ROOT / "features" / "tmux-persistence.py"
HELPERS = (
    "nv-agent-register",
    "nv-agent-resume",
    "nv-tmux-bootstrap",
    "nv-tmux-restore",
    "nv-tmux-save",
    "nv-tmux-snapshot",
)


class TmuxPersistenceFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.config = self.base / "config" / "envy"
        self.state = self.base / "state" / "envy"
        self.data = self.base / "data" / "envy"
        self.bin = self.base / "custom bin"
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "settings": {
                        "tmux_persistence": {"agent_adapters": ["claude", "codex"]}
                    }
                }
            ),
            encoding="utf-8",
        )
        for plugin in (
            self.data / "tmux" / "tmux-resurrect" / "resurrect.tmux",
            self.data / "tmux" / "tmux-continuum" / "continuum.tmux",
        ):
            plugin.parent.mkdir(parents=True, exist_ok=True)
            plugin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            plugin.chmod(0o755)
        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "ENVY_CONFIG_DIR": str(self.config),
            "ENVY_STATE_DIR": str(self.state),
            "ENVY_DATA_DIR": str(self.data),
            "ENVY_BIN_DIR": str(self.bin),
            "ENVY_MANIFEST": str(self.manifest),
            # Prevent the feature from finding and sourcing a real tmux server.
            "PATH": "/usr/bin:/bin",
        }

    @property
    def ownership(self) -> Path:
        return self.state / "tmux-persistence" / "ownership.json"

    def run_feature(
        self,
        action: str,
        *,
        bin_dir: Path | str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if bin_dir is not None:
            environment["ENVY_BIN_DIR"] = str(bin_dir)
        return subprocess.run(
            [sys.executable, str(FEATURE), action],
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def source(self, helper: str) -> Path:
        return ROOT / "chezmoi" / "dot_local" / "bin" / f"executable_{helper}"

    def test_alternate_bin_hooks_marker_and_conservative_removal(self) -> None:
        self.bin.mkdir(parents=True)
        preexisting = self.bin / "nv-agent-resume"
        preexisting.write_bytes(self.source("nv-agent-resume").read_bytes())
        preexisting.chmod(0o755)

        self.run_feature("converge")
        self.run_feature("verify")

        marker = (self.config / "tmux-persistence.enabled").read_text(encoding="utf-8")
        self.assertEqual(
            marker,
            f"version=3\nbin_dir={self.bin}\nadapters=claude,codex\n",
        )
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["bin_dir"], str(self.bin))
        self.assertFalse(state["helpers"][str(preexisting)]["created"])
        for helper in set(HELPERS) - {"nv-agent-resume"}:
            target = self.bin / helper
            self.assertTrue(target.is_file())
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertTrue(state["helpers"][str(target)]["created"])

        expected_register = shlex.quote(str(self.bin / "nv-agent-register"))
        for agent, path in (
            ("claude", self.home / ".claude" / "settings.json"),
            ("codex", self.home / ".codex" / "hooks.json"),
        ):
            value = json.loads(path.read_text(encoding="utf-8"))
            commands = json.dumps(value)
            self.assertIn(f"{expected_register} {agent} start", commands)
            self.assertIn(f"{expected_register} {agent} end", commands)

        before = self.ownership.read_bytes()
        self.run_feature("converge")
        self.assertEqual(self.ownership.read_bytes(), before)

        self.run_feature("remove")
        self.assertTrue(preexisting.is_file())
        for helper in set(HELPERS) - {"nv-agent-resume"}:
            self.assertFalse((self.bin / helper).exists())
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["helpers"], {})
        self.assertIsNone(state["bin_dir"])

    def test_modified_owned_helper_fails_closed_then_is_preserved_and_deowned(self) -> None:
        self.run_feature("converge")
        modified = self.bin / "nv-agent-register"
        modified.write_text("locally modified\n", encoding="utf-8")

        result = self.run_feature("converge", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("helper was modified; refusing to overwrite", result.stderr)
        self.assertEqual(modified.read_text(encoding="utf-8"), "locally modified\n")

        self.run_feature("remove")
        self.assertEqual(modified.read_text(encoding="utf-8"), "locally modified\n")
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["helpers"], {})

    def test_path_migration_removes_unchanged_and_deowns_modified_old_helpers(self) -> None:
        self.run_feature("converge")
        modified_old = self.bin / "nv-agent-register"
        modified_old.write_text("keep this local copy\n", encoding="utf-8")
        new_bin = self.base / "replacement bin"

        self.run_feature("converge", bin_dir=new_bin)

        self.assertEqual(modified_old.read_text(encoding="utf-8"), "keep this local copy\n")
        for helper in set(HELPERS) - {"nv-agent-register"}:
            self.assertFalse((self.bin / helper).exists())
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["bin_dir"], str(new_bin))
        self.assertEqual(set(state["helpers"]), {str(new_bin / item) for item in HELPERS})
        self.assertNotIn(str(modified_old), state["helpers"])

    def test_conflicting_preexisting_helper_is_never_overwritten(self) -> None:
        self.bin.mkdir(parents=True)
        # This is the last helper in deployment order. If preflight stopped at
        # each copy instead of covering the whole set, five earlier files would
        # already have appeared by the time this conflict was discovered.
        conflict = self.bin / "nv-tmux-snapshot"
        conflict.write_text("pre-existing\n", encoding="utf-8")
        conflict.chmod(0o755)

        result = self.run_feature("converge", check=False)

        self.assertEqual(result.returncode, 1)
        self.assertIn("helper conflicts with the managed asset", result.stderr)
        self.assertEqual(conflict.read_text(encoding="utf-8"), "pre-existing\n")
        self.assertEqual(list(self.bin.iterdir()), [conflict])

    def test_broken_symlink_conflict_is_preserved_without_partial_deployment(self) -> None:
        self.bin.mkdir(parents=True)
        # Keep the conflict last so the assertion also proves all six targets
        # were preflighted before the first helper could be copied.
        conflict = self.bin / "nv-tmux-snapshot"
        conflict.symlink_to(self.base / "missing-helper-target")

        result = self.run_feature("converge", check=False)

        self.assertEqual(result.returncode, 1)
        self.assertIn("helper target is not a regular file", result.stderr)
        self.assertTrue(conflict.is_symlink())
        self.assertFalse(conflict.exists())
        self.assertEqual(list(self.bin.iterdir()), [conflict])

    def test_doctor_rejects_symlink_even_when_referent_is_current(self) -> None:
        self.run_feature("converge")
        replaced = self.bin / "nv-tmux-snapshot"
        replaced.unlink()
        replaced.symlink_to(self.source("nv-tmux-snapshot"))

        result = self.run_feature("doctor", check=False)

        self.assertEqual(result.returncode, 1)
        self.assertIn("asset invalid (expected a regular file)", result.stderr)
        self.assertIn(str(replaced), result.stderr)

    def test_broken_owned_symlink_is_preserved_and_deowned_on_remove(self) -> None:
        self.run_feature("converge")
        replaced = self.bin / "nv-agent-register"
        replaced.unlink()
        replaced.symlink_to(self.base / "missing-owned-target")

        self.run_feature("remove")

        self.assertTrue(replaced.is_symlink())
        self.assertFalse(replaced.exists())
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["helpers"], {})

    def test_broken_owned_symlink_is_preserved_and_deowned_on_path_migration(self) -> None:
        self.run_feature("converge")
        replaced = self.bin / "nv-agent-register"
        replaced.unlink()
        replaced.symlink_to(self.base / "missing-owned-target")
        new_bin = self.base / "replacement bin"

        self.run_feature("converge", bin_dir=new_bin)

        self.assertTrue(replaced.is_symlink())
        self.assertFalse(replaced.exists())
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertNotIn(str(replaced), state["helpers"])
        self.assertEqual(set(state["helpers"]), {str(new_bin / item) for item in HELPERS})

    def test_unchanged_feature_created_helper_is_upgraded(self) -> None:
        self.bin.mkdir(parents=True)
        target = self.bin / "nv-agent-register"
        old_bytes = b"#!/bin/sh\n# previous managed version\n"
        target.write_bytes(old_bytes)
        target.chmod(0o755)
        self.ownership.parent.mkdir(parents=True)
        self.ownership.write_text(
            json.dumps(
                {
                    "version": 2,
                    "files": {},
                    "helpers": {
                        str(target): {
                            "created": True,
                            "digest": hashlib.sha256(old_bytes).hexdigest(),
                        }
                    },
                    "bin_dir": str(self.bin),
                }
            ),
            encoding="utf-8",
        )

        self.run_feature("converge")

        self.assertEqual(target.read_bytes(), self.source("nv-agent-register").read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(
            state["helpers"][str(target)]["digest"],
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )

    def test_v1_hook_ownership_is_migrated(self) -> None:
        self.ownership.parent.mkdir(parents=True)
        old_hook = self.home / ".claude" / "settings.json"
        self.ownership.write_text(
            json.dumps(
                {
                    "version": 1,
                    "files": {str(old_hook): {"created": True, "backups": []}},
                }
            ),
            encoding="utf-8",
        )

        self.run_feature("converge")

        state = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 2)
        self.assertIn(str(old_hook), state["files"])
        self.assertEqual(len(state["helpers"]), len(HELPERS))

    def test_bin_dir_must_be_absolute_and_free_of_control_characters(self) -> None:
        relative = self.run_feature("verify", bin_dir="relative/bin", check=False)
        self.assertEqual(relative.returncode, 1)
        self.assertIn("must be an absolute path", relative.stderr)

        controlled = self.run_feature("verify", bin_dir="/tmp/bad\npath", check=False)
        self.assertEqual(controlled.returncode, 1)
        self.assertIn("must not contain control characters", controlled.stderr)

    def test_remove_without_prior_convergence_does_not_create_state(self) -> None:
        self.run_feature("remove")
        self.assertFalse(self.ownership.exists())


if __name__ == "__main__":
    unittest.main()
