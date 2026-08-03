#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import dependencies  # noqa: E402


class DependencyPinsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )
        (self.repo / "value").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "value"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "one"], check=True
        )
        self.first = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (self.repo / "value").write_text("two\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qam", "two"], check=True
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def item(self) -> dict[str, str]:
        return {
            "name": "fixture",
            "installed_path": str(self.repo),
            "commit": self.first,
        }

    def test_clean_checkout_is_detached_at_exact_commit(self) -> None:
        dependencies.verify_one(self.item(), converge=True)
        actual = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(actual, self.first)
        dependencies.verify_one(self.item(), converge=False)

    def test_missing_checkout_is_drift_but_does_not_fail_convergence(self) -> None:
        # Verification must report drift so the owning feature reconverges and
        # re-clones; convergence itself only warns, so an unreachable external
        # never fails chezmoi or anything downstream of it.
        item = {**self.item(), "installed_path": str(self.repo / "absent")}
        with self.assertRaises(dependencies.DependencyError):
            dependencies.verify_one(item, converge=False)
        dependencies.verify_one(item, converge=True)

    def test_dirty_checkout_is_not_changed(self) -> None:
        (self.repo / "value").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(dependencies.DependencyError):
            dependencies.verify_one(self.item(), converge=True)


if __name__ == "__main__":
    unittest.main()
