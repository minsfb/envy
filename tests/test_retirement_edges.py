"""Retirement lifecycle boundaries using owned fixture files only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

import test_retirement as fixture_module
import bundles
import render_env
import retirement
import state


class RetirementEdges(unittest.TestCase):
    def setUp(self):
        self.rig = fixture_module.RetirementTest(methodName="runTest")
        self.rig.setUp()
        self.addCleanup(self.rig.doCleanups)

    def normalized(self):
        rig = self.rig
        manifest = rig.compose()
        path = rig.root / "resolved.json"
        path.write_text(json.dumps(manifest))
        state.migrate(rig.state_path, path, "linux", "bash", str(rig.core))
        return manifest, path

    def test_uninstall_purge_cannot_purge_retired_user_data(self):
        r = self.rig
        r.seed()
        r.cli("uninstall", "--only", "departing", "--purge", "--yes")
        self.assertEqual(r.record()["retirement"]["status"], "retired")
        self.assertEqual((r.home / "user-data").read_text(), "retain")

    def test_reconfigure_does_not_lose_pending_retirement(self):
        r = self.rig
        r.seed()
        r.cli("install", "--reconfigure", "--preset", "core", "--yes")
        self.assertEqual(r.record()["retirement"]["status"], "retired")

    def test_scope_prevents_cleanup(self):
        r = self.rig
        r.seed()
        r.definition["scope"] = {"darwin": True}
        r.write_bundle()
        r.cli("upgrade")
        self.assertTrue((r.home / "owned").exists())
        r.cli("upgrade", "--only", "departing", expected=1)

    def test_origin_mismatch_refuses(self):
        r = self.rig
        r.seed()
        value = json.loads(r.state_path.read_text())
        value["features"]["departing"]["origin"] = "other-package"
        r.state_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "another package"):
            self.normalized()
        self.assertFalse((r.home / "calls").exists())

    def test_ownership_record_enables_disabled_cleanup(self):
        r = self.rig
        r.seed(enabled=False, history=False)
        value = json.loads(r.state_path.read_text())
        value["ownership"]["departing"] = {"path": str(r.home / "owned"), "created": True}
        r.state_path.write_text(json.dumps(value))
        self.normalized()
        self.assertEqual(r.record()["retirement"]["status"], "pending")

    def test_completed_revision_and_drift_are_rechecked(self):
        r = self.rig
        r.seed()
        _, path = self.normalized()
        # Runner child gets only synthetic HOME/path. No ambient credentials.
        def run():
            p = subprocess.run([sys.executable, "-B", str(r.core / "lib/retirement.py"),
                                str(path), str(r.state_path), "departing"],
                               env=r.environment, capture_output=True, timeout=20)
            self.assertEqual(p.returncode, 0)
        run()
        run()
        self.assertEqual((r.home / "calls").read_text(), "retire\n")
        (r.home / "owned").touch()
        run()
        r.definition["retirement_revision"] = "2"
        r.write_bundle()
        path.write_text(json.dumps(r.compose()))
        run()
        self.assertEqual((r.home / "calls").read_text().count("retire"), 3)

    def test_deprecated_environment_excluded_despite_stale_enabled_state(self):
        result = render_env.render({"features": {"departing": {
            "lifecycle": "deprecated", "env": {"PATH^": "/do-not-use"}}}},
            {"features": {"departing": {"enabled": True, "status": "current"}}}, "/core")
        self.assertNotIn("/do-not-use", result)

    def test_detach_pending_blocks_and_attach_does_not_activate(self):
        r = self.rig
        r.seed()
        manifest, path = self.normalized()
        machine = state.load(r.state_path)
        empty = {**manifest, "bundles": [item for item in manifest["bundles"] if item["core"]], "features": {}}
        with self.assertRaisesRegex(bundles.BundleError, "complete feature retirement"):
            bundles.plan_detach(manifest, machine, "fixture", empty, ["linux", "global"])
        machine["features"]["departing"]["retirement"]["status"] = "retired"
        machine["features"]["departing"]["retirement"]["revision"] = "1"
        plan = bundles.plan_detach(manifest, machine, "fixture", empty, ["linux", "global"])
        self.assertNotIn("departing", plan["departing_features"])
        detached = machine["features"]["departing"]
        detached["enabled"] = True  # persisted before lifecycle awareness
        machine["detached_features"] = {"departing": detached}
        machine["features"] = {}
        plan = bundles.plan_attach(empty, machine, manifest, "fixture", ["linux", "global"])
        self.assertNotIn("departing", plan["forced_features"])

    def test_signal_stops_owned_child_and_leaves_retryable_progress(self):
        r = self.rig
        r.seed()
        r.handler.write_text("import os, pathlib, time\n"
                             "root=pathlib.Path(os.environ['HOME'])\n"
                             "(root/'child-ready').write_text(str(os.getpid()))\n"
                             "time.sleep(20)\n(root/'must-not-write').touch()\n")
        _, path = self.normalized()
        process = subprocess.Popen([sys.executable, "-B", str(r.core / "lib/retirement.py"),
                                    str(path), str(r.state_path), "departing"],
                                   env=r.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            until = time.monotonic() + 5
            while not (r.home / "child-ready").exists() and time.monotonic() < until:
                time.sleep(0.02)
            self.assertTrue((r.home / "child-ready").exists())
            child_pid = int((r.home / "child-ready").read_text())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(r.record()["retirement"]["status"], "failed")
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            self.assertFalse((r.home / "must-not-write").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
