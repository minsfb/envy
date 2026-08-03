#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import migrate  # noqa: E402
import paths  # noqa: E402


class MigrateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.saved = {
            key: os.environ.get(key)
            for key in (
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_STATE_HOME",
                "XDG_DATA_HOME",
                "ENVY_CONFIG_DIR",
                "ENVY_STATE_DIR",
                "ENVY_DATA_DIR",
            )
        }
        for key in self.saved:
            os.environ.pop(key, None)
        os.environ["HOME"] = str(self.home)

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def legacy_config(self) -> Path:
        return self.home / ".config" / "mins-env"

    def seed_legacy(self) -> Path:
        legacy = self.legacy_config()
        legacy.mkdir(parents=True)
        (legacy / "state.json").write_text(
            json.dumps({"installed": True, "note": "keep"}), encoding="utf-8"
        )
        (legacy / "init-sh.env").write_text("export A=1\n", encoding="utf-8")
        return legacy

    def test_no_legacy_layout_is_a_no_op(self) -> None:
        self.assertEqual(migrate.migrate(), 0)
        self.assertFalse(paths.config_dir().exists())

    def test_legacy_config_is_migrated_then_removed(self) -> None:
        legacy = self.seed_legacy()
        self.assertEqual(migrate.migrate(), 0)
        self.assertFalse(legacy.exists())
        current = paths.config_dir()
        self.assertEqual(
            json.loads((current / "state.json").read_text())["note"], "keep"
        )
        self.assertTrue(migrate.marker().is_file())

    def test_dry_run_changes_nothing(self) -> None:
        legacy = self.seed_legacy()
        self.assertEqual(migrate.migrate(dry_run=True), 0)
        self.assertTrue(legacy.exists())
        self.assertFalse(paths.config_dir().exists())
        self.assertFalse(migrate.marker().exists())

    def test_references_into_moved_directories_are_repointed(self) -> None:
        legacy_state = self.home / ".local" / "state" / "mins-env"
        legacy_state.mkdir(parents=True)
        (legacy_state / "agent-assets.json").write_text(
            json.dumps({"links": {"/x": str(legacy_state / "owned")}}), encoding="utf-8"
        )
        self.assertEqual(migrate.migrate(), 0)
        moved = json.loads((paths.state_dir() / "agent-assets.json").read_text())
        self.assertEqual(moved["links"]["/x"], str(paths.state_dir() / "owned"))
        self.assertNotIn("mins-env", moved["links"]["/x"])

    def test_populated_destination_is_not_overwritten(self) -> None:
        legacy = self.seed_legacy()
        current = paths.config_dir()
        current.mkdir(parents=True)
        (current / "state.json").write_text('{"note": "current"}', encoding="utf-8")
        self.assertEqual(migrate.migrate(), 0)
        self.assertEqual(
            json.loads((current / "state.json").read_text())["note"], "current"
        )
        # The superseded legacy copy is still reclaimed.
        self.assertFalse(legacy.exists())

    def test_failed_verification_keeps_the_legacy_layout(self) -> None:
        self.seed_legacy()
        original = migrate.digest_tree

        def corrupt(root: Path) -> dict[str, str]:
            result = original(root)
            # Simulate the destination not matching the source.
            if root == paths.config_dir():
                result["state.json"] = "mismatch"
            return result

        migrate.digest_tree = corrupt
        try:
            with self.assertRaises(migrate.MigrationError):
                migrate.migrate()
        finally:
            migrate.digest_tree = original
        self.assertTrue(self.legacy_config().exists())
        self.assertFalse(migrate.marker().exists())

    def test_migration_does_not_repeat(self) -> None:
        self.seed_legacy()
        self.assertEqual(migrate.migrate(), 0)
        self.legacy_config().mkdir(parents=True, exist_ok=True)
        (self.legacy_config() / "reappeared.txt").write_text("x", encoding="utf-8")
        self.assertEqual(migrate.migrate(), 0)
        self.assertTrue(self.legacy_config().exists())

    def test_superseded_commands_are_removed(self) -> None:
        self.seed_legacy()
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        orphan = bin_dir / "mins-agent-notify"
        orphan.write_text("#!/bin/sh\n", encoding="utf-8")
        keeper = bin_dir / "envy-agent-notify"
        keeper.write_text("#!/bin/sh\n", encoding="utf-8")
        self.assertEqual(migrate.migrate(), 0)
        self.assertFalse(orphan.exists())
        self.assertTrue(keeper.exists())

    def test_launcher_dry_run_does_not_migrate(self) -> None:
        # A dry run reaches migrate() through bin/envy; it must stay read-only.
        legacy = self.seed_legacy()
        self.assertEqual(migrate.migrate(dry_run=True), 0)
        self.assertTrue((legacy / "state.json").is_file())
        self.assertFalse(paths.config_dir().exists())

    def test_explicit_root_override_is_honored(self) -> None:
        self.seed_legacy()
        target = self.home / "elsewhere" / "envy-config"
        os.environ["ENVY_CONFIG_DIR"] = str(target)
        self.assertEqual(migrate.migrate(), 0)
        self.assertTrue((target / "state.json").is_file())


if __name__ == "__main__":
    unittest.main()
