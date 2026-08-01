#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import managed_json  # noqa: E402


class ManagedJsonTest(unittest.TestCase):
    def test_local_choices_and_symlink_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            managed = root / "managed.json"
            backing = root / "backing.json"
            target = root / "settings.json"
            managed.write_text(
                json.dumps({"choice": "managed", "added": True, "items": ["managed"]}),
                encoding="utf-8",
            )
            backing.write_text(
                json.dumps({"choice": "local", "items": ["local"]}),
                encoding="utf-8",
            )
            target.symlink_to(backing)
            expected = managed_json.desired(managed, target)
            self.assertEqual(
                expected,
                {"choice": "local", "added": True, "items": ["local", "managed"]},
            )
            saved = managed_json.backup(target, root / "backups")
            self.assertIsNotNone(saved)
            managed_json.atomic_write(target, expected)
            self.assertTrue(target.is_symlink())
            self.assertEqual(json.loads(backing.read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
