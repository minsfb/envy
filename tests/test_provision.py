#!/usr/bin/env python3
from __future__ import annotations

import io
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
            "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME"),
            "ENVY_BIN_HOME": os.environ.get("ENVY_BIN_HOME"),
            "ENVY_NETWORK_PROXY": os.environ.get("ENVY_NETWORK_PROXY"),
            **{key: os.environ.get(key) for key in provision.PROXY_VARIABLES},
        }
        os.environ["XDG_STATE_HOME"] = str(self.state)
        os.environ["XDG_DATA_HOME"] = str(self.base / "data")
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

    def tree_archive(
        self, escaping_symlink: bool = False, with_extensions: bool = False
    ) -> Path:
        path = self.base / "fixturetree.tar.gz"
        with tarfile.open(path, "w:gz") as bundle:
            executable = tarfile.TarInfo("fixturetree/bin/fixturetool")
            content = (
                b'#!/bin/sh\necho "[timestamp 41.601] info"\necho "1.2.3 fixturetree"\n'
            )
            executable.size = len(content)
            executable.mode = 0o755
            bundle.addfile(executable, io.BytesIO(content))
            link = tarfile.TarInfo("fixturetree/bin/fixture-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../escape" if escaping_symlink else "fixturetool"
            bundle.addfile(link)
            if with_extensions:
                for name in ("keep", "remove"):
                    extension = tarfile.TarInfo(
                        f"fixturetree/lib/extensions/{name}/package.json"
                    )
                    payload = json.dumps({"name": name}).encode()
                    extension.size = len(payload)
                    extension.mode = 0o644
                    bundle.addfile(extension, io.BytesIO(payload))
        return path

    def write_tree_manifest(
        self, archive: Path, exclude_paths: list[str] | None = None
    ) -> None:
        document = {
            "version": 1,
            "dependencies": [
                {
                    "name": "fixturetool",
                    "version": "1.2.3",
                    "license": "MIT",
                    "provision": {
                        "kind": "tree",
                        "command": "fixturetool",
                        "executable": "bin/fixturetool",
                        "install_path": "${ENVY_DATA_DIR}/tools/fixturetool/1.2.3",
                        "exclude_paths": exclude_paths or [],
                        "version_pattern": "(?m)^(\\d+(?:\\.\\d+)+)\\b",
                        "artifacts": {
                            provision.platform_key(): {
                                "url": archive.as_uri(),
                                "strip_components": 1,
                                "sha256": provision.digest_of(archive),
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

    def test_envy_network_proxy_fills_standard_proxy_environment(self) -> None:
        for key in provision.PROXY_VARIABLES:
            os.environ.pop(key, None)
        os.environ["ENVY_NETWORK_PROXY"] = "http://proxy.example:8080"
        environment = provision.external_environment()
        for key in provision.PROXY_VARIABLES:
            self.assertEqual(environment[key], "http://proxy.example:8080")

    def test_explicit_proxy_environment_takes_precedence(self) -> None:
        for key in provision.PROXY_VARIABLES:
            os.environ.pop(key, None)
        os.environ["ENVY_NETWORK_PROXY"] = "http://fallback.example:8080"
        os.environ["HTTPS_PROXY"] = "http://explicit.example:8080"
        environment = provision.external_environment()
        self.assertEqual(environment["HTTPS_PROXY"], "http://explicit.example:8080")
        self.assertNotIn("HTTP_PROXY", environment)

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

    def test_verified_tree_installs_symlinks_and_detects_drift(self) -> None:
        archive = self.tree_archive()
        self.write_tree_manifest(archive)
        self.assertEqual(provision.install("fixturetool"), 0)
        item = provision.dependency("fixturetool")
        target = provision.install_path(item)
        self.assertEqual(os.readlink(target / "bin" / "fixture-link"), "fixturetool")
        provision.verify_tree(item)
        (target / "unexpected").write_text("drift", encoding="utf-8")
        with self.assertRaises(provision.ProvisionError):
            provision.verify_tree(item)

    def test_tree_rejects_escaping_symlink(self) -> None:
        archive = self.tree_archive(escaping_symlink=True)
        self.write_tree_manifest(archive)
        with self.assertRaises(provision.ProvisionError):
            provision.install("fixturetool")
        self.assertFalse(
            provision.install_path(provision.dependency("fixturetool")).exists()
        )

    def test_tree_excludes_declared_paths_and_records_policy(self) -> None:
        archive = self.tree_archive(with_extensions=True)
        exclusion = "lib/extensions/remove"
        self.write_tree_manifest(archive, [exclusion])
        self.assertEqual(provision.install("fixturetool"), 0)
        item = provision.dependency("fixturetool")
        target = provision.install_path(item)
        self.assertTrue((target / "lib/extensions/keep/package.json").is_file())
        self.assertFalse((target / exclusion).exists())
        manifest = json.loads(
            (target / provision.TREE_MANIFEST).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["excluded_paths"], [exclusion])
        provision.verify_tree(item)

    def test_tree_rejects_exclusion_absent_from_archive(self) -> None:
        archive = self.tree_archive(with_extensions=True)
        self.write_tree_manifest(archive, ["lib/extensions/absent"])
        with self.assertRaisesRegex(
            provision.ProvisionError, "exclusions are absent from archive"
        ):
            provision.install("fixturetool")

    def test_tree_rejects_excluding_a_file(self) -> None:
        archive = self.tree_archive()
        self.write_tree_manifest(archive, ["bin/fixturetool"])
        with self.assertRaisesRegex(
            provision.ProvisionError, "exclusion is not a directory"
        ):
            provision.install("fixturetool")

    def test_tree_verification_detects_exclusion_policy_change(self) -> None:
        archive = self.tree_archive(with_extensions=True)
        self.write_tree_manifest(archive, ["lib/extensions/remove"])
        self.assertEqual(provision.install("fixturetool"), 0)
        self.write_tree_manifest(archive, ["lib/extensions/keep"])
        with self.assertRaisesRegex(
            provision.ProvisionError, "manifest does not match its pin"
        ):
            provision.verify_tree(provision.dependency("fixturetool"))

    def test_owned_tree_can_be_removed(self) -> None:
        archive = self.tree_archive()
        self.write_tree_manifest(archive)
        self.assertEqual(provision.install("fixturetool"), 0)
        target = provision.install_path(provision.dependency("fixturetool"))
        self.assertEqual(provision.remove("fixturetool"), 0)
        self.assertFalse(target.exists())


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


class RepositoryPolicyTest(unittest.TestCase):
    def test_code_server_excludes_vendor_service_integrations(self) -> None:
        document = json.loads((ROOT / "dependencies.json").read_text(encoding="utf-8"))
        code_server = next(
            item for item in document["dependencies"] if item["name"] == "code-server"
        )
        self.assertEqual(
            code_server["provision"]["exclude_paths"],
            [
                "lib/vscode/extensions/copilot",
                "lib/vscode/extensions/github",
                "lib/vscode/extensions/github-authentication",
                "lib/vscode/extensions/microsoft-authentication",
            ],
        )


if __name__ == "__main__":
    unittest.main()
