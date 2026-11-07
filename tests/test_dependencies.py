#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import dependencies  # noqa: E402


def config_context(channel, settings, inherited=None):
    """Return only appended fixture settings; never replace inherited pairs."""
    inherited = os.environ if inherited is None else inherited
    if channel == "parameters":
        added = " ".join("'" + (key + "=" + value).replace("'", "'\\''") + "'"
                         for key, value in settings.items())
        previous = inherited.get("GIT_CONFIG_PARAMETERS", "")
        return {"GIT_CONFIG_PARAMETERS": previous + (" " if previous and added else "") + added}
    if channel != "count":
        raise RuntimeError("unsupported fixture Git config channel")
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


class GitConfigContextTest(unittest.TestCase):
    def test_parameters_preserve_prefix_quoting_and_unrelated_context(self) -> None:
        for case, previous in (("absent", None), ("empty", ""),
                               ("nonempty", "'fixture.original=earlier value'")):
            with self.subTest(case=case):
                inherited = {"FIXTURE_CONTEXT": "retained", "GIT_CONFIG_COUNT": "1",
                             "GIT_CONFIG_KEY_0": "fixture.counted", "GIT_CONFIG_VALUE_0": "retained"}
                if previous is not None:
                    inherited["GIT_CONFIG_PARAMETERS"] = previous
                before = dict(inherited)
                context = config_context("parameters", {"fixture.appended": "later ' value"}, inherited)
                expected = (previous + " " if previous else "") + "'fixture.appended=later '\\'' value'"
                self.assertTrue(context == {"GIT_CONFIG_PARAMETERS": expected},
                                "Parameter composition changed its prefix or quoting")
                merged = {**inherited, **context}
                self.assertTrue(all(merged[key] == value for key, value in before.items()
                                    if key != "GIT_CONFIG_PARAMETERS"),
                                "Parameter composition changed unrelated context")
                self.assertTrue(config_context("parameters", {}, inherited)
                                == {"GIT_CONFIG_PARAMETERS": previous or ""},
                                "Empty additions changed parameter text")
                self.assertTrue(inherited == before, "Parameter composition mutated its input")

    def test_count_appends_and_rejects_malformed_values_without_echo(self) -> None:
        for case, raw_count, count in (("absent", None, 0), ("zero", "0", 0),
                                       ("nonzero", "2", 2), ("signed", "+2", 2),
                                       ("leading-space", "\t+2", 2), ("negative-zero", "-0", 0)):
            with self.subTest(case=case):
                inherited = {"GIT_CONFIG_PARAMETERS": "'fixture.parameter=retained'",
                             "FIXTURE_CONTEXT": "retained"}
                if raw_count is not None:
                    inherited["GIT_CONFIG_COUNT"] = raw_count
                for index in range(count):
                    inherited[f"GIT_CONFIG_KEY_{index}"] = f"fixture.original{index}"
                    inherited[f"GIT_CONFIG_VALUE_{index}"] = "retained"
                before = dict(inherited)
                context = config_context("count", {"fixture.appended": "later value"}, inherited)
                expected = {"GIT_CONFIG_COUNT": str(count + 1),
                            f"GIT_CONFIG_KEY_{count}": "fixture.appended",
                            f"GIT_CONFIG_VALUE_{count}": "later value"}
                self.assertTrue(context == expected, "Counted config used the wrong append offset")
                merged = {**inherited, **context}
                self.assertTrue(all(merged[key] == value for key, value in before.items()
                                    if key != "GIT_CONFIG_COUNT"),
                                "Counted config replaced an inherited pair or unrelated value")
                self.assertTrue(inherited == before, "Counted config mutated its input")
        for case, value in (("empty", ""), ("negative", "-1"), ("fraction", "1.5"),
                            ("trailing-space", "1 "), ("unicode", "١"), ("non-text", 1)):
            with self.subTest(case=case):
                inherited = {"GIT_CONFIG_COUNT": value, "FIXTURE_CONTEXT": "retained"}
                before = dict(inherited)
                with self.assertRaises(RuntimeError) as caught:
                    config_context("count", {"fixture.appended": "later"}, inherited)
                self.assertTrue(str(caught.exception) == "invalid inherited Git config count",
                                "Malformed count diagnostic was not static")
                self.assertTrue(inherited == before, "Rejected count mutated its input")


class DependencyPinsTest(unittest.TestCase):
    ignore_stat = ("-c", "core.ignoreStat=false")

    def setUp(self) -> None:
        # Match the existing reflect fixture's Git boundary: refuse repository
        # redirection, retain native selection and unrelated security context.
        routing = {"GIT_CONFIG", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                   "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"}
        self.assertFalse(routing.intersection(os.environ), "fixture Git routing override")
        executable = shutil.which("git")
        self.assertIsNotNone(executable)
        self.executable = str(Path(executable).absolute())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--template=")
        self.assertNotIn("hooks", {entry.name for entry in (self.repo / ".git").iterdir()})
        (self.repo / ".git/fixture-hooks").mkdir()
        for name in ("fixture-attributes", "fixture-excludes"):
            (self.repo / ".git" / name).write_text("")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.repo / "value").write_text("one\n", encoding="utf-8")
        self.git("add", "value")
        self.git("commit", "-qm", "one")
        self.first = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repo / "value").write_text("two\n", encoding="utf-8")
        self.git("commit", "-qam", "two")
        # Keep verify_one's real Git observations/checkout and return codes;
        # scope only this test's process adapter, not subprocess globally.
        runner = mock.patch.object(dependencies, "run", side_effect=self.dependency_git)
        runner.start()
        self.addCleanup(runner.stop)

    def git(self, *arguments, check=True):
        environment = {**os.environ, "GIT_ATTR_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                       "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        result = subprocess.run(
            [self.executable, "--no-optional-locks", "--git-dir", str(self.repo / ".git"),
             "--work-tree", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgSign=false", "-c", "core.fsmonitor=false",
             *self.ignore_stat,
             "-c", f"core.hooksPath={self.repo / '.git/fixture-hooks'}",
             "-c", f"core.attributesFile={self.repo / '.git/fixture-attributes'}",
             "-c", f"core.excludesFile={self.repo / '.git/fixture-excludes'}", *arguments],
            cwd=self.repo, env=environment, text=True, capture_output=True, timeout=15)
        if check:
            self.assertEqual(result.returncode, 0, "owned fixture Git failed")
        return result

    def dependency_git(self, path, *arguments):
        self.assertEqual(Path(path), self.repo)
        return self.git(*arguments, check=False)

    def item(self) -> dict[str, str]:
        return {
            "name": "fixture",
            "installed_path": str(self.repo),
            "commit": self.first,
        }

    def test_clean_checkout_is_detached_at_exact_commit(self) -> None:
        dependencies.verify_one(self.item(), converge=True)
        actual = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(actual, self.first)
        dependencies.verify_one(self.item(), converge=False)

    def test_missing_checkout_is_drift_but_does_not_fail_convergence(self) -> None:
        # Verification must report drift so the owning feature reconverges and
        # re-clones; convergence itself only warns, so an unreachable external
        # never fails chezmoi or anything downstream of it.
        item = {**self.item(), "installed_path": str(self.repo / "absent")}
        with self.assertRaises(dependencies.DependencyError):
            dependencies.verify_one(item, converge=False)
        dependencies.verify_one(item, converge=True)

    def test_dirty_checkout_is_not_changed(self) -> None:
        (self.repo / "value").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(dependencies.DependencyError):
            dependencies.verify_one(self.item(), converge=True)

    def test_config_context_preserves_effective_prior_and_appended_settings(self) -> None:
        before = dict(os.environ)
        for channel in ("parameters", "count"):
            with self.subTest(channel=channel), mock.patch.dict(
                    os.environ, config_context(channel, {"fixture.original": "earlier ' value"})):
                with mock.patch.dict(os.environ, config_context(channel, {"fixture.appended": "later value"})):
                    for key, value in (("fixture.original", "earlier ' value"),
                                       ("fixture.appended", "later value")):
                        self.assertTrue(self.git("config", "--get", key).stdout == value + "\n",
                                        "Owned Git lost a prior or appended setting")
            self.assertTrue(dict(os.environ) == before, "Fixture changed its caller's context")

    def test_inherited_ignore_stat_cannot_hide_dirty_checkout(self) -> None:
        # Both cases inherit the same setting. Only this fixture's -c override
        # is omitted in the counterexample; no assume-unchanged flag is set by hand.
        with mock.patch.dict(os.environ, config_context("parameters", {"core.ignoreStat": "true"})):
            for protected in (True, False):
                with self.subTest(protected=protected):
                    fixture = type(self)("runTest")
                    try:
                        fixture.setUp()
                        overrides = fixture.ignore_stat if protected else ()
                        with mock.patch.object(fixture, "ignore_stat", overrides):
                            expected = "false\n" if protected else "true\n"
                            self.assertTrue(fixture.git("config", "--bool", "core.ignoreStat").stdout == expected,
                                            "Owned Git did not select the intended ignoreStat setting")
                            value = fixture.repo / "value"
                            value.write_bytes(b"two\n")
                            # A real stat registration marks matching entries assumed
                            # unchanged under ignoreStat; the protective override does not.
                            fixture.git("update-index", "--really-refresh")
                            expected_flag = "H value\n" if protected else "h value\n"
                            self.assertTrue(fixture.git("ls-files", "-v", "--", "value").stdout == expected_flag,
                                            "Registration did not establish the intended index flag")
                            value.write_bytes(b"dirty owned fixture\n")
                            index = fixture.repo / ".git/index"
                            before_index = index.read_bytes()
                            before_head = fixture.git("rev-parse", "HEAD").stdout
                            status = fixture.git("status", "--porcelain").stdout
                            self.assertTrue(status == (" M value\n" if protected else ""),
                                            "Owned dirty observation did not discriminate the override")
                            if protected:
                                with self.assertRaises(dependencies.DependencyError) as caught:
                                    dependencies.verify_one(fixture.item(), converge=True)
                                self.assertTrue(str(caught.exception)
                                                == f"refusing to change modified dependency: {fixture.repo}",
                                                "Dirty checkout was not refused at the expected boundary")
                            # The control demonstrates the blind status oracle only;
                            # do not ask it to check out over deliberately hidden edits.
                            self.assertTrue(value.read_bytes() == b"dirty owned fixture\n",
                                            "Dirty fixture bytes changed")
                            self.assertTrue(fixture.git("rev-parse", "HEAD").stdout == before_head,
                                            "Dirty fixture HEAD changed")
                            self.assertTrue(index.read_bytes() == before_index, "Dirty fixture index changed")
                    finally:
                        fixture.doCleanups()

    def test_inherited_hook_configuration_cannot_execute_in_fixture(self) -> None:
        outside = self.repo.parent / "ambient"
        outside.mkdir()
        hooks = outside / "hooks"
        hooks.mkdir()
        marker = outside / "hook-ran"
        for name in ("pre-commit", "post-checkout"):
            hook = hooks / name
            hook.write_text("#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexit 95\n")
            hook.chmod(0o700)
        config = outside / "gitconfig"
        config.write_text('[core]\n\thooksPath = "' + str(hooks) + '"\n')
        # Only the first subcase deliberately selects an owned global file.
        # Parameter/count subcases append without discarding inherited settings.
        contexts = (("global", {"GIT_CONFIG_GLOBAL": str(config)}),
                    ("parameters", config_context("parameters", {"core.hooksPath": str(hooks)})),
                    ("count", config_context("count", {"core.hooksPath": str(hooks)})))
        before = {str(path.relative_to(outside)): path.read_bytes()
                  for path in outside.rglob("*") if path.is_file()}
        # GIT_CONFIG redirects even an explicitly repository-bound git-config
        # write. Refuse before allocation or any native child can reach it.
        fixture = type(self)("runTest")
        with mock.patch.dict(os.environ, {"GIT_CONFIG": str(config)}), mock.patch.object(
                tempfile, "TemporaryDirectory", side_effect=AssertionError("unexpected allocation")) as allocation:
            with mock.patch.object(fixture, "git", side_effect=AssertionError("unexpected Git child")) as git:
                with self.assertRaisesRegex(AssertionError, "fixture Git routing override"):
                    fixture.setUp()
                allocation.assert_not_called()
                git.assert_not_called()
        self.assertEqual({str(path.relative_to(outside)): path.read_bytes()
                          for path in outside.rglob("*") if path.is_file()}, before)
        for channel, context in contexts:
            with self.subTest(channel=channel), mock.patch.dict(os.environ, context):
                fixture = type(self)("runTest")
                try:
                    fixture.setUp()
                    dependencies.verify_one(fixture.item(), converge=True)
                    self.assertEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.first)
                finally:
                    fixture.doCleanups()
                self.assertFalse(marker.exists())
                self.assertEqual({str(path.relative_to(outside)): path.read_bytes()
                                  for path in outside.rglob("*") if path.is_file()}, before)


if __name__ == "__main__":
    unittest.main()
