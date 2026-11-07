"""Workflow installation and ownership tests, not model-behavior tests."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "assets/agent-assets/skills"
FAMILY = (
    "develop", "design", "plan", "implement", "coordinate-workstreams",
    "qa", "review", "review-git", "review-architecture", "ship", "ship-git",
    "pr-triage", "reflect", "handoff", "retrospective-research",
)
SPEC = importlib.util.spec_from_file_location("workflow_asset_fixture", ROOT / "tests/test_reflect_assets.py")
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class WorkflowAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow-assets-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"schema_version": 3, "installed": True,
            "features": {"agent-assets": {"enabled": True}}}))
        self.ownership = self.root / "owned.json"
        self.resolved = self.root / "resolved.json"

    def run_assets(self, action="converge", manifests=None, platform="linux"):
        manifests = manifests or [ROOT / "assets/agent-assets.json"]
        self.resolved.write_text(json.dumps({"resources": {
            "agent_asset_manifests": [str(path) for path in manifests]}}))
        environment = FIXTURE.asset_environment(
            self.home, manifests[0], self.state, self.ownership, platform)
        environment.pop("ENVY_AGENT_ASSETS_MANIFEST", None)
        environment["ENVY_MANIFEST"] = str(self.resolved)
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "features/agent-assets.py"), action,
             "--state", str(self.state), "--ownership", str(self.ownership), "--json"],
            cwd=ROOT, env=environment, capture_output=True, timeout=20,
        )

    def bundle(self, name):
        bundle = self.root / name
        skill = bundle / "skills/pr-triage"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: pr-triage\ndescription: Previous bundle-owned PR workflow.\n---\n")
        manifest = bundle / "manifest.json"
        manifest.write_text(json.dumps({"version": 1, "skill_groups": [{
            "name": name, "path": "skills", "targets": ["claude", "codex"]}]}))
        return manifest

    def test_all_workflow_entries_share_sources_and_preserve_git_helpers(self):
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                for _ in range(2):
                    result = self.run_assets(platform=platform)
                    self.assertEqual(result.returncode, 0, result.stderr)
                for harness in (".claude", ".agents"):
                    directory = self.home / harness / "skills"
                    for name in FAMILY:
                        self.assertEqual((directory / name).resolve(), SKILLS / name)
                    for relative in ("review/scripts/review_bundle.py", "ship/scripts/git_publish.py"):
                        result = subprocess.run(
                            [sys.executable, "-I", "-B", str(directory / relative), "--help"],
                            capture_output=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.run_assets("verify", platform=platform).returncode, 0)

    def test_legacy_pr_triage_requires_declared_handoff(self):
        previous = self.root / "previous-assets"
        shutil.copytree(ROOT / "assets", previous)
        shutil.rmtree(previous / "agent-assets/skills/pr-triage")
        previous_manifest = previous / "agent-assets.json"
        value = json.loads(previous_manifest.read_text())
        value["version"] = 1
        value.pop("skill_contracts", None)
        previous_manifest.write_text(json.dumps(value))
        legacy = self.bundle("previous-workflow-bundle")
        result = self.run_assets(manifests=[previous / "agent-assets.json", legacy])
        self.assertEqual(result.returncode, 0, result.stderr)
        for harness in (".claude", ".agents"):
            self.assertEqual((self.home / harness / "skills/pr-triage").resolve(),
                             legacy.parent / "skills/pr-triage")
        before = FIXTURE.snapshot(self.home)
        recorded = self.ownership.read_bytes()
        result = self.run_assets()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.home), before)
        self.assertEqual(self.ownership.read_bytes(), recorded)
        for harness in (".claude", ".agents"):
            self.assertEqual((self.home / harness / "skills/pr-triage").resolve(),
                             legacy.parent / "skills/pr-triage")

    def test_duplicate_old_bundle_is_rejected_before_linking(self):
        result = self.run_assets(manifests=[ROOT / "assets/agent-assets.json", self.bundle("old-bundle")])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / ".claude/skills").exists())
        self.assertFalse((self.home / ".agents/skills").exists())

    def test_foreign_design_skill_is_preserved(self):
        skill = self.home / ".agents/skills/design"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Foreign skill, preserve.\n")
        before = FIXTURE.snapshot(self.home)
        result = self.run_assets()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.home), before)


if __name__ == "__main__":
    unittest.main()
