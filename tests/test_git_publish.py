"""Offline publication fixtures: every remote is an owned local bare repo."""

import json
import argparse
import importlib.util
import io
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets/agent-assets/skills/ship/scripts/git_publish.py"


def bounded_fixture_run(command, **kwargs):
    # This intentionally stricter 30-second bound is for owned local fixtures,
    # not the publisher's 120-second runtime guard or a public timing contract.
    # A fixture timeout does not establish that the publisher's own guard fired.
    try:
        return subprocess.run(command, timeout=30, **kwargs)
    except subprocess.TimeoutExpired:
        raise AssertionError("owned publication fixture command timed out") from None


def command_hook_allocation(case, scratch, plan, existing_hook, receipt):
    """Identify the live publisher allocation, not every native TMPDIR entry."""
    candidates = [path for path in scratch.iterdir() if path.name.startswith("git-publish-")]
    case.assertEqual(len(candidates), 1, "Expected exactly one command-hook allocation")
    allocation = candidates[0]
    metadata = allocation.lstat()
    case.assertTrue(stat.S_ISDIR(metadata.st_mode), "Command-hook allocation must be a directory")
    case.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
    case.assertEqual({path.name for path in allocation.iterdir()}, {"context.json", "pre-push"})
    context, wrapper = allocation / "context.json", allocation / "pre-push"
    for path, mode in ((context, 0o600), (wrapper, 0o700)):
        info = path.lstat()
        case.assertTrue(stat.S_ISREG(info.st_mode), "Command-hook input must be a regular file")
        case.assertEqual(stat.S_IMODE(info.st_mode), mode)
    expected_context = {"plan": plan, "existing_hook": str(existing_hook.resolve()),
                        "receipt_path": str(receipt.resolve())}
    case.assertEqual(context.read_text(), json.dumps(expected_context, indent=2, sort_keys=True) + "\n")
    command = [sys.executable, str(SCRIPT.resolve()), "_pre_push", "--context", str(context)]
    case.assertEqual(wrapper.read_text(), "#!/bin/sh\nexec " + " ".join(map(shlex.quote, command)) + ' "$@"\n')
    identity = (metadata.st_dev, metadata.st_ino)
    current = allocation.lstat()
    case.assertEqual((current.st_dev, current.st_ino), identity, "Allocation changed during validation")
    return allocation, identity


def assert_command_hook_removed(case, allocation):
    # lexists rejects a retained/replaced dangling symlink as well as a directory.
    case.assertFalse(os.path.lexists(allocation), "Validated command-hook allocation must be removed")


class CommandHookAllocationTests(unittest.TestCase):
    """Fixture-only discriminating controls; these do not run Git or the publisher."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="command-hook-control-")
        self.addCleanup(directory.cleanup)
        self.scratch = Path(directory.name).resolve()
        self.plan = {"candidate": "1" * 40, "destination": "refs/heads/feature"}
        self.hook, self.receipt = self.scratch / "existing-hook", self.scratch / "receipt.json"

    def allocation(self, name="git-publish-fixture"):
        path = self.scratch / name
        path.mkdir(mode=0o700)
        context = path / "context.json"
        context.write_text(json.dumps({"plan": self.plan, "existing_hook": str(self.hook),
                                       "receipt_path": str(self.receipt)}, indent=2, sort_keys=True) + "\n")
        context.chmod(0o600)
        wrapper = path / "pre-push"
        wrapper.write_text('#!/bin/sh\nexec %s %s _pre_push --context %s "$@"\n' % (
            shlex.quote(sys.executable), shlex.quote(str(SCRIPT.resolve())), shlex.quote(str(context))))
        wrapper.chmod(0o700)
        return path

    def observe(self):
        return command_hook_allocation(self, self.scratch, self.plan, self.hook, self.receipt)

    def test_positive_allocation_retains_path_and_inode(self):
        path = self.allocation()
        info = path.lstat()
        self.assertEqual(self.observe(), (path, (info.st_dev, info.st_ino)))

    def test_missing_allocation_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.observe()

    def test_ambiguous_allocation_is_rejected(self):
        self.allocation()
        self.allocation("git-publish-second")
        with self.assertRaises(AssertionError):
            self.observe()

    def test_wrong_context_is_rejected(self):
        path = self.allocation() / "context.json"
        original = json.loads(path.read_text())
        for key, wrong in (("plan", {**self.plan, "candidate": "2" * 40}),
                           ("existing_hook", str(self.scratch / "wrong-hook")),
                           ("receipt_path", str(self.scratch / "wrong-receipt"))):
            with self.subTest(field=key):
                path.write_text(json.dumps({**original, key: wrong}, indent=2, sort_keys=True) + "\n")
                with self.assertRaises(AssertionError):
                    self.observe()

    def test_wrong_wrapper_is_rejected(self):
        path = self.allocation() / "pre-push"
        path.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaises(AssertionError):
            self.observe()

    def test_symlink_allocation_is_rejected(self):
        target = self.allocation("different-directory")
        (self.scratch / "git-publish-link").symlink_to(target, target_is_directory=True)
        with self.assertRaises(AssertionError):
            self.observe()

    def test_removal_requires_absence_including_dangling_symlink(self):
        path = self.scratch / "git-publish-fixture"
        assert_command_hook_removed(self, path)
        path.mkdir()
        with self.assertRaises(AssertionError):
            assert_command_hook_removed(self, path)
        path.rmdir()
        path.symlink_to(self.scratch / "missing-target", target_is_directory=True)
        with self.assertRaises(AssertionError):
            assert_command_hook_removed(self, path)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="publication-test-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve()
        self.repo = self.base / "work"
        self.remote = self.base / "remote.git"
        self.other = self.base / "other.git"
        self.repo.mkdir()
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({"HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / "config"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "TMPDIR": str(self.scratch), "PYTHONDONTWRITEBYTECODE": "1"})
        self.git("init", "--bare", "--template=", str(self.remote))
        self.git("init", "--bare", "--template=", str(self.other))
        self.git("init", "--template=")
        for metadata in (self.remote, self.other, self.repo / ".git"):
            self.assertNotIn("hooks", {entry.name for entry in metadata.iterdir()})
            # Later hook controls populate only these explicitly owned empty directories.
            (metadata / "hooks").mkdir()
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.initial = self.commit("initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "origin", "HEAD:refs/heads/main")
        self.git("--git-dir", str(self.remote), "symbolic-ref", "HEAD", "refs/heads/main")
        self.git("switch", "-c", "feature")
        self.candidate = self.commit("candidate")
        self.plan_file = self.base / "plan.json"
        self.receipt = self.base / "receipt.json"

    def git(self, *args, allowed=(0,), cwd=None):
        result = bounded_fixture_run(["git", "-C", str(cwd or self.repo), *args], text=True, capture_output=True, env=self.env)
        self.assertIn(result.returncode, allowed, result.stderr)
        return result.stdout.strip()

    def commit(self, text):
        (self.repo / "content").write_text(text)
        self.git("add", "content")
        self.git("commit", "-m", text)
        return self.git("rev-parse", "HEAD")

    def call(self, *args, success=True, env=None):
        result = bounded_fixture_run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, env=env or self.env)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def plan(self, destination="refs/heads/feature", intent="feature", success=True, **options):
        args = ["plan", "--repo", str(options.pop("repo", self.repo)), "--source", options.pop("source", "HEAD"), "--remote", "origin", "--destination", destination, "--intent", intent, "--server-contract", "ordinary-refs", "--output", str(self.plan_file)]
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), value])
        result = self.call(*args, success=success)
        if success:
            return json.loads(result.stdout)
        return result

    def apply(self, plan, success=True, repo=None, **options):
        args = ["apply", "--repo", str(repo or self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), "--approve", options.get("approve", plan["digest"]), "--authorize", options.get("authorize", plan["plan"]["intent"])]
        return self.call(*args, success=success)

    def remote_oid(self, ref="refs/heads/feature", remote=None):
        return self.git("--git-dir", str(remote or self.remote), "rev-parse", "--verify", ref, allowed=(0, 128))

    def hook(self, script, configured=False):
        directory = self.repo / ("custom-hooks" if configured else ".git/hooks")
        directory.mkdir(exist_ok=True)
        path = directory / "pre-push"
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(0o700)
        if configured:
            self.git("config", "core.hooksPath", "custom-hooks")
        return path

    def test_tracking_main_push_defaults_tags_and_dirty_work_are_preserved(self):
        self.git("branch", "--set-upstream-to=origin/main", "feature")
        self.git("config", "push.default", "upstream")
        self.git("config", "remote.origin.push", "HEAD:refs/heads/main")
        self.git("config", "push.followTags", "true")
        self.git("tag", "-a", "candidate-tag", "-m", "tag")
        (self.repo / "untracked").write_text("unrelated")
        (self.repo / "content").write_text("uncommitted")
        status = self.git("status", "--porcelain")
        config = (self.repo / ".git/config").read_bytes()
        plan = self.plan()
        receipt = json.loads(self.apply(plan).stdout)
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(receipt["observed"]["oid"], self.candidate)
        self.assertEqual(self.remote_oid(), self.candidate)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)
        self.assertEqual(self.remote_oid("refs/tags/candidate-tag"), "")
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)
        self.assertEqual(self.plan_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_main_requires_separate_authority(self):
        result = self.plan(destination="refs/heads/main", success=False)
        self.assertIn("protected", result.stderr)
        self.assertFalse(self.plan_file.exists())
        plan = self.plan(destination="refs/heads/main", intent="protected")
        self.apply(plan, authorize="feature", success=False)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)
        self.apply(plan)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.candidate)

    def test_custom_protected_ref(self):
        result = self.plan(destination="refs/heads/release", protected_ref="refs/heads/release", success=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(
            result.stderr == "Default/protected destination requires protected-branch authorization, not feature publication.\n",
            "Expected the static protected-ref diagnostic",
        )
        self.assertFalse(self.plan_file.exists())
        self.assertFalse(self.receipt.exists())
        plan = self.plan(destination="refs/heads/release", intent="protected", protected_ref="refs/heads/release")
        self.assertEqual(plan["plan"]["destination"], "refs/heads/release")
        self.assertEqual(plan["plan"]["intent"], "protected")
        self.assertIn("refs/heads/release", plan["plan"]["protected_refs"])
        self.assertTrue(self.plan_file.is_file())
        self.assertFalse(self.receipt.exists())

    def test_invalid_custom_protected_refs_are_rejected(self):
        for case, protected_ref, diagnostic in (
            ("invalid-prefix", "refs/tags/release", "Protected refs must be full branch refs.\n"),
            ("malformed-branch", "refs/heads/release..candidate", "Git operation failed (exit 1); inspect privately; do not repair or retry automatically.\n"),
        ):
            with self.subTest(case=case):
                result = self.plan(protected_ref=protected_ref, success=False)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stdout == "", "Rejected planning must not emit stdout")
                self.assertTrue(result.stderr == diagnostic, "Expected the static protected-ref refusal")
                if case == "malformed-branch":
                    guard = self.load_guard()
                    args = argparse.Namespace(
                        repo=str(self.repo), source="HEAD", remote="origin",
                        destination="refs/heads/feature", intent="feature", protected_ref=[protected_ref],
                        server_contract="ordinary-refs", output=str(self.plan_file),
                    )
                    with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
                        guard, "git", wraps=guard.git,
                    ) as git_calls:
                        with self.assertRaises(guard.Stop) as stopped:
                            guard.make_plan(args)
                    self.assertTrue(str(stopped.exception) + "\n" == diagnostic,
                                    "Expected the same static malformed-ref refusal")
                    self.assertTrue(
                        bool(git_calls.call_args_list) and git_calls.call_args_list[-1].args ==
                        (str(self.repo), "check-ref-format", protected_ref),
                        "Malformed protection must fail at its Git ref-format check",
                    )
                self.assertFalse(self.plan_file.exists())
                self.assertFalse(self.receipt.exists())
                self.assertEqual(self.remote_oid(), "")
                self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_unrelated_custom_protected_ref_allows_feature_planning(self):
        plan = self.plan(protected_ref="refs/heads/release")
        self.assertEqual(plan["plan"]["destination"], "refs/heads/feature")
        self.assertEqual(plan["plan"]["intent"], "feature")
        self.assertIn("refs/heads/release", plan["plan"]["protected_refs"])
        self.assertTrue(self.plan_file.is_file())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.remote_oid(), "")
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_release_destination_allows_feature_intent_without_custom_protection(self):
        guard = self.load_guard()
        args = argparse.Namespace(
            repo=str(self.repo), source="HEAD", remote="origin",
            destination="refs/heads/release", intent="feature", protected_ref=[],
            server_contract="ordinary-refs", output=str(self.plan_file),
        )
        with mock.patch.dict(os.environ, self.env, clear=True):
            plan = guard.make_plan(args)
        self.assertTrue(plan["plan"]["destination"] == "refs/heads/release",
                        "Expected the unprotected release destination")
        self.assertTrue(plan["plan"]["intent"] == "feature", "Expected feature-only planning")
        self.assertFalse("refs/heads/release" in plan["plan"]["protected_refs"],
                         "Release must not be protected without an explicit rule")
        self.assertTrue(self.plan_file.is_file())
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.remote_oid("refs/heads/release") == "", "Planning must not publish release")
        self.assertTrue(self.remote_oid("refs/heads/main") == self.initial, "Planning must preserve main")

    def test_default_branch_not_named_main_is_protected(self):
        self.git("--git-dir", str(self.remote), "symbolic-ref", "HEAD", "refs/heads/production")
        self.git("push", "origin", "HEAD:refs/heads/production")
        self.plan(destination="refs/heads/production", success=False)

    def test_wrong_repository_is_rejected(self):
        plan = self.plan()
        second = self.base / "second"
        self.git("clone", "--template=", str(self.remote), str(second))
        self.assertFalse((second / ".git/hooks").exists())
        result = self.apply(plan, repo=second, success=False)
        self.assertIn("Wrong repository", result.stderr)
        self.assertEqual(self.remote_oid(), "")

    def test_moved_candidate_is_rejected(self):
        plan = self.plan()
        self.commit("later")
        result = self.apply(plan, success=False)
        self.assertIn("candidate moved", result.stderr)
        self.assertEqual(self.remote_oid(), "")

    def test_plan_digest_is_bound_to_approval(self):
        plan = self.plan()
        self.apply(plan, approve="0" * 64, success=False)
        plan["plan"]["destination"] = "refs/heads/main"
        self.plan_file.write_text(json.dumps(plan))
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_changed_remote_is_rejected(self):
        plan = self.plan()
        self.git("remote", "set-url", "origin", str(self.other))
        self.assertIn("endpoint changed", self.apply(plan, success=False).stderr)
        self.assertEqual(self.remote_oid(), "")
        self.assertEqual(self.remote_oid(remote=self.other), "")

    def test_destination_drift_is_rejected(self):
        plan = self.plan()
        self.git("push", "origin", self.initial + ":refs/heads/feature")
        self.assertIn("destination/default changed", self.apply(plan, success=False).stderr)
        self.assertEqual(self.remote_oid(), self.initial)

    def test_multiple_push_urls_and_mirror_are_rejected(self):
        self.git("remote", "set-url", "--add", "--push", "origin", str(self.remote))
        self.git("remote", "set-url", "--add", "--push", "origin", str(self.other))
        self.assertIn("one effective push URL", self.plan(success=False).stderr)
        self.git("config", "--unset-all", "remote.origin.pushurl")
        self.git("config", "remote.origin.mirror", "true")
        self.assertIn("Mirror", self.plan(success=False).stderr)

    def test_configured_push_options_are_rejected_without_exposing_values(self):
        for key, values in (
            ("push.pushOption", ["fixture-option-secret"]),
            ("push.pushOption", [""]),
            ("PUSH.PUSHOPTION", ["first-fixture-secret", "second-fixture-secret"]),
        ):
            with self.subTest(key=key, count=len(values)):
                self.git("config", "--unset-all", "push.pushOption", allowed=(0, 5))
                for value in values:
                    self.git("config", "--add", key, value)
                result = self.plan(success=False)
                self.assertIn("Configured push options", result.stderr)
                self.assertNotIn("fixture", result.stdout + result.stderr)
                self.assertFalse(self.plan_file.exists())
                self.assertFalse(self.receipt.exists())
                self.assertEqual(self.remote_oid(), "")

    def test_push_options_added_after_plan_stop_before_receipt_or_publication(self):
        plan = self.plan()
        self.git("config", "push.pushOption", "fixture-option-secret")
        result = self.apply(plan, success=False)
        self.assertIn("Configured push options", result.stderr)
        self.assertNotIn("fixture-option-secret", result.stdout + result.stderr + self.plan_file.read_text())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.remote_oid(), "")

    def test_hook_added_push_option_is_rejected_and_can_be_reconciled(self):
        self.hook("git config push.pushOption fixture-option-secret")
        plan = self.plan()
        result = self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), "")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["pre_push"], "guard_rejected")
        self.assertEqual(receipt["status"], "rejected")
        self.assertNotIn("fixture-option-secret", result.stdout + result.stderr + self.receipt.read_text())
        result = self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)
        self.assertIn("hook rejected", result.stderr)
        self.assertNotIn("fixture-option-secret", result.stdout + result.stderr + self.receipt.read_text())

    def test_push_endpoint_not_fetch_endpoint_is_verified(self):
        self.git("remote", "set-url", "--push", "origin", str(self.other))
        plan = self.plan()
        self.apply(plan)
        self.assertEqual(self.remote_oid(remote=self.other), self.candidate)
        self.assertEqual(self.remote_oid(), "")

    def test_effective_url_rewrite_is_bound_and_nested_rewrites_rejected(self):
        self.git("remote", "set-url", "origin", "fixture:repo")
        self.git("config", "url." + str(self.remote) + ".insteadOf", "fixture:repo")
        plan = self.plan()
        self.assertEqual(plan["plan"]["endpoint"], str(self.remote))
        self.git("config", "url." + str(self.other) + ".insteadOf", str(self.remote))
        self.assertIn("rewritten", self.apply(plan, success=False).stderr)

    def test_push_instead_of_uses_resolved_push_destination(self):
        self.git("config", "url." + str(self.other) + ".pushInsteadOf", str(self.remote))
        plan = self.plan()
        self.assertEqual(plan["plan"]["endpoint"], str(self.other))
        self.apply(plan)
        self.assertEqual(self.remote_oid(remote=self.other), self.candidate)
        self.assertEqual(self.remote_oid(), "")

    def test_credential_urls_never_enter_output_or_plan(self):
        for url in ("https://fixture-secret@example.invalid/repo", "https://example.invalid/repo?token=fixture-secret", "ext::fixture-secret"):
            with self.subTest(url=url):
                self.git("remote", "set-url", "origin", url)
                result = self.plan(success=False)
                self.assertNotIn("fixture-secret", result.stdout + result.stderr)
                self.assertFalse(self.plan_file.exists())

    def test_ambiguous_scp_credentials_are_rejected_before_contact(self):
        self.git("remote", "set-url", "origin", "fixture-user:fixture-secret@example.invalid:path")
        guard = self.load_guard()
        args = argparse.Namespace(
            repo=str(self.repo), source="HEAD", remote="origin",
            destination="refs/heads/feature", intent="feature", protected_ref=[],
            server_contract="ordinary-refs", output=str(self.plan_file),
        )
        # Observe the existing Git runner, not just the remote_state helper.
        # This is a runner-boundary trace, not arbitrary-network containment.
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
            guard, "git", wraps=guard.git,
        ) as git_calls:
            with self.assertRaises(guard.Stop) as stopped:
                guard.make_plan(args)
            self.assertGreater(git_calls.call_count, 0)
            self.assertFalse(
                any(command in call.args[1:] for call in git_calls.call_args_list
                    for command in ("ls-remote", "fetch", "push")),
                "Rejected endpoint must not reach a Git contact command",
            )
        self.assertTrue(
            str(stopped.exception) == "Credential-ambiguous scp-like endpoints are not supported; use a credential helper or SSH agent.",
            "Expected the static ambiguous-endpoint diagnostic",
        )
        self.assertFalse(self.plan_file.exists())
        self.assertFalse(self.receipt.exists())

        # The wrapping spy must also see a real read of our owned bare repo.
        self.git("remote", "set-url", "origin", str(self.remote))
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
            guard, "git", wraps=guard.git,
        ) as git_calls:
            plan = guard.make_plan(args)
            self.assertTrue(
                any(call.args[1:] == ("ls-remote", "--symref", "--", str(self.remote), "HEAD", args.destination)
                    for call in git_calls.call_args_list),
                "Owned-local positive must reach the real Git contact command",
            )
            self.assertFalse(
                any(command in call.args[1:] for call in git_calls.call_args_list
                    for command in ("fetch", "push")),
                "Planning must not fetch or publish",
            )
        self.assertEqual(plan["plan"]["candidate"], self.candidate)
        self.assertEqual(plan["plan"]["endpoint"], str(self.remote))
        self.assertTrue(self.plan_file.is_file())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.remote_oid(), "")
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_ambiguous_scp_cli_refusal_is_static_and_non_echoing(self):
        self.git("remote", "set-url", "origin", "fixture-user:fixture-secret@example.invalid:path")
        result = bounded_fixture_run(
            [sys.executable, str(SCRIPT), "plan", "--repo", str(self.repo),
             "--source", "HEAD", "--remote", "origin", "--destination", "refs/heads/feature",
             "--intent", "feature", "--server-contract", "ordinary-refs", "--output", str(self.plan_file)],
            text=True, capture_output=True, env=self.env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout == "", "Rejected endpoint must not emit stdout")
        self.assertTrue(
            result.stderr == "Credential-ambiguous scp-like endpoints are not supported; use a credential helper or SSH agent.\n",
            "Expected the static, non-echoing ambiguous-endpoint diagnostic",
        )
        self.assertFalse(self.plan_file.exists())
        self.assertFalse(self.receipt.exists())

    def test_ordinary_scp_user_endpoint_is_preserved(self):
        url = "fixture-user@example.invalid:owner/repo.git"
        self.git("remote", "set-url", "origin", url)
        guard = self.load_guard()
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(guard.endpoint(self.repo, "origin"), url)

    def test_relative_endpoint_from_subdirectory_uses_repository_root(self):
        subdirectory = self.repo / "subdirectory"
        subdirectory.mkdir()
        self.git("remote", "set-url", "origin", "../remote.git")
        marker = self.base / "existing-hook-ran"
        self.hook("touch " + str(marker))
        plan = self.plan(repo=subdirectory)
        self.assertEqual(plan["plan"]["endpoint"], str(self.remote))
        self.apply(plan, repo=subdirectory)
        self.call("verify", "--repo", str(subdirectory), "--plan", str(self.plan_file), "--receipt", str(self.receipt))
        self.assertEqual(self.remote_oid(), self.candidate)
        self.assertTrue(marker.exists())

    def test_rejecting_hook_remains_authoritative_from_subdirectory(self):
        subdirectory = self.repo / "subdirectory"
        subdirectory.mkdir()
        self.git("remote", "set-url", "origin", "../remote.git")
        marker = self.base / "rejecting-hook-ran"
        self.hook("touch " + str(marker) + "\nexit 1")
        plan = self.plan(repo=subdirectory)
        self.apply(plan, repo=subdirectory, success=False)
        self.call("verify", "--repo", str(subdirectory), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)
        self.assertEqual(self.remote_oid(), "")
        self.assertTrue(marker.exists())
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["pre_push"], "existing_hook_rejected")
        self.assertEqual(receipt["status"], "rejected")

    def test_existing_hook_rejection_preserves_hook_and_configuration(self):
        path = self.hook("echo fixture-secret >&2\nexit 1", configured=True)
        original = path.read_bytes()
        config = (self.repo / ".git/config").read_bytes()
        plan = self.plan()
        result = self.apply(plan, success=False)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)
        self.assertEqual(self.remote_oid(), "")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["pre_push"], "existing_hook_rejected")
        self.assertEqual(receipt["push_result"], "failed_or_interrupted")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_existing_hook_receives_original_name_endpoint_and_update(self):
        self.hook('printf "%s\\n%s\\n" "$1" "$2" > hook-args\ncat > hook-input')
        plan = self.plan()
        self.apply(plan)
        self.assertEqual((self.repo / "hook-args").read_text().splitlines(), ["origin", str(self.remote)])
        row = (self.repo / "hook-input").read_text().split()
        self.assertEqual(row[1:], [self.candidate, "refs/heads/feature", "0" * 40])

    def test_candidate_moved_by_existing_hook_is_rejected(self):
        self.hook('git update-ref refs/heads/feature ' + self.initial)
        plan = self.plan()
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), "")

    def test_hook_rejection_stays_authoritative_even_if_candidate_appears(self):
        self.hook('git -c core.hooksPath=/dev/null push ' + str(self.remote) + ' ' + self.candidate + ':refs/heads/feature\nexit 1')
        plan = self.plan()
        self.apply(plan, success=False)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["observed"]["oid"], self.candidate)
        self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)

    def test_remote_move_after_advertisement_is_rejected_by_receive_cas(self):
        server_hook = self.remote / "hooks/update"
        server_hook.write_text('#!/bin/sh\ngit update-ref refs/heads/feature ' + self.initial + '\n')
        server_hook.chmod(0o700)
        plan = self.plan()
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), self.initial)

    def test_remote_move_between_preflight_and_advertisement_is_rejected(self):
        # A launcher races *after* apply's ls-remote, just before real git push.
        # The push, advertisements, pre-push stdin and receive-pack are real.
        wrapper_dir = self.base / "launcher"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        actual_git = shutil.which("git", path=self.env["PATH"])
        wrapper.write_text('#!/bin/sh\nfor arg in "$@"; do\n  if [ "$arg" = push ]; then\n    ' + actual_git + ' --git-dir=' + str(self.remote) + ' update-ref refs/heads/feature ' + self.initial + '\n  fi\ndone\nexec ' + actual_git + ' "$@"\n')
        wrapper.chmod(0o700)
        plan = self.plan()
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + self.env["PATH"]
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), self.initial)

    def test_post_receive_change_is_reported_without_rollback(self):
        hook = self.remote / "hooks/post-receive"
        hook.write_text("#!/bin/sh\ngit update-ref refs/heads/feature " + self.initial + "\n")
        hook.chmod(0o700)
        plan = self.plan()
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), self.initial)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["push_result"], "completed")
        self.assertEqual(receipt["status"], "needs_reconciliation")

    def test_push_error_after_success_is_reconciled_not_retried(self):
        plan = self.plan()
        wrapper_dir = self.base / "launcher"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        actual_git = shutil.which("git", path=self.env["PATH"])
        wrapper.write_text('#!/bin/sh\nfor arg in "$@"; do\n  if [ "$arg" = push ]; then\n    ' + actual_git + ' "$@"\n    exit 1\n  fi\ndone\nexec ' + actual_git + ' "$@"\n')
        wrapper.chmod(0o700)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + self.env["PATH"]
        receipt = json.loads(self.apply(plan).stdout)
        self.assertEqual(receipt["push_result"], "failed_or_interrupted")
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(self.remote_oid(), self.candidate)

    def test_failed_readback_retains_receipt_for_read_only_recovery(self):
        plan = self.plan()
        wrapper_dir = self.base / "launcher"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        actual_git = shutil.which("git", path=self.env["PATH"])
        wrapper.write_text('#!/bin/sh\nfor arg in "$@"; do\n  if [ "$arg" = ls-remote ] && grep -q \'"pre_push": "accepted"\' ' + str(self.receipt) + '; then exit 1; fi\ndone\nexec ' + actual_git + ' "$@"\n')
        wrapper.chmod(0o700)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + self.env["PATH"]
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid(), self.candidate)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "attempting")
        self.assertEqual(receipt["push_result"], "completed")
        self.env["PATH"] = os.pathsep.join(self.env["PATH"].split(os.pathsep)[1:])
        self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt))
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "verified")

    def test_interruption_cleans_command_hook_and_retains_pending_receipt(self):
        marker = self.base / "hook-started"
        existing_hook = self.hook("touch " + str(marker) + "\nexec sleep 30")
        plan = self.plan()
        process = subprocess.Popen([sys.executable, str(SCRIPT), "apply", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), "--approve", plan["digest"], "--authorize", "feature"], env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "pre-push hook did not start")
            allocation, allocation_identity = command_hook_allocation(
                self, self.scratch, plan["plan"], existing_hook, self.receipt,
            )
            self.assertIsNone(process.poll(), "Publisher must still be live while its hook is blocked")
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=10)
            with self.subTest(check="native interruption result"):
                self.assertEqual(process.returncode, 130,
                                 "Interrupted publication must return the expected exit code")
                self.assertTrue(output == "", "Interrupted publication must not emit stdout")
                self.assertTrue(
                    error == "Publication interrupted; reconcile the receipt before any retry.\n",
                    "Expected the static SIGTERM reconciliation diagnostic",
                )
            with self.subTest(check="pending receipt"):
                self.assertEqual(json.loads(self.receipt.read_text()), {
                    "plan_digest": plan["digest"], "candidate": self.candidate,
                    "destination": "refs/heads/feature", "status": "attempting",
                    "pre_push": "pending", "server_contract": "ordinary-refs",
                    "push_result": "failed_or_interrupted",
                })
            # Native tools can allocate unrelated TMPDIR entries. Only the
            # positively validated publisher allocation is this cleanup oracle.
            # Neither path absence nor this remote read proves process quiescence.
            with self.subTest(check="command-hook cleanup", allocation_identity=allocation_identity):
                assert_command_hook_removed(self, allocation)
            with self.subTest(check="remote destination"):
                self.assertEqual(self.remote_oid(), "")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def load_guard(self):
        specification = importlib.util.spec_from_file_location("git_publication_guard", SCRIPT)
        guard = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(guard)
        return guard

    def test_rejected_hook_verdict_survives_interrupt_after_real_git_push(self):
        # Reproduce the combined race: a real hook publishes independently, then
        # rejects; the parent is interrupted immediately after Git returns.
        self.hook('git -c core.hooksPath=/dev/null push ' + str(self.remote) + ' ' + self.candidate + ':refs/heads/feature\nexit 1')
        plan = self.plan()
        guard = self.load_guard()
        real_git = guard.git
        real_temporary_directory = guard.tempfile.TemporaryDirectory
        hook_directories = []

        def observe_temporary_directory(*arguments, **keywords):
            directory = real_temporary_directory(*arguments, **keywords)
            path = Path(directory.name)
            if path.name.startswith("git-publish-"):
                hook_directories.append(path)
            return directory

        def interrupt_after_rejection(repo, *arguments, **keywords):
            try:
                return real_git(repo, *arguments, **keywords)
            except guard.Stop:
                if "push" in arguments:
                    raise KeyboardInterrupt
                raise

        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan_file), receipt=str(self.receipt), approve=plan["digest"], authorize="feature")
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
            guard, "git", side_effect=interrupt_after_rejection,
        ), mock.patch.object(guard.tempfile, "TemporaryDirectory", side_effect=observe_temporary_directory):
            with self.assertRaises(KeyboardInterrupt):
                guard.apply(args)
        self.assertEqual(len(hook_directories), 1, "Expected the real command-hook allocation")
        self.assertFalse(hook_directories[0].exists(), "Actual command-hook directory must be removed")
        self.assertFalse(hook_directories[0].is_symlink(), "Actual command-hook path must not remain a symlink")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["pre_push"], "existing_hook_rejected")
        self.assertEqual(receipt["push_result"], "failed_or_interrupted")
        self.assertEqual(self.remote_oid(), self.candidate)
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "rejected")

    def test_unknown_interrupted_approval_cannot_be_inferred_from_remote_sha(self):
        plan = self.plan()
        self.git("push", "origin", self.candidate + ":refs/heads/feature")
        # A process can be killed before obtaining/persisting a hook verdict.
        # Even a matching SHA cannot establish whether its hook accepted.
        self.receipt.write_text(json.dumps({"plan_digest": plan["digest"], "status": "attempting", "pre_push": "pending"}))
        result = self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)
        self.assertIn("unknown/interrupted", result.stderr)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "needs_reconciliation")

    def test_interrupted_receipt_replace_cleans_only_its_owned_staging_file(self):
        guard = self.load_guard()
        self.receipt.write_text('{"status":"attempting"}')
        unrelated = self.base / "unrelated.tmp"
        unrelated.write_text("keep")
        original_paths = set(self.base.iterdir())
        with mock.patch.object(guard.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                guard.replace_receipt(self.receipt, {"status": "verified"})
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "attempting")
        self.assertEqual(set(self.base.iterdir()), original_paths)
        self.assertEqual(unrelated.read_text(), "keep")

    def test_observed_destination_alias_is_rejected_before_planning(self):
        self.git("--git-dir", str(self.remote), "symbolic-ref", "refs/heads/feature", "refs/heads/main")
        self.assertIn("Symbolic destination", self.plan(success=False).stderr)
        self.assertFalse(self.plan_file.exists())
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_destination_alias_created_before_apply_is_rejected(self):
        plan = self.plan()
        self.git("--git-dir", str(self.remote), "symbolic-ref", "refs/heads/feature", "refs/heads/main")
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)

    def test_destination_alias_created_by_client_hook_is_rejected(self):
        self.hook('git --git-dir=' + str(self.remote) + ' symbolic-ref refs/heads/feature refs/heads/main')
        plan = self.plan()
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.initial)
        self.assertEqual(json.loads(self.receipt.read_text())["pre_push"], "guard_rejected")

    def test_server_alias_mutation_after_guard_is_outside_supported_contract(self):
        # Deliberately violate the acknowledged server contract *after* the
        # client's final check. A client cannot prevent a server from aliasing
        # refs; Git's SHA CAS alone does not provide physical-ref isolation.
        self.git("push", "origin", self.initial + ":refs/heads/feature")
        server_hook = self.remote / "hooks/update"
        server_hook.write_text('#!/bin/sh\ngit symbolic-ref refs/heads/feature refs/heads/main\n')
        server_hook.chmod(0o700)
        plan = self.plan()
        self.apply(plan, success=False)
        self.assertEqual(self.remote_oid("refs/heads/main"), self.candidate)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "unsupported_server_state")
        self.assertEqual(receipt["server_contract"], "ordinary-refs")
        self.assertEqual(receipt["observed"]["symbolic_target"], "refs/heads/main")

    def test_server_contract_must_be_explicitly_acknowledged(self):
        self.call("plan", "--repo", str(self.repo), "--source", "HEAD", "--remote", "origin", "--destination", "refs/heads/feature", "--intent", "feature", "--output", str(self.plan_file), success=False)
        self.assertFalse(self.plan_file.exists())

    def test_no_op_publication_records_that_no_hook_authorization_was_needed(self):
        self.git("push", "origin", self.candidate + ":refs/heads/feature")
        self.hook("exit 1")
        receipt = json.loads(self.apply(self.plan()).stdout)
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(receipt["pre_push"], "not_required")

    def managed_asset_environment(self, home, ambient=None):
        config = home / ".config/envy"
        config.mkdir(parents=True)
        state = config / "state.json"
        state.write_text(json.dumps({"schema_version": 3, "installed": True, "platform": "linux", "features": {"agent-assets": {"enabled": True}}}))
        manifest = config / "resolved-manifest.json"
        manifest.write_text(json.dumps({"resources": {"agent_asset_manifests": [str(ROOT / "assets/agent-assets.json")]}}))
        temporary = home / "tmp"
        temporary.mkdir()
        # Preserve caller security/transport context. Override only this
        # fixture's destinations and declared source/configuration inputs.
        return dict(os.environ if ambient is None else ambient,
                    HOME=str(home), TMPDIR=str(temporary),
                    XDG_CONFIG_HOME=str(home / ".config"),
                    XDG_CACHE_HOME=str(home / ".cache"),
                    XDG_STATE_HOME=str(home / ".local/state"),
                    XDG_DATA_HOME=str(home / ".local/share"),
                    ENVY_CONFIG_DIR=str(config),
                    ENVY_STATE_DIR=str(home / ".local/state/envy"),
                    ENVY_DATA_DIR=str(home / ".local/share/envy"),
                    ENVY_BIN_DIR=str(home / ".local/bin"),
                    ENVY_ROOT=str(ROOT), ENVY_DIR=str(ROOT), ENVY_PLATFORM="linux",
                    ENVY_STATE_FILE=str(state), ENVY_MANIFEST=str(manifest),
                    ENVY_AGENT_ASSETS_MANIFEST=str(ROOT / "assets/agent-assets.json"),
                    ENVY_AGENT_ASSETS_OWNERSHIP=str(home / ".local/state/envy/agent-assets.json"))

    def check_managed_entrypoints(self, home, environment):
        expected_assets = {
            relative: (SCRIPT.parent.parent / relative).read_bytes()
            for relative in ("SKILL.md", "scripts/git_publish.py", "references/git-publication.md")
        }
        native_source = ROOT / "assets/agent-assets/skills/ship-git"
        expected_native = (native_source / "SKILL.md").read_bytes()
        for operation in ("converge", "verify"):
            result = bounded_fixture_run([sys.executable, "-B", str(ROOT / "features/agent-assets.py"), operation], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((home / ".local/state/envy/agent-assets.json").is_file())
        for harness in (".claude", ".agents"):
            installed = home / harness / "skills/ship"
            self.assertTrue(installed.is_symlink())
            for relative, expected in expected_assets.items():
                self.assertTrue((installed / relative).read_bytes() == expected,
                                "Installed asset must match its pre-convergence source snapshot")
            # The native procedure is a separate installed skill. Check actual
            # distribution, not phrases from the pre-split shipping instructions.
            native = home / harness / "skills/ship-git"
            self.assertTrue(native.is_symlink())
            self.assertEqual(native.resolve(), native_source)
            self.assertEqual((native / "SKILL.md").read_bytes(), expected_native)
            self.assertEqual((installed / "scripts/git_publish.py").resolve(), SCRIPT)
            result = bounded_fixture_run([sys.executable, "-B", str(installed / "scripts/git_publish.py"), "--help"], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((installed / "references/git-publication.md").is_file())

    def test_helper_is_available_through_both_managed_skill_entrypoints(self):
        home = self.base / "managed-home"
        self.check_managed_entrypoints(home, self.managed_asset_environment(home))

    def test_managed_entrypoint_fixture_preserves_poisoned_ambient_paths(self):
        ambient_root = self.base / "ambient"
        ambient_root.mkdir()
        ambient = dict(os.environ)
        file_keys = {"ENVY_STATE_FILE", "ENVY_MANIFEST", "ENVY_AGENT_ASSETS_MANIFEST", "ENVY_AGENT_ASSETS_OWNERSHIP"}
        path_keys = file_keys | {"HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "ENVY_CONFIG_DIR", "ENVY_STATE_DIR", "ENVY_DATA_DIR", "ENVY_BIN_DIR", "ENVY_ROOT", "ENVY_DIR"}
        for key in path_keys:
            path = ambient_root / key
            if key in file_keys:
                path.write_text("ambient file sentinel\n")
            else:
                path.mkdir()
                (path / ".sentinel").write_text("ambient directory sentinel\n")
            ambient[key] = str(path)
        ambient["FIXTURE_INHERITED_CONTEXT"] = "preserve without interpreting"

        def snapshot():
            entries = {}
            for path in ambient_root.rglob("*"):
                metadata = path.lstat()
                content = ("symlink", os.readlink(path)) if path.is_symlink() else (
                    ("file", path.read_bytes()) if path.is_file() else ("directory",))
                entries[str(path.relative_to(ambient_root))] = (metadata.st_mode, metadata.st_mtime_ns, content)
            return entries

        before = snapshot()
        home = self.base / "managed-home"
        environment = self.managed_asset_environment(home, ambient)
        self.assertTrue(all(environment.get(key) == value for key, value in ambient.items()
                            if key not in path_keys | {"ENVY_PLATFORM"}),
                        "Non-path inherited context must be preserved")
        self.check_managed_entrypoints(home, environment)
        self.assertEqual(snapshot(), before)

    def test_non_fast_forward_plan_is_rejected(self):
        self.git("push", "origin", "HEAD:refs/heads/feature")
        self.git("switch", "--detach", self.initial)
        self.commit("divergent")
        self.plan(success=False)
        self.assertEqual(self.remote_oid(), self.candidate)

    def test_verify_reconciles_interruption_and_detects_later_drift(self):
        plan = self.plan()
        self.apply(plan)

        def observations():
            refs = []
            for directory in (self.repo / ".git", self.remote, self.other):
                result = bounded_fixture_run(
                    ["git", "--git-dir", str(directory), "for-each-ref",
                     "--format=%(refname) %(objectname)"],
                    text=True, capture_output=True, env=self.env,
                )
                self.assertTrue(result.returncode == 0 and result.stderr == "",
                                "Owned ref snapshot must succeed without stderr")
                refs.append(result.stdout)
            return self.plan_file.read_bytes(), self.receipt.read_bytes(), refs

        before = observations()
        contaminated = {**self.env, "GIT_DIR": str(self.other)}
        result = bounded_fixture_run(
            [sys.executable, str(SCRIPT), "verify", "--repo", str(self.repo),
             "--plan", str(self.plan_file), "--receipt", str(self.receipt)],
            text=True, capture_output=True, env=contaminated,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout == "", "Routing refusal must not emit stdout")
        self.assertTrue(
            result.stderr == "Unset repository-routing Git environment overrides before publication.\n",
            "Expected the exact static verify routing refusal",
        )
        self.assertTrue(observations() == before,
                        "Rejected verify must preserve plan, receipt and owned refs")
        self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt))
        self.apply(plan, success=False)  # same receipt cannot authorize a retry
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/feature", self.initial)
        self.call("verify", "--repo", str(self.repo), "--plan", str(self.plan_file), "--receipt", str(self.receipt), success=False)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "needs_reconciliation")

    def test_bounded_fixture_command_timeout_is_static(self):
        timeout = subprocess.TimeoutExpired(
            ["fixture-command"], 30, output=b"untrusted output", stderr=b"untrusted error",
        )
        with mock.patch.object(subprocess, "run", side_effect=timeout) as runner:
            with self.assertRaises(AssertionError) as caught:
                bounded_fixture_run(["fixture-command"], env=self.env, capture_output=True)
        self.assertTrue(str(caught.exception) == "owned publication fixture command timed out",
                        "Publication timeout must use the static non-echoing diagnostic")
        self.assertTrue(caught.exception.__suppress_context__,
                        "Publication timeout must suppress the captured exception context")
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)
        self.assertTrue(runner.call_args.kwargs["env"] is self.env,
                        "Bounded call must preserve the supplied fixture environment")

    def test_routing_environment_overrides_are_rejected(self):
        routing_names = (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        )
        self.assertTrue(all(name not in self.env for name in routing_names),
                        "Expected the existing fixture's no-routing environment")
        command = [
            sys.executable, str(SCRIPT), "plan", "--repo", str(self.repo),
            "--source", "HEAD", "--remote", "origin", "--destination", "refs/heads/feature",
            "--intent", "feature", "--server-contract", "ordinary-refs", "--output", str(self.plan_file),
        ]

        def destination_refs():
            snapshots = []
            for destination in (self.remote, self.other):
                result = bounded_fixture_run(
                    ["git", "--git-dir", str(destination), "for-each-ref",
                     "--format=%(refname) %(objectname)"],
                    text=True, capture_output=True, env=self.env,
                )
                self.assertTrue(result.returncode == 0, "Owned destination ref snapshot must succeed")
                self.assertTrue(result.stderr == "", "Owned destination ref snapshot must not emit stderr")
                snapshots.append(result.stdout)
            return snapshots

        before_refs = destination_refs()
        for name in routing_names:
            with self.subTest(variable=name):
                contaminated = dict(self.env)
                contaminated[name] = str(self.other)
                result = bounded_fixture_run(command, text=True, capture_output=True, env=contaminated)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stdout == "", "Routing refusal must not emit stdout")
                self.assertTrue(
                    result.stderr == "Unset repository-routing Git environment overrides before publication.\n",
                    "Expected the exact static routing refusal",
                )
                self.assertFalse(self.plan_file.exists())
                self.assertFalse(self.receipt.exists())
                self.assertTrue(destination_refs() == before_refs, "Routing refusal must preserve owned refs")

        result = bounded_fixture_run(command, text=True, capture_output=True, env=self.env)
        self.assertTrue(result.returncode == 0, "Otherwise-identical no-routing planning must succeed")
        self.assertTrue(result.stderr == "", "Valid planning must not emit stderr")
        plan = json.loads(result.stdout)
        self.assertTrue(plan["plan"]["candidate"] == self.candidate, "Planning must bind the fixture candidate")
        self.assertTrue(plan["plan"]["destination"] == "refs/heads/feature", "Planning must bind the feature ref")
        self.assertTrue(plan["plan"]["intent"] == "feature", "Planning must retain feature-only intent")
        self.assertTrue(self.plan_file.is_file(), "Valid planning must create its plan")
        self.assertTrue(json.loads(self.plan_file.read_text()) == plan, "Planning must save its returned envelope")
        self.assertFalse(self.receipt.exists())
        self.assertTrue(destination_refs() == before_refs, "Planning must not change owned refs")

    def test_routing_guard_empty_values_are_allowed_at_dispatch(self):
        # Public dispatch checks truthiness, unlike the reflect fixture's presence
        # guard. Mocked dispatch does not qualify empty values for real Git or publish.
        guard = self.load_guard()
        inherited = dict(os.environ)
        empty_routing = {name: "" for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        )}
        command = [
            str(SCRIPT), "plan", "--repo", str(self.repo), "--source", "HEAD",
            "--remote", "origin", "--destination", "refs/heads/feature", "--intent", "feature",
            "--server-contract", "ordinary-refs", "--output", str(self.plan_file),
        ]
        for case, overrides, expected_status in (
            ("empty", empty_routing, 0),
            ("nonempty", {**empty_routing, "GIT_DIR": str(self.other)}, 1),
        ):
            with self.subTest(case=case):
                expected_environment = {**inherited, **overrides}

                def dispatched_plan(_args):
                    self.assertTrue(dict(os.environ) == expected_environment,
                                    "Dispatch must preserve unrelated inherited environment values")

                with mock.patch.dict(os.environ, overrides), mock.patch.object(
                    guard.sys, "argv", command,
                ), mock.patch.object(guard.sys, "stdout", io.StringIO()) as output, mock.patch.object(
                    guard.sys, "stderr", io.StringIO(),
                ) as error, mock.patch.object(guard, "make_plan", side_effect=dispatched_plan) as plan_call, mock.patch.object(
                    guard, "git",
                ) as git_call:
                    self.assertTrue(dict(os.environ) == expected_environment,
                                    "Routing controls must overlay only the seven routing keys")
                    status = guard.main()
                self.assertEqual(status, expected_status)
                self.assertTrue(output.getvalue() == "", "Routing dispatch controls must not emit stdout")
                expected_error = ("" if expected_status == 0 else
                                  "Unset repository-routing Git environment overrides before publication.\n")
                self.assertTrue(error.getvalue() == expected_error,
                                "Routing dispatch must retain its exact static diagnostic")
                self.assertEqual(plan_call.call_count, 1 if expected_status == 0 else 0)
                self.assertEqual(git_call.call_count, 0, "Mocked dispatch must not execute Git")
                self.assertTrue(dict(os.environ) == inherited,
                                "Routing controls must restore the inherited environment")
                self.assertFalse(self.plan_file.exists())
                self.assertFalse(self.receipt.exists())

    def test_apply_routing_environment_overrides_are_rejected(self):
        plan = self.plan()
        command = [
            sys.executable, str(SCRIPT), "apply", "--repo", str(self.repo),
            "--plan", str(self.plan_file), "--receipt", str(self.receipt),
            "--approve", plan["digest"], "--authorize", "feature",
        ]

        def observations():
            refs = []
            for directory in (self.repo / ".git", self.remote, self.other):
                result = bounded_fixture_run(
                    ["git", "--git-dir", str(directory), "for-each-ref",
                     "--format=%(refname) %(objectname)"],
                    text=True, capture_output=True, env=self.env,
                )
                self.assertTrue(result.returncode == 0 and result.stderr == "",
                                "Owned ref snapshot must succeed without stderr")
                refs.append(result.stdout)
            receipt = self.receipt.read_bytes() if self.receipt.exists() else None
            return self.plan_file.read_bytes(), receipt, refs

        before = observations()
        self.assertTrue(before[1] is None, "Otherwise-valid apply must start without a receipt")
        for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        ):
            with self.subTest(variable=name):
                contaminated = {**self.env, name: str(self.other)}
                result = bounded_fixture_run(command, text=True, capture_output=True, env=contaminated)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stdout == "", "Rejected apply must not emit stdout")
                self.assertTrue(
                    result.stderr == "Unset repository-routing Git environment overrides before publication.\n",
                    "Expected the exact static apply routing refusal",
                )
                self.assertTrue(observations() == before,
                                "Rejected apply must preserve plan, receipt absence and owned refs")

        result = bounded_fixture_run(command, text=True, capture_output=True, env=self.env)
        self.assertTrue(result.returncode == 0, "Otherwise-identical no-routing apply must succeed")
        self.assertTrue(result.stderr == "", "Valid owned publication must not emit stderr")
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["status"] == "verified", "Owned publication must be verified")
        self.assertTrue(receipt["pre_push"] == "accepted", "The real pre-push guard must accept")
        self.assertTrue(receipt["push_result"] == "completed", "The real Git push must complete")
        self.assertTrue(json.loads(self.receipt.read_text()) == receipt,
                        "Publication must retain its returned receipt")
        self.assertTrue(self.plan_file.read_bytes() == before[0], "Publication must preserve its plan")
        self.assertTrue(self.remote_oid() == self.candidate, "Owned feature ref must match the candidate")
        self.assertTrue(self.remote_oid("refs/heads/main") == self.initial, "Main must remain unchanged")
        self.assertTrue(self.remote_oid(remote=self.other) == "", "Other owned remote must remain unchanged")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_pre_push_dispatch_preserves_routing_environment_exemption(self):
        # Only CLI dispatch is exercised here; the mock does not run a hook or publish.
        guard = self.load_guard()
        context = self.base / "hook-context.json"
        contaminated = dict(self.env)
        for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        ):
            contaminated[name] = str(self.other)

        def dispatched_hook(_args):
            self.assertTrue(dict(os.environ) == contaminated,
                            "Internal hook dispatch must preserve the supplied fixture environment")

        with mock.patch.dict(os.environ, contaminated, clear=True), mock.patch.object(
            guard.sys, "argv", [str(SCRIPT), "_pre_push", "--context", str(context), "origin", str(self.remote)],
        ), mock.patch.object(guard.sys, "stdout", io.StringIO()) as output, mock.patch.object(
            guard.sys, "stderr", io.StringIO(),
        ) as error, mock.patch.object(guard, "hook", side_effect=dispatched_hook) as hook_call, mock.patch.object(
            guard, "git",
        ) as git_call:
            status = guard.main()
        self.assertEqual(status, 0)
        self.assertTrue(output.getvalue() == "", "Internal hook dispatch must not emit stdout")
        self.assertTrue(error.getvalue() == "", "Exempt internal hook dispatch must not emit stderr")
        self.assertEqual(hook_call.call_count, 1)
        dispatched = hook_call.call_args.args[0]
        self.assertTrue(dispatched.command == "_pre_push" and dispatched.context == str(context)
                        and dispatched.remote_args == ["origin", str(self.remote)],
                        "Internal hook dispatch must retain its parsed arguments")
        self.assertEqual(git_call.call_count, 0, "Mocked dispatch must not execute Git")
        self.assertFalse(self.plan_file.exists())
        self.assertFalse(self.receipt.exists())


if __name__ == "__main__":
    unittest.main()
