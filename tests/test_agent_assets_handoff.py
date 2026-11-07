"""Synthetic installer I/O fixtures; no providers, managed host state, or skill behavior evals."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "features/agent-assets.py"
DRIVER = ROOT / "tests/fixtures/agent-assets-handoff/driver.py"
SPEC = importlib.util.spec_from_file_location("handoff_fixture_policy", ROOT / "tests/test_reflect_assets.py")
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def write_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


def entry_metadata(path):
    info = path.lstat()
    return {name: getattr(info, name) for name in (
        "st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink", "st_size",
        "st_mtime_ns", "st_ctime_ns")}


def metadata_snapshot(root):
    # No atime assertion: read-only inspection may update access time.
    result = {".": entry_metadata(root)}
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            result[str(path.relative_to(root))] = entry_metadata(path)
    return result


class HandoffInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="asset-handoff-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.ownership = self.root / "owned.json"
        self.resolved = self.root / "resolved.json"
        self.public = self.root / "public/manifest.json"
        self.site = self.root / "site/manifest.json"
        self.old = self.root / "site/skills/workflow"
        self.new = self.root / "public/skills/workflow"
        self.adapter = self.root / "site/skills/site-adapter"
        self.skill(self.root / "public/skills/common-tool")
        self.skill(self.old)
        self.legacy_digest = hashlib.sha256((self.old / "SKILL.md").read_bytes()).hexdigest()
        self.destinations = {"claude": {"skills": str(self.home / ".claude/skills")},
                             "codex": {"skills": str(self.home / ".agents/skills")}}
        self.public_v1 = {"version": 1, "destinations": self.destinations,
                          "skill_groups": [{"name": "common", "path": str(self.root / "public/skills"),
                                            "targets": ["claude", "codex"]}]}
        self.site_v1 = {"version": 1, "skill_groups": [{"name": "site", "path": str(self.root / "site/skills"),
                                                       "targets": ["claude", "codex"]}]}
        write_json(self.public, self.public_v1)
        write_json(self.site, self.site_v1)
        write_json(self.resolved, {"resources": {"agent_asset_manifests": [str(self.public), str(self.site)]}})
        write_json(self.state, {"features": {"agent-assets": {"enabled": True}}})
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "sentinel").write_text("outside fixture operation scope\n")

    def skill(self, path):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {path.name}\ndescription: Synthetic fixture skill.\n---\nFixture.\n")

    def targets(self, name="workflow"):
        return [self.home / harness / "skills" / name for harness in (".claude", ".agents")]

    def run_assets(self, action="converge", *, dry=False, fault=None, platform="linux", default_ownership=False,
                   xdg_state_home=None, without_manifests=False):
        environment = FIXTURE.asset_environment(self.home, self.public, self.state, self.ownership, platform)
        environment.update({"ENVY_AGENT_ASSETS_MANIFEST": "", "ENVY_MANIFEST": str(self.resolved),
                            "TEST_AGENT_ASSETS_ROOT": str(self.root)})
        if without_manifests:
            environment.pop("ENVY_AGENT_ASSETS_MANIFEST")
            environment.pop("ENVY_MANIFEST")
        arguments = [sys.executable, "-B"]
        arguments += [str(DRIVER), str(SOURCE), fault] if fault else [str(SOURCE)]
        arguments += [action, "--state", str(self.state), "--json"]
        if default_ownership:
            environment.pop("ENVY_AGENT_ASSETS_OWNERSHIP")
            if xdg_state_home is not None:
                environment["XDG_STATE_HOME"] = xdg_state_home
        else:
            arguments += ["--ownership", str(self.ownership)]
        if dry:
            arguments.append("--dry-run")
        return subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, timeout=15)

    def install_old(self):
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))

    def activate(self):
        self.skill(self.new)
        self.skill(self.adapter)
        public = {**self.public_v1, "version": 2,
                  "skill_contracts": [{"group": "common", "name": "workflow", "version": 1}]}
        site = {**self.site_v1, "version": 2,
                "skill_groups": [{**self.site_v1["skill_groups"][0], "exclude_names": ["workflow"]}],
                "skill_handoffs": [{"group": "site", "name": "workflow",
                    "replacement": {"group": "common", "version": 1}, "adapter": "site-adapter",
                    "legacy_files": {"SKILL.md": self.legacy_digest}}]}
        write_json(self.public, public)
        write_json(self.site, site)

    def activate_protected_only(self, *, public_only=False):
        self.activate()
        public = json.loads(self.public.read_text())
        public["skill_groups"][0]["exclude_names"] = ["common-tool"]
        write_json(self.public, public)
        if public_only:
            write_json(self.resolved, {"resources": {"agent_asset_manifests": [str(self.public)]}})

    def ledger(self):
        return json.loads(self.ownership.read_text())

    def assert_refusal_unchanged(self, **kwargs):
        before = FIXTURE.snapshot(self.root)
        metadata = metadata_snapshot(self.root)
        result = self.run_assets(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        return result

    def assert_no_staging_reads(self, result):
        records = [json.loads(line)["staging_read_witness"] for line in result.stderr.splitlines()
                   if line.startswith(b'{"staging_read_witness":')]
        self.assertEqual(len(records), 1, result.stderr)
        self.assertEqual(records[0], {"following_stat_attempts": 0, "content_open_attempts": 0,
                         "scanners": {name: 0 for name in (
                             "link_report", "destination_metadata_issues", "audit_roots_report", "plugin_report")}})
        self.assertIn(b"unexplained staging entry requires reconciliation", result.stdout + result.stderr)
        self.assertNotIn(b"outside-read-witness", result.stdout + result.stderr)

    def prepare_checkpoint_boundary(self, fault):
        result = self.run_assets(fault=fault)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)["checkpoint_boundary"]
        self.assertEqual(record["limit"], 16 * 1024 * 1024)
        self.assertGreater(record["padding_bytes"], 0)
        self.assertLess(record["prior"], record["pending"])
        self.assertLessEqual(record["pending"], record["limit"])
        self.assertEqual(record["admitted_mode"], "pending" if fault == "prepare-oversize-pending" else "legacy")
        return record

    def assert_checkpoint_refusal_unchanged(self):
        before = FIXTURE.snapshot(self.root)
        metadata = metadata_snapshot(self.root)
        result = self.run_assets()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        # Compare the full bytes, but do not construct a 16 MiB assertion diff.
        self.assertTrue(FIXTURE.snapshot(self.root) == before, "checkpoint refusal changed fixture content")
        self.assertEqual(metadata_snapshot(self.root), metadata)
        return result

    def assert_complete(self):
        value = self.ledger()
        self.assertEqual(value["version"], 2)
        self.assertEqual(value["handoff"]["state"], "complete")
        for target in self.targets():
            self.assertEqual(os.readlink(target), str(self.new))
            self.assertEqual(value["links"][str(target)], str(self.new))
        for target in self.targets("site-adapter"):
            self.assertEqual(os.readlink(target), str(self.adapter))
        self.assertEqual(hashlib.sha256((self.old / "SKILL.md").read_bytes()).hexdigest(), self.legacy_digest)

    def ordinary_fixture(self, *, single=False):
        fixture = type(self)("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        declaration = {**fixture.public_v1, "version": 2}
        if single:
            declaration["skill_groups"] = [{**declaration["skill_groups"][0], "targets": ["claude"]}]
        write_json(fixture.public, declaration)
        write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
        result = fixture.run_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(fixture.ledger()["handoff"])
        return fixture

    def move_ordinary_source(self):
        moved = self.root / "ordinary-next/skills"
        self.skill(moved / "common-tool")
        declaration = json.loads(self.public.read_text())
        declaration["skill_groups"][0]["path"] = str(moved)
        write_json(self.public, declaration)
        return moved / "common-tool"

    def ordinary_capacity(self, fault):
        result = self.run_assets(fault=fault)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["ordinary_capacity"]

    def test_control_paths_refuse_launcher_overlap_without_changes(self):
        for relation in ("equal", "ancestor", "descendant", "lock-ancestor"):
            with self.subTest(relation=relation):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.activate_protected_only(public_only=True)
                launcher = fixture.home / ".local/bin/agent-assets"
                fixture.ownership = {"equal": launcher, "ancestor": launcher.parent,
                                     "descendant": launcher / "owned.json",
                                     "lock-ancestor": fixture.root / "launcher-container"}[relation]
                if relation == "lock-ancestor":
                    fixture.home = fixture.root / "launcher-container.lock/home"
                    fixture.home.mkdir(parents=True)
                for action, dry in (("converge", False), ("converge", True), ("status", False),
                                    ("verify", False), ("doctor", False), ("remove", True)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                    self.assertIn(b"ownership control path overlaps managed destination", result.stdout + result.stderr)

    def test_control_paths_refuse_asset_overlap_without_changes(self):
        for kind in ("ordinary", "provider", "adapter", "directory", "descendant", "lock-directory", "missing-provider"):
            with self.subTest(kind=kind):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.activate()
                target = fixture.targets()[0]
                fixture.ownership = {
                    "ordinary": fixture.targets("common-tool")[0], "provider": target,
                    "adapter": fixture.targets("site-adapter")[0], "directory": target.parent,
                    "descendant": target / "owned.json", "lock-directory": fixture.root / "skill-store",
                    "missing-provider": target}[kind]
                if kind == "lock-directory":
                    declaration = json.loads(fixture.public.read_text())
                    declaration["destinations"]["claude"]["skills"] = str(fixture.root / "skill-store.lock")
                    write_json(fixture.public, declaration)
                if kind == "missing-provider":
                    fixture.new.rename(fixture.root / "unavailable-provider")
                for action, dry in (("converge", False), ("converge", True), ("status", False),
                                    ("verify", False), ("doctor", False)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                    self.assertIn(b"ownership control path overlaps managed destination", result.stdout + result.stderr)

    def test_control_paths_refuse_recorded_overlap_without_selection(self):
        for control in ("ownership", "lock"):
            for relation in ("equal", "ancestor", "descendant"):
                with self.subTest(control=control, relation=relation):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    path = fixture.ownership if control == "ownership" else fixture.ownership.with_name("owned.json.lock")
                    target = {"equal": path, "ancestor": path / "payload", "descendant": path.parent}[relation]
                    write_json(fixture.ownership, {"version": 2, "manifests": [str(fixture.public)],
                               "links": {str(target): str(fixture.old)},
                               "launcher": str(fixture.home / ".local/bin/agent-assets"), "handoff": None})
                    fixture.public.unlink()
                    fixture.site.unlink()
                    for dry in (False, True):
                        result = fixture.assert_refusal_unchanged(action="remove", dry=dry, without_manifests=True)
                        self.assertIn(b"ownership control path overlaps managed destination", result.stdout + result.stderr)
                        self.assertIn(str(target).encode(), result.stdout + result.stderr)

    def test_pending_payload_control_collision_refuses_without_selection(self):
        fixture = self.ordinary_fixture(single=True)
        fixture.skill(fixture.public.parent / "skills/added-tool")
        stopped = fixture.run_assets(fault="ordinary-after-intent")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        value = fixture.ledger()
        target = next(step["target"] for step in value["ordinary_operation"]["plan"]["steps"]
                      if step["kind"] == "link")
        self.assertNotIn(target, value["links"])
        fixture.ownership = Path(target)
        write_json(fixture.ownership, value)
        fixture.public.unlink()
        fixture.site.unlink()
        for action in ("converge", "remove", "status", "verify", "doctor"):
            result = fixture.assert_refusal_unchanged(action=action, without_manifests=True)
            self.assertIn(b"ownership control path overlaps managed destination", result.stdout + result.stderr)

    def test_control_path_siblings_remain_usable(self):
        for sibling in ("launcher", "asset"):
            with self.subTest(sibling=sibling):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.activate_protected_only(public_only=True)
                parent = fixture.home / ".local/bin" if sibling == "launcher" else fixture.targets()[0].parent
                fixture.ownership = parent / "owned.json"
                result = fixture.run_assets()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.run_assets("verify").returncode, 0)
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                result = fixture.run_assets("remove", dry=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_launcher_destination_overlap_refuses_before_mutation(self):
        for harness in ("claude", "codex"):
            for kind in ("skills", "agents"):
                for relation in ("equal", "ancestor", "descendant"):
                    with self.subTest(harness=harness, kind=kind, relation=relation):
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        self.addCleanup(fixture.doCleanups)
                        fixture.activate_protected_only(public_only=True)
                        launcher = fixture.home / ".local/bin/agent-assets"
                        target = {"equal": launcher, "ancestor": launcher.parent,
                                  "descendant": launcher / "nested"}[relation]
                        declaration = json.loads(fixture.public.read_text())
                        declaration["destinations"][harness][kind] = str(target)
                        write_json(fixture.public, declaration)
                        for action, dry in (("converge", False), ("converge", True),
                                            ("status", False), ("verify", False), ("doctor", False)):
                            result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                            self.assertEqual(result.returncode, 2)
                            self.assertIn(b"launcher path overlaps managed asset destination", result.stdout)
                        self.assertFalse(fixture.ownership.exists())
                        self.assertFalse(fixture.ownership.with_name("owned.json.lock").exists())
                        self.assertFalse(launcher.parent.exists())

    def test_launcher_recorded_payload_overlap_refuses_without_selection(self):
        for relation in ("equal", "ancestor", "descendant"):
            with self.subTest(relation=relation):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                launcher = fixture.home / ".local/bin/agent-assets"
                launcher.parent.mkdir(parents=True)
                if relation == "descendant":
                    launcher.mkdir()
                    target = launcher / "foreign-asset"
                    target.write_text("preserve foreign nested content\n")
                else:
                    launcher.write_text("preserve foreign launcher content\n")
                    target = launcher if relation == "equal" else launcher.parent
                write_json(fixture.ownership, {"version": 2, "manifests": [str(fixture.public)],
                           "links": {str(target): str(fixture.old)}, "launcher": str(launcher), "handoff": None})
                fixture.public.unlink()
                fixture.site.unlink()
                for action, dry in (("remove", False), ("remove", True), ("converge", False),
                                    ("status", False), ("verify", False), ("doctor", False)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry, without_manifests=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"launcher path overlaps managed asset destination", result.stdout)
                    self.assertIn(str(target).encode(), result.stdout)

    def test_launcher_pending_payload_overlap_refuses_without_selection(self):
        for relation in ("equal", "ancestor", "descendant"):
            with self.subTest(relation=relation):
                fixture = self.ordinary_fixture(single=True)
                fixture.skill(fixture.public.parent / "skills/added-tool")
                stopped = fixture.run_assets(fault="ordinary-after-intent")
                self.assertEqual(stopped.returncode, 71, stopped.stderr)
                value = fixture.ledger()
                launcher = fixture.home / ".local/bin/agent-assets"
                target = {"equal": launcher, "ancestor": launcher.parent,
                          "descendant": launcher / "nested"}[relation]
                record = value["ordinary_operation"]
                step = next(item for item in record["plan"]["steps"] if item["kind"] == "link")
                self.assertEqual(step["before"], {"kind": "absent"})
                step["target"] = str(target)
                step["stage"] = f".{target.name}.agent-assets-ordinary-" + "a" * 24
                record["plan_sha256"] = hashlib.sha256(json.dumps(
                    record["plan"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                write_json(fixture.ownership, value)
                fixture.public.unlink()
                for action in ("converge", "remove", "status", "verify", "doctor"):
                    result = fixture.assert_refusal_unchanged(action=action)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"launcher path overlaps managed asset destination", result.stdout)

    def test_launcher_sibling_destinations_and_own_record_remain_valid(self):
        self.activate_protected_only(public_only=True)
        launcher = self.home / ".local/bin/agent-assets"
        declaration = json.loads(self.public.read_text())
        for harness in ("claude", "codex"):
            declaration["destinations"][harness]["skills"] = str(launcher.parent / (harness + "-skills"))
        write_json(self.public, declaration)
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ledger()["launcher"], str(launcher))
        self.assertEqual(self.run_assets("verify").returncode, 0)
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.run_assets("remove", dry=True).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        self.assertEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(self.ledger()["launcher"], str(launcher))
        self.assertEqual(self.run_assets().returncode, 0)

    def test_asset_destination_namespace_overlaps_refuse_before_mutation(self):
        cases = (("skills", "skills", False), ("skills", "skills", True),
                 ("skills", "agents", False), ("agents", "skills", True),
                 ("agents", "agents", False))
        for outer_kind, inner_kind, reverse in cases:
            for protected in (False, True):
                with self.subTest(outer_kind=outer_kind, inner_kind=inner_kind,
                                  reverse=reverse, protected=protected):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    if protected:
                        fixture.activate_protected_only(public_only=True)
                        declaration = json.loads(fixture.public.read_text())
                    else:
                        declaration = {"version": 2, "destinations": {}, "skill_groups": []}
                    outer = fixture.home / "selected-assets" / ("alpha" if outer_kind == "skills" else "alpha.md")
                    inner = outer / "nested" / ("beta" if inner_kind == "skills" else "beta.md")
                    entries = [("alpha", outer_kind, outer), ("beta", inner_kind, inner)]
                    for name, kind, target in reversed(entries) if reverse else entries:
                        source = fixture.root / ("ordinary-" + name)
                        if kind == "skills":
                            fixture.skill(source / name)
                        else:
                            source.mkdir()
                            (source / (name + ".md")).write_text(
                                f"---\nname: {name}\ndescription: Synthetic fixture agent.\n---\nFixture.\n")
                        declaration["destinations"][name] = {kind: str(target.parent)}
                        key = "skill_groups" if kind == "skills" else "agent_groups"
                        declaration.setdefault(key, []).append(
                            {"name": "ordinary-" + name, "path": str(source), "targets": [name]})
                    write_json(fixture.public, declaration)
                    write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                    for action, dry in (("converge", False), ("converge", True), ("status", False)):
                        result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(b"managed asset destinations overlap", result.stdout)
                    for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                                 outer, inner, *fixture.targets(), fixture.home / ".local/bin"):
                        self.assertFalse(os.path.lexists(path), str(path))

    def test_asset_destination_cannot_replace_required_directory(self):
        for kind in ("skills", "agents"):
            for relation in ("equal", "descendant"):
                with self.subTest(kind=kind, relation=relation):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    source = fixture.root / "ordinary-alpha"
                    target = fixture.home / "selected-assets" / ("alpha" if kind == "skills" else "alpha.md")
                    if kind == "skills":
                        fixture.skill(source / "alpha")
                    else:
                        source.mkdir()
                        (source / "alpha.md").write_text(
                            "---\nname: alpha\ndescription: Synthetic fixture agent.\n---\nFixture.\n")
                    directory = target if relation == "equal" else target / "nested"
                    unused_kind = "agents" if kind == "skills" else "skills"
                    key = "skill_groups" if kind == "skills" else "agent_groups"
                    declaration = {"version": 2,
                                   "destinations": {"alpha": {kind: str(target.parent)},
                                                    "unused": {unused_kind: str(directory)}},
                                   key: [{"name": "ordinary-alpha", "path": str(source), "targets": ["alpha"]}]}
                    write_json(fixture.public, declaration)
                    write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                    for dry in (False, True):
                        result = fixture.assert_refusal_unchanged(dry=dry)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(b"managed asset destination overlaps required directory", result.stdout)
                    for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                                 target, directory, fixture.home / ".local/bin"):
                        self.assertFalse(os.path.lexists(path), str(path))

    def test_canonical_asset_destination_aliases_refuse_before_mutation(self):
        cases = (("Selected-Assets", "selected-assets", "nested", False),
                 ("Selected-Assets", "selected-assets", "equal", False),
                 ("Caf\u00e9", "Cafe\u0301", "nested", False),
                 ("Stra\u00dfe", "STRASSE", "equal", False),
                 ("Selected-Assets", "selected-assets", "nested", True))
        for outer_root, inner_root, relation, protected in cases:
            with self.subTest(roots=(outer_root, inner_root), relation=relation, protected=protected):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                if protected:
                    fixture.activate_protected_only(public_only=True)
                    declaration = json.loads(fixture.public.read_text())
                else:
                    declaration = {"version": 2, "destinations": {}, "skill_groups": []}
                outer = fixture.home / outer_root / "alpha"
                inner = fixture.home / inner_root / "alpha"
                if relation == "nested":
                    inner = inner / "nested/beta"
                for harness, target in (("left", outer), ("right", inner)):
                    source = fixture.root / ("ordinary-" + harness)
                    fixture.skill(source / target.name)
                    declaration["destinations"][harness] = {"skills": str(target.parent)}
                    declaration["skill_groups"].append(
                        {"name": "ordinary-" + harness, "path": str(source), "targets": [harness]})
                write_json(fixture.public, declaration)
                write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                for action, dry in (("converge", False), ("converge", True), ("status", False)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"managed asset destinations overlap", result.stdout)
                for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                             outer, inner, *fixture.targets(), fixture.home / ".local/bin"):
                    self.assertFalse(os.path.lexists(path), str(path))

    def test_canonical_required_directory_aliases_refuse_before_mutation(self):
        for outer_root, alias_root in (("Selected-Assets", "selected-assets"),
                                       ("Caf\u00e9", "Cafe\u0301"), ("Stra\u00dfe", "STRASSE")):
            for relation in ("equal", "descendant"):
                with self.subTest(roots=(outer_root, alias_root), relation=relation):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    source = fixture.root / "ordinary-alpha"
                    fixture.skill(source / "alpha")
                    target = fixture.home / outer_root / "alpha"
                    directory = fixture.home / alias_root / "alpha"
                    if relation == "descendant":
                        directory = directory / "nested"
                    declaration = {"version": 2,
                                   "destinations": {"alpha": {"skills": str(target.parent)},
                                                    "unused": {"agents": str(directory)}},
                                   "skill_groups": [{"name": "ordinary-alpha", "path": str(source),
                                                     "targets": ["alpha"]}]}
                    write_json(fixture.public, declaration)
                    write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                    for dry in (False, True):
                        result = fixture.assert_refusal_unchanged(dry=dry)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(b"managed asset destination overlaps required directory", result.stdout)
                    for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                                 target, directory, fixture.home / ".local/bin"):
                        self.assertFalse(os.path.lexists(path), str(path))

    def test_canonical_control_and_launcher_aliases_refuse_before_mutation(self):
        for control in ("ownership", "lock", "launcher"):
            with self.subTest(control=control):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                declaration = {**fixture.public_v1, "version": 2,
                               "destinations": {**fixture.destinations}}
                if control == "launcher":
                    directory = fixture.home / ".LOCAL/BIN/agent-assets"
                    diagnostic = b"launcher path overlaps managed asset destination"
                else:
                    fixture.ownership = fixture.home / "Selected-Assets/owned.json"
                    directory = fixture.home / "selected-assets" / (
                        "owned.json.lock" if control == "lock" else "owned.json")
                    diagnostic = b"ownership control path overlaps managed destination"
                declaration["destinations"]["unused"] = {"skills": str(directory)}
                write_json(fixture.public, declaration)
                write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                for action, dry in (("converge", False), ("converge", True), ("status", False)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(diagnostic, result.stdout)
                for path in (fixture.ownership, fixture.ownership.with_name(fixture.ownership.name + ".lock"),
                             directory, fixture.home / ".local/bin"):
                    self.assertFalse(os.path.lexists(path), str(path))

    def test_asset_destination_namespace_siblings_and_containers_remain_valid(self):
        for arrangement in ("siblings", "shared-root", "nested-roots", "case-shared-root",
                            "case-nested-roots", "canonical-shared-root"):
            with self.subTest(arrangement=arrangement):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                root = fixture.home / "selected-assets"
                roots = {"siblings": (root / "left", root / "right"),
                         "shared-root": (root, root), "nested-roots": (root, root / "nested"),
                         "case-shared-root": (fixture.home / "Selected-Assets", root),
                         "case-nested-roots": (fixture.home / "Selected-Assets", root / "nested"),
                         "canonical-shared-root": (fixture.home / "Caf\u00e9", fixture.home / "Cafe\u0301")}[arrangement]
                declaration = {"version": 2, "destinations": {}, "skill_groups": []}
                expected = {}
                for name, directory in zip(("alpha", "beta"), roots):
                    source = fixture.root / ("ordinary-" + name)
                    fixture.skill(source / name)
                    declaration["destinations"][name] = {"skills": str(directory)}
                    declaration["skill_groups"].append(
                        {"name": "ordinary-" + name, "path": str(source), "targets": [name]})
                    expected[str(directory / name)] = str(source / name)
                write_json(fixture.public, declaration)
                write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                result = fixture.run_assets()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.ledger()["links"], expected)
                for target, source in expected.items():
                    self.assertEqual(os.readlink(target), source)
                self.assertEqual(fixture.run_assets("verify").returncode, 0)
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                for action, dry in (("converge", False), ("remove", True)):
                    result = fixture.run_assets(action=action, dry=dry)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_destination_source_claims_conflict_across_harnesses(self):
        for kind in ("skills", "agents"):
            for reverse in (False, True):
                with self.subTest(kind=kind, reverse=reverse):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    directory = fixture.home / "selected-assets"
                    target = directory / ("shared" if kind == "skills" else "shared.md")
                    declaration = {"version": 2, "destinations": {}, "skill_groups": []}
                    harnesses = ("left", "right")
                    for harness in reversed(harnesses) if reverse else harnesses:
                        source = fixture.root / ("source-" + harness)
                        if kind == "skills":
                            fixture.skill(source / "shared")
                        else:
                            source.mkdir()
                            (source / "shared.md").write_text(
                                "---\nname: shared\ndescription: Synthetic fixture agent.\n---\nFixture.\n")
                        declaration["destinations"][harness] = {kind: str(directory)}
                        key = "skill_groups" if kind == "skills" else "agent_groups"
                        declaration.setdefault(key, []).append(
                            {"name": "source-" + harness, "path": str(source), "targets": [harness]})
                    write_json(fixture.public, declaration)
                    write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                    for action, dry in (("converge", False), ("converge", True), ("status", False)):
                        result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(b"managed asset destination has conflicting sources", result.stdout)
                    for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                                 target, *fixture.targets(), fixture.home / ".local/bin"):
                        self.assertFalse(os.path.lexists(path), str(path))

    def test_destination_source_claims_cannot_replace_protected_pair(self):
        for extra in ("a-extra", "z-extra"):
            with self.subTest(extra=extra):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.activate_protected_only(public_only=True)
                declaration = json.loads(fixture.public.read_text())
                source = fixture.root / "competing-skills"
                fixture.skill(source / "workflow")
                declaration["destinations"][extra] = {
                    "skills": declaration["destinations"]["claude"]["skills"]}
                declaration["skill_groups"].append(
                    {"name": extra, "path": str(source), "targets": [extra]})
                write_json(fixture.public, declaration)
                for action, dry in (("converge", False), ("converge", True), ("status", False)):
                    result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"managed asset destination has conflicting sources", result.stdout)
                for path in (fixture.ownership, fixture.ownership.with_name("owned.json.lock"),
                             *fixture.targets(), fixture.home / ".local/bin"):
                    self.assertFalse(os.path.lexists(path), str(path))

    def test_destination_source_claims_same_source_sharing_remains_valid(self):
        for arrangement in ("one-group", "two-groups"):
            with self.subTest(arrangement=arrangement):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                directory = fixture.home / "selected-assets"
                source = fixture.root / "shared-source"
                fixture.skill(source / "shared")
                declaration = {"version": 2, "destinations": {
                    harness: {"skills": str(directory)} for harness in ("left", "right")},
                    "skill_groups": []}
                groups = (("shared", ["left", "right"]),) if arrangement == "one-group" else (
                    ("left", ["left"]), ("right", ["right"]))
                for name, targets in groups:
                    declaration["skill_groups"].append(
                        {"name": name, "path": str(source), "targets": targets})
                write_json(fixture.public, declaration)
                write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                result = fixture.run_assets()
                self.assertEqual(result.returncode, 0, result.stderr)
                target = directory / "shared"
                self.assertEqual(fixture.ledger()["links"], {str(target): str(source / "shared")})
                self.assertEqual(os.readlink(target), str(source / "shared"))
                self.assertEqual(fixture.run_assets("verify").returncode, 0)
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                for action, dry in (("converge", False), ("remove", True)):
                    result = fixture.run_assets(action=action, dry=dry)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_protected_same_source_claims_install_complete_units_once(self):
        for extra in ("a-extra", "z-extra"):
            for harness in ("claude", "codex"):
                for reverse in (False, True):
                    for adapter in (False, True):
                        with self.subTest(extra=extra, harness=harness, reverse=reverse, adapter=adapter):
                            fixture = type(self)("runTest")
                            fixture.setUp()
                            self.addCleanup(fixture.doCleanups)
                            fixture.activate_protected_only(public_only=not adapter)
                            declaration = json.loads(fixture.public.read_text())
                            if reverse:
                                declaration["skill_groups"][0]["targets"].reverse()
                                if adapter:
                                    site = json.loads(fixture.site.read_text())
                                    site["skill_groups"][0]["targets"].reverse()
                                    write_json(fixture.site, site)
                            name = "site-adapter" if adapter else "workflow"
                            source = fixture.adapter if adapter else fixture.new
                            declaration["destinations"][extra] = {
                                "skills": declaration["destinations"][harness]["skills"]}
                            declaration["skill_groups"].append({
                                "name": extra, "path": str(source.parent), "targets": [extra],
                                "exclude_names": ["workflow" if adapter else "common-tool"]})
                            write_json(fixture.public, declaration)
                            # A third write to this two-target unit must never occur.
                            result = fixture.run_assets(fault=f"after-{name}-3")
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            for target in fixture.targets(name):
                                self.assertEqual(os.readlink(target), str(source))
                                self.assertEqual(fixture.ledger()["links"][str(target)], str(source))
                            self.assertEqual(fixture.run_assets("verify").returncode, 0)
                            before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                            self.assertEqual(fixture.run_assets().returncode, 0)
                            self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                            self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_overlapping_declared_protected_units_merge_transitively(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.activate_protected_only(public_only=True)
                declaration = json.loads(fixture.public.read_text())
                for harness in ("third", "fourth"):
                    declaration["destinations"][harness] = {"skills": str(fixture.home / harness / "skills")}
                for group, targets in (("overlap", ["codex", "third"]), ("tail", ["third", "fourth"])):
                    declaration["skill_groups"].append({
                        "name": group, "path": str(fixture.new.parent), "targets": targets,
                        "exclude_names": ["common-tool"]})
                    declaration["skill_contracts"].append({"group": group, "name": "workflow", "version": 1})
                if reverse:
                    declaration["skill_contracts"].reverse()
                    for group in declaration["skill_groups"]:
                        group["targets"].reverse()
                write_json(fixture.public, declaration)
                result = fixture.run_assets(fault="after-workflow-5")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                targets = [Path(value["skills"]) / "workflow" for value in declaration["destinations"].values()]
                self.assertEqual(fixture.ledger()["links"], {str(target): str(fixture.new) for target in targets})
                self.assertTrue(all(os.readlink(target) == str(fixture.new) for target in targets))
                self.assertEqual(fixture.run_assets("verify").returncode, 0)

    def test_recorded_payload_namespace_collisions_refuse_before_mutation(self):
        cases = (("Skills", "skills", False), ("Skills", "skills", True),
                 ("Caf\u00e9", "Cafe\u0301", False), ("Stra\u00dfe", "STRASSE", False))
        for first, second, nested in cases:
            for version in (1, 2):
                for both_recorded in (False, True):
                    with self.subTest(first=first, second=second, nested=nested,
                                      version=version, both_recorded=both_recorded):
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        self.addCleanup(fixture.doCleanups)
                        source = fixture.root / "public/skills/common-tool"
                        outer = fixture.home / first / "common-tool"
                        inner = fixture.home / second / "common-tool"
                        if nested:
                            inner = inner / "inside/common-tool"
                        else:
                            # On a case-insensitive host both spellings can name one physical link.
                            for target in (outer, inner):
                                target.parent.mkdir(parents=True, exist_ok=True)
                                if not os.path.lexists(target):
                                    target.symlink_to(source)
                        declaration = {"version": 2, "destinations": {"selected": {"skills": str(inner.parent)}},
                                       "skill_groups": [{"name": "selected", "path": str(source.parent),
                                                         "targets": ["selected"]}]}
                        write_json(fixture.public, declaration)
                        write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                        links = {str(outer): str(source)}
                        if both_recorded:
                            links[str(inner)] = str(source)
                        ledger = {"version": version, "manifests": [str(fixture.public)], "links": links,
                                  "launcher": str(fixture.home / ".local/bin/agent-assets")}
                        if version == 2:
                            ledger["handoff"] = None
                        write_json(fixture.ownership, ledger)
                        actions = [("converge", False), ("converge", True), ("status", False)]
                        if both_recorded:
                            actions += [("remove", True), ("remove", False)]
                        for action, dry in actions:
                            result = fixture.assert_refusal_unchanged(
                                action=action, dry=dry, without_manifests=action == "remove")
                            self.assertEqual(result.returncode, 2)
                            self.assertIn(b"managed asset destinations overlap", result.stdout)
                        self.assertNotIn("ordinary_operation", fixture.ledger())
                        self.assertFalse(os.path.lexists(fixture.ownership.with_name("owned.json.lock")))
                        self.assertFalse(os.path.lexists(fixture.home / ".local/bin"))

    def test_pending_payload_namespace_collision_refuses_without_discovery(self):
        fixture = self.ordinary_fixture(single=True)
        fixture.skill(fixture.public.parent / "skills/pending")
        self.assertEqual(fixture.run_assets(fault="ordinary-after-intent").returncode, 71)
        ledger = fixture.ledger()
        record = ledger["ordinary_operation"]
        step = next(item for item in record["plan"]["steps"] if item["kind"] == "link")
        alias = {**step, "target": str(Path(step["target"]).with_name("PENDING")),
                 "stage": ".PENDING.agent-assets-ordinary-" + "a" * 24}
        record["plan"]["steps"].append(alias)
        record["plan_sha256"] = hashlib.sha256(json.dumps(
            record["plan"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        write_json(fixture.ownership, ledger)
        for action in ("converge", "status", "remove"):
            result = fixture.assert_refusal_unchanged(action=action, without_manifests=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"managed asset destinations overlap", result.stdout)

    def test_v2_remove_dry_run_reports_planned_and_preserved_paths(self):
        fixture = self.ordinary_fixture()
        targets = fixture.targets("common-tool")
        launcher = fixture.home / ".local/bin/agent-assets"
        for drift in (False, True):
            with self.subTest(drift=drift):
                if drift:
                    targets[0].unlink()
                    targets[0].symlink_to(fixture.outside)
                    launcher.write_text("foreign launcher\n")
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                result = fixture.run_assets("remove", dry=True)
                self.assertEqual(result.returncode, 1 if drift else 0, result.stderr)
                expected = b"would remove 1 recorded entries; preserve 2 changed entries" if drift else b"would remove 3 recorded entries; preserve 0 changed entries"
                self.assertIn(expected, result.stdout)
                self.assertIn(f"remove link: {targets[1]}".encode(), result.stdout)
                if drift:
                    self.assertIn(f"preserve link: {targets[0]}".encode(), result.stdout)
                    self.assertIn(f"preserve launcher: {launcher}".encode(), result.stdout)
                    self.assertIn(f"[target: {targets[0]}]".encode(), result.stderr)
                    self.assertIn(f"[target: {launcher}]".encode(), result.stderr)
                else:
                    self.assertIn(f"remove launcher: {launcher}".encode(), result.stdout)
                self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_pending_remove_dry_run_excludes_already_removed_entry(self):
        fixture = self.ordinary_fixture(single=True)
        stopped = fixture.run_assets("remove", fault="ordinary-after-unlink")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        fixture.public.unlink()
        fixture.site.unlink()
        before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
        result = fixture.run_assets("remove", dry=True, without_manifests=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"would resume pending ordinary remove", result.stdout)
        self.assertIn(b"would remove 1 recorded entries; preserve 0 changed entries", result.stdout)
        self.assertIn(f"remove launcher: {fixture.home / '.local/bin/agent-assets'}".encode(), result.stdout)
        self.assertNotIn(b"remove link:", result.stdout)
        self.assertEqual(FIXTURE.snapshot(fixture.root), before)
        self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_ordinary_source_move_recovers_checkpoint_phases(self):
        for point in ("after-intent", "after-prepared", "before-rename", "after-rename",
                      "before-progress", "after-progress", "before-complete", "after-complete"):
            with self.subTest(point=point):
                fixture = self.ordinary_fixture()
                moved = fixture.move_ordinary_source()
                launcher = fixture.home / ".local/bin/agent-assets"
                launcher_before = entry_metadata(launcher)
                stopped = fixture.run_assets(fault="ordinary-" + point)
                self.assertNotEqual(stopped.returncode, 0, stopped.stdout)
                result = fixture.run_assets()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("ordinary_operation", fixture.ledger())
                for target in fixture.targets("common-tool"):
                    self.assertEqual(os.readlink(target), str(moved))
                self.assertEqual(entry_metadata(launcher), launcher_before)
                stable = metadata_snapshot(fixture.root)
                self.assertEqual(fixture.run_assets().returncode, 0)
                self.assertEqual(metadata_snapshot(fixture.root), stable)

    def test_ordinary_added_asset_intent_precedes_payload(self):
        fixture = self.ordinary_fixture()
        fixture.skill(fixture.public.parent / "skills/added-tool")
        before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
        failed = fixture.run_assets(fault="ordinary-before-intent")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(FIXTURE.snapshot(fixture.root), before)
        self.assertEqual(metadata_snapshot(fixture.root), metadata)
        stopped = fixture.run_assets(fault="ordinary-after-rename")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        record = fixture.ledger()["ordinary_operation"]
        self.assertIsNotNone(record["prepared"])
        result = fixture.run_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in fixture.targets("added-tool"):
            self.assertEqual(os.readlink(target), str(fixture.public.parent / "skills/added-tool"))

    def test_ordinary_lost_responses_do_not_repeat_renames(self):
        for fault in ("ordinary-lost-rename", "ordinary-lost-complete"):
            with self.subTest(fault=fault):
                fixture = self.ordinary_fixture()
                moved = fixture.move_ordinary_source()
                result = fixture.run_assets(fault=fault)
                self.assertEqual(result.returncode, 0, result.stderr)
                witness = [json.loads(line)["ordinary_witness"] for line in result.stderr.splitlines()
                           if line.startswith(b'{"ordinary_witness":')][0]
                self.assertEqual(witness["rename"], 2)
                self.assertNotIn("ordinary_operation", fixture.ledger())
                self.assertTrue(all(os.readlink(target) == str(moved) for target in fixture.targets("common-tool")))

    def test_ordinary_staging_requires_exact_active_prepared_identity(self):
        for fault in ("ordinary-before-prepared", "ordinary-replaced-stage", "ordinary-extra-stage",
                      "ordinary-missing-stage", "ordinary-wrong-cursor-stage"):
            with self.subTest(fault=fault):
                fixture = self.ordinary_fixture()
                fixture.move_ordinary_source()
                result = fixture.run_assets(fault=fault)
                self.assertNotEqual(result.returncode, 0)
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                for action in ("converge", "status", "verify", "doctor"):
                    refused = fixture.run_assets(action)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    if action == "converge":
                        if fault != "ordinary-missing-stage":
                            self.assertIn(b"unexplained ordinary staging", refused.stdout + refused.stderr)
                    else:
                        self.assertEqual(refused.returncode, 2)
                        self.assertIn(b"ordinary converge is pending; resume agent-assets converge", refused.stdout + refused.stderr)

    def test_ordinary_identical_foreign_postimage_is_not_adopted(self):
        for fault in ("ordinary-identical-foreign", "ordinary-identical-foreign-success"):
            with self.subTest(fault=fault):
                fixture = self.ordinary_fixture()
                fixture.move_ordinary_source()
                result = fixture.run_assets(fault=fault)
                self.assertNotEqual(result.returncode, 0)
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                record = fixture.ledger()["ordinary_operation"]
                target = Path(record["plan"]["steps"][record["cursor"]]["target"])
                self.assertEqual(os.readlink(target), os.readlink(fixture.root / "observed-ordinary-replacement"))
                self.assertNotEqual(entry_metadata(target)["st_ino"],
                                    entry_metadata(fixture.root / "observed-ordinary-replacement")["st_ino"])
                refused = fixture.run_assets()
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(b"prepared object changed", refused.stdout + refused.stderr)
                self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                self.assertEqual(metadata_snapshot(fixture.root), metadata)

    def test_ordinary_future_and_completed_target_drift_refuse(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                fixture = self.ordinary_fixture()
                fixture.move_ordinary_source()
                fault = "ordinary-after-progress" if completed else "ordinary-future-drift"
                self.assertNotEqual(fixture.run_assets(fault=fault).returncode, 0)
                if completed:
                    record = fixture.ledger()["ordinary_operation"]
                    target = Path(record["plan"]["steps"][0]["target"])
                    target.unlink()
                    target.symlink_to(fixture.outside)
                fixture.assert_refusal_unchanged()

    def test_ordinary_remove_resumes_without_selection_or_cross_action(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                if completed:
                    fixture.install_old()
                fixture.activate()
                self.assertEqual(fixture.run_assets().returncode, 0)
                handoff = fixture.ledger()["handoff"]
                stopped = fixture.run_assets("remove", fault="ordinary-after-unlink")
                self.assertEqual(stopped.returncode, 71, stopped.stderr)
                fixture.public.unlink()
                fixture.site.unlink()
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                for action in ("converge", "status", "verify", "doctor"):
                    refused = fixture.run_assets(action)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn(b"ordinary remove is pending", refused.stdout + refused.stderr)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                result = fixture.run_assets("remove")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.ledger()["links"], {})
                self.assertEqual(fixture.ledger()["handoff"], handoff)
                self.assertNotIn("ordinary_operation", fixture.ledger())

    def test_ordinary_stale_unlink_and_lost_delete_response_recover(self):
        for point in ("before-unlink", "after-unlink", "lost-unlink"):
            with self.subTest(point=point):
                fixture = self.ordinary_fixture()
                declaration = json.loads(fixture.public.read_text())
                declaration["skill_groups"][0]["exclude_names"] = ["common-tool"]
                write_json(fixture.public, declaration)
                result = fixture.run_assets(fault="ordinary-" + point)
                self.assertEqual(result.returncode == 0, point == "lost-unlink", result.stderr)
                if point != "lost-unlink":
                    self.assertEqual(fixture.run_assets().returncode, 0)
                self.assertEqual(fixture.ledger()["links"], {})
                self.assertTrue(all(not os.path.lexists(path) for path in fixture.targets("common-tool")))

    def test_ordinary_launcher_prepared_and_removed_progress_recovers(self):
        for point in ("after-prepared", "after-rename", "lost-rename"):
            with self.subTest(point=point):
                fixture = self.ordinary_fixture()
                launcher = fixture.home / ".local/bin/agent-assets"
                expected = launcher.read_bytes()
                launcher.unlink()
                result = fixture.run_assets(fault="ordinary-" + point)
                self.assertEqual(result.returncode == 0, point == "lost-rename", result.stderr)
                self.assertEqual(fixture.run_assets().returncode, 0)
                self.assertEqual(launcher.read_bytes(), expected)
                self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
        fixture = self.ordinary_fixture()
        declaration = json.loads(fixture.public.read_text())
        declaration["skill_groups"] = []
        write_json(fixture.public, declaration)
        self.assertEqual(fixture.run_assets().returncode, 0)
        stopped = fixture.run_assets("remove", fault="ordinary-after-unlink")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        self.assertEqual(fixture.run_assets("remove").returncode, 0)
        self.assertFalse((fixture.home / ".local/bin/agent-assets").exists())

    def test_owned_launcher_mode_drift_is_reported_and_repaired(self):
        for completed in (False, True):
            for mode in (0o644, 0o700):
                with self.subTest(completed=completed, mode=oct(mode)):
                    if completed:
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        self.addCleanup(fixture.doCleanups)
                        fixture.install_old()
                        fixture.activate()
                        self.assertEqual(fixture.run_assets().returncode, 0)
                        fixture.assert_complete()
                    else:
                        fixture = self.ordinary_fixture()
                    launcher = fixture.home / ".local/bin/agent-assets"
                    content, ledger = launcher.read_bytes(), fixture.ledger()
                    links = {path: entry_metadata(Path(path)) for path in ledger["links"]}
                    launcher.chmod(mode)
                    before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                    for action in ("status", "verify", "doctor"):
                        report = fixture.run_assets(action)
                        self.assertEqual(report.returncode, 1, report.stdout + report.stderr)
                        self.assertIn(b"launcher-mode-drift", report.stdout + report.stderr)
                        if action != "verify":
                            self.assertEqual(json.loads(report.stdout)["launcher"]["status"], "mode-drift")
                        self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                        self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    dry = fixture.run_assets(dry=True)
                    self.assertEqual(dry.returncode, 0, dry.stderr)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    repaired = fixture.run_assets(fault="ordinary-size-witness")
                    self.assertEqual(repaired.returncode, 0, repaired.stderr)
                    witness = [json.loads(line)["ordinary_witness"] for line in repaired.stderr.splitlines()
                               if line.startswith(b'{"ordinary_witness":')][0]
                    self.assertEqual(witness["rename"], 1)
                    self.assertGreater(witness["checkpoint"], 0)
                    self.assertEqual(launcher.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(launcher.lstat().st_mode), 0o755)
                    self.assertEqual(fixture.ledger(), ledger)
                    self.assertEqual({path: entry_metadata(Path(path)) for path in links}, links)
                    before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                    noop = fixture.run_assets(fault="ordinary-size-witness")
                    self.assertEqual(noop.returncode, 0, noop.stderr)
                    witness = [json.loads(line)["ordinary_witness"] for line in noop.stderr.splitlines()
                               if line.startswith(b'{"ordinary_witness":')][0]
                    self.assertEqual(witness, {"checkpoint": 0, "rename": 0, "unlink": 0, "prepared_bytes": []})
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    self.assertEqual(fixture.run_assets("verify").returncode, 0)
        # Existing exact-limit construction: a true no-op needs no extra headroom,
        # but a mode repair must admit its ordinary checkpoints before staging.
        self.install_old()
        self.activate()
        boundary = self.prepare_checkpoint_boundary("prepare-complete-at-limit")
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.ownership.stat().st_size, boundary["limit"])
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertTrue(FIXTURE.snapshot(self.root) == before, "no-op changed exact-limit ownership")
        self.assertEqual(metadata_snapshot(self.root), metadata)
        (self.home / ".local/bin/agent-assets").chmod(0o644)
        refused = self.assert_checkpoint_refusal_unchanged()
        self.assertIn(b"ownership checkpoint exceeds size limit", refused.stdout + refused.stderr)

    def test_launcher_mode_repair_preserves_foreign_entries(self):
        for kind in ("writable", "content", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                fixture = self.ordinary_fixture()
                launcher = fixture.home / ".local/bin/agent-assets"
                if kind == "writable":
                    launcher.chmod(0o666)
                elif kind == "content":
                    launcher.write_text("Foreign launcher: preserve.\n")
                    launcher.chmod(0o644)
                elif kind == "symlink":
                    copied = fixture.outside / "same-launcher"
                    copied.write_bytes(launcher.read_bytes())
                    copied.chmod(0o755)
                    launcher.unlink()
                    launcher.symlink_to(copied)
                else:
                    os.link(launcher, fixture.outside / "same-launcher")
                for action, dry in (("converge", False), ("converge", True), ("status", False),
                                    ("verify", False), ("doctor", False)):
                    fixture.assert_refusal_unchanged(action=action, dry=dry)
        fixture = self.ordinary_fixture()
        launcher = fixture.home / ".local/bin/agent-assets"
        launcher.chmod(0o644)
        before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
        self.assertEqual(fixture.run_assets("remove", dry=True).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(fixture.root), before)
        self.assertEqual(metadata_snapshot(fixture.root), metadata)
        self.assertEqual(fixture.run_assets("remove").returncode, 0)
        self.assertFalse(os.path.lexists(launcher))
        self.assertEqual(fixture.ledger()["links"], {})

    def test_pending_ordinary_launcher_mode_drift_refuses(self):
        for point in ("untouched", "future", "prepared", "renamed", "completed"):
            with self.subTest(point=point):
                fixture = self.ordinary_fixture()
                launcher = fixture.home / ".local/bin/agent-assets"
                if point in {"untouched", "future"}:
                    fixture.move_ordinary_source()
                    fault = "ordinary-after-intent"
                else:
                    fault = {"prepared": "ordinary-after-prepared", "renamed": "ordinary-after-rename",
                             "completed": "ordinary-after-progress"}[point]
                if point != "untouched":
                    launcher.chmod(0o644)
                stopped = fixture.run_assets(fault=fault)
                self.assertEqual(stopped.returncode, 71, stopped.stderr)
                record = fixture.ledger()["ordinary_operation"]
                steps = record["plan"]["steps"]
                if point == "untouched":
                    self.assertTrue(all(step["kind"] == "link" for step in steps))
                elif point == "future":
                    self.assertEqual(steps[-1]["kind"], "launcher")
                    self.assertGreater(len(steps) - 1, record["cursor"])
                    self.assertEqual(steps[-1]["before"]["mode"], 0o644)
                else:
                    self.assertEqual(len(steps), 1)
                    self.assertEqual(steps[0]["kind"], "launcher")
                    self.assertEqual(record["cursor"], 1 if point == "completed" else 0)
                changed = launcher.with_name(steps[0]["stage"]) if point == "prepared" else launcher
                changed.chmod(0o700 if point == "future" else 0o644)
                refused = fixture.assert_refusal_unchanged()
                self.assertEqual(fixture.ledger()["ordinary_operation"], record)
                if point == "untouched":
                    self.assertIn(b"ordinary untouched launcher changed", refused.stdout + refused.stderr)

    def test_legacy_handoff_requires_current_launcher_mode(self):
        self.install_old()
        launcher = self.home / ".local/bin/agent-assets"
        launcher.chmod(0o644)
        self.activate()
        for dry in (False, True):
            self.assert_refusal_unchanged(dry=dry)
        self.assertEqual(self.ledger()["version"], 1)
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))
        launcher.chmod(0o755)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assert_complete()

    def test_mixed_upgrade_prepares_ordinary_links_before_handoff(self):
        self.install_old()
        self.activate()
        self.skill(self.root / "public/skills/added-tool")
        before = FIXTURE.snapshot(self.root)
        metadata = metadata_snapshot(self.root)
        dry = self.run_assets(dry=True)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn(b"prepare 2 ordinary link updates before handoff", dry.stdout)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_complete()
        for target in self.targets("added-tool"):
            self.assertEqual(os.readlink(target), str(self.root / "public/skills/added-tool"))
        self.assertEqual(self.ledger()["handoff"]["plan"]["prior_ownership"]["version"], 2)
        before = FIXTURE.snapshot(self.root)
        metadata = metadata_snapshot(self.root)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.run_assets("verify").returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)

    def test_mixed_upgrade_resumes_preparation_and_handoff(self):
        for point in ("ordinary-after-intent", "ordinary-after-prepared", "ordinary-after-rename",
                      "ordinary-after-progress", "ordinary-after-complete", "after-pending"):
            with self.subTest(point=point):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.install_old()
                fixture.activate()
                fixture.skill(fixture.root / "public/skills/added-tool")
                stopped = fixture.run_assets(fault=point)
                self.assertEqual(stopped.returncode, 71, stopped.stdout + stopped.stderr)
                self.assertTrue(all(os.readlink(path) == str(fixture.old) for path in fixture.targets()))
                before = FIXTURE.snapshot(fixture.root)
                dry = fixture.run_assets(dry=True)
                self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
                self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                if "ordinary_operation" in fixture.ledger():
                    fixture.assert_refusal_unchanged(action="remove")
                result = fixture.run_assets()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                fixture.assert_complete()
                self.assertEqual(fixture.run_assets("verify").returncode, 0)

    def test_mixed_upgrade_refuses_foreign_entries_before_preparation(self):
        self.install_old()
        self.activate()
        self.skill(self.root / "public/skills/added-tool")
        target = self.targets("common-tool")[0]
        target.unlink()
        target.symlink_to(self.outside)
        self.assert_refusal_unchanged()
        self.assert_refusal_unchanged(dry=True)

    def test_mixed_preparation_rechecks_protected_inputs(self):
        for changed in ("provider", "predecessor", "adapter", "generic-link", "adapter-link", "manifest"):
            with self.subTest(changed=changed):
                fixture = type(self)("runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.install_old()
                fixture.activate()
                fixture.skill(fixture.root / "public/skills/added-tool")
                self.assertEqual(fixture.run_assets(fault="ordinary-after-intent").returncode, 71)
                if changed in ("provider", "predecessor", "adapter"):
                    source = {"provider": fixture.new, "predecessor": fixture.old, "adapter": fixture.adapter}[changed]
                    with (source / "SKILL.md").open("a") as stream:
                        stream.write("changed\n")
                elif changed == "generic-link":
                    fixture.targets()[0].unlink()
                    fixture.targets()[0].symlink_to(fixture.new)
                elif changed == "adapter-link":
                    fixture.targets("site-adapter")[0].symlink_to(fixture.adapter)
                else:
                    with fixture.public.open("a") as stream:
                        stream.write("\n")
                fixture.assert_refusal_unchanged()
                fixture.assert_refusal_unchanged(dry=True)

    def test_mixed_preparation_supports_owned_source_move_and_deselection(self):
        self.install_old()
        self.activate()
        moved = self.root / "moved/skills"
        self.skill(moved / "common-tool")
        declaration = json.loads(self.public.read_text())
        declaration["skill_groups"][0]["exclude_names"] = ["common-tool"]
        declaration["skill_groups"].append({"name": "moved", "path": str(moved), "targets": ["claude"]})
        write_json(self.public, declaration)
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_complete()
        self.assertEqual(os.readlink(self.targets("common-tool")[0]), str(moved / "common-tool"))
        self.assertFalse(self.targets("common-tool")[1].exists())
        self.assertEqual(self.run_assets("verify").returncode, 0)

    def test_mixed_preparation_schema_refuses_invalid_marker_without_changes(self):
        self.install_old()
        self.activate()
        self.skill(self.root / "public/skills/added-tool")
        self.assertEqual(self.run_assets(fault="ordinary-after-intent").returncode, 71)
        original = self.ledger()
        for marker in (None, False, "", [], "g" * 64):
            with self.subTest(marker=marker):
                value = json.loads(json.dumps(original))
                plan = value["ordinary_operation"]["plan"]
                plan["handoff_preparation"] = marker
                value["ordinary_operation"]["plan_sha256"] = hashlib.sha256(
                    json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                write_json(self.ownership, value)
                self.assert_refusal_unchanged()
        write_json(self.ownership, original)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assert_complete()

    def test_mixed_handoff_prior_cannot_nest_transfer_or_pending_operation(self):
        self.install_old()
        self.activate()
        self.skill(self.root / "public/skills/added-tool")
        self.assertEqual(self.run_assets(fault="after-pending").returncode, 71)
        original = self.ledger()
        for field, payload in (("handoff", original["handoff"]), ("ordinary_operation", {})):
            with self.subTest(field=field):
                value = json.loads(json.dumps(original))
                record = value["handoff"]
                record["plan"]["prior_ownership"][field] = payload
                record["plan_sha256"] = hashlib.sha256(json.dumps(
                    record["plan"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                write_json(self.ownership, value)
                self.assert_refusal_unchanged()
        write_json(self.ownership, original)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assert_complete()

    def test_ordinary_pending_schema_and_action_are_closed(self):
        fixture = self.ordinary_fixture()
        fixture.move_ordinary_source()
        self.assertEqual(fixture.run_assets(fault="ordinary-after-intent").returncode, 71)
        original = fixture.ownership.read_bytes()
        refused = fixture.run_assets("remove")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(b"ordinary converge is pending", refused.stdout + refused.stderr)
        for cursor in (True, -1, 10**100, [], None):
            with self.subTest(cursor=repr(cursor)):
                value = json.loads(original)
                value["ordinary_operation"]["cursor"] = cursor
                write_json(fixture.ownership, value)
                fixture.assert_refusal_unchanged()
        fixture.ownership.write_bytes(original)
        self.assertEqual(fixture.run_assets().returncode, 0)

    def test_pending_converge_reporting_precedes_unavailable_selection(self):
        fixture = self.ordinary_fixture()
        fixture.move_ordinary_source()
        self.assertEqual(fixture.run_assets(fault="ordinary-after-intent").returncode, 71)
        self.assertEqual(fixture.ledger()["ordinary_operation"]["plan"]["action"], "converge")
        original_manifest = fixture.public.read_bytes()
        for selection in ("missing", "malformed"):
            with self.subTest(selection=selection):
                if selection == "missing":
                    fixture.public.unlink()
                else:
                    fixture.public.write_text("{malformed fixture manifest\n")
                for action in ("status", "verify", "doctor"):
                    result = fixture.assert_refusal_unchanged(action=action)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(b"ordinary converge is pending; resume agent-assets converge", result.stdout)
                    self.assertNotIn(b"Traceback", result.stderr)
                # Actual same-action resume still validates current selection.
                for dry in (False, True):
                    result = fixture.assert_refusal_unchanged(dry=dry)
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn(b"ordinary converge is pending", result.stdout + result.stderr)
                fixture.public.write_bytes(original_manifest)
                fixture.public.chmod(0o600)
        self.assertEqual(fixture.run_assets().returncode, 0)
        self.assertNotIn("ordinary_operation", fixture.ledger())

    def test_pending_converge_reporting_forbids_discovery_but_resume_requires_it(self):
        fixture = self.ordinary_fixture()
        moved = fixture.move_ordinary_source()
        self.assertEqual(fixture.run_assets(fault="ordinary-after-intent").returncode, 71)
        expected = {name: 0 for name in (
            "configured_manifests", "compose_manifests", "_load_selection", "_load_assets",
            "link_report", "destination_metadata_issues", "audit_roots_report", "plugin_report")}

        def witness(result, selection_calls=0):
            records = [json.loads(line)["selection_read_witness"] for line in result.stderr.splitlines()
                       if line.startswith(b'{"selection_read_witness":')]
            self.assertEqual(records, [{**expected, "_load_selection": selection_calls}], result.stderr)

        for action in ("status", "verify", "doctor"):
            result = fixture.assert_refusal_unchanged(action=action, fault="selection-read-witness")
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"ordinary converge is pending; resume agent-assets converge", result.stdout)
            witness(result)
        original = fixture.ownership.read_bytes()
        invalid = json.loads(original)
        invalid["ordinary_operation"]["cursor"] = True
        write_json(fixture.ownership, invalid)
        result = fixture.assert_refusal_unchanged(action="status", fault="selection-read-witness")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"ordinary converge is pending", result.stdout)
        self.assertIn(b"synthetic selection inspection forbidden", result.stdout)
        witness(result, 1)  # Invalid ledger content never becomes pending authority.
        fixture.ownership.write_bytes(original)
        result = fixture.assert_refusal_unchanged(fault="selection-read-witness")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"synthetic selection inspection forbidden", result.stdout)
        witness(result, 1)  # Prove the interception and mandatory resume selection.
        self.assertEqual(fixture.run_assets().returncode, 0)
        self.assertNotIn("ordinary_operation", fixture.ledger())
        for target in fixture.targets("common-tool"):
            self.assertEqual(os.readlink(target), str(moved))

    def test_ordinary_capacity_precedes_mutation_and_pending_resume(self):
        for fault in ("ordinary-prepare-initial-limit", "ordinary-prepare-pending-overflow", "ordinary-prepare-prepared-over"):
            with self.subTest(fault=fault):
                fixture = self.ordinary_fixture(single=True)
                fixture.move_ordinary_source()
                boundary = fixture.ordinary_capacity(fault)
                if fault == "ordinary-prepare-prepared-over":
                    self.assertEqual(boundary["prepared"], boundary["limit"] + 1)
                else:
                    self.assertEqual(boundary["initial"], boundary["limit"])
                self.assertGreater(boundary["prepared"], boundary["limit"])
                result = fixture.assert_checkpoint_refusal_unchanged()
                self.assertIn(b"ownership checkpoint exceeds size limit", result.stdout + result.stderr)
        fixture = self.ordinary_fixture()
        fixture.activate()
        write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public), str(fixture.site)]}})
        fixture.skill(fixture.public.parent / "skills/added-tool")
        fixture.ordinary_capacity("ordinary-prepare-initial-limit")
        result = fixture.assert_checkpoint_refusal_unchanged()
        self.assertIn(b"ownership checkpoint exceeds size limit", result.stdout + result.stderr)
        for name in ("workflow", "site-adapter"):
            self.assertTrue(all(not os.path.lexists(path) for path in fixture.targets(name)))

    def test_ordinary_prepared_exact_limit_and_cursor_width_are_admitted(self):
        fixture = self.ordinary_fixture(single=True)
        fixture.move_ordinary_source()
        boundary = fixture.ordinary_capacity("ordinary-prepare-prepared-limit")
        self.assertEqual(boundary["prepared"], boundary["limit"])
        self.assertLess(boundary["complete"], boundary["limit"])
        result = fixture.run_assets(fault="ordinary-size-witness")
        self.assertEqual(result.returncode, 0, result.stderr)
        witness = [json.loads(line)["ordinary_witness"] for line in result.stderr.splitlines()
                   if line.startswith(b'{"ordinary_witness":')][0]
        self.assertEqual(witness["prepared_bytes"], [boundary["limit"]])
        self.assertNotIn("ordinary_operation", fixture.ledger())
        fixture = self.ordinary_fixture(single=True)
        for index in range(11):
            fixture.skill(fixture.public.parent / f"skills/extra-{index}")
        boundary = fixture.ordinary_capacity("ordinary-prepare-cursor-envelope")
        self.assertGreaterEqual(boundary["last_replacement"], 10)
        self.assertEqual(boundary["admitted_maximum"], boundary["limit"])

    def copied_public_cli(self):
        layout = self.root / "public-cli"
        for name in ("features/agent-assets.py", "features/secrets.py", "lib/paths.py", "assets/agent-assets.json"):
            target = layout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        # Copy only the public asset subtree; never run any copied skill/helper.
        shutil.copytree(ROOT / "assets/agent-assets", layout / "assets/agent-assets", symlinks=True)
        declared = json.loads((layout / "assets/agent-assets.json").read_text())
        self.assertIs(type(declared["version"]), int)
        self.assertEqual(declared["version"], 2)
        self.assertEqual(declared["skill_contracts"], [{"group": "common", "name": "pr-triage", "version": 1}])
        self.assertEqual(declared["skill_groups"][0]["targets"], ["claude", "codex"])
        return layout

    def run_public_cli(self, layout, action="converge", *, dry=False):
        manifest = layout / "assets/agent-assets.json"
        environment = FIXTURE.asset_environment(self.home, manifest, self.state, self.ownership, "linux")
        arguments = [sys.executable, "-B", str(layout / "features/agent-assets.py"), action,
                     "--manifest", str(manifest), "--state", str(self.state),
                     "--ownership", str(self.ownership), "--json"]
        if dry:
            arguments.append("--dry-run")
        return subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, timeout=15)

    def test_actual_public_source_relocation_has_conditional_feature_wide_recovery(self):
        layout = self.copied_public_cli()
        self.assertEqual(self.run_public_cli(layout).returncode, 0)
        launcher = self.home / ".local/bin/agent-assets"
        launcher_before = entry_metadata(launcher)
        original = self.ledger()
        self.assertIsNone(original["handoff"])
        moved = layout / "moved-skills"
        (layout / "assets/agent-assets/skills").rename(moved)
        manifest = layout / "assets/agent-assets.json"
        declared = json.loads(manifest.read_text())
        declared["skill_groups"][0]["path"] = str(moved)
        write_json(manifest, declared)
        before = FIXTURE.snapshot(self.root)
        refused = self.run_public_cli(layout)
        self.assertNotEqual(refused.returncode, 0)
        for text in (b"source relocation is not automatic", b"all owned assets", b"owned launcher"):
            self.assertIn(text, refused.stdout + refused.stderr)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        for target in self.targets("pr-triage"):
            target.unlink()
        before = FIXTURE.snapshot(self.root)
        self.assertNotEqual(self.run_public_cli(layout).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(entry_metadata(launcher), launcher_before)
        self.assertEqual(self.run_public_cli(layout, "remove", dry=True).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        removed = self.run_public_cli(layout, "remove")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue(all(not os.path.lexists(target) for target in original["links"]))
        self.assertFalse(launcher.exists())
        self.assertEqual(self.ledger()["links"], {})
        self.assertIsNone(self.ledger()["handoff"])
        result = self.run_public_cli(layout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_public_cli(layout, "verify").returncode, 0)
        self.assertTrue(all(os.readlink(target) == str(moved / "pr-triage") for target in self.targets("pr-triage")))

    def test_checkout_and_completed_handoff_relocation_do_not_gain_cleanup_authority(self):
        layout = self.copied_public_cli()
        self.assertEqual(self.run_public_cli(layout).returncode, 0)
        launcher = self.home / ".local/bin/agent-assets"
        original_launcher = launcher.read_bytes()
        moved = layout.with_name("moved-public-cli")
        layout.rename(moved)  # The real CLI __file__ and generated launcher path move too.
        before, metadata = FIXTURE.snapshot(self.root), entry_metadata(launcher)
        self.assertNotEqual(self.run_public_cli(moved).returncode, 0)
        dry = self.run_public_cli(moved, "remove", dry=True)
        self.assertNotEqual(dry.returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        removed = self.run_public_cli(moved, "remove")
        self.assertEqual(removed.returncode, 1, removed.stderr)
        self.assertEqual(self.ledger()["links"], {})
        self.assertEqual(launcher.read_bytes(), original_launcher)
        self.assertEqual(entry_metadata(launcher), metadata)
        self.assertNotEqual(self.run_public_cli(moved).returncode, 0)
        moved.rename(layout)
        self.assertEqual(self.run_public_cli(layout).returncode, 0)

        fixture = type(self)("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.install_old()
        fixture.activate()
        self.assertEqual(fixture.run_assets().returncode, 0)
        record = fixture.ledger()["handoff"]
        relocated = fixture.root / "relocated-public-skills"
        (fixture.public.parent / "skills").rename(relocated)
        declared = json.loads(fixture.public.read_text())
        declared["skill_groups"][0]["path"] = str(relocated)
        write_json(fixture.public, declared)
        refusal = fixture.assert_refusal_unchanged()
        self.assertIn(b"provenance pins canonical source paths", refusal.stdout + refusal.stderr)
        self.assertEqual(fixture.run_assets("remove").returncode, 0)
        self.assertEqual(fixture.ledger()["handoff"], record)
        fixture.assert_refusal_unchanged()

    def test_direct_cli_with_sibling_secrets_feature(self):
        # Preserve the direct-script import layout, including the real sibling
        # feature that shadows the standard-library secrets module.
        layout = self.root / "cli-layout"
        for name in ("features/agent-assets.py", "features/secrets.py", "lib/paths.py"):
            target = layout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        before = FIXTURE.snapshot(layout)
        environment = FIXTURE.asset_environment(self.home, self.public, self.state, self.ownership, "linux")
        environment.update({"ENVY_AGENT_ASSETS_MANIFEST": "", "ENVY_MANIFEST": str(self.resolved),
                            "TEST_AGENT_ASSETS_ROOT": str(self.root)})
        command = [sys.executable, "-B", str(layout / "features/agent-assets.py"), "converge",
                   "--state", str(self.state), "--ownership", str(self.ownership), "--json"]
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ledger()["version"], 1)
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))
        self.activate()
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_complete()
        self.assertEqual(FIXTURE.snapshot(layout), before)

    def test_completed_handoff_repairs_only_ordinary_absence(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        site = json.loads(self.site.read_text())
        site.pop("skill_handoffs")
        write_json(self.site, site)
        record = self.ledger()["handoff"]
        protected = {path: entry_metadata(path) for path in self.targets() + self.targets("site-adapter")}
        for target in self.targets("common-tool"):
            target.unlink()
            before = FIXTURE.snapshot(self.root)
            self.assertEqual(self.run_assets(dry=True).returncode, 0)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
            result = self.run_assets()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.ledger()["handoff"], record)
            self.assertEqual(os.readlink(target), str(self.root / "public/skills/common-tool"))
            self.assertEqual({path: entry_metadata(path) for path in protected}, protected)
        for target in protected:
            source = os.readlink(target)
            target.unlink()
            self.assert_refusal_unchanged()
            self.assert_refusal_unchanged(dry=True)
            target.symlink_to(source)

    def test_missing_ordinary_link_preserves_transition_and_deselection_guards(self):
        for state in ("legacy", "pending", "deselected"):
            for harness in (".claude", ".agents"):
                with self.subTest(state=state, harness=harness):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    try:
                        fixture.install_old()
                        if state == "deselected":
                            public = {**fixture.public_v1, "version": 2,
                                      "skill_groups": [{**fixture.public_v1["skill_groups"][0],
                                                        "exclude_names": ["common-tool"]}]}
                            write_json(fixture.public, public)
                        else:
                            fixture.activate()
                            if state == "pending":
                                self.assertEqual(fixture.run_assets(fault="after-pending").returncode, 71)
                        target = fixture.home / harness / "skills/common-tool"
                        target.unlink()
                        result = fixture.assert_refusal_unchanged()
                        fixture.assert_refusal_unchanged(dry=True)
                        if state == "deselected":
                            self.assertIn(str(target).encode(), result.stderr)
                            self.assertIn(b"ownership-drift", result.stderr)
                            self.assertIn(b"missing", result.stderr)
                    finally:
                        fixture.doCleanups()

    def test_explicit_v1_operations_without_unix_locking(self):
        fault = "without-unix-locking"
        before = FIXTURE.snapshot(self.root)
        self.assertEqual(self.run_assets(dry=True, fault=fault).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(self.run_assets(fault=fault).returncode, 0)
        self.assertEqual(self.ledger()["version"], 1)
        launcher = self.home / ".local/bin/agent-assets"
        launcher.chmod(0o644)  # Legacy no-lock content recognition is unchanged.
        before = FIXTURE.snapshot(self.root)
        for action in ("status", "verify", "doctor"):
            result = self.run_assets(action, fault=fault)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(self.run_assets("remove", dry=True, fault=fault).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(self.run_assets("remove", fault=fault).returncode, 0)
        self.assertFalse(self.ownership.exists())
        self.assertFalse(os.path.lexists(launcher))
        self.assertTrue(all(not path.is_symlink() for path in self.targets()))

    def test_legacy_cleanup_without_unix_locking_uses_only_recorded_entries(self):
        self.install_old()
        (self.home / ".local/bin/agent-assets").chmod(0o644)
        recorded = self.ledger()
        self.activate()
        # No discovery: even the now-selected provider may be unavailable.
        self.new.rename(self.root / "unavailable-provider")
        sibling = self.targets()[0].parent / "unrecorded"
        sibling.symlink_to(self.old)
        protected = {path: entry_metadata(path) for path in (sibling, self.outside / "sentinel")}
        sources = {name: FIXTURE.snapshot(self.root / name) for name in ("public", "site", "outside")}
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        result = self.run_assets("remove", dry=True, fault="without-unix-locking")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        result = self.run_assets("remove", fault="without-unix-locking")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ownership.exists())
        self.assertFalse((self.home / ".local/bin/agent-assets").exists())
        self.assertTrue(all(not os.path.lexists(path) for path in recorded["links"]))
        self.assertEqual({path: entry_metadata(path) for path in protected}, protected)
        self.assertEqual({name: FIXTURE.snapshot(self.root / name) for name in sources}, sources)
        self.assertEqual(self.run_assets("remove", fault="without-unix-locking").returncode, 0)

    def test_legacy_cleanup_without_unix_locking_preserves_drift_and_v1_records(self):
        self.install_old()
        self.activate()
        changed = self.targets()[0]
        changed.unlink()
        changed.symlink_to(self.outside)
        absent = self.targets("common-tool")[0]
        absent.unlink()
        launcher = self.home / ".local/bin/agent-assets"
        launcher.write_text("Foreign launcher: preserve.\n")
        protected = {path: entry_metadata(path) for path in (changed, launcher, self.outside / "sentinel")}
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        result = self.run_assets("remove", dry=True, fault="without-unix-locking")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        result = self.run_assets("remove", fault="without-unix-locking")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.ledger()["version"], 1)
        self.assertNotIn("handoff", self.ledger())
        self.assertEqual(self.ledger()["links"], {str(changed): str(self.old)})
        self.assertEqual(self.ledger()["launcher"], str(launcher))
        self.assertEqual({path: entry_metadata(path) for path in protected}, protected)

    def test_legacy_cleanup_does_not_adopt_an_unrecorded_launcher(self):
        self.install_old()
        value = self.ledger()
        value.pop("launcher")
        write_json(self.ownership, value)
        self.activate()
        launcher = self.home / ".local/bin/agent-assets"
        content, identity = launcher.read_bytes(), entry_metadata(launcher)
        result = self.run_assets("remove", fault="without-unix-locking")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ownership.exists())
        self.assertEqual(launcher.read_bytes(), content)
        self.assertEqual(entry_metadata(launcher), identity)

    def test_no_unix_cleanup_does_not_bypass_selection_or_store_refusals(self):
        self.install_old()
        prior = self.ownership.read_bytes()
        self.activate()
        for action, dry in (("converge", False), ("converge", True), ("status", False),
                            ("verify", False), ("doctor", False)):
            result = self.assert_refusal_unchanged(action=action, dry=dry, fault="without-unix-locking")
            self.assertIn(b"v2 assets require Unix locking", result.stdout + result.stderr)
        public = self.public.read_bytes()
        invalid = json.loads(public)
        invalid["unknown"] = True
        write_json(self.public, invalid)
        self.assert_refusal_unchanged(action="remove", fault="without-unix-locking")
        self.public.write_bytes(public)
        site = self.site.read_bytes()
        # A malformed v1 contributor must not qualify through the v2 capability error.
        write_json(self.site, {"version": 1, "skill_groups": [{"name": "site", "path": str(self.old.parent),
                                                               "targets": "not-an-array"}]})
        self.assert_refusal_unchanged(action="remove", fault="without-unix-locking")
        self.site.write_bytes(site)
        self.assertEqual(self.ownership.read_bytes(), prior)
        write_json(self.ownership, {"version": 1, "links": []})
        self.assert_refusal_unchanged(action="remove", fault="without-unix-locking")
        self.ownership.write_bytes(prior)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assert_refusal_unchanged(action="remove", fault="without-unix-locking")

    def test_unix_launcher_removal_rechecks_observed_identity(self):
        for version in (1, 2):
            for kind in ("file", "link"):
                with self.subTest(version=version, kind=kind):
                    fixture = type(self)("runTest")
                    fixture.setUp()
                    try:
                        fixture.install_old()
                        if version == 2:
                            fixture.activate()
                            self.assertEqual(fixture.run_assets().returncode, 0)
                        launcher = fixture.home / ".local/bin/agent-assets"
                        original = launcher.read_bytes()
                        outside = metadata_snapshot(fixture.outside)
                        result = fixture.run_assets("remove", fault=f"launcher-replaced-{kind}")
                        records = [json.loads(line)["launcher_removal_witness"] for line in result.stderr.splitlines()
                                   if line.startswith(b'{"launcher_removal_witness":')]
                        self.assertEqual(len(records), 1, result.stderr)
                        self.assertEqual(records[0]["injections"], 1)
                        # The same driver also witnesses baseline Path.read_text; it
                        # must not silently miss the descriptor reader introduced here.
                        self.assertIn(records[0]["reader"], {"path-read", "descriptor-read"})
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertEqual(entry_metadata(launcher), records[0]["entry"])
                        self.assertEqual((fixture.root / "observed-launcher").read_bytes(), original)
                        if kind == "file":
                            self.assertEqual(launcher.read_text(), "Foreign launcher: preserve.\n")
                        else:
                            self.assertEqual(os.readlink(launcher), str(fixture.outside / "sentinel"))
                        self.assertEqual(metadata_snapshot(fixture.outside), outside)
                        self.assertEqual(fixture.ledger()["version"], version)
                    finally:
                        fixture.doCleanups()

    def test_unix_launcher_removal_normal_and_foreign_controls(self):
        for version in (1, 2):
            fixture = type(self)("runTest")
            fixture.setUp()
            try:
                fixture.install_old()
                if version == 2:
                    fixture.activate()
                    self.assertEqual(fixture.run_assets().returncode, 0)
                launcher = fixture.home / ".local/bin/agent-assets"
                original = launcher.read_bytes()
                before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                self.assertEqual(fixture.run_assets("remove", dry=True).returncode, 0)
                self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                self.assertEqual(metadata_snapshot(fixture.root), metadata)
                launcher.write_text("Foreign launcher: preserve.\n")
                identity = entry_metadata(launcher)
                # Explicit v1 retains its pre-existing foreign-launcher policy;
                # v2 retains the changed record and reports partial removal.
                self.assertEqual(fixture.run_assets("remove").returncode, 0 if version == 1 else 1)
                self.assertEqual(entry_metadata(launcher), identity)
                launcher.write_bytes(original)
                if version == 1:
                    self.assertEqual(fixture.run_assets().returncode, 0)
                self.assertEqual(fixture.run_assets("remove").returncode, 0)
                self.assertFalse(launcher.exists())
            finally:
                fixture.doCleanups()

    def test_legacy_handoff_repeat_and_selected_harnesses(self):
        self.install_old()
        self.activate()
        for platform in ("linux", "darwin"):
            result = self.run_assets(platform=platform)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_complete()
            self.assertEqual(self.run_assets("verify", platform=platform).returncode, 0)

    def test_fresh_pair_and_public_only(self):
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertIsNone(self.ledger()["handoff"])
        self.assertEqual(self.run_assets("verify").returncode, 0)
        write_json(self.public, self.public_v1)
        write_json(self.site, self.site_v1)
        self.assert_refusal_unchanged()
        self.assertEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(self.ledger()["version"], 2)
        self.assertIsNone(self.ledger()["handoff"])

    def test_unjournaled_fresh_partial_pair_is_not_adopted(self):
        self.activate()
        self.assertEqual(self.run_assets(fault="after-workflow-1").returncode, 71)
        self.assertFalse(self.ownership.exists())
        self.assertTrue(self.targets()[0].is_symlink())
        self.assertFalse(self.targets()[1].exists())
        self.assert_refusal_unchanged()

    def test_protected_staging_fresh_orphan_requires_reconciliation(self):
        for public_only in (False, True):
            with self.subTest(public_only=public_only):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.activate_protected_only(public_only=public_only)
                    self.assertEqual(fixture.run_assets(fault="orphan").returncode, 72)
                    stages = list(fixture.targets()[0].parent.glob(".workflow.agent-assets-*"))
                    self.assertEqual(len(stages), 1)
                    self.assertEqual(os.readlink(stages[0]), str(fixture.new))
                    self.assertFalse(fixture.ownership.exists())
                    self.assertFalse((fixture.home / ".local/bin/agent-assets").exists())
                    for target in fixture.targets() + fixture.targets("site-adapter"):
                        self.assertFalse(os.path.lexists(target))
                    for action, dry in (("converge", False), ("converge", True),
                                        ("verify", False), ("status", False), ("doctor", False)):
                        result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                        self.assertIn(b"unexplained staging entry requires reconciliation",
                                      result.stdout + result.stderr)
                    self.assertFalse(fixture.ownership.exists())
                finally:
                    fixture.doCleanups()

    def test_protected_staging_reporting_has_no_following_reads(self):
        for public_only in (False, True):
            for name in (("workflow",) if public_only else ("workflow", "site-adapter")):
                for harness in (0, 1):
                    with self.subTest(public_only=public_only, name=name, harness=harness):
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        try:
                            fixture.activate_protected_only(public_only=public_only)
                            (fixture.outside / "SKILL.md").write_text(
                                "---\nname: outside-read-witness\ndescription: Synthetic outside metadata.\n---\n")
                            target = fixture.targets(name)[harness]
                            target.parent.mkdir(parents=True)
                            target.with_name(f".{name}.agent-assets-read-witness").symlink_to(fixture.outside)
                            for action, dry in (("converge", False), ("converge", True),
                                                ("status", False), ("verify", False), ("doctor", False)):
                                with self.subTest(action=action, dry=dry):
                                    result = fixture.assert_refusal_unchanged(
                                        action=action, dry=dry, fault="staging-read-witness")
                                    fixture.assert_no_staging_reads(result)
                        finally:
                            fixture.doCleanups()

    def test_retained_adapter_staging_stops_doctor_extensions(self):
        for harness in (0, 1):
            with self.subTest(harness=harness):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.install_old()
                    fixture.activate()
                    self.assertEqual(fixture.run_assets().returncode, 0)
                    site = json.loads(fixture.site.read_text())
                    site.pop("skill_handoffs")
                    write_json(fixture.site, site)
                    target = fixture.targets("site-adapter")[harness]
                    public = json.loads(fixture.public.read_text())
                    public.update(plugin_audit=True, audit_roots=[{
                        "path": str(target.parent), "name": "fixture-destination", "read_only": True}])
                    write_json(fixture.public, public)
                    write_json(fixture.home / ".claude/settings.json", {"enabledPlugins": {}})
                    (fixture.outside / "SKILL.md").write_text(
                        "---\nname: outside-read-witness\ndescription: Synthetic outside metadata.\n---\n")
                    target.with_name(".site-adapter.agent-assets-read-witness").symlink_to(fixture.outside)
                    for action, dry in (("converge", False), ("converge", True),
                                        ("status", False), ("verify", False), ("doctor", False)):
                        with self.subTest(action=action, dry=dry):
                            result = fixture.assert_refusal_unchanged(
                                action=action, dry=dry, fault="staging-read-witness")
                            fixture.assert_no_staging_reads(result)
                finally:
                    fixture.doCleanups()

    def assert_missing_provider_staging_refusal(self, *, definition_only):
        for selection in ("public-only", "paired", "retained"):
            for name in (("workflow",) if selection == "public-only" else ("workflow", "site-adapter")):
                for harness in (0, 1):
                    with self.subTest(selection=selection, name=name, harness=harness):
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        try:
                            if selection == "retained":
                                fixture.install_old()
                                fixture.activate()
                                self.assertEqual(fixture.run_assets().returncode, 0)
                                site = json.loads(fixture.site.read_text())
                                site.pop("skill_handoffs")
                                write_json(fixture.site, site)
                            else:
                                fixture.activate_protected_only(public_only=selection == "public-only")
                            provider = fixture.new if name == "workflow" else fixture.adapter
                            missing = provider / "SKILL.md" if definition_only else provider
                            missing.rename(fixture.root / "unselected-provider")
                            self.assertFalse(os.path.lexists(missing))
                            target = fixture.targets(name)[harness]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            public = json.loads(fixture.public.read_text())
                            public.update(plugin_audit=True, audit_roots=[{
                                "path": str(target.parent), "name": "fixture-destination", "read_only": True}])
                            write_json(fixture.public, public)
                            write_json(fixture.home / ".claude/settings.json", {"enabledPlugins": {}})
                            (fixture.outside / "SKILL.md").write_text(
                                "---\nname: outside-read-witness\ndescription: Synthetic outside metadata.\n---\n")
                            target.with_name(f".{name}.agent-assets-read-witness").symlink_to(fixture.outside)
                            for action, dry in (("converge", False), ("converge", True),
                                                ("status", False), ("verify", False), ("doctor", False)):
                                with self.subTest(action=action, dry=dry):
                                    result = fixture.assert_refusal_unchanged(
                                        action=action, dry=dry, fault="staging-read-witness")
                                    fixture.assert_no_staging_reads(result)
                        finally:
                            fixture.doCleanups()

    def test_missing_provider_directory_staging_has_no_following_reads(self):
        self.assert_missing_provider_staging_refusal(definition_only=False)

    def test_missing_provider_definition_staging_has_no_following_reads(self):
        self.assert_missing_provider_staging_refusal(definition_only=True)

    def test_completion_checkpoint_capacity_preserves_predecessor(self):
        self.install_old()
        self.activate()
        boundary = self.prepare_checkpoint_boundary("prepare-pending-at-limit")
        self.assertEqual(boundary["pending"], boundary["limit"])
        self.assertGreater(boundary["complete"], boundary["limit"])
        self.assertEqual(self.ownership.stat().st_size, boundary["prior"])
        result = self.assert_checkpoint_refusal_unchanged()
        self.assertIn(b"ownership checkpoint exceeds size limit", result.stdout + result.stderr)
        self.assertEqual(self.ledger()["version"], 1)
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))
        self.assertTrue(all(not os.path.lexists(path) for path in self.targets("site-adapter")))

    def test_completion_checkpoint_exact_size_limit_is_accepted(self):
        self.install_old()
        self.activate()
        boundary = self.prepare_checkpoint_boundary("prepare-complete-at-limit")
        self.assertEqual(boundary["complete"], boundary["limit"])
        self.assertLess(boundary["pending"], boundary["limit"])
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_complete()
        self.assertEqual(self.ownership.stat().st_size, boundary["limit"])
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        noop = self.run_assets()
        self.assertEqual(noop.returncode, 0, noop.stderr)
        self.assertTrue(FIXTURE.snapshot(self.root) == before, "no-op changed exact-limit ownership")
        self.assertEqual(metadata_snapshot(self.root), metadata)

    def test_existing_pending_completion_over_limit_does_not_advance(self):
        self.install_old()
        self.activate()
        boundary = self.prepare_checkpoint_boundary("prepare-oversize-pending")
        self.assertEqual(boundary["pending"], boundary["limit"])
        self.assertGreater(boundary["complete"], boundary["limit"])
        self.assertEqual(self.ownership.stat().st_size, boundary["limit"])
        result = self.assert_checkpoint_refusal_unchanged()
        self.assertIn(b"ownership checkpoint exceeds size limit", result.stdout + result.stderr)
        self.assertEqual(self.ledger()["handoff"]["state"], "pending")
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))
        self.assertTrue(all(not os.path.lexists(path) for path in self.targets("site-adapter")))

    def test_protected_staging_entry_types_and_both_harnesses(self):
        self.activate_protected_only()
        for name in ("workflow", "site-adapter"):
            for target in self.targets(name):
                for kind in ("symlink", "broken", "file", "directory", "fifo"):
                    with self.subTest(name=name, harness=target.parent.parent.name, kind=kind):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        stage = target.with_name(f".{name}.agent-assets-unexplained")
                        if kind in {"symlink", "broken"}:
                            stage.symlink_to(self.outside if kind == "symlink" else self.outside / "missing")
                        elif kind == "file":
                            stage.write_text("unexplained staging evidence\n")
                        elif kind == "directory":
                            stage.mkdir()
                        else:
                            os.mkfifo(stage, 0o600)
                        result = self.assert_refusal_unchanged()
                        self.assertIn(b"unexplained staging entry requires reconciliation",
                                      result.stdout + result.stderr)
                        if kind == "directory":
                            stage.rmdir()
                        else:
                            stage.unlink()

    def test_protected_staging_current_and_retained_adapter_refuse(self):
        for retained in (False, True):
            with self.subTest(retained=retained):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    if retained:
                        fixture.install_old()
                        fixture.activate()
                    else:
                        fixture.activate_protected_only()
                    self.assertEqual(fixture.run_assets().returncode, 0)
                    if retained:
                        site = json.loads(fixture.site.read_text())
                        site.pop("skill_handoffs")
                        write_json(fixture.site, site)
                    target = fixture.targets("site-adapter")[1]
                    target.with_name(".site-adapter.agent-assets-unexplained").symlink_to(fixture.outside)
                    for action, dry in (("converge", False), ("converge", True), ("verify", False)):
                        result = fixture.assert_refusal_unchanged(action=action, dry=dry)
                        self.assertIn(b"unexplained staging entry requires reconciliation",
                                      result.stdout + result.stderr)
                finally:
                    fixture.doCleanups()

    def test_protected_staging_scope_and_missing_parents(self):
        self.activate_protected_only()
        before = FIXTURE.snapshot(self.root)
        metadata = metadata_snapshot(self.root)
        for action, dry in (("converge", True), ("status", False), ("verify", False), ("doctor", False)):
            result = self.run_assets(action, dry=dry)
            self.assertEqual(result.returncode, 0 if dry else 1)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
            self.assertEqual(metadata_snapshot(self.root), metadata)
        target = self.targets()[0]
        target.parent.mkdir(parents=True)
        unrelated = [target.with_name(f".{name}.agent-assets-unexplained")
                     for name in ("workflow-extra", "common-tool")]
        for stage in unrelated:
            stage.symlink_to(self.outside)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.run_assets("verify").returncode, 0)
        stage = target.with_name(".workflow.agent-assets-unexplained")
        stage.symlink_to(self.outside)
        evidence = {path: entry_metadata(path) for path in [*unrelated, stage]}
        self.assertEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(self.ledger()["links"], {})
        for path, info in evidence.items():
            self.assertEqual(os.readlink(path), str(self.outside))
            self.assertEqual(entry_metadata(path), info)

    def test_protected_staging_does_not_change_v1(self):
        target = self.targets()[0]
        target.parent.mkdir(parents=True)
        stage = target.with_name(".workflow.agent-assets-unexplained")
        stage.symlink_to(self.outside)
        evidence = entry_metadata(stage)
        self.install_old()
        self.assertEqual(self.run_assets("verify").returncode, 0)
        self.assertEqual(os.readlink(stage), str(self.outside))
        self.assertEqual(entry_metadata(stage), evidence)

    def test_handoff_dry_run_reports_outstanding_atomic_work(self):
        self.install_old()
        self.activate()
        for fault, expected in ((None, b"start handoff: create 2 adapter links and atomically replace 2 generic links"),
                                ("after-workflow-1", b"resume handoff: create 0 adapter links and atomically replace 1 generic links")):
            with self.subTest(fault=fault):
                if fault:
                    self.assertEqual(self.run_assets(fault=fault).returncode, 71)
                before = FIXTURE.snapshot(self.root)
                metadata = metadata_snapshot(self.root)
                result = self.run_assets(dry=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertIn(b"preserve launcher and predecessor source", result.stdout)
                self.assertNotIn(b"stale links", result.stdout)
                self.assertEqual(FIXTURE.snapshot(self.root), before)
                self.assertEqual(metadata_snapshot(self.root), metadata)

    def test_public_only_fresh_and_undeclared_occupied_target(self):
        self.install_old()
        self.activate()
        write_json(self.resolved, {"resources": {"agent_asset_manifests": [str(self.public)]}})
        self.assert_refusal_unchanged()
        # Reset only the fixture's explicitly recorded install via the old valid manifests.
        write_json(self.public, self.public_v1)
        write_json(self.site, self.site_v1)
        write_json(self.resolved, {"resources": {"agent_asset_manifests": [str(self.public), str(self.site)]}})
        self.assertEqual(self.run_assets("remove").returncode, 0)
        write_json(self.public, {**self.public_v1, "version": 2,
            "skill_contracts": [{"group": "common", "name": "workflow", "version": 1}]})
        write_json(self.resolved, {"resources": {"agent_asset_manifests": [str(self.public)]}})
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.run_assets("verify").returncode, 0)

    def test_interrupted_fresh_pair_reports_unrecorded_reconciliation(self):
        self.activate_protected_only(public_only=True)
        stopped = self.run_assets(fault="after-workflow-1")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        self.assertFalse(self.ownership.exists())
        first, second = self.targets()
        self.assertEqual(os.readlink(first), str(self.new))
        self.assertFalse(os.path.lexists(second))
        for action, dry in (("converge", False), ("converge", True), ("status", False),
                            ("verify", False), ("doctor", False)):
            result = self.assert_refusal_unchanged(action=action, dry=dry)
            output = result.stdout + result.stderr
            self.assertIn(str(first).encode(), output)
            self.assertIn(b"unrecorded targets", output)
            self.assertIn(b"interrupted fresh-installation or foreign entries", output)
            self.assertIn(b"managed remove preserves unrecorded entries", output)
            self.assertNotIn(b"feature-wide remove then converge", output)
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        for dry in (True, False):
            result = self.run_assets("remove", dry=dry)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
            self.assertEqual(metadata_snapshot(self.root), metadata)
            self.assertFalse(self.ownership.exists())

    def test_exact_base_installer_rejects_each_activation_manifest(self):
        self.activate()
        # A fixed local blob read only; no fetch, checkout, provider, or remote.
        historical = subprocess.run(["git", "--no-pager", "show", "--no-ext-diff", "--no-textconv",
            "a3727aecca62203b8f175765cd19bfa716904eba:features/agent-assets.py"],
            cwd=ROOT, capture_output=True, timeout=15, check=True).stdout
        old_source = self.root / "historical/features/agent-assets.py"
        old_source.parent.mkdir(parents=True)
        old_source.write_bytes(historical)
        old_lib = self.root / "historical/lib"
        old_lib.mkdir()
        (old_lib / "paths.py").write_bytes((ROOT / "lib/paths.py").read_bytes())
        for manifest in (self.public, self.site):
            before = FIXTURE.snapshot(self.root)
            environment = FIXTURE.asset_environment(self.home, manifest, self.state, self.ownership, "linux")
            result = subprocess.run([sys.executable, "-B", str(old_source), "converge",
                "--manifest", str(manifest), "--state", str(self.state), "--ownership", str(self.ownership)],
                cwd=ROOT, env=environment, capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(FIXTURE.snapshot(self.root), before)

    def test_missing_and_relative_old_link_are_not_adopted(self):
        self.install_old()
        self.activate()
        target = self.targets()[0]
        target.unlink()
        self.assert_refusal_unchanged()
        target.symlink_to(os.path.relpath(self.old, target.parent))
        self.assert_refusal_unchanged()

    def test_unrecorded_already_new_and_inside_root_retarget_refuse(self):
        self.install_old()
        self.activate()
        target = self.targets()[0]
        target.unlink()
        target.symlink_to(self.new)
        value = self.ledger()
        value["links"].pop(str(target))
        write_json(self.ownership, value)
        self.assert_refusal_unchanged()

    def test_raw_links_and_ledger_cannot_be_rebased(self):
        self.install_old()
        self.activate()
        target = self.targets()[0]
        original = self.ledger()
        for replacement in (self.root / "site/skills", self.outside, self.new):
            target.unlink()
            target.symlink_to(replacement)
            self.assert_refusal_unchanged()
        target.unlink()
        target.symlink_to(self.old)
        value = self.ledger()
        value["links"][str(target)] = str(self.new)
        write_json(self.ownership, value)
        self.assert_refusal_unchanged()
        value["links"].pop(str(target))
        write_json(self.ownership, value)
        self.assert_refusal_unchanged()
        write_json(self.ownership, original)
        self.assertEqual(self.run_assets().returncode, 0)
        target.unlink()
        target.symlink_to(os.path.relpath(self.new, target.parent))
        self.assert_refusal_unchanged()

    def test_edited_extra_symlink_and_missing_predecessor(self):
        self.install_old()
        self.activate()
        original = (self.old / "SKILL.md").read_bytes()
        (self.targets()[0] / "SKILL.md").write_bytes(original + b"user edit\n")
        self.assert_refusal_unchanged()
        (self.old / "SKILL.md").write_bytes(original)
        extra = self.old / "extra"
        extra.symlink_to(self.outside / "sentinel")
        self.assert_refusal_unchanged()
        extra.unlink()
        extra.mkdir()
        self.assert_refusal_unchanged()
        extra.rmdir()
        (self.old / "SKILL.md").unlink()
        (self.old / "SKILL.md").symlink_to(self.outside / "sentinel")
        self.assert_refusal_unchanged()
        (self.old / "SKILL.md").unlink()
        (self.old / "SKILL.md").write_bytes(original)
        (self.old / "SKILL.md").rename(self.old / "unexpected.md")
        self.assert_refusal_unchanged()

    def test_unrelated_drift_and_other_harness_file_block_whole_plan(self):
        self.install_old()
        self.activate()
        target = self.targets("common-tool")[0]
        target.unlink()
        target.symlink_to(self.outside)
        self.assert_refusal_unchanged()
        target.unlink()
        target.symlink_to(self.root / "public/skills/common-tool")
        target = self.targets()[1]
        target.unlink()
        target.write_text("foreign entry\n")
        self.assert_refusal_unchanged()

    def test_named_interruption_points_resume_real_checkpoints(self):
        # Each subcase owns a completely new fixture, including its initial v1 ledger.
        for fault in ("after-pending", "after-site-adapter-1", "after-site-adapter-2",
                      "after-workflow-1", "after-workflow-2", "before-checkpoint"):
            with self.subTest(fault=fault):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.install_old()
                    fixture.activate()
                    self.assertNotEqual(fixture.run_assets(fault=fault).returncode, 0)
                    self.assertEqual(fixture.ledger()["handoff"]["state"], "pending")
                    fixture.assert_refusal_unchanged(action="remove")
                    fixture.assert_refusal_unchanged(action="remove", dry=True)
                    self.assertNotEqual(fixture.run_assets("verify").returncode, 0)
                    result = fixture.run_assets()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    fixture.assert_complete()
                finally:
                    fixture.doCleanups()

    def test_handoff_ownership_parent_layout_completes(self):
        for harness in (".claude", ".agents"):
            with self.subTest(harness=harness):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.ownership = fixture.home / harness / "owned.json"
                    fixture.install_old()
                    fixture.activate()
                    unrelated = {str(path): (os.readlink(path), entry_metadata(path))
                                 for path in fixture.targets("common-tool")}
                    sentinel = fixture.outside / "sentinel"
                    sentinel_before = (sentinel.read_bytes(), entry_metadata(sentinel))
                    before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                    result = fixture.run_assets(dry=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    for action in ("converge", "converge", "verify"):
                        result = fixture.run_assets(action)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        fixture.assert_complete()
                    self.assertEqual({str(path): (os.readlink(path), entry_metadata(path))
                                      for path in fixture.targets("common-tool")}, unrelated)
                    self.assertEqual((sentinel.read_bytes(), entry_metadata(sentinel)), sentinel_before)
                finally:
                    fixture.doCleanups()

    def test_handoff_ownership_parent_layout_resumes_checkpoints(self):
        checkpoints = (("after-pending", 0), ("after-site-adapter-1", 1),
                       ("after-site-adapter-2", 2), ("after-workflow-1", 3),
                       ("after-workflow-2", 4), ("before-checkpoint", 4))
        for fault, completed_steps in checkpoints:
            with self.subTest(fault=fault):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.ownership = fixture.home / ".claude/owned.json"
                    fixture.install_old()
                    fixture.activate()
                    result = fixture.run_assets(fault=fault)
                    self.assertEqual(result.stderr, b"")
                    if fault == "before-checkpoint":
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(json.loads(result.stdout), {"summary": {"errors": 1},
                            "issues": ["synthetic final checkpoint failure"]})
                    else:
                        self.assertEqual(result.returncode, 71)
                        self.assertEqual(result.stdout, b"")
                    pending = fixture.ledger()["handoff"]
                    self.assertEqual(pending["state"], "pending")
                    for index, target in enumerate(fixture.targets("site-adapter") + fixture.targets()):
                        if index < completed_steps:
                            expected = fixture.adapter if index < 2 else fixture.new
                            self.assertEqual(os.readlink(target), str(expected))
                        elif index < 2:
                            self.assertFalse(os.path.lexists(target))
                        else:
                            self.assertEqual(os.readlink(target), str(fixture.old))
                    fixture.assert_refusal_unchanged(action="remove")
                    fixture.assert_refusal_unchanged(action="remove", dry=True)
                    verification = fixture.run_assets("verify")
                    self.assertEqual(verification.returncode, 1, verification.stderr)
                    self.assertEqual(verification.stdout, b"")
                    self.assertIn(b"handoff-pending: ownership handoff is pending; convergence must reconcile it",
                                  verification.stderr)
                    result = fixture.run_assets()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    fixture.assert_complete()
                    self.assertEqual(fixture.ledger()["handoff"], {**pending, "state": "complete"})
                    for action in ("converge", "verify"):
                        result = fixture.run_assets(action)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        fixture.assert_complete()
                finally:
                    fixture.doCleanups()

    def test_handoff_mutation_directory_preserves_unrelated_entries(self):
        for change in ("added", "removed", "changed", "child-directory"):
            with self.subTest(change=change):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.ownership = fixture.home / ".claude/owned.json"
                    fixture.install_old()
                    destination = fixture.targets()[0].parent
                    note = destination / "foreign-note"
                    note.write_text("original foreign entry\n")
                    directory = destination / "foreign-directory"
                    directory.mkdir()
                    fixture.activate()
                    result = fixture.run_assets(fault="after-site-adapter-1")
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (71, b"", b""))
                    pending = fixture.ledger()
                    self.assertEqual(pending["handoff"]["state"], "pending")
                    if change == "added":
                        (destination / "new-foreign-entry").write_text("preserve\n")
                    elif change == "removed":
                        note.unlink()
                    elif change == "changed":
                        note.write_text("changed foreign entry with a different length\n")
                    else:
                        info = directory.stat()
                        (directory / "new-child").write_text("preserve\n")
                        os.utime(directory, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
                        self.assertNotEqual(directory.stat().st_mtime_ns, info.st_mtime_ns)
                    result = fixture.assert_refusal_unchanged()
                    self.assertIn(b"pending handoff plan, source, or unrelated state changed", result.stderr)
                    self.assertEqual(fixture.ledger(), pending)
                    self.assertEqual(os.readlink(fixture.targets("site-adapter")[0]), str(fixture.adapter))
                    self.assertFalse(os.path.lexists(fixture.targets("site-adapter")[1]))
                    self.assertTrue(all(os.readlink(target) == str(fixture.old) for target in fixture.targets()))
                finally:
                    fixture.doCleanups()

    def test_handoff_mutation_directory_identity_drift_refuses(self):
        def entries(directory):
            result = {}
            for child in directory.iterdir():
                self.assertTrue(child.is_symlink())
                info = child.lstat()
                result[child.name] = (info.st_mode, info.st_size, info.st_mtime_ns, os.readlink(child))
            return result

        for change in ("mode", "replacement"):
            with self.subTest(change=change):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.ownership = fixture.home / ".claude/owned.json"
                    fixture.install_old()
                    fixture.activate()
                    result = fixture.run_assets(fault="after-site-adapter-1")
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (71, b"", b""))
                    pending = fixture.ledger()
                    self.assertEqual(pending["handoff"]["state"], "pending")
                    destination = fixture.targets()[0].parent
                    original = destination.stat()
                    original_entries = entries(destination)
                    if change == "mode":
                        mode = 0o755 if stat.S_IMODE(original.st_mode) == 0o700 else 0o700
                        destination.chmod(mode)
                        self.assertNotEqual(stat.S_IMODE(destination.stat().st_mode), stat.S_IMODE(original.st_mode))
                    else:
                        displaced = fixture.root / "displaced-skills"
                        roots = {fixture.ownership.parent}
                        roots.update(Path(value) for definitions in fixture.destinations.values()
                                     for value in definitions.values())
                        self.assertTrue(all(not displaced.is_relative_to(root) for root in roots))
                        destination.rename(displaced)
                        destination.mkdir(mode=stat.S_IMODE(original.st_mode))
                        for name, observation in original_entries.items():
                            child = destination / name
                            child.symlink_to(observation[3])
                            os.utime(child, ns=(observation[2], observation[2]), follow_symlinks=False)
                        os.utime(destination, ns=(original.st_atime_ns, original.st_mtime_ns))
                        replacement = destination.stat()
                        self.assertNotEqual(replacement.st_ino, original.st_ino)
                        self.assertEqual((replacement.st_dev, replacement.st_uid), (original.st_dev, original.st_uid))
                        # Match every old directory/child observation so an
                        # unrelated entry, size or mtime cannot mask the inode gate.
                        self.assertEqual((replacement.st_mode, replacement.st_size, replacement.st_mtime_ns),
                                         (original.st_mode, original.st_size, original.st_mtime_ns))
                    self.assertEqual(entries(destination), original_entries)
                    result = fixture.assert_refusal_unchanged()
                    self.assertIn(b"pending handoff plan, source, or unrelated state changed", result.stderr)
                    self.assertEqual(fixture.ledger(), pending)
                    self.assertEqual(os.readlink(fixture.targets("site-adapter")[0]), str(fixture.adapter))
                    self.assertFalse(os.path.lexists(fixture.targets("site-adapter")[1]))
                    self.assertTrue(all(os.readlink(target) == str(fixture.old) for target in fixture.targets()))
                finally:
                    fixture.doCleanups()

    def test_pending_source_drift_and_missing_target_refuse(self):
        self.install_old()
        self.activate()
        self.run_assets(fault="after-pending")
        target = self.targets()[0]
        target.unlink()
        self.assert_refusal_unchanged()
        target.symlink_to(self.old)
        (self.new / "extra").write_text("changed candidate\n")
        self.assert_refusal_unchanged()

    def test_completed_checkpoint_and_lost_response_are_not_replayed(self):
        for fault in ("lost-link-response", "lost-checkpoint-response"):
            with self.subTest(fault=fault):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    fixture.install_old()
                    fixture.activate()
                    result = fixture.run_assets(fault=fault)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    records = [json.loads(line)["handoff_response_witness"] for line in result.stderr.splitlines()
                               if line.startswith(b'{"handoff_response_witness":')]
                    self.assertEqual(records, [{"lost_link": 4 if fault == "lost-link-response" else 0,
                                               "lost_checkpoint": 1 if fault == "lost-checkpoint-response" else 0,
                                               "renames": {"workflow": 2, "site-adapter": 2}}])
                    fixture.assert_complete()
                    stable = metadata_snapshot(fixture.root)
                    noop = fixture.run_assets(fault=fault)
                    self.assertEqual(noop.returncode, 0, noop.stderr)
                    records = [json.loads(line)["handoff_response_witness"] for line in noop.stderr.splitlines()
                               if line.startswith(b'{"handoff_response_witness":')]
                    self.assertEqual(records, [{"lost_link": 0, "lost_checkpoint": 0,
                                               "renames": {"workflow": 0, "site-adapter": 0}}])
                    self.assertEqual(metadata_snapshot(fixture.root), stable)
                finally:
                    fixture.doCleanups()
        # Complete checkpoint interruption has committed provenance, not pending work.
        fixture = type(self)("runTest")
        fixture.setUp()
        try:
            fixture.install_old()
            fixture.activate()
            self.assertEqual(fixture.run_assets(fault="after-complete").returncode, 71)
            fixture.assert_complete()
            self.assertEqual(fixture.run_assets().returncode, 0)
        finally:
            fixture.doCleanups()

    def test_unrelated_drift_during_pending_stops_remaining_writes(self):
        self.install_old()
        self.activate()
        self.assertNotEqual(self.run_assets(fault="unrelated-drift").returncode, 0)
        self.assertEqual(self.ledger()["handoff"]["state"], "pending")
        self.assertTrue(all(os.readlink(target) == str(self.old) for target in self.targets()))
        self.assertTrue(self.targets("site-adapter")[0].is_symlink())
        self.assertFalse(self.targets("site-adapter")[1].exists())
        self.assert_refusal_unchanged()

    def test_orphan_staging_is_preserved_and_blocks_resume(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets(fault="orphan").returncode, 72)
        self.assertTrue(list(self.targets()[0].parent.glob(".workflow.agent-assets-*")))
        self.assert_refusal_unchanged()

    def test_collision_and_failed_pending_write_preserve_foreign_entries(self):
        self.install_old()
        self.activate()
        self.assert_refusal_unchanged(fault="journal-failure")
        foreign = self.targets("site-adapter")[0].parent / (".site-adapter.agent-assets-" + "00" * 16)
        foreign.symlink_to(self.outside)
        result = self.assert_refusal_unchanged()
        self.assertIn(b"unexplained staging entry requires reconciliation", result.stdout + result.stderr)
        self.assertEqual(os.readlink(foreign), str(self.outside))
        self.assertEqual(self.ledger()["version"], 1)

    def test_late_collision_preserves_foreign_entry_and_pending_ownership(self):
        self.install_old()
        self.activate()
        result = self.run_assets(fault="collision")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"could not allocate an exclusive symlink staging entry", result.stdout + result.stderr)
        foreign = self.targets("site-adapter")[0].parent / (".site-adapter.agent-assets-" + "00" * 16)
        self.assertEqual(os.readlink(foreign), str(self.outside))
        self.assertEqual(self.ledger()["handoff"]["state"], "pending")
        self.assertTrue(all(os.readlink(path) == str(self.old) for path in self.targets()))
        self.assertTrue(all(not os.path.lexists(path) for path in self.targets("site-adapter")))
        self.assert_refusal_unchanged()

    def test_read_only_operations_and_busy_writer_leave_no_writes(self):
        self.install_old()
        self.activate()
        before = FIXTURE.snapshot(self.root)
        for action, dry in (("converge", True), ("status", False), ("verify", False), ("doctor", False)):
            self.run_assets(action, dry=dry)
            self.assertEqual(FIXTURE.snapshot(self.root), before)
        for action in ("converge", "remove", "status", "verify", "doctor"):
            self.assert_refusal_unchanged(action=action, fault="busy")

    def test_fifo_symlink_hardlink_and_invalid_mode_controls_fail_without_hang(self):
        self.install_old()
        lock = self.ownership.with_name(self.ownership.name + ".lock")
        lock.unlink()
        for kind in ("fifo", "symlink", "hardlink", "mode"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(lock, 0o600)
                elif kind == "symlink":
                    lock.symlink_to(self.outside / "sentinel")
                elif kind == "hardlink":
                    os.link(self.outside / "sentinel", lock)
                else:
                    lock.touch(mode=0o644)
                metadata = entry_metadata(lock)
                sentinel_metadata = entry_metadata(self.outside / "sentinel")
                self.assertNotEqual(self.run_assets().returncode, 0)
                self.assertTrue(os.path.lexists(lock))
                self.assertEqual(entry_metadata(lock), metadata)
                self.assertEqual(entry_metadata(self.outside / "sentinel"), sentinel_metadata)
                lock.unlink()
        self.ownership.unlink()
        os.mkfifo(self.ownership, 0o600)
        metadata = entry_metadata(self.ownership)
        self.assertNotEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(entry_metadata(self.ownership), metadata)

    def test_ownership_regular_to_fifo_race_refuses_without_open_hang(self):
        self.install_old()
        staged_fifo = self.ownership.with_name("." + self.ownership.name + ".control-fifo-race")
        staged_fifo.write_text("foreign staging entry\n")
        before, metadata = FIXTURE.snapshot(self.root), metadata_snapshot(self.root)
        collision = self.run_assets("remove", fault="control-fifo-race")
        self.assertEqual(collision.returncode, 1, collision.stderr)
        self.assertEqual(collision.stdout, b"")
        self.assertEqual(collision.stderr, b"control-fifo-race staging collision\n")
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertEqual(metadata_snapshot(self.root), metadata)
        staged_fifo.unlink()  # This test owns the collision sentinel.
        targets_before = {str(target): entry_metadata(target) for target in self.targets()}
        sentinel_before = entry_metadata(self.outside / "sentinel")
        result = self.run_assets("remove", fault="control-fifo-race")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, b"")
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 3, result.stderr)
        witness = json.loads(lines[0])
        self.assertEqual(set(witness), {"control_fifo_race"})
        record = witness["control_fifo_race"]
        self.assertEqual(set(record), {"target", "injections", "open_returned_fifo", "nonblocking",
                                       "nofollow", "parent", "fifo"})
        self.assertEqual(record["target"], self.ownership.name)
        self.assertEqual(record["injections"], 1)
        for key in ("open_returned_fifo", "nonblocking", "nofollow"):
            self.assertIs(record[key], True)
        parent = self.root.lstat()
        self.assertEqual(record["parent"], {"st_dev": parent.st_dev, "st_ino": parent.st_ino})
        self.assertEqual(lines[1:], [b"Issues:",
                         b"  ERROR   ownership-invalid: expected a single-link regular file"])
        self.assertTrue(stat.S_ISFIFO(self.ownership.lstat().st_mode))
        self.assertEqual(entry_metadata(self.ownership), record["fifo"])
        self.assertEqual(stat.S_IMODE(self.ownership.lstat().st_mode), 0o600)
        self.assertFalse(os.path.lexists(staged_fifo))
        self.assertTrue(all(os.readlink(target) == str(self.old) for target in self.targets()))
        self.assertEqual({str(target): entry_metadata(target) for target in self.targets()}, targets_before)
        self.assertEqual(entry_metadata(self.outside / "sentinel"), sentinel_before)

    def test_closed_schema_and_malformed_ledger_refuse(self):
        self.install_old()
        self.activate()
        value = json.loads(self.site.read_text())
        value["skill_handoffs"][0]["unknown"] = True
        write_json(self.site, value)
        self.assert_refusal_unchanged()
        value["skill_handoffs"][0].pop("unknown")
        write_json(self.site, value)
        write_json(self.ownership, {"version": 7, "links": {}})
        self.assert_refusal_unchanged(action="remove")

    def test_duplicate_keys_and_malformed_pending_are_not_empty_baselines(self):
        self.install_old()
        self.activate()
        content = self.site.read_text()
        self.site.write_text(content.replace('"version": 2', '"version": 2, "version": 2'))
        self.assert_refusal_unchanged()
        self.site.write_text(content)
        self.assertEqual(self.run_assets(fault="after-pending").returncode, 71)
        value = self.ledger()
        value["handoff"]["unknown"] = "unsupported"
        write_json(self.ownership, value)
        self.assert_refusal_unchanged()
        self.assert_refusal_unchanged(action="remove")

    def test_malformed_handoff_field_types_are_diagnostics_not_tracebacks(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets(fault="after-pending").returncode, 71)
        self.assert_refusal_unchanged(action="remove", without_manifests=True)
        self.assert_refusal_unchanged(action="remove", dry=True, without_manifests=True)
        original = self.ownership.read_text()
        for field, invalid in (("state", []), ("state", {}), ("state", None),
                               ("state", True), ("state", 1), ("plan_sha256", [])):
            with self.subTest(field=field, invalid=invalid):
                value = json.loads(original)
                value["handoff"][field] = invalid
                write_json(self.ownership, value)
                result = self.assert_refusal_unchanged(action="remove", without_manifests=True)
                self.assertIn(b"ownership-invalid", result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_complete_v2_never_demotes_and_remove_retains_provenance(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        record = self.ledger()["handoff"]
        write_json(self.public, self.public_v1)
        write_json(self.site, self.site_v1)
        refused = self.assert_refusal_unchanged()
        self.assertIn(b"ownership-v2-selection-incompatible", refused.stdout + refused.stderr)
        self.assertIn(str(self.ownership).encode(), refused.stdout + refused.stderr)
        self.assertIn(b"requires a compatible reviewed v2 selection", refused.stdout + refused.stderr)
        self.assertIn(b"do not delete/rewrite the ledger to downgrade", refused.stdout + refused.stderr)
        self.assertEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(self.ledger()["version"], 2)
        self.assertEqual(self.ledger()["handoff"], record)
        self.assertEqual(self.ledger()["links"], {})
        refused = self.assert_refusal_unchanged()
        self.assertIn(str(self.ownership).encode(), refused.stdout + refused.stderr)
        self.assertIn(b"do not delete/rewrite the ledger to downgrade", refused.stdout + refused.stderr)

    def test_completed_declaration_can_be_omitted_not_adapter_selection(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        record = self.ledger()["handoff"]
        site = json.loads(self.site.read_text())
        site.pop("skill_handoffs")
        write_json(self.site, site)
        (self.old / "SKILL.md").unlink()  # Only this synthetic retired fixture, never real source.
        self.old.rmdir()
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.ledger()["handoff"], record)
        site["skill_groups"][0]["features"] = ["disabled-fixture"]
        write_json(self.site, site)
        self.assert_refusal_unchanged()

    def test_completed_omitted_declaration_keeps_adapter_pair_and_fresh_reinstall_rules(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        record = self.ledger()["handoff"]
        site = json.loads(self.site.read_text())
        site.pop("skill_handoffs")
        write_json(self.site, site)
        target = self.targets("site-adapter")[0]
        target.unlink()
        value = self.ledger()
        value["links"].pop(str(target))
        write_json(self.ownership, value)
        self.assert_refusal_unchanged()
        self.assertEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(self.ledger()["handoff"], record)
        self.assert_complete()

    def test_completed_omitted_declaration_resumes_between_fresh_pairs(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        record = self.ledger()["handoff"]
        site = json.loads(self.site.read_text())
        site.pop("skill_handoffs")
        write_json(self.site, site)
        self.assertEqual(self.run_assets("remove").returncode, 0)
        # Existing injection stops after the first completed pair checkpoint,
        # not midway through an unrecorded pair or during the original handoff.
        stopped = self.run_assets(fault="after-complete")
        self.assertEqual(stopped.returncode, 71, stopped.stderr)
        self.assertEqual(self.ledger()["handoff"], record)
        self.assertEqual(self.ledger()["links"],
                         {str(target): str(self.new) for target in self.targets()})
        protected = {target: entry_metadata(target) for target in self.targets()}
        for target in self.targets("site-adapter"):
            self.assertFalse(os.path.lexists(target))
        resumed = self.run_assets()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.ledger()["handoff"], record)
        self.assert_complete()
        for target, metadata in protected.items():
            self.assertEqual(entry_metadata(target), metadata)
        for target in self.targets("site-adapter"):
            self.assertEqual(self.ledger()["links"][str(target)], str(self.adapter))
        self.assertNotIn("ordinary_operation", self.ledger())

    def test_v2_remove_disabled_selection_preserves_drift_and_provenance(self):
        self.install_old()
        self.activate()
        self.assertEqual(self.run_assets().returncode, 0)
        record = self.ledger()["handoff"]
        write_json(self.state, {"features": {"agent-assets": {"enabled": False}}})
        target = self.targets()[0]
        target.unlink()
        target.symlink_to(self.outside)
        before = FIXTURE.snapshot(self.root)
        self.assertNotEqual(self.run_assets("remove", dry=True).returncode, 0)
        self.assertEqual(FIXTURE.snapshot(self.root), before)
        self.assertNotEqual(self.run_assets("remove").returncode, 0)
        self.assertEqual(os.readlink(target), str(self.outside))
        self.assertEqual(self.ledger()["links"], {str(target): str(self.new)})
        self.assertEqual(self.ledger()["handoff"], record)
        self.assertEqual(self.ledger()["version"], 2)

    def test_v2_removal_needs_only_ledger_when_selection_is_unavailable(self):
        for complete in (False, True):
            for selection in ("unconfigured", "missing-manifest", "discovery-error"):
                for drift in (False, True):
                    with self.subTest(complete=complete, selection=selection, drift=drift):
                        fixture = type(self)("runTest")
                        fixture.setUp()
                        try:
                            if complete:
                                fixture.install_old()
                            fixture.activate()
                            self.assertEqual(fixture.run_assets().returncode, 0)
                            record = fixture.ledger()["handoff"]
                            self.assertEqual(record is not None, complete)
                            if selection == "missing-manifest":
                                fixture.public.unlink()
                            elif selection == "discovery-error":
                                relocated = fixture.root / "relocated-new-source"
                                fixture.new.rename(relocated)
                                fixture.new.symlink_to(relocated, target_is_directory=True)
                                fixture.assert_refusal_unchanged()
                            target = fixture.targets()[0]
                            if drift:
                                target.unlink()
                                target.symlink_to(fixture.outside)
                                target_metadata = entry_metadata(target)
                            sources_before = {name: metadata_snapshot(fixture.root / name)
                                              for name in ("public", "site", "outside")}
                            options = {"without_manifests": selection == "unconfigured"}
                            before = FIXTURE.snapshot(fixture.root)
                            metadata = metadata_snapshot(fixture.root)
                            expected_exit = 1 if drift else 0
                            result = fixture.run_assets("remove", dry=True, **options)
                            self.assertEqual(result.returncode, expected_exit, result.stderr)
                            self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                            self.assertEqual(metadata_snapshot(fixture.root), metadata)
                            result = fixture.run_assets("remove", **options)
                            self.assertEqual(result.returncode, expected_exit, result.stderr)
                            value = fixture.ledger()
                            self.assertEqual(value["version"], 2)
                            self.assertEqual(value["handoff"], record)
                            self.assertEqual(value["links"], {str(target): str(fixture.new)} if drift else {})
                            if drift:
                                self.assertEqual(os.readlink(target), str(fixture.outside))
                                self.assertEqual(entry_metadata(target), target_metadata)
                            self.assertEqual({name: metadata_snapshot(fixture.root / name)
                                              for name in sources_before}, sources_before)
                        finally:
                            fixture.doCleanups()

    def test_partial_activation_and_wrong_harness_refuse(self):
        self.install_old()
        self.activate()
        public = json.loads(self.public.read_text())
        site = json.loads(self.site.read_text())
        write_json(self.site, self.site_v1)
        self.assert_refusal_unchanged()
        write_json(self.site, site)
        write_json(self.public, self.public_v1)
        self.assert_refusal_unchanged()
        write_json(self.public, public)
        site["skill_groups"][0]["targets"] = ["claude"]
        write_json(self.site, site)
        self.assert_refusal_unchanged()

    def test_first_v1_scaffolding_explicit_and_xdg_default(self):
        self.ownership = self.root / "owned-state/child/owned.json"
        self.assertEqual(self.run_assets().returncode, 0)
        self.assertEqual(stat.S_IMODE(self.ownership.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.ownership.parent.parent.stat().st_mode), 0o700)
        self.assertEqual(self.run_assets(default_ownership=True).returncode, 0)
        self.assertTrue((self.home / ".local/state/envy/agent-assets.json").is_file())

    def test_absent_read_only_and_remove_do_not_create_scaffolding(self):
        self.ownership = self.root / "absent-state/child/owned.json"
        before = FIXTURE.snapshot(self.root)
        for action, dry in (("converge", True), ("status", False), ("verify", False),
                            ("doctor", False), ("remove", False), ("remove", True)):
            self.run_assets(action, dry=dry)
            self.assertEqual(FIXTURE.snapshot(self.root), before)

    def test_raw_ownership_and_xdg_components_are_not_silently_normalized(self):
        self.ownership = str(self.root) + "/owned-state/./owned.json"
        self.assert_refusal_unchanged()
        self.ownership = self.root / "owned.json"
        for suffix in ("/state/./child", "/state//child", "/state/../child"):
            self.assert_refusal_unchanged(default_ownership=True, xdg_state_home=str(self.root) + suffix)

    def test_foreign_parent_and_post_scaffolding_conflict(self):
        alias = self.root / "alias"
        alias.symlink_to(self.outside, target_is_directory=True)
        self.ownership = alias / "owned.json"
        self.assert_refusal_unchanged()
        writable = self.root / "writable"
        writable.mkdir(mode=0o700)
        writable.chmod(0o777)
        self.ownership = writable / "owned.json"
        self.assert_refusal_unchanged()
        self.ownership = self.root / "owned-state/child/owned.json"
        result = self.run_assets(fault="scaffold-race")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.ownership.exists())
        self.assertTrue(self.ownership.parent.is_dir())
        self.assertTrue(self.ownership.with_name(self.ownership.name + ".lock").is_file())
        self.assertFalse(self.targets()[0].exists())
        self.assertIn(b"created_ownership_directories", result.stderr)

    def test_modeled_root_ancestor_requires_sticky_when_writable(self):
        for case, allowed in (("root-0777", False), ("root-0775", False), ("root-0755", True),
                              ("root-1777", True), ("user-1777", False)):
            with self.subTest(case=case):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    ancestor = fixture.root / "modeled-ancestor"
                    ancestor.mkdir(mode=0o700)
                    anchor = ancestor / "private"
                    anchor.mkdir(mode=0o700)
                    fixture.ownership = anchor / "owned.json"
                    write_json(fixture.public, {**fixture.public_v1, "version": 2})
                    write_json(fixture.resolved, {"resources": {"agent_asset_manifests": [str(fixture.public)]}})
                    installed = fixture.run_assets()
                    self.assertEqual(installed.returncode, 0, installed.stderr)
                    before, metadata = FIXTURE.snapshot(fixture.root), metadata_snapshot(fixture.root)
                    result = fixture.run_assets("status", fault="ancestor-" + case)
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                    records = [json.loads(line)["ancestor_witness"] for line in result.stderr.splitlines()
                               if line.startswith(b'{"ancestor_witness":')]
                    self.assertEqual(len(records), 1, result.stderr)
                    witness = records[0]
                    self.assertEqual(witness["uid"], 0 if case.startswith("root-") else os.geteuid())
                    self.assertEqual(witness["mode"], oct(int(case.split("-")[1], 8)))
                    self.assertGreater(witness["stat"], 0)
                    if allowed:
                        self.assertGreater(witness["fstat"], 0)
                    else:
                        self.assertIn(b"ownership path directory is writable by others", result.stdout + result.stderr)
                    self.assertTrue(witness["real_metadata_unchanged"])
                    self.assertEqual(FIXTURE.snapshot(fixture.root), before)
                    self.assertEqual(metadata_snapshot(fixture.root), metadata)
                    self.assertEqual(stat.S_IMODE(ancestor.lstat().st_mode), 0o700)
                    self.assertEqual(ancestor.lstat().st_uid, os.geteuid())
                finally:
                    fixture.doCleanups()

    def test_scaffolding_appearance_reports_only_its_owned_prefix(self):
        self.ownership = self.root / "owned-state/child/owned.json"
        result = self.run_assets(fault="scaffold-appearance")
        self.assertNotEqual(result.returncode, 0)
        receipts = [json.loads(line) for line in result.stderr.splitlines()
                    if line.startswith(b'{"created_ownership_directories"')]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["created_ownership_directories"], [str(self.root / "owned-state")])
        self.assertEqual(receipts[0]["created_ownership_controls"], [])
        self.assertEqual(stat.S_IMODE(self.ownership.parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.ownership.parent.parent.stat().st_mode), 0o700)
        self.assertFalse(self.ownership.exists())
        self.assertFalse(self.ownership.with_name(self.ownership.name + ".lock").exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_preliminary_first_component_suffix_and_anchor_observations_are_bound(self):
        for fault in ("scaffold-first-appearance", "scaffold-suffix-appearance", "scaffold-anchor-replaced"):
            with self.subTest(fault=fault):
                fixture = type(self)("runTest")
                fixture.setUp()
                try:
                    anchor = fixture.root / "anchor"
                    anchor.mkdir(mode=0o700)
                    fixture.ownership = anchor / "owned-state/child/owned.json"
                    sentinel_metadata = entry_metadata(fixture.outside / "sentinel")
                    result = fixture.run_assets(fault=fault)
                    self.assertNotEqual(result.returncode, 0)
                    records = [json.loads(line)["injected_scaffolding"] for line in result.stderr.splitlines()
                               if line.startswith(b'{"injected_scaffolding"')]
                    self.assertEqual(len(records), 1)
                    for path, metadata in records[0].items():
                        self.assertEqual(entry_metadata(Path(path)), metadata)
                    self.assertEqual(entry_metadata(fixture.outside / "sentinel"), sentinel_metadata)
                    self.assertNotIn(b"created_ownership_directories", result.stderr)
                    self.assertFalse(fixture.ownership.exists())
                    self.assertFalse(fixture.ownership.with_name(fixture.ownership.name + ".lock").exists())
                    self.assertFalse((fixture.home / ".claude").exists())
                    if fault != "scaffold-suffix-appearance":
                        self.assertFalse(fixture.ownership.parent.exists())
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
