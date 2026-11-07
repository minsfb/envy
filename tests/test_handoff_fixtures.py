#!/usr/bin/env python3
"""Check exercise integrity, not whether an agent produces a good handoff."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "handoff_fixture", ROOT / "tests/fixtures/handoff/prepare.py"
)
assert SPEC is not None and SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class HandoffFixtureTest(unittest.TestCase):
    @staticmethod
    def snapshot(repo: Path) -> dict[str, bytes]:
        # Include outside content, index, objects and refs, not just HEAD.
        return {
            str(path.relative_to(repo)): path.read_bytes()
            for path in repo.rglob("*") if path.is_file()
        }

    @staticmethod
    def config_context(root: Path, channel: str, settings: dict[str, str]) -> dict[str, str]:
        if channel == "global":
            config = root / "fixture-global-config"
            for key, value in settings.items():
                subprocess.run(
                    [FIXTURE.fixture_git_executable(), "config", "--file", str(config), key, value],
                    check=True, capture_output=True,
                )
            return {"GIT_CONFIG_GLOBAL": str(config)}
        if channel == "parameters":
            return {"GIT_CONFIG_PARAMETERS": " ".join(
                "'" + (key + "=" + value).replace("'", "'\\''") + "'"
                for key, value in settings.items()
            )}
        context = {"GIT_CONFIG_COUNT": str(len(settings))}
        for index, (key, value) in enumerate(settings.items()):
            context[f"GIT_CONFIG_KEY_{index}"] = key
            context[f"GIT_CONFIG_VALUE_{index}"] = value
        return context

    def test_bundled_handoff_template_exists(self) -> None:
        skill = ROOT / "assets/agent-assets/skills/handoff"
        self.assertTrue((skill / "assets/handoff-template.md").is_file())

    def test_local_dirty_shelved_and_expired_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = FIXTURE.prepare(Path(temporary) / "exercise")
            repo = Path(state["project"])
            self.assertEqual(
                FIXTURE.git(repo, "symbolic-ref", "HEAD"), "refs/heads/work/labels"
            )
            self.assertEqual(FIXTURE.git(repo, "rev-parse", "HEAD"), state["local_revision"])
            self.assertEqual(FIXTURE.git(repo, "rev-parse", "main"), state["deployed_record"])
            self.assertEqual(
                FIXTURE.git(repo, "rev-parse", "shelf/unicode-labels"),
                state["shelved_revision"],
            )
            revisions = ("local_revision", "deployed_record", "shelved_revision")
            self.assertEqual(len({state[key] for key in revisions}), 3)
            self.assertEqual(FIXTURE.git(repo, "remote"), "")
            dirty_state = {" M notes.md", "?? scratch.txt"}
            self.assertEqual(
                set(FIXTURE.git(repo, "status", "--porcelain").splitlines()),
                dirty_state,
            )
            self.assertFalse((repo / ".temporary/validation.log").exists())
            self.assertTrue((repo / ".handoff/validation.md").is_file())
            self.assertEqual(
                FIXTURE.git(repo, "check-ignore", ".handoff/restricted-note.md"),
                ".handoff/restricted-note.md",
            )

            # Exercise the authorized command and show it leaves user work intact.
            before = {
                name: (repo / name).read_bytes()
                for name in ("notes.md", "scratch.txt")
            }
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover",
                 "-s", "tests", "-p", "test_*.py"],
                cwd=repo, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Ran 3 tests", result.stderr)
            self.assertEqual(before, {name: (repo / name).read_bytes() for name in before})
            self.assertEqual(FIXTURE.git(repo, "rev-parse", "HEAD"), state["local_revision"])
            self.assertEqual(
                set(FIXTURE.git(repo, "status", "--porcelain").splitlines()),
                dirty_state,
            )

    def test_missing_evidence_variant_has_no_durable_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = FIXTURE.prepare(Path(temporary) / "exercise", missing_evidence=True)
            repo = Path(state["project"])
            self.assertFalse((repo / ".temporary/validation.log").exists())
            self.assertFalse((repo / ".handoff/validation.md").exists())

    def test_existing_destination_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / "existing"
            existing.mkdir()
            sentinel = existing / "user-work.txt"
            sentinel.write_text("leave intact\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                FIXTURE.prepare(existing)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "leave intact\n")
            self.assertEqual(list(existing.iterdir()), [sentinel])

    def test_canonical_destination_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = FIXTURE.prepare(root / "unused" / ".." / "exercise")
            self.assertEqual(state["project"], str(root.resolve() / "exercise/project"))

    def test_dangling_destination_symlink_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "not-created"
            link = root / "existing-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(FileExistsError):
                FIXTURE.prepare(link)
            self.assertTrue(link.is_symlink())
            self.assertFalse(target.exists())

    def test_normalized_destination_preserves_final_symlink_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "not-created"
            link = root / "existing-link"
            link.symlink_to(target, target_is_directory=True)
            before = os.readlink(link)
            destination = root / "missing-component" / ".." / link.name

            with self.assertRaises(FileExistsError):
                FIXTURE.prepare(destination)

            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), before)
            self.assertFalse(target.exists())
            self.assertEqual(set(root.iterdir()), {link})

    def test_path_selected_git_retains_transport_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_bin = root / "tools"
            tool_bin.mkdir()
            fake_git = tool_bin / "git"
            dispatcher = root / "dispatcher"
            dispatcher.write_text(
                '#!/bin/sh\n'
                '[ "$GIT_SSH_COMMAND" = "fixture-ssh" ] || exit 91\n'
                '[ "$GIT_SSL_CAINFO" = "fixture-ca" ] || exit 92\n'
                '[ "$HTTPS_PROXY" = "https://proxy.example.invalid:443" ] || exit 93\n'
                '[ "${0##*/}" = "git" ] || exit 94\n'
                '[ "$GIT_CONFIG_COUNT" = "1" ] || exit 95\n'
                '[ "$GIT_CONFIG_KEY_0" = "http.sslVerify" ] || exit 96\n'
                '[ "$GIT_CONFIG_VALUE_0" = "true" ] || exit 97\n'
                '[ "$GIT_CONFIG_GLOBAL" = "fixture-global-config" ] || exit 98\n'
                '[ "$GIT_ATTR_NOSYSTEM" = "1" ] || exit 99\n'
                'printf "path-selected-git\\n"\n',
                encoding="utf-8",
            )
            dispatcher.chmod(0o700)
            fake_git.symlink_to(dispatcher)
            context = {
                "PATH": str(tool_bin) + os.pathsep + os.environ.get("PATH", ""),
                "GIT_SSH_COMMAND": "fixture-ssh",
                "GIT_SSL_CAINFO": "fixture-ca",
                "HTTPS_PROXY": "https://proxy.example.invalid:443",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.sslVerify",
                "GIT_CONFIG_VALUE_0": "true",
                "GIT_CONFIG_GLOBAL": "fixture-global-config",
                "GIT_ATTR_NOSYSTEM": "0",
            }
            with patch.dict(os.environ, context):
                self.assertEqual(FIXTURE.git(root, "status"), "path-selected-git")

    def test_missing_git_fails_before_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "not-created"
            with patch.dict(os.environ, {"PATH": str(root)}):
                with self.assertRaisesRegex(RuntimeError, "requires Git"):
                    FIXTURE.prepare(destination)
            self.assertFalse(destination.exists())

    def test_config_channels_preserve_outside_tree_and_security_settings(self) -> None:
        for channel in ("global", "count", "parameters"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                outside = Path(FIXTURE.prepare(root / "outside")["project"])
                before = self.snapshot(outside)
                security = {
                    "credential.helper": "fixture-credential-helper",
                    "http.proxy": "https://proxy.example.invalid:443",
                    "http.sslCAInfo": str(root / "synthetic-ca"),
                    "http.sslVerify": "true",
                    "protocol.file.allow": "never",
                }
                settings = {"core.worktree": str(outside), **security}
                with patch.dict(os.environ, self.config_context(root, channel, settings)):
                    state = FIXTURE.prepare(root / "exercise")
                    repo = Path(state["project"])
                    self.assertEqual(FIXTURE.git(repo, "rev-parse", "--show-toplevel"), str(repo))
                    self.assertEqual(FIXTURE.git(repo, "rev-parse", "--absolute-git-dir"), str(repo / ".git"))
                    for key, value in security.items():
                        self.assertTrue(FIXTURE.git(repo, "config", "--get", key) == value,
                                        "inherited configuration was not retained")
                self.assertEqual(self.snapshot(outside), before)

    def test_owned_binding_overrides_local_worktree_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outside = Path(FIXTURE.prepare(root / "outside")["project"])
            state = FIXTURE.prepare(root / "exercise")
            repo = Path(state["project"])
            before = self.snapshot(outside)
            FIXTURE.git(repo, "config", "--local", "core.worktree", str(outside))
            self.assertEqual(FIXTURE.git(repo, "rev-parse", "--show-toplevel"), str(repo))
            FIXTURE.git(repo, "checkout", "shelf/unicode-labels")
            self.assertTrue((repo / "unicode_experiment.py").is_file())
            FIXTURE.git(repo, "checkout", "work/labels")
            self.assertFalse((repo / "unicode_experiment.py").exists())
            self.assertEqual(FIXTURE.git(repo, "rev-parse", "HEAD"), state["local_revision"])
            self.assertEqual(self.snapshot(outside), before)

    def test_init_template_channels_cannot_install_routing_or_attributes(self) -> None:
        for channel in ("global", "count", "parameters", "template-environment"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                outside = Path(FIXTURE.prepare(root / "outside")["project"])
                template = root / "template"
                template.mkdir()
                subprocess.run(
                    [FIXTURE.fixture_git_executable(), "config", "--file",
                     str(template / "config"), "core.worktree", str(outside)],
                    check=True, capture_output=True,
                )
                (template / "info").mkdir()
                (template / "info/attributes").write_text("* filter=fixture\n", encoding="utf-8")
                (template / "outside-link").symlink_to(outside, target_is_directory=True)
                before = self.snapshot(outside)
                context = (
                    {"GIT_TEMPLATE_DIR": str(template)} if channel == "template-environment"
                    else self.config_context(root, channel, {"init.templateDir": str(template)})
                )
                with patch.dict(os.environ, context):
                    state = FIXTURE.prepare(root / "exercise")
                    repo = Path(state["project"])
                    self.assertEqual(FIXTURE.git(repo, "rev-parse", "--show-toplevel"), str(repo))
                    self.assertFalse((repo / ".git/info/attributes").exists())
                    self.assertFalse((repo / ".git/outside-link").is_symlink())
                self.assertEqual(self.snapshot(outside), before)

    def test_global_ignores_and_attributes_do_not_change_fixture_content(self) -> None:
        for channel in ("global", "count", "parameters"):
            for source in ("excludes", "attributes"):
                with self.subTest(channel=channel, source=source), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    inherited = root / "inherited-content-rules"
                    marker = root / "filter-ran"
                    inherited.write_text("*\n" if source == "excludes" else "* filter=env5fixture\n", encoding="utf-8")
                    filter_command = "printf invoked >> " + shlex.quote(str(marker)) + "; cat"
                    settings = {
                        ("core.excludesFile" if source == "excludes" else "core.attributesFile"): str(inherited),
                        "filter.env5fixture.clean": filter_command,
                        "filter.env5fixture.smudge": filter_command,
                        "filter.env5fixture.required": "true",
                    }
                    with patch.dict(os.environ, self.config_context(root, channel, settings)):
                        state = FIXTURE.prepare(root / "exercise")
                        repo = Path(state["project"])
                        self.assertFalse(marker.exists(), "inherited content filter ran")
                        self.assertEqual(
                            FIXTURE.git(repo, "show", "HEAD:labels.py"),
                            'def label(value):\n    return " ".join(value.split())',
                        )
                        self.assertEqual(
                            set(FIXTURE.git(repo, "status", "--porcelain").splitlines()),
                            {" M notes.md", "?? scratch.txt"},
                        )
                        self.assertEqual((repo / ".git/fixture-excludes").read_bytes(), b"")
                        self.assertEqual((repo / ".git/fixture-attributes").read_bytes(), b"")

    def test_repository_routing_is_rejected_without_outside_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = Path(FIXTURE.prepare(root / "outside")["project"])

            def snapshot() -> dict[str, bytes]:
                return {
                    str(path.relative_to(outside)): path.read_bytes()
                    for path in outside.rglob("*") if path.is_file()
                }

            before = snapshot()  # Includes user files, the index, objects and refs.
            routing = {
                "GIT_DIR": str(outside / ".git"),
                "GIT_WORK_TREE": str(outside),
                "GIT_COMMON_DIR": str(outside / ".git"),
                "GIT_INDEX_FILE": str(outside / ".git/index"),
                "GIT_OBJECT_DIRECTORY": str(outside / ".git/objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(outside / ".git/objects"),
                "GIT_NAMESPACE": "outside-fixture-namespace",
            }
            for name, value in routing.items():
                with self.subTest(variable=name):
                    destination = root / ("not-created-" + name)
                    with patch.dict(os.environ, {name: value}):
                        with self.assertRaisesRegex(
                            RuntimeError, "repository-routing"
                        ) as error:
                            FIXTURE.prepare(destination)
                        self.assertNotIn(value, str(error.exception))
                        with self.assertRaisesRegex(RuntimeError, "repository-routing"):
                            FIXTURE.git(outside, "status")
                    self.assertFalse(destination.exists())
                    self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
