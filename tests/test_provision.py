#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import dependencies  # noqa: E402
import provision  # noqa: E402

TOOL = """#!/bin/sh
echo "fixturetool version %s"
"""


class ProvisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.bin = self.base / "bin"
        self.previous = {
            "XDG_STATE_HOME": os.environ.get("XDG_STATE_HOME"),
            "ENVY_BIN_HOME": os.environ.get("ENVY_BIN_HOME"),
        }
        os.environ["XDG_STATE_HOME"] = str(self.state)
        os.environ["ENVY_BIN_HOME"] = str(self.bin)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.original_root = provision.ROOT
        provision.ROOT = self.root

    def tearDown(self) -> None:
        provision.ROOT = self.original_root
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def archive(self, version: str) -> Path:
        payload = self.base / "fixturetool"
        payload.write_text(TOOL % version, encoding="utf-8")
        payload.chmod(0o755)
        path = self.base / f"fixturetool-{version}.tar.gz"
        with tarfile.open(path, "w:gz") as bundle:
            bundle.add(payload, arcname="fixturetool/bin/fixturetool")
        return path

    def write_manifest(self, url: str, sha256: str, version: str = "1.2.3") -> None:
        document = {
            "version": 1,
            "dependencies": [
                {
                    "name": "fixturetool",
                    "version": version,
                    "license": "MIT",
                    "provision": {
                        "command": "fixturetool",
                        "install_path": "~/.local/bin/fixturetool",
                        "artifacts": {
                            provision.platform_key(): {
                                "url": url,
                                "member": "fixturetool/bin/fixturetool",
                                "sha256": sha256,
                            }
                        },
                    },
                }
            ],
        }
        (self.root / "dependencies.json").write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )

    def test_platform_key_normalizes_architecture(self) -> None:
        key = provision.platform_key()
        self.assertRegex(key, r"^(linux|darwin|windows)-")

    def test_missing_digest_refuses_to_install(self) -> None:
        archive = self.archive("1.2.3")
        self.write_manifest(archive.as_uri(), "")
        usable, reason = provision.can_install(provision.dependency("fixturetool"))
        self.assertFalse(usable)
        self.assertIn("no recorded sha256", reason)
        self.assertEqual(provision.install("fixturetool"), 1)
        self.assertFalse((self.bin / "fixturetool").exists())

    def test_digest_mismatch_refuses_to_install(self) -> None:
        archive = self.archive("1.2.3")
        self.write_manifest(archive.as_uri(), "0" * 64)
        self.assertEqual(provision.install("fixturetool"), 1)
        self.assertFalse((self.bin / "fixturetool").exists())
        self.assertFalse(provision.marker("fixturetool").exists())

    def test_verified_artifact_installs_and_records_ownership(self) -> None:
        archive = self.archive("1.2.3")
        self.write_manifest(archive.as_uri(), provision.digest_of(archive))
        self.assertEqual(provision.install("fixturetool"), 0)
        installed = self.bin / "fixturetool"
        self.assertTrue(installed.exists())
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertTrue(provision.marker("fixturetool").is_file())
        item = provision.dependency("fixturetool")
        self.assertEqual(provision.installed_version(item, str(installed)), "1.2.3")

    def test_install_is_idempotent(self) -> None:
        archive = self.archive("1.2.3")
        self.write_manifest(archive.as_uri(), provision.digest_of(archive))
        self.assertEqual(provision.install("fixturetool"), 0)
        installed = self.bin / "fixturetool"
        first = installed.read_bytes()
        # On PATH at the pinned version, a second converge must not refetch.
        path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{path}"
        archive.unlink()
        try:
            self.assertEqual(provision.install("fixturetool"), 0)
        finally:
            os.environ["PATH"] = path
        self.assertEqual(installed.read_bytes(), first)

    def test_failed_record_leaves_manifest_untouched(self) -> None:
        self.write_manifest("file:///nonexistent/missing.tar.gz", "")
        manifest = self.root / "dependencies.json"
        before = manifest.read_bytes()
        self.assertEqual(provision.record("fixturetool", []), 1)
        self.assertEqual(manifest.read_bytes(), before)

    def test_record_stores_the_digest(self) -> None:
        archive = self.archive("1.2.3")
        self.write_manifest(archive.as_uri(), "")
        self.assertEqual(provision.record("fixturetool", []), 0)
        recorded = provision.artifact(provision.dependency("fixturetool"))
        self.assertEqual(recorded["sha256"], provision.digest_of(archive))
        self.assertEqual(provision.install("fixturetool"), 0)

    def test_wrong_member_is_reported(self) -> None:
        archive = self.archive("1.2.3")
        with self.assertRaises(provision.ProvisionError):
            provision.extract_member(archive, "absent/path", self.base / "out")

    def test_installed_binary_reporting_wrong_version_fails(self) -> None:
        archive = self.archive("9.9.9")
        self.write_manifest(archive.as_uri(), provision.digest_of(archive))
        self.assertEqual(provision.install("fixturetool"), 1)


class PinEnforcementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(Path(self.temporary.name) / "state")
        self.original_version = provision.installed_version

    def tearDown(self) -> None:
        provision.installed_version = self.original_version
        if self.previous is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.previous
        self.temporary.cleanup()

    def item(self) -> dict[str, object]:
        return {
            "name": "fixturetool",
            "version": "1.2.3",
            "provision": {"command": "fixturetool", "install_path": "/nonexistent"},
        }

    def test_absent_tool_is_skipped(self) -> None:
        provision.installed_version = lambda item, command=None: None
        dependencies.verify_binary(self.item(), converge=False)

    def test_matching_version_passes(self) -> None:
        provision.installed_version = lambda item, command=None: "1.2.3"
        dependencies.verify_binary(self.item(), converge=False)

    def test_foreign_mismatch_warns_without_failing(self) -> None:
        provision.installed_version = lambda item, command=None: "1.0.0"
        dependencies.verify_binary(self.item(), converge=False)

    def test_owned_mismatch_fails_verification(self) -> None:
        provision.installed_version = lambda item, command=None: "1.0.0"
        provision.marker("fixturetool").parent.mkdir(parents=True, exist_ok=True)
        provision.marker("fixturetool").touch()
        with self.assertRaises(dependencies.DependencyError):
            dependencies.verify_binary(self.item(), converge=False)


if __name__ == "__main__":
    unittest.main()
