#!/usr/bin/env python3
"""Smart-merge lifecycle checks must work without writing to their checkout."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HANDLER = ROOT / "features/smart-merge.sh"
LAUNCHERS = ("nv-smart-merge", "nv-smart-merge-git", "nv-smart-merge-git-setup")


class SmartMergeFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.checkout = self.base / "checkout with spaces"
        self.engine = self.checkout / "lib/smart_merge.py"
        self.engine.parent.mkdir(parents=True)
        (self.checkout / "bin").mkdir()
        shutil.copy2(ROOT / "lib/smart_merge.py", self.engine)
        for name in LAUNCHERS:
            shutil.copy2(ROOT / "bin" / name, self.checkout / "bin" / name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.environment = {
            "HOME": str(self.home),
            "PATH": os.environ["PATH"],
            "ENVY_ROOT": str(self.checkout),
        }

    def run_handler(self, action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(HANDLER), action],
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def snapshot(self) -> dict[str, tuple[int, int, str | None]]:
        return {
            str(path.relative_to(self.base)): (
                path.stat().st_mode,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
            for path in self.base.rglob("*")
        }

    def test_checks_leave_checkout_and_home_unchanged(self) -> None:
        before = self.snapshot()
        for action in ("verify", "doctor", "converge"):
            with self.subTest(action=action):
                process = self.run_handler(action)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_checks_succeed_when_bytecode_cache_cannot_be_created(self) -> None:
        # A file blocks cache-directory creation even when tests run as root.
        (self.engine.parent / "__pycache__").write_text("not a cache directory\n")
        before = self.snapshot()
        for action in ("verify", "doctor", "converge"):
            with self.subTest(action=action):
                process = self.run_handler(action)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_cli_imports_do_not_write_bytecode(self) -> None:
        self.engine.write_text("import helper\n")
        (self.engine.parent / "helper.py").write_text("value = 42\n")
        before = self.snapshot()
        process = self.run_handler("verify")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_python_still_fails_verification(self) -> None:
        self.engine.write_text("def invalid(:\n")
        before = self.snapshot()
        process = self.run_handler("verify")
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("SyntaxError", process.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_unavailable_launchers_still_fail_verification(self) -> None:
        for name in LAUNCHERS:
            launcher = self.checkout / "bin" / name
            with self.subTest(launcher=name):
                mode = launcher.stat().st_mode
                launcher.chmod(0o644)
                try:
                    self.assertNotEqual(self.run_handler("verify").returncode, 0)
                finally:
                    launcher.chmod(mode)

    def test_failed_cli_smoke_check_is_not_ignored(self) -> None:
        self.engine.write_text("raise SystemExit(23)\n")
        self.assertEqual(self.run_handler("verify").returncode, 23)


if __name__ == "__main__":
    unittest.main()
