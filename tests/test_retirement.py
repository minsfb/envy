"""R1-R10: disposable production CLI/state acceptance; no credential services."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import manifest
import state


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="envy-retirement-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.core = self.root / "core"
        self.core.mkdir()
        for folder in ("bin", "lib"):
            shutil.copytree(ROOT / folder, self.core / folder,
                            ignore=shutil.ignore_patterns("__pycache__"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.package = self.root / "package"
        self.package.mkdir()
        self.handler = self.package / "retire.py"
        self.handler.write_text(
            "import os, pathlib, sys\n"
            "root = pathlib.Path(os.environ['HOME'])\n"
            "assert os.environ.get('ENVY_PURGE') == 'false'\n"
            "assert not os.environ.get('ENVY_DRY_RUN') in ('1', 'true')\n"
            "if sys.argv[1] == 'verify':\n"
            "    sys.exit(int((root / 'owned').exists() or (root / 'bad-verify').exists()))\n"
            "with (root / 'calls').open('a') as f: f.write('retire\\n')\n"
            "if (root / 'fail').exists(): sys.exit(17)\n"
            "(root / 'owned').unlink(missing_ok=True)\n")
        (self.core / "envy.json").write_text(json.dumps({
            "version": 3, "id": "envy-core", "features": {},
        }))
        self.definition = {
            "description": "Retained retirement", "tier": "optional",
            "lifecycle": "deprecated", "retirement_revision": "1",
            "scope": {"global": True},
            "retire": {"run": "retire.py", "args": ["retire"]},
            "verify_retired": {"run": "retire.py", "args": ["verify"]},
        }
        self.bundle = {
            "version": 2, "id": "fixture", "portable": True,
            "requires": ["feature-retirement-v1"],
            "features": {"departing": self.definition,
                         "independent": {"description": "Unrelated", "tier": "optional"}},
        }
        self.write_bundle()
        self.environment = {
            "HOME": str(self.home), "PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin",
            "SHELL": "/bin/bash", "ENVY_PLATFORM_OVERRIDE": "linux",
            "ENVY_SKIP_DEPENDENCY_PINS": "1", "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.state_path = self.home / ".config/envy/state.json"

    def write_bundle(self):
        (self.package / "envy.bundle.json").write_text(json.dumps(self.bundle))

    def compose(self):
        return manifest.compose(self.core / "envy.json", [str(self.package)])

    def seed(self, *, enabled=True, history=True, detached=False):
        # Seed schema-v3 existing-install records without requiring implemented
        # deprecation support, allowing pre-implementation CLI tests to run red.
        features = {"departing": {
            "origin": "fixture", "enabled": enabled, "selection_explicit": True,
            "tier": "optional", "status": "current" if enabled else "disabled",
            "verification": "unknown", "data": {},
        }, "independent": {"origin": "fixture", "enabled": True,
                            "selection_explicit": True, "tier": "optional", "status": "current"}}
        if history:
            features["departing"]["last_converged_at"] = "2026-01-01T00:00:00Z"
        value = {
            "schema_version": 3, "installed": True, "preset": "custom",
            "bundles": [{"id": "fixture", "path": str(self.package), "core": False}],
            "features": features, "detached_features": {}, "ownership": {},
        }
        if detached:
            value["detached_features"]["departing"] = features.pop("departing")
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps(value))
        (self.home / "owned").write_text("owned fixture")
        (self.home / "user-data").write_text("retain")

    def cli(self, *args, expected=0):
        result = subprocess.run([str(self.core / "bin/envy"), *args],
                                env=self.environment, cwd=self.home, text=True,
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def record(self):
        return json.loads(self.state_path.read_text())["features"]["departing"]

    def test_capability_and_strict_manifest(self):
        self.assertEqual(self.compose()["features"]["departing"]["lifecycle"], "deprecated")
        base = copy.deepcopy(self.bundle)
        for mutate in (
            lambda v: v.update(version=1),
            lambda v: v.update(requires=["unknown-capability"]),
            lambda v: v.update(recommended_features=["departing"]),
            lambda v: v.update(aliases={"alias": "departing"}),
            lambda v: v["features"]["independent"].update(depends_on=["departing"]),
            lambda v: v["features"]["departing"].update(lifecycle="typo"),
            lambda v: v["features"]["departing"].pop("verify_retired"),
            lambda v: v["features"]["departing"].update(converge={"run": "retire.py"}),
        ):
            self.bundle = copy.deepcopy(base)
            mutate(self.bundle)
            self.write_bundle()
            with self.assertRaises(manifest.ManifestError):
                self.compose()

    def test_fresh_selection_refused_and_no_cleanup(self):
        self.cli("--bundle", str(self.package), "install", "--enable", "departing", "--yes", expected=1)
        self.assertFalse(self.state_path.exists())
        self.cli("--bundle", str(self.package), "install", "--preset", "core", "--yes")
        self.assertFalse(self.record()["enabled"])
        self.assertFalse((self.home / "calls").exists())

    def test_upgrade_retires_once_and_preserves_user_data(self):
        self.seed()
        self.cli("upgrade")
        self.assertFalse((self.home / "owned").exists())
        self.assertEqual((self.home / "user-data").read_text(), "retain")
        self.assertEqual(self.record()["retirement"]["status"], "retired")
        self.assertFalse(self.record()["enabled"])
        self.cli("upgrade")
        self.assertEqual((self.home / "calls").read_text(), "retire\n")

    def test_disabled_historical_feature_is_retired(self):
        self.seed(enabled=False)
        self.cli("upgrade", "--only", "departing")
        self.assertEqual(self.record()["retirement"]["status"], "retired")

    def test_disabled_never_installed_is_not_cleaned(self):
        self.seed(enabled=False, history=False)
        self.cli("upgrade")
        self.assertTrue((self.home / "owned").exists())
        self.assertFalse((self.home / "calls").exists())

    def test_attached_package_detached_feature_history_is_retired(self):
        self.seed(detached=True)
        self.cli("upgrade")
        self.assertEqual(self.record()["retirement"]["status"], "retired")

    def test_selectors_and_dry_run_do_not_widen_or_clean(self):
        self.seed()
        before = self.state_path.read_bytes()
        self.assertIn("WOULD RETIRE", self.cli("upgrade", "--only", "departing", "--dry-run"))
        self.assertEqual(self.state_path.read_bytes(), before)
        self.cli("upgrade", "--only", "independent")
        self.cli("upgrade", "--except", "departing")
        self.assertTrue((self.home / "owned").exists())
        self.assertFalse((self.home / "calls").exists())

    def test_status_verify_do_not_clean(self):
        self.seed()
        self.assertIn("deprecated", self.cli("status"))
        self.cli("verify", "--only", "departing", expected=1)
        self.assertTrue((self.home / "owned").exists())
        self.assertFalse((self.home / "calls").exists())

    def test_failed_retirement_retries_but_changed_implementation_refuses(self):
        self.seed()
        (self.home / "fail").touch()
        self.cli("upgrade", "--only", "departing", expected=1)
        self.assertEqual(self.record()["retirement"]["status"], "failed")
        original = self.handler.read_text()
        self.handler.write_text(original + "# implementation changed\n")
        self.cli("upgrade", "--only", "departing", expected=1)
        self.assertEqual((self.home / "calls").read_text(), "retire\n")
        self.handler.write_text(original)
        (self.home / "fail").unlink()
        self.cli("upgrade", "--only", "departing")
        self.assertEqual(self.record()["retirement"]["status"], "retired")

    def test_verification_failure_is_not_success_and_retries(self):
        self.seed()
        (self.home / "bad-verify").touch()
        self.cli("upgrade", "--only", "departing", expected=1)
        self.assertEqual(self.record()["retirement"]["status"], "failed")
        (self.home / "bad-verify").unlink()
        self.cli("upgrade", "--only", "departing")
        self.assertEqual(self.record()["retirement"]["status"], "retired")


if __name__ == "__main__":
    unittest.main()
