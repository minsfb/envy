"""Owned install and synthetic-data checks, not agent behavior evaluations."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "assets/agent-assets/skills/pr-triage"
CASES = ROOT / "tests/fixtures/pr-triage"
# Reuse the existing installer-owned path/context policy, not a second one.
SPEC = importlib.util.spec_from_file_location(
    "triage_asset_fixture", ROOT / "tests/test_reflect_assets.py"
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class TriageAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="triage-assets-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.ownership = self.root / "ownership.json"
        self.state.write_text(json.dumps({"schema_version": 3, "installed": True,
            "features": {"agent-assets": {"enabled": True}}}), encoding="utf-8")

    def run_assets(self, action="converge", *, manifest=None, platform="linux", dry_run=False, resolved=None):
        manifest = manifest or ROOT / "assets/agent-assets.json"
        arguments = [sys.executable, "-B", str(ROOT / "features/agent-assets.py"),
                     action, "--state", str(self.state),
                     "--ownership", str(self.ownership), "--json"]
        environment = FIXTURE.asset_environment(self.home, manifest, self.state,
                                                self.ownership, platform)
        if resolved is None:
            arguments += ["--manifest", str(manifest)]
        else:
            environment.update({"ENVY_AGENT_ASSETS_MANIFEST": "", "ENVY_MANIFEST": str(resolved)})
        if dry_run:
            arguments.append("--dry-run")
        return subprocess.run(
            arguments, cwd=ROOT, env=environment,
            capture_output=True, timeout=15,
        )

    def links(self):
        return [self.home / harness / "skills/pr-triage"
                for harness in (".claude", ".agents")]

    def identity(self, path):
        info = path.lstat()
        return tuple(getattr(info, name) for name in (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size",
            "st_mtime_ns", "st_ctime_ns"))

    def test_actual_v2_repairs_only_missing_ordinary_links(self):
        self.assertEqual(self.run_assets().returncode, 0)
        protected = {link: self.identity(link) for link in self.links()}
        ledger = json.loads(self.ownership.read_text())
        self.assertEqual(ledger["version"], 2)
        for name in ("qa", "reflect"):
            for harness in (".claude", ".agents"):
                with self.subTest(name=name, harness=harness):
                    target = self.home / harness / "skills" / name
                    target.unlink()
                    before = FIXTURE.snapshot(self.root)
                    for action in ("status", "verify", "doctor"):
                        result = self.run_assets(action)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn(str(target).encode(), result.stdout + result.stderr)
                        self.assertEqual(FIXTURE.snapshot(self.root), before)
                    result = self.run_assets(dry_run=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(FIXTURE.snapshot(self.root), before)
                    result = self.run_assets()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(os.readlink(target), ledger["links"][str(target)])
                    self.assertEqual(json.loads(self.ownership.read_text()), ledger)
                    self.assertEqual({link: self.identity(link) for link in self.links()}, protected)

    def test_actual_v2_changed_ordinary_entries_are_actionable_and_preserved(self):
        self.assertEqual(self.run_assets().returncode, 0)
        protected = {link: self.identity(link) for link in self.links()}
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("Preserve owned outside control.\n")
        for harness in (".claude", ".agents"):
            for kind in ("other-public-source", "outside", "file", "directory"):
                with self.subTest(harness=harness, kind=kind):
                    target = self.home / harness / "skills/qa"
                    target.unlink()
                    if kind in {"other-public-source", "outside"}:
                        target.symlink_to(SKILL if kind == "other-public-source" else outside)
                    elif kind == "file":
                        target.write_text("Preserve foreign entry.\n")
                    else:
                        target.mkdir()
                    before, identity = FIXTURE.snapshot(self.root), self.identity(target)
                    for action, dry in (("converge", False), ("converge", True),
                                        ("status", False), ("verify", False), ("doctor", False)):
                        result = self.run_assets(action, dry_run=dry)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        output = result.stdout + result.stderr
                        for text in ("ownership-drift", str(target), "changed", "do not delete"):
                            self.assertIn(text.encode(), output)
                        self.assertEqual(FIXTURE.snapshot(self.root), before)
                        self.assertEqual(self.identity(target), identity)
                        self.assertEqual({link: self.identity(link) for link in self.links()}, protected)
                    target.rmdir() if kind == "directory" else target.unlink()
                    self.assertEqual(self.run_assets().returncode, 0)

    def test_actual_v2_mixed_v1_upgrade_repairs_absence_without_adoption(self):
        bundle = self.root / "bundle"
        skill = bundle / "skills/bundle-tool"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: bundle-tool\ndescription: Owned synthetic bundle skill.\n---\n")
        manifest = bundle / "manifest.json"
        manifest.write_text(json.dumps({"version": 1,
            "destinations": {"claude": {"skills": str(self.home / ".claude/skills")},
                             "codex": {"skills": str(self.home / ".agents/skills")}},
            "skill_groups": [{"name": "bundle", "path": str(skill.parent),
                              "targets": ["claude", "codex"]}]}))
        self.assertEqual(self.run_assets(manifest=manifest).returncode, 0)
        self.assertEqual(json.loads(self.ownership.read_text())["version"], 1)
        # Omit bundle destinations in the composed input; the real public manifest
        # supplies them. This does not manufacture or downgrade its v2 declaration.
        value = json.loads(manifest.read_text())
        value.pop("destinations")
        manifest.write_text(json.dumps(value))
        resolved = self.root / "resolved.json"
        resolved.write_text(json.dumps({"resources": {"agent_asset_manifests": [
            str(ROOT / "assets/agent-assets.json"), str(manifest)]}}))
        for harness in (".claude", ".agents"):
            target = self.home / harness / "skills/bundle-tool"
            target.unlink()
            self.assertEqual(self.run_assets(resolved=resolved).returncode, 0)
            ledger = json.loads(self.ownership.read_text())
            self.assertEqual(ledger["version"], 2)
            self.assertEqual(os.readlink(target), str(skill))
            target.unlink()
            target.symlink_to(SKILL)  # Even a real selected public source is foreign here.
            before = FIXTURE.snapshot(self.root)
            result = self.run_assets(resolved=resolved)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"ownership-drift", result.stderr)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
            target.unlink()
            self.assertEqual(self.run_assets(resolved=resolved).returncode, 0)

    def test_public_only_both_harnesses_share_skill_and_reference(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                self.assertEqual(self.run_assets(platform=platform).returncode, 0)
                self.assertEqual(self.run_assets(platform=platform).returncode, 0)
                for link in self.links():
                    self.assertEqual(link.resolve(), SKILL)
                    for relative in ("SKILL.md", "references/operations-and-extensions.md"):
                        self.assertEqual((link / relative).read_bytes(),
                                         (SKILL / relative).read_bytes())
                    self.assertEqual(len(list(link.parent.glob("pr-triage"))), 1)
                self.assertEqual(self.run_assets("verify", platform=platform).returncode, 0)
                recorded = json.loads(self.ownership.read_text(encoding="utf-8"))["links"]
                for link in self.links():
                    self.assertEqual(recorded[str(link)], str(SKILL))

    def test_actual_public_v2_contract_installs_and_preserves_observations(self):
        manifest = ROOT / "assets/agent-assets.json"
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIs(type(declared["version"]), int)
        self.assertEqual(declared["version"], 2)
        self.assertEqual(declared["skill_contracts"],
                         [{"group": "common", "name": "pr-triage", "version": 1}])
        self.assertIs(type(declared["skill_contracts"][0]["version"]), int)
        common = [group for group in declared["skill_groups"] if group["name"] == "common"]
        self.assertEqual(len(common), 1)
        self.assertEqual(common[0]["targets"], ["claude", "codex"])
        self.assertFalse(self.ownership.exists())
        self.assertTrue(all(not link.exists() and not link.is_symlink() for link in self.links()))

        for _ in range(2):
            result = self.run_assets(manifest=manifest)
            self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads(self.ownership.read_text(encoding="utf-8"))
        self.assertEqual(recorded["version"], 2)
        self.assertIsNone(recorded["handoff"])
        self.assertEqual(recorded["manifests"], [str(manifest)])
        for link in self.links():
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), str(SKILL))
            self.assertEqual(recorded["links"][str(link)], str(SKILL))
            for relative in ("SKILL.md", "references/operations-and-extensions.md"):
                self.assertEqual((link / relative).read_bytes(), (SKILL / relative).read_bytes())

        before = FIXTURE.snapshot(self.root)
        status = self.run_assets("status", manifest=manifest)
        self.assertEqual(status.returncode, 0, status.stderr)
        report = json.loads(status.stdout)
        selected = [asset for asset in report["expected"] if asset["name"] == "pr-triage"]
        self.assertEqual(len(selected), 2)
        self.assertEqual({asset["harness"] for asset in selected}, {"claude", "codex"})
        result = self.run_assets("verify", manifest=manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(FIXTURE.snapshot(self.root), before)

    def test_dry_run_and_status_do_not_install_or_change_fixture(self):
        before = FIXTURE.snapshot(self.root)
        self.assertEqual(self.run_assets(dry_run=True).returncode, 0)
        self.assertNotEqual(self.run_assets("status").returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertFalse(self.ownership.exists())
        self.assertTrue(all(not link.is_symlink() for link in self.links()))

    def test_verification_and_status_preserve_installed_fixture(self):
        self.assertEqual(self.run_assets().returncode, 0)
        before = FIXTURE.snapshot(self.root)
        self.assertEqual(self.run_assets("verify").returncode, 0)
        result = self.run_assets("status")
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        selected = [asset for asset in report["expected"] if asset["name"] == "pr-triage"]
        self.assertEqual({asset["harness"] for asset in selected}, {"claude", "codex"})
        self.assertEqual(FIXTURE.snapshot(self.root), before)

    def test_foreign_skill_prevents_partial_install(self):
        foreign, other = self.links()
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("User-owned skill.\n", encoding="utf-8")
        before = FIXTURE.snapshot(self.root)
        self.assertNotEqual(self.run_assets().returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertFalse(other.is_symlink())

    def test_modified_entry_directory_is_preserved(self):
        self.assertEqual(self.run_assets().returncode, 0)
        local, other = self.links()
        local.unlink()  # Owned fixture link only, never its source directory.
        local.mkdir()
        (local / "SKILL.md").write_text("Local customization.\n", encoding="utf-8")
        other.unlink()
        before = FIXTURE.snapshot(self.root)
        self.assertNotEqual(self.run_assets().returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertFalse(other.is_symlink())

    def test_duplicate_downstream_name_blocks_without_mutating_links(self):
        self.assertEqual(self.run_assets().returncode, 0)
        legacy = self.root / "sample-bundle/skills/pr-triage"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: pr-triage\ndescription: Synthetic downstream workflow.\n---\n",
            encoding="utf-8",
        )
        composed = json.loads((ROOT / "assets/agent-assets.json").read_text(encoding="utf-8"))
        for key in ("skill_groups", "agent_groups"):
            for group in composed[key]:
                group["path"] = str(ROOT / "assets" / group["path"])
        composed["skill_groups"].append({"name": "sample-bundle",
            "path": str(legacy.parent), "targets": ["claude", "codex"]})
        manifest = self.root / "duplicate-manifest.json"
        manifest.write_text(json.dumps(composed), encoding="utf-8")
        before = FIXTURE.snapshot(self.root)
        result = self.run_assets(manifest=manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"source-duplicate", result.stderr)
        self.assertEqual(FIXTURE.snapshot(self.root), before)

    def test_installer_owned_paths_preserve_outside_sentinels(self):
        outside = self.root / "outside"
        outside.mkdir()
        context = {}
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                     "ENVY_CONFIG_DIR", "ENVY_STATE_DIR", "ENVY_DATA_DIR", "ENVY_BIN_DIR",
                     "ENVY_DIR", "ENVY_MANIFEST", "ENVY_AGENT_ASSETS_MANIFEST",
                     "ENVY_STATE_FILE", "ENVY_AGENT_ASSETS_OWNERSHIP"):
            target = outside / name
            target.mkdir()
            (target / "sentinel").write_text("Preserve.\n", encoding="utf-8")
            context[name] = str(target)
        before = FIXTURE.snapshot(outside)
        with patch.dict(os.environ, context):
            self.assertEqual(self.run_assets().returncode, 0)
            self.assertEqual(self.run_assets("verify").returncode, 0)
        self.assertEqual(FIXTURE.snapshot(outside), before)


class TriageScenarioDataTests(unittest.TestCase):
    def test_case_and_operator_expectation_coverage_match(self):
        cases = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
        expectations = json.loads((CASES / "expectations.json").read_text(encoding="utf-8"))
        self.assertEqual(cases["version"], 1)
        self.assertEqual(expectations["version"], 1)
        self.assertIs(expectations["operator_only"], True)
        identities = [case["id"] for case in cases["cases"]]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(set(identities), set(expectations["cases"]))
        self.assertTrue(cases["evaluation_boundary"])
        for case in cases["cases"]:
            self.assertEqual(set(case), {"id", "request", "observations"})
            self.assertTrue(case["request"])
            self.assertTrue(expectations["cases"][case["id"]]["required_findings"])

    def test_synthetic_repository_and_candidate_shapes(self):
        cases = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
        for case in cases["cases"]:
            observations = case["observations"]
            self.assertIn(observations["repository"]["host"],
                          {"github.com", "forge.example.invalid"})
            self.assertEqual(observations["repository"]["name"], "sample")
            for pr in observations["prs"]:
                self.assertIsInstance(pr["number"], int)
                self.assertGreater(pr["number"], 0)
                self.assertTrue(re.fullmatch(r"[a-f0-9]{40}", pr["head_oid"]))
            coverage = observations.get("coverage")
            if coverage is not None:
                self.assertEqual(coverage["returned"], len(observations["prs"]))

    def test_negative_control_observations_are_distinct(self):
        data = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case["observations"] for case in data["cases"]}
        stale = cases["stale-approved-head"]
        self.assertNotEqual(stale["prs"][0]["head_oid"], stale["supplied_review"]["head_oid"])
        queued, closed = cases["queued-and-closed-unmerged"]["prs"]
        self.assertEqual(queued["state"], "open")
        self.assertEqual(queued["merge_operation"], "queued")
        self.assertEqual(closed["state"], "closed")
        self.assertIsNone(closed["merged_at"])
        self.assertTrue(cases["required-access-unavailable"]["adapter"]["required"])
        self.assertFalse(cases["optional-tracking-unavailable"]["adapter"]["required"])
        self.assertNotEqual(cases["incompatible-adapter"]["adapter"]["contract_version"], 1)
        self.assertFalse(cases["truncated-discovery"]["coverage"]["complete"])
        self.assertTrue(cases["truncated-discovery"]["coverage"]["has_next_page"])
        uncertain = cases["uncertain-merge-response"]["merge_attempt"]
        self.assertEqual(uncertain["outcome"], "response_lost")
        self.assertEqual(uncertain["readback"], "not_performed")


if __name__ == "__main__":
    unittest.main()
