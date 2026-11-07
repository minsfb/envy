#!/usr/bin/env python3
"""Offline installation/fixture checks, not agent behavior evaluations."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "assets/agent-assets/skills/reflect"
FIXTURE = ROOT / "tests/fixtures/reflect"
SPEC = importlib.util.spec_from_file_location("reflect_fixture", FIXTURE / "prepare.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the owned reflect preparer")
PREPARER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARER)


ASSET_SPEC = importlib.util.spec_from_file_location("asset_fixture", ROOT / "tests/asset_fixture.py")
if ASSET_SPEC is None or ASSET_SPEC.loader is None:
    raise RuntimeError("Cannot load the owned asset fixture")
ASSET_FIXTURE = importlib.util.module_from_spec(ASSET_SPEC)
ASSET_SPEC.loader.exec_module(ASSET_FIXTURE)
snapshot = ASSET_FIXTURE.snapshot
asset_environment = ASSET_FIXTURE.asset_environment


class ReflectAssetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({
            "schema_version": 3, "installed": True,
            "features": {"agent-assets": {"enabled": True}},
        }), encoding="utf-8")

    def run_assets(self, action="converge", manifest=None, platform="linux"):
        manifest = manifest or ROOT / "assets/agent-assets.json"
        ownership = self.root / "owned.json"
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "features/agent-assets.py"), action,
             "--manifest", str(manifest),
             "--state", str(self.state), "--ownership", str(ownership),
             "--json"],
            cwd=ROOT,
            env=asset_environment(self.home, manifest, self.state, ownership, platform),
            capture_output=True, text=True, check=False, timeout=15,
        )

    def links(self):
        return [self.home / name / "skills/reflect" for name in (".claude", ".agents")]

    def test_both_harnesses_share_canonical_source_and_reconverge(self) -> None:
        expected = (SKILL / "SKILL.md").read_bytes()
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                for _ in range(2):
                    result = self.run_assets(platform=platform)
                    self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
                for link in self.links():
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(link.resolve(), SKILL)
                    self.assertTrue((link / "SKILL.md").read_bytes() == expected,
                                    "Installed reflect body differs from its pre-convergence source")
                ownership = json.loads((self.root / "owned.json").read_text())
                self.assertEqual(ownership["version"], 2)
                for link in self.links():
                    self.assertTrue(ownership["links"].get(str(link)) == str(SKILL),
                                    "Ownership must record both canonical reflect links")
                for target, source in ownership["links"].items():
                    link = Path(target)
                    self.assertTrue(self.home in link.parents and link.is_symlink(),
                                    "Recorded links must belong to the owned installation")
                    self.assertTrue(link.resolve() == Path(source),
                                    "Recorded source must match the installed link")
                result = self.run_assets("verify", platform=platform)
                self.assertEqual(result.returncode, 0, "Owned asset command must succeed")

    def test_owned_previous_source_moves_to_current_source(self) -> None:
        old_assets = self.root / "old-assets"
        shutil.copytree(ROOT / "assets", old_assets)
        old_skill = old_assets / "agent-assets/skills/reflect/SKILL.md"
        old_skill.write_text("---\nname: reflect\ndescription: Previous fixture skill.\n---\n",
                             encoding="utf-8")
        # This old fixture moves reflect, not an unrelated protected triage pair.
        old_manifest = old_assets / "agent-assets.json"
        declared = json.loads(old_manifest.read_text(encoding="utf-8"))
        common = next(group for group in declared["skill_groups"] if group["name"] == "common")
        common.setdefault("exclude_names", []).append("pr-triage")
        declared["skill_contracts"].remove({"group": "common", "name": "pr-triage", "version": 1})
        old_manifest.write_text(json.dumps(declared), encoding="utf-8")
        result = self.run_assets(manifest=old_manifest)
        self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
        triage_links = [self.home / harness / "skills/pr-triage"
                        for harness in (".claude", ".agents")]
        for link in triage_links:
            self.assertFalse(os.path.lexists(link))
        for link in self.links():
            self.assertEqual(link.resolve(), old_skill.parent)
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
        for link in triage_links:
            self.assertEqual(os.readlink(link), str(ROOT / "assets/agent-assets/skills/pr-triage"))
        for link in self.links():
            self.assertEqual(link.resolve(), SKILL)

    def test_modified_installed_entry_is_preserved_without_partial_links(self) -> None:
        result = self.run_assets()
        self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
        modified, other = self.links()
        modified.unlink()
        modified.mkdir()
        local_skill = modified / "SKILL.md"
        local_skill.write_text("user-owned local skill\n", encoding="utf-8")
        other.unlink()
        result = self.run_assets()
        self.assertEqual(result.returncode, 1)
        expected_error = (
            "Issues:\n  ERROR   ownership-drift: "
            f"recorded target is changed: {modified}; inspect agent-assets status "
            "and the recorded link before reconciling it; preserve foreign entries "
            "and do not delete the ownership ledger\n"
            "  ERROR   target-conflict: "
            f"refusing to replace unmanaged target: {modified}\n"
        )
        self.assertTrue(
            result.stderr == expected_error,
            "Expected exact ordered ownership-drift and target-conflict diagnostics",
        )
        self.assertTrue(result.stdout == "", "Target conflict must not emit stdout")
        self.assertTrue(local_skill.read_text() == "user-owned local skill\n",
                        "Target conflict must preserve the user-owned body")
        self.assertFalse(other.exists())
        self.assertFalse(other.is_symlink())

    def test_unowned_symlink_is_preserved_without_partial_links(self) -> None:
        expected = (SKILL / "SKILL.md").read_bytes()
        source_before = snapshot(SKILL)
        for conflict_index in (0, 1):
            with self.subTest(conflict_index=conflict_index):
                result = self.run_assets()
                self.assertTrue(result.returncode == 0, "Owned installation must succeed")
                outside = self.root / ("outside-" + str(conflict_index))
                outside.mkdir()
                (outside / "SKILL.md").write_text("outside fixture body\n", encoding="utf-8")
                links = self.links()
                modified, other = links[conflict_index], links[1 - conflict_index]
                modified.unlink()
                modified.symlink_to(outside, target_is_directory=True)
                other.unlink()
                link_text = os.readlink(modified)
                outside_before = snapshot(outside)
                home_before = snapshot(self.home)
                ownership_before = (self.root / "owned.json").read_bytes()
                result = self.run_assets()
                self.assertEqual(result.returncode, 1)
                expected_error = (
                    "Issues:\n  ERROR   ownership-drift: "
                    f"recorded target is changed: {modified}; inspect agent-assets status "
                    "and the recorded link before reconciling it; preserve foreign entries "
                    "and do not delete the ownership ledger\n"
                    "  ERROR   target-conflict: "
                    f"refusing to replace unmanaged target: {modified}\n"
                )
                self.assertTrue(result.stderr == expected_error,
                                "Expected exact ordered ownership-drift and target-conflict diagnostics")
                self.assertTrue(result.stdout == "", "Symlink conflict must not emit stdout")
                self.assertTrue(modified.is_symlink() and os.readlink(modified) == link_text,
                                "Conflict must preserve the unowned symlink")
                self.assertTrue(snapshot(outside) == outside_before,
                                "Conflict must preserve the external target contents")
                self.assertTrue(snapshot(self.home) == home_before,
                                "Conflict must not partially change the owned installation")
                self.assertTrue(snapshot(SKILL) == source_before,
                                "Conflict must preserve the canonical source")
                self.assertTrue((self.root / "owned.json").read_bytes() == ownership_before,
                                "Conflict must not rewrite the ownership record")
                self.assertFalse(other.exists())
                self.assertFalse(other.is_symlink())

                # Model explicit operator resolution; remove only our fixture's link,
                # not the external target or an installer-discovered unmanaged entry.
                modified.unlink()
                for action in ("converge", "verify"):
                    result = self.run_assets(action)
                    self.assertTrue(result.returncode == 0, "Resolved conflict must converge and verify")
                    ownership = json.loads((self.root / "owned.json").read_text())
                    for link in self.links():
                        self.assertTrue(link.is_symlink() and link.resolve() == SKILL,
                                        "Recovery must restore both canonical reflect links")
                        self.assertTrue((link / "SKILL.md").read_bytes() == expected,
                                        "Recovered reflect content must match its source snapshot")
                        self.assertTrue(ownership["links"].get(str(link)) == str(SKILL),
                                        "Recovery must record both canonical reflect links")
                    self.assertTrue(snapshot(outside) == outside_before,
                                    "Recovery must preserve the external target contents")
                    self.assertTrue(snapshot(SKILL) == source_before,
                                    "Recovery must preserve the canonical source")

    def test_shared_snapshot_preserves_entry_kinds_without_following_links(self) -> None:
        tree = self.root / "snapshot-tree"
        tree.mkdir()
        (tree / "directory").mkdir()
        (tree / "file").write_bytes(b"fixture body")
        outside = self.root / "snapshot-outside"
        outside.mkdir()
        (outside / "sentinel").write_bytes(b"outside body")
        (tree / "external").symlink_to(outside, target_is_directory=True)
        (tree / "dangling").symlink_to("missing")
        expected = {
            "directory": ("directory",),
            "file": ("file", b"fixture body"),
            "external": ("symlink", str(outside)),
            "dangling": ("symlink", "missing"),
        }
        self.assertTrue(snapshot(tree) == expected,
                        "Shared snapshot must retain entry kinds without following links")

    def test_owned_paths_and_command_ignore_outside_overrides(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        context = {}
        owned_paths = (
            "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
            "ENVY_CONFIG_DIR", "ENVY_STATE_DIR", "ENVY_DATA_DIR", "ENVY_BIN_DIR",
            "ENVY_DIR", "ENVY_MANIFEST", "ENVY_AGENT_ASSETS_MANIFEST",
            "ENVY_STATE_FILE", "ENVY_AGENT_ASSETS_OWNERSHIP",
        )
        for name in owned_paths:
            target = outside / name
            target.mkdir()
            (target / "sentinel").write_text("outside owned-path sentinel\n")
            context[name] = str(target)
        tool_bin = outside / "bin"
        tool_bin.mkdir()
        for name in ("agent-assets", "python3"):
            command = tool_bin / name
            command.write_text("#!/bin/sh\nexit 93\n")
            command.chmod(0o700)
        context.update({
            "PATH": str(tool_bin) + os.pathsep + os.environ.get("PATH", ""),
            "ENVY_PLATFORM": "unsupported-fixture-platform",
            "ENVY_PLATFORM_HIERARCHY_OVERRIDE": "unsupported-fixture-platform",
            "ENVY_DRY_RUN": "true",
        })
        before = snapshot(outside)
        with patch.dict(os.environ, context):
            result = self.run_assets()
            self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
            result = self.run_assets("verify")
            self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
            result = self.run_assets("status")
            self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], str(self.state))
        self.assertEqual(report["ownership"], str(self.root / "owned.json"))
        self.assertEqual(report["platform"], "linux")
        self.assertEqual(report["launcher"]["path"],
                         str(self.home / ".local/bin/agent-assets"))
        for link in self.links():
            self.assertEqual(link.resolve(), SKILL)
        self.assertEqual(snapshot(outside), before)

    def test_installer_retains_unrelated_context_and_uses_explicit_command(self) -> None:
        context = {
            "HTTPS_PROXY": "https://proxy.example.invalid:443",
            "SSL_CERT_FILE": "fixture-ca",
            "CURL_CA_BUNDLE": "fixture-ca-bundle",
            "GIT_SSH_COMMAND": "fixture-ssh",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.sslVerify",
            "GIT_CONFIG_VALUE_0": "true",
            "FIXTURE_HARNESS_CONTEXT": "preserve",
        }
        with patch.dict(os.environ, context):
            with patch.object(subprocess, "run", wraps=subprocess.run) as runner:
                result = self.run_assets()
            self.assertEqual(result.returncode, 0, "Owned asset command must succeed")
            passed = runner.call_args.kwargs["env"]
            for name, value in context.items():
                self.assertTrue(passed[name] == value, "unrelated context was discarded")
            self.assertTrue(passed["PATH"] == os.environ["PATH"], "PATH was replaced")
            self.assertEqual(runner.call_args.args[0][:3],
                             [sys.executable, "-B", str(ROOT / "features/agent-assets.py")])
            self.assertEqual(runner.call_args.kwargs["cwd"], ROOT)
            self.assertEqual(runner.call_args.kwargs["timeout"], 15)


class ReflectFixtureTest(unittest.TestCase):
    @staticmethod
    def prepare(parent, name):
        destination = parent / name
        destination.mkdir()
        PREPARER.prepare(destination, (SKILL / "SKILL.md").read_text())
        return destination

    @staticmethod
    def config_context(root, channel, settings, inherited=None):
        if channel == "global":
            config = root / "fixture-global-config"
            for key, value in settings.items():
                try:
                    result = subprocess.run(
                        [PREPARER.fixture_git_executable(), "config", "--file", str(config),
                         key, value], capture_output=True, check=False, timeout=30,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError("synthetic Git config command timed out") from None
                if result.returncode:
                    raise RuntimeError("failed to prepare synthetic Git config")
            return {"GIT_CONFIG_GLOBAL": str(config)}
        inherited = os.environ if inherited is None else inherited
        if channel == "parameters":
            added = " ".join(
                "'" + (key + "=" + value).replace("'", "'\\''") + "'"
                for key, value in settings.items()
            )
            previous = inherited.get("GIT_CONFIG_PARAMETERS", "")
            return {"GIT_CONFIG_PARAMETERS": previous + (" " if previous and added else "") + added}
        raw_count = inherited.get("GIT_CONFIG_COUNT", "0")
        if not isinstance(raw_count, str) or re.fullmatch(r"[ \t\n\r\v\f]*[+-]?[0-9]+", raw_count) is None:
            raise RuntimeError("invalid inherited Git config count")
        try:
            count = int(raw_count)
        except ValueError:
            raise RuntimeError("invalid inherited Git config count") from None
        if count < 0:
            raise RuntimeError("invalid inherited Git config count")
        context = {"GIT_CONFIG_COUNT": str(count + len(settings))}
        for index, (key, value) in enumerate(settings.items(), start=count):
            context[f"GIT_CONFIG_KEY_{index}"] = key
            context[f"GIT_CONFIG_VALUE_{index}"] = value
        return context

    def test_config_parameter_mapping_preserves_absent_empty_and_nonempty_context(self) -> None:
        for case, previous in (("absent", None), ("empty", ""),
                               ("nonempty", "'fixture.original=older value'")):
            with self.subTest(case=case):
                inherited = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "fixture.counted",
                             "GIT_CONFIG_VALUE_0": "retained", "FIXTURE_CONTEXT": "retained"}
                if previous is not None:
                    inherited["GIT_CONFIG_PARAMETERS"] = previous
                before = dict(inherited)
                context = self.config_context(Path("."), "parameters",
                                              {"fixture.appended": "later value"}, inherited)
                expected = ((previous + " ") if previous else "") + "'fixture.appended=later value'"
                self.assertTrue(context == {"GIT_CONFIG_PARAMETERS": expected},
                                "Parameter composition changed its inherited prefix or separator")
                merged = {**inherited, **context}
                self.assertTrue(all(merged[key] == value for key, value in before.items()
                                    if key != "GIT_CONFIG_PARAMETERS"),
                                "Parameter composition discarded unrelated context")
                self.assertTrue(inherited == before, "Parameter composition mutated its input")
                unchanged = self.config_context(Path("."), "parameters", {}, inherited)
                self.assertTrue(unchanged == {"GIT_CONFIG_PARAMETERS": previous or ""},
                                "Empty additions changed the inherited parameter text")

    def test_config_count_mapping_appends_without_replacing_inherited_pairs(self) -> None:
        for case, raw_count, count in (("absent", None, 0), ("zero", "0", 0),
                                       ("nonzero", "2", 2), ("signed", "+2", 2)):
            with self.subTest(case=case):
                inherited = {"GIT_CONFIG_PARAMETERS": "'fixture.parameter=retained'",
                             "FIXTURE_CONTEXT": "retained"}
                if raw_count is not None:
                    inherited["GIT_CONFIG_COUNT"] = raw_count
                for index in range(count):
                    inherited[f"GIT_CONFIG_KEY_{index}"] = f"fixture.original{index}"
                    inherited[f"GIT_CONFIG_VALUE_{index}"] = "retained"
                before = dict(inherited)
                context = self.config_context(Path("."), "count",
                                              {"fixture.appended": "later value"}, inherited)
                expected = {"GIT_CONFIG_COUNT": str(count + 1),
                            f"GIT_CONFIG_KEY_{count}": "fixture.appended",
                            f"GIT_CONFIG_VALUE_{count}": "later value"}
                self.assertTrue(context == expected, "Counted config did not append at the inherited offset")
                merged = {**inherited, **context}
                self.assertTrue(all(merged[key] == value for key, value in before.items()
                                    if key != "GIT_CONFIG_COUNT"),
                                "Counted config replaced an inherited pair or unrelated value")
                self.assertTrue(inherited == before, "Counted config mutated its input")

    def test_config_count_mapping_rejects_malformed_counts_without_echo(self) -> None:
        for case, value in (("empty", ""), ("negative", "-1"), ("fraction", "1.5"),
                            ("text", "untrusted-count-text"), ("underscore", "1_0"),
                            ("trailing-space", "1 "), ("non-ascii", "\u0661"), ("not-text", None)):
            with self.subTest(case=case):
                inherited = {"GIT_CONFIG_COUNT": value, "FIXTURE_CONTEXT": "retained"}
                before = dict(inherited)
                with self.assertRaises(RuntimeError) as caught:
                    self.config_context(Path("."), "count", {"fixture.appended": "later"}, inherited)
                self.assertTrue(str(caught.exception) == "invalid inherited Git config count",
                                "Malformed count must produce only the static diagnostic")
                self.assertTrue(inherited == before, "Rejected count changed inherited context")

    def test_config_composition_retains_inherited_settings_in_owned_git(self) -> None:
        for channel in ("parameters", "count"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                destination = self.prepare(parent, "exercise")
                before = snapshot(destination)
                ambient = dict(os.environ)
                original = {"fixture.original": "older 'quoted' value"}
                # Both layers append to the actual caller context; neither clears it.
                initial = self.config_context(parent, channel, original)
                with patch.dict(os.environ, initial):
                    inherited = dict(os.environ)
                    added = self.config_context(parent, channel, {"fixture.appended": "later value"})
                    with patch.dict(os.environ, added):
                        self.assertTrue(all(os.environ.get(key) == value for key, value in inherited.items()
                                            if key not in added),
                                        "Composition discarded unrelated inherited environment")
                        with patch.object(subprocess, "run", wraps=subprocess.run) as runner:
                            for key, value in {**original, "fixture.appended": "later value"}.items():
                                self.assertTrue(PREPARER.git(destination / "product", "config", "--get", key).strip() == value,
                                                "Owned Git did not retain both configuration layers")
                        owned = {"GIT_ATTR_NOSYSTEM", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                 "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}
                        self.assertEqual(runner.call_count, 2)
                        for call in runner.call_args_list:
                            passed = call.kwargs["env"]
                            self.assertTrue(all(passed.get(key) == value for key, value in os.environ.items()
                                                if key not in owned),
                                            "Owned Git discarded caller security or transport context")
                self.assertTrue(dict(os.environ) == ambient, "Config contexts changed the caller environment")
                self.assertTrue(snapshot(destination) == before, "Config reads changed the owned fixture")

    def test_preparer_builds_isolated_dirty_repositories_and_shared_skill(self) -> None:
        module = PREPARER
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            module.prepare(root, (SKILL / "SKILL.md").read_text())
            self.assertEqual(module.git(root / "product", "status", "--porcelain=v1", "--untracked-files=all"),
                             " M config/local.txt\n?? notes.txt\n")
            for name in module.REPOSITORIES:
                self.assertEqual(module.git(root / name, "remote"), "")
            for name in (".claude", ".agents"):
                self.assertEqual(
                    (root / "home" / name / "skills/reflect").resolve(),
                    root / "portable-tools/assets/skills/reflect",
                )
            with self.assertRaises(ValueError):
                module.prepare(root, "do not overwrite")
        evals = json.loads((FIXTURE / "evals.json").read_text())["evals"]
        self.assertEqual(len({case["id"] for case in evals}), len(evals))
        self.assertTrue({case["id"] for case in evals} == {1, 2, 3, 4, 5, 6},
                        "Reflect evaluation case IDs must remain complete")
        for case in evals:
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expectations"])

    def test_routing_overrides_fail_before_content_and_preserve_outside_state(self) -> None:
        routing_variables = {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        }
        self.assertTrue(PREPARER.REPOSITORY_ROUTING_VARIABLES == routing_variables,
                        "Preparer routing guard must retain the seven-variable floor")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            outside = self.prepare(parent, "outside")
            before = snapshot(outside)
            for name in sorted(routing_variables):
                # Fixture preparation rejects presence, deliberately stricter
                # than the publisher's public-dispatch truthiness guard.
                for case, value in (("empty", ""), ("nonempty", str(outside / "product/.git"))):
                    with self.subTest(variable=name, case=case):
                        destination = parent / (name + "-" + case)
                        destination.mkdir()
                        with patch.dict(os.environ, {name: value}):
                            with self.assertRaisesRegex(
                                RuntimeError, "^refusing fixture with repository-routing Git variables set$",
                            ):
                                PREPARER.prepare(destination, "fixture skill")
                        self.assertEqual(list(destination.iterdir()), [])
                        self.assertEqual(snapshot(outside), before)

    def test_current_content_and_git_history_do_not_reveal_a_reverted_uncommitted_edit(self) -> None:
        # Fixture evidence limit, not a test of an agent's historical claims.
        with tempfile.TemporaryDirectory() as temporary:
            root = self.prepare(Path(temporary).resolve(), "exercise")
            repo = root / "product"
            document = repo / "docs/release.md"

            def observations():
                return {
                    "content": document.read_bytes(),
                    "status": PREPARER.git(repo, "--no-optional-locks", "status",
                                           "--porcelain=v1", "--untracked-files=all"),
                    "commits": PREPARER.git(repo, "log", "--all", "--format=%H"),
                    "reflog": (repo / ".git/logs/HEAD").read_bytes(),
                }

            before = observations()
            user_files = {name: (repo / name).read_bytes()
                          for name in ("config/local.txt", "notes.txt")}
            document.write_bytes(before["content"] + b"\nTemporary uncommitted procedure.\n")
            self.assertNotEqual(document.read_bytes(), before["content"])
            self.assertIn(" M docs/release.md", PREPARER.git(
                repo, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all"))
            document.write_bytes(before["content"])

            # A never-edited history and this applied-then-reverted history have
            # identical current content, status, commit history and HEAD reflog.
            self.assertEqual(observations(), before)
            self.assertEqual({name: (repo / name).read_bytes() for name in user_files},
                             user_files)

    def test_missing_git_fails_before_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with patch.dict(os.environ, {"PATH": str(destination)}):
                with self.assertRaisesRegex(RuntimeError, "requires Git"):
                    PREPARER.prepare(destination, "fixture skill")
            self.assertEqual(list(destination.iterdir()), [])

    def test_path_selected_git_preserves_context_and_dispatcher_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            tool_bin = root / "tools"
            tool_bin.mkdir()
            dispatcher = root / "dispatcher"
            dispatcher.write_text(
                '#!/bin/sh\n'
                '[ "${0##*/}" = "git" ] || exit 90\n'
                '[ "$GIT_SSH_COMMAND" = "fixture-ssh" ] || exit 91\n'
                '[ "$GIT_SSL_CAINFO" = "fixture-ca" ] || exit 92\n'
                '[ "$HTTPS_PROXY" = "https://proxy.example.invalid:443" ] || exit 93\n'
                '[ "$SSL_CERT_FILE" = "fixture-cert" ] || exit 94\n'
                '[ "$GIT_CONFIG_COUNT" = "1" ] || exit 95\n'
                '[ "$GIT_CONFIG_KEY_0" = "http.sslVerify" ] || exit 96\n'
                '[ "$GIT_CONFIG_VALUE_0" = "true" ] || exit 97\n'
                '[ "$GIT_CONFIG_GLOBAL" = "fixture-global" ] || exit 98\n'
                '[ "$GIT_ATTR_NOSYSTEM" = "1" ] || exit 99\n'
                '[ "$GIT_AUTHOR_NAME" = "Fixture" ] || exit 100\n'
                'printf "path-selected-git\\n"\n', encoding="utf-8",
            )
            dispatcher.chmod(0o700)
            (tool_bin / "git").symlink_to(dispatcher)
            context = {
                "PATH": os.path.relpath(tool_bin) + os.pathsep + os.environ.get("PATH", ""),
                "GIT_SSH_COMMAND": "fixture-ssh",
                "GIT_SSL_CAINFO": "fixture-ca",
                "HTTPS_PROXY": "https://proxy.example.invalid:443",
                "SSL_CERT_FILE": "fixture-cert",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.sslVerify",
                "GIT_CONFIG_VALUE_0": "true",
                "GIT_CONFIG_GLOBAL": "fixture-global",
                "GIT_ATTR_NOSYSTEM": "0",
                "GIT_AUTHOR_NAME": "caller identity",
            }
            with patch.dict(os.environ, context):
                self.assertEqual(PREPARER.git(root, "status"), "path-selected-git\n")

    def test_git_failure_does_not_disclose_wrapper_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / "git"
            command.write_text(
                "#!/bin/sh\nprintf 'untrusted-wrapper-output' >&2\nexit 23\n"
            )
            command.chmod(0o700)
            with patch.dict(os.environ, {"PATH": str(root)}):
                with self.assertRaises(RuntimeError) as caught:
                    PREPARER.git(root, "status")
            self.assertEqual(str(caught.exception), "fixture Git command failed (exit 23)")

    def test_git_timeout_is_static_and_non_echoing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            timeout = subprocess.TimeoutExpired(
                ["fixture-command"], 30, output=b"untrusted output", stderr=b"untrusted error",
            )
            with patch.object(PREPARER.subprocess, "run", side_effect=timeout) as runner:
                with self.assertRaises(RuntimeError) as caught:
                    PREPARER.git(Path(temporary), "status")
            self.assertTrue(str(caught.exception) == "fixture Git command timed out",
                            "Git timeout must use the static non-echoing diagnostic")
            self.assertTrue(caught.exception.__suppress_context__,
                            "Git timeout must suppress the captured exception context")
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args.kwargs["timeout"], 30)

    def test_global_config_timeout_is_static_and_non_echoing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            timeout = subprocess.TimeoutExpired(
                ["fixture-command"], 30, output=b"untrusted output", stderr=b"untrusted error",
            )
            with patch.object(subprocess, "run", side_effect=timeout) as runner:
                with self.assertRaises(RuntimeError) as caught:
                    self.config_context(Path(temporary), "global", {"fixture.key": "value"})
            self.assertTrue(str(caught.exception) == "synthetic Git config command timed out",
                            "Config timeout must use the static non-echoing diagnostic")
            self.assertTrue(caught.exception.__suppress_context__,
                            "Config timeout must suppress the captured exception context")
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args.kwargs["timeout"], 30)

    def test_owned_git_paths_template_content_and_preserved_security_config(self) -> None:
        for channel in ("global", "count", "parameters"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                outside = self.prepare(parent, "outside")
                template = outside / "template"
                template.mkdir()
                (template / "config").write_text(
                    '[core]\n\tworktree = ' + json.dumps(str(outside / "product")) + '\n'
                )
                (template / "template-sentinel").write_text("must not be copied\n")
                excludes = outside / "excludes"
                excludes.write_text("*\n")
                attributes = outside / "attributes"
                attributes.write_text("* filter=fixture\n")
                hook = outside / "unexpected-command"
                marker = outside / "unexpected-command-ran"
                hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 91\n")
                hook.chmod(0o700)
                hooks = outside / "hooks"
                hooks.mkdir()
                (hooks / "pre-commit").symlink_to(hook)
                security = {
                    "credential.helper": "fixture-credential-helper",
                    "http.proxy": "https://proxy.example.invalid:443",
                    "http.sslCAInfo": str(outside / "synthetic-ca"),
                    "http.sslVerify": "true",
                    "protocol.file.allow": "never",
                }
                settings = {
                    "core.worktree": str(outside / "product"),
                    "init.templateDir": str(template),
                    "core.excludesFile": str(excludes),
                    "core.attributesFile": str(attributes),
                    "core.hooksPath": str(hooks),
                    "core.fsmonitor": str(hook),
                    "core.ignoreStat": "true",
                    "filter.fixture.clean": str(hook),
                    "commit.gpgSign": "true",
                    "gpg.program": str(hook),
                    "status.showUntrackedFiles": "no",
                    **security,
                }
                context = self.config_context(parent, channel, settings)
                context.update({
                    "GIT_AUTHOR_NAME": "caller identity",
                    "GIT_AUTHOR_EMAIL": "caller@example.invalid",
                    "GIT_COMMITTER_NAME": "caller identity",
                    "GIT_COMMITTER_EMAIL": "caller@example.invalid",
                })
                before = snapshot(outside)
                with patch.dict(os.environ, context):
                    destination = self.prepare(parent, "exercise")
                    for name in PREPARER.REPOSITORIES:
                        repo = destination / name
                        self.assertEqual(PREPARER.git(repo, "rev-parse", "--show-toplevel").strip(),
                                         str(repo))
                        self.assertEqual(PREPARER.git(repo, "rev-parse", "--absolute-git-dir").strip(),
                                         str(repo / ".git"))
                        inherited_ignore_stat = PREPARER.git_output(
                            repo, "--git-dir", str(repo / ".git"), "--work-tree", str(repo),
                            "config", "--get", "core.ignoreStat",
                        )
                        self.assertTrue(inherited_ignore_stat.strip() == "true",
                                        "Owned control must observe the injected ignoreStat setting")
                        self.assertTrue(PREPARER.git(repo, "config", "--get", "core.ignoreStat").strip() == "false",
                                        "Fixture Git must override only the dirty-observation setting")
                        self.assertFalse((repo / ".git/template-sentinel").exists())
                        self.assertEqual(PREPARER.git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce").strip(),
                                         "Fixture|fixture@example.invalid|Fixture|fixture@example.invalid")
                        for key, value in security.items():
                            self.assertTrue(PREPARER.git(repo, "config", "--get", key).strip() == value,
                                            "inherited security config was not retained")
                    repo = destination / "product"
                    configured_status = PREPARER.git(repo, "status", "--short")
                    self.assertFalse("notes.txt" in configured_status,
                                     "presentation counterexample did not hide the untracked fixture")
                    self.assertTrue(configured_status == " M config/local.txt\n",
                                    "Configured status must retain the exact tracked fixture row")
                    observed = PREPARER.git(repo, "status", "--porcelain=v1", "--untracked-files=all")
                    self.assertTrue(observed == " M config/local.txt\n?? notes.txt\n",
                                    "explicit status options did not preserve the fixture observation")

                    control = self.prepare(parent, "ignore-stat-control") / "site-bundle"
                    git_output = PREPARER.git_output

                    def without_ignore_stat_override(directory, *arguments):
                        pairs = [index for index in range(len(arguments) - 1)
                                 if arguments[index:index + 2] == ("-c", "core.ignoreStat=false")]
                        self.assertTrue(len(pairs) == 1,
                                        "Counterexample must remove only the single ignoreStat override")
                        index = pairs[0]
                        return git_output(directory, *arguments[:index], *arguments[index + 2:])

                    def ambient_ignore_stat(directory, *arguments):
                        with patch.object(PREPARER, "git_output", side_effect=without_ignore_stat_override):
                            return PREPARER.git(directory, *arguments)

                    # ignoreStat affects index registration, not just a status call.
                    # Keep every other owned-path, hook/filter and security control.
                    relative = "adapters/registry.md"
                    for label, target, command, tag, expected_status in (
                        ("ambient-ignore-stat", control, ambient_ignore_stat, "h", ""),
                        ("wrapped-ignore-stat", destination / "site-bundle", PREPARER.git,
                         "H", " M adapters/registry.md\n"),
                    ):
                        with self.subTest(ignore_stat=label):
                            document = target / relative
                            original = document.read_bytes()
                            self.assertTrue(command(target, "status", "--porcelain=v1",
                                                    "--untracked-files=no", "--", relative) == "",
                                            "Counterexample must start from a clean tracked document")
                            # Refresh the otherwise-clean index even when its stat data matches.
                            command(target, "update-index", "--really-refresh")
                            self.assertTrue(command(target, "ls-files", "-v", "--", relative) ==
                                            tag + " " + relative + "\n",
                                            "Index registration did not discriminate assume-unchanged state")
                            changed = original + b"\nOwned ignoreStat counterexample.\n"
                            document.write_bytes(changed)
                            self.assertTrue(document.read_bytes() == changed and changed != original,
                                            "Counterexample must contain the owned tracked edit")
                            self.assertTrue(command(target, "status", "--porcelain=v1",
                                                    "--untracked-files=no", "--", relative) == expected_status,
                                            "Tracked status did not discriminate the ignoreStat override")
                            for key, value in security.items():
                                self.assertTrue(command(target, "config", "--get", key).strip() == value,
                                                "IgnoreStat comparison discarded inherited security config")
                self.assertEqual(snapshot(outside), before)
                self.assertFalse(marker.exists())

    def test_revision_read_supports_a_linked_source_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            destination = self.prepare(parent, "exercise")
            repo = destination / "product"
            linked = parent / "linked-source"
            PREPARER.git(repo, "worktree", "add", "--detach", str(linked), "HEAD")
            self.assertTrue((linked / ".git").is_file())
            self.assertEqual(PREPARER.git_output(linked, "--no-optional-locks", "show", "HEAD:docs/release.md"),
                             (repo / "docs/release.md").read_text())


if __name__ == "__main__":
    unittest.main()
