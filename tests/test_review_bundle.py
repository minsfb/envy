"""Owned local Git and fake-reviewer tests; no provider or network calls."""

import contextlib
import copy
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets/agent-assets/skills/review/scripts/review_bundle.py"
SPEC = importlib.util.spec_from_file_location("review_bundle", SCRIPT)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def credential_shapes():
    """Non-issued pattern fixtures, constructed so source contains no full value."""
    # The sentinel capture test binds this entire module as unexecuted source.
    # Keep pattern fixtures constructed: a contiguous match would fail its guard.
    body = "0" * 20
    cases = [("github-" + kind, "gh" + kind + "_" + body) for kind in "pousr"]
    cases.append(("key-token", "sk" + "-" + body))
    cases.extend(("private-key-" + (kind.lower() or "bare"),
                  "-----BEGIN " + (kind + " " if kind else "") + "PRIVATE KEY-----")
                 for kind in ("", "RSA", "EC", "OPENSSH"))
    cases.extend(("url-" + scheme, scheme + "://" + "fixture:unused" + "@" + "example.invalid")
                 for scheme in ("http", "https"))
    return cases


def assert_no_fixture_output(test_case, output):
    values = [value for _, value in credential_shapes()] + ["SYNTHETIC_REVIEW_SECRET_"]
    if isinstance(output, bytes):
        values = [value.encode() for value in values]
    test_case.assertFalse(any(value in output for value in values),
                          "captured diagnostic included private fixture text")


class StringGuardTests(unittest.TestCase):
    def test_retained_credential_patterns_reject_generated_shapes(self):
        for name, value in credential_shapes():
            with self.subTest(case=name):
                with self.assertRaises(REVIEW.Stop) as caught:
                    REVIEW.string(value)
                diagnostic = str(caught.exception)
                assert_no_fixture_output(self, diagnostic)
                self.assertEqual(diagnostic, "sensitive_input")

    def test_synthetic_sentinel_is_ordinary_text(self):
        marker = "SYNTHETIC_REVIEW_SECRET_DO_NOT_PRINT"
        self.assertTrue(REVIEW.string(marker) == marker, "ordinary test marker was changed")

    def test_retained_credential_guard_near_misses_are_ordinary_text(self):
        body = "0" * 19
        cases = [("github-" + kind, "gh" + kind + "_" + body) for kind in "pousr"]
        cases.append(("key-token", "sk" + "-" + body))
        cases.append(("public-key", "-----BEGIN " + "PUBLIC KEY-----"))
        for name, value in cases:
            with self.subTest(case=name):
                self.assertTrue(REVIEW.string(value) == value, "ordinary near-miss fixture was changed")

    def test_control_characters_reject_cr_nul_esc_and_preserve_lf_tab(self):
        for name, number in (("carriage-return", 13), ("nul", 0), ("escape", 27)):
            with self.subTest(case=name):
                value = "before" + chr(number) + "after"
                with self.assertRaises(REVIEW.Stop) as caught:
                    REVIEW.string(value)
                self.assertTrue(str(caught.exception) == "invalid_string",
                                "control-character refusal used an unexpected diagnostic")
        for name, number in (("line-feed", 10), ("tab", 9)):
            with self.subTest(case=name):
                value = "before" + chr(number) + "after"
                self.assertTrue(REVIEW.string(value) == value, "allowed control character was changed")

    def test_ordinary_urls_and_path_at_signs_are_preserved(self):
        for scheme in ("http", "https"):
            for path in ("/resource", "/path@label"):
                with self.subTest(scheme=scheme, path=path):
                    value = scheme + "://example.invalid" + path
                    self.assertTrue(REVIEW.string(value) == value, "ordinary URL fixture was changed")
        value = "relative/path@label"
        self.assertTrue(REVIEW.string(value) == value, "ordinary path fixture was changed")

    def test_fixture_output_check_uses_non_echoing_failure(self):
        value = credential_shapes()[0][1]
        for name, output in (("text", value), ("bytes", value.encode())):
            with self.subTest(case=name):
                with self.assertRaises(AssertionError) as caught:
                    assert_no_fixture_output(self, output)
                self.assertFalse(value in str(caught.exception), "privacy assertion exposed fixture data")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.store = self.root / "review"
        self.inputs = self.root / "inputs.json"
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        # Refuse redirection, retain the selected Git and unrelated security context.
        REVIEW.need(not REVIEW.ROUTING.intersection(self.environment), "fixture_git_routing_override")
        self.git("init", "-q", "--template=", "--object-format=sha1")
        (self.repo / ".git/fixture-attributes").write_text("")
        (self.repo / ".git/fixture-excludes").write_text("")
        (self.repo / "content.txt").write_text("baseline\n")
        self.base = self.commit("Baseline")
        (self.repo / "content.txt").write_text("candidate\n")
        self.head = self.commit("Candidate")
        (self.root / "supplied.txt").write_text("Implementer supplied: one isolated check passed.\n")
        self.spec = {
            "scope": "Review the committed content change, no publication.",
            "questions": ["Does the change satisfy its stated purpose?"],
            "dependency_changes": "None.",
            "gitlink_scope": "none",
            "reviewers": [
                {"id": name, "required": True, "adapter": "fake-native",
                 "requested_model": "example-model", "identity_basis": "harness_metadata"}
                for name in ("first", "second")
            ],
            "artifacts": [{"name": "supplied-check", "path": "supplied.txt"}],
        }
        self.save_inputs()

    def git(self, *args):
        executable = shutil.which("git")
        self.assertIsNotNone(executable)
        result = subprocess.run(
            [str(Path(executable).absolute()), "--no-optional-locks",
             "--git-dir", str(self.repo / ".git"), "--work-tree", str(self.repo),
             "-c", "commit.gpgSign=false", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + str(self.repo / ".git/fixture-hooks"),
             "-c", "core.attributesFile=" + str(self.repo / ".git/fixture-attributes"),
             "-c", "core.excludesFile=" + str(self.repo / ".git/fixture-excludes"),
             *args], cwd=self.repo, env=self.environment, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, "fixture Git failed")
        return result.stdout.decode().strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def save_inputs(self):
        self.inputs.write_text(json.dumps(self.spec))

    def call(self, command, *args, success=True, failure_code=1):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = REVIEW.main([command, "--store", str(self.store), *map(str, args)])
        assert_no_fixture_output(self, output.getvalue() + errors.getvalue())
        if success:
            self.assertEqual(code, 0, "fixture command unexpectedly failed")
        else:
            self.assertEqual(code, failure_code, "fixture refusal returned the wrong exit code")
            if failure_code == 1:
                self.assertTrue(output.getvalue() == "", "fixture error must not emit stdout")
            elif failure_code == 2:
                self.assertTrue(errors.getvalue() == "", "readiness report must not emit stderr")
        return code, json.loads(output.getvalue() or errors.getvalue())

    def test_refusal_helper_pins_exit_code_and_error_channel(self):
        def respond(code, stream, body):
            def fake_main(_):
                print(json.dumps(body), file=getattr(sys, stream))
                return code
            return fake_main

        error = {"error": "fixture_refusal"}
        for code, stream in ((2, "stderr"), (130, "stderr"), (1, "stdout")):
            with self.subTest(code=code, stream=stream), \
                 patch.object(REVIEW, "main", side_effect=respond(code, stream, error)), \
                 self.assertRaises(AssertionError):
                self.call("status", success=False)
        with patch.object(REVIEW, "main", side_effect=respond(1, "stderr", error)):
            self.assertEqual(self.call("status", success=False), (1, error))
        report = {"state": "reviewing"}
        with patch.object(REVIEW, "main", side_effect=respond(2, "stderr", report)), \
             self.assertRaises(AssertionError) as caught:
            self.call("status", "--require-ready", success=False, failure_code=2)
        self.assertTrue(str(caught.exception).endswith("readiness report must not emit stderr"),
                        "Readiness refusal failed outside its stderr-empty oracle")

        with patch.object(REVIEW, "main", side_effect=respond(2, "stdout", report)):
            self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2),
                             (2, report))

    def prepare(self):
        _, result = self.call("prepare", "--repo", self.repo, "--base", self.base,
                              "--head", self.head, "--inputs", self.inputs, "--sanitized")
        self.candidate = result["candidate"]
        return result

    def start(self, slot="first", **options):
        path = self.root / ("request-" + slot + "-" + str(len(list(self.root.glob("request-*")))) + ".json")
        args = ["--expect", self.candidate, "--slot", slot, "--reason", "Authorized independent review", "--output", path]
        for name, value in options.items():
            args += ["--" + name, value]
        _, summary = self.call("start", *args)
        return summary, json.loads(path.read_text())

    def result(self, request, verdict="pass"):
        context = request["request"]
        return {
            "version": 1, "candidate": context["candidate"], "attempt": context["attempt"],
            "request_digest": request["request_digest"], "slot": context["slot"],
            "adapter": context["adapter"], "run_id": "run-" + context["attempt"],
            "verdict": verdict,
            "identity": {"basis": "harness_metadata", "reviewer": "reviewer-" + context["slot"],
                         "model": "example-model", "evidence": "Synthetic terminal runtime metadata, not a model claim."},
            "findings": [], "executed_checks": [], "supplied_evidence": ["supplied-check"],
            "limitations": ["No commands executed by this fake reviewer."],
            "provenance": {"importer": "fixture-operator", "source": "synthetic terminal metadata",
                           "source_sha256": hashlib.sha256(b"synthetic metadata").hexdigest()},
        }

    def record(self, result, success=True):
        path = self.root / "result.json"
        path.write_text(json.dumps(result))
        return self.call("record", "--expect", self.candidate, "--result", path,
                         "--sanitized", success=success)[1]

    def approve_all(self):
        self.prepare()
        _, first = self.start()
        self.record(self.result(first))
        _, second = self.start("second")
        return self.record(self.result(second))

    def ledger(self):
        return json.loads((self.store / "ledger.json").read_text())

    def bounded_failure(self, command, *args):
        # subprocess.run kills/reaps only its own child if a regression blocks.
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), command, "--store", str(self.store), *map(str, args)],
            cwd=self.root, env=self.environment, capture_output=True, timeout=5,
        )
        assert_no_fixture_output(self, result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
        return result.returncode, json.loads(result.stdout or result.stderr)

    @contextlib.contextmanager
    def fifo_replacing(self, path):
        backup = self.root / ("saved-" + path.name)
        path.rename(backup)
        try:
            os.mkfifo(path, 0o600)  # No writer is ever opened by these fixtures.
            yield backup
        finally:
            path.unlink(missing_ok=True)
            backup.rename(path)

    def assert_lock_released(self):
        with (self.store / "lock").open("rb") as stream:
            REVIEW.fcntl.flock(stream, REVIEW.fcntl.LOCK_EX | REVIEW.fcntl.LOCK_NB)

    def assert_ledger_shape_refusal(self, original_bytes, invalid, expected):
        path = self.store / "ledger.json"
        malformed = REVIEW.canonical(invalid) + b"\n"
        try:
            path.write_bytes(malformed)
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = REVIEW.main(["status", "--store", str(self.store), "--require-ready"])
            assert_no_fixture_output(self, output.getvalue() + errors.getvalue())
            self.assertTrue(code == 1 and output.getvalue() == "" and
                            json.loads(errors.getvalue()) == {"error": expected},
                            "Malformed ledger did not produce the expected static diagnostic")
            self.assertTrue(path.read_bytes() == malformed, "Rejected ledger bytes were changed")
            ReviewTests.assert_lock_released(self)
        finally:
            path.write_bytes(original_bytes)  # Restore only this owned fixture ledger.
        code, recovered = self.call("status", "--require-ready")
        self.assertEqual((code, recovered["state"]), (0, "ready"))
        self.assertEqual(recovered["blockers"], [])
        self.assertTrue(path.read_bytes() == original_bytes, "Recovery changed the original records")

    def test_prepare_requires_sanitized_acknowledgment_without_creating_store(self):
        before = set(self.root.iterdir())
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                               "--head", self.head, "--inputs", self.inputs, success=False)
        self.assertTrue((code, error) == (1, {"error": "sanitization_acknowledgment_required"}),
                        "missing prepare acknowledgment used an unexpected diagnostic")
        self.assertFalse(self.store.exists())
        self.assertTrue(set(self.root.iterdir()) == before, "rejected prepare changed fixture membership")
        self.assertEqual(self.prepare()["state"], "prepared")

    def test_supersede_requires_sanitized_acknowledgment_without_mutation(self):
        self.prepare()
        before = (self.store / "ledger.json").read_bytes()
        previous = self.candidate
        self.spec["questions"].append("Does the successor retain the same source boundary?")
        self.save_inputs()
        args = ("--expect", previous, "--reason", "Changed fixture review question",
                "--repo", self.repo, "--base", self.base, "--head", self.head, "--inputs", self.inputs)
        code, error = self.call("supersede", *args, success=False)
        self.assertTrue((code, error) == (1, {"error": "sanitization_acknowledgment_required"}),
                        "missing supersede acknowledgment used an unexpected diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "rejected supersede changed ledger")
        self.assertEqual(self.ledger()["active"], previous)
        self.assert_lock_released()
        summary = self.call("supersede", *args, "--sanitized")[1]
        self.assertEqual(summary["state"], "prepared")
        self.assertNotEqual(summary["candidate"], previous)
        self.assertEqual(self.ledger()["active"], summary["candidate"])

    def test_record_requires_sanitized_acknowledgment_without_mutation(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        path = self.root / "result.json"
        path.write_text(json.dumps(result))
        before = (self.store / "ledger.json").read_bytes()
        args = ("--expect", self.candidate, "--result", path)
        code, error = self.call("record", *args, success=False)
        self.assertTrue((code, error) == (1, {"error": "sanitization_acknowledgment_required"}),
                        "missing record acknowledgment used an unexpected diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "rejected record changed ledger")
        self.assertEqual(self.ledger()["active"], self.candidate)
        self.assert_lock_released()
        self.assertEqual(self.call("record", *args, "--sanitized")[1]["slots"][0]["state"], "pass")

    def test_fixture_pins_sha1_despite_nondefault_object_format(self):
        # Change only the object-format default; retain inherited Git/security context.
        with patch.dict(os.environ, {"GIT_DEFAULT_HASH": "sha256"}):
            fixture = ReviewTests()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            self.assertEqual(fixture.environment["GIT_DEFAULT_HASH"], "sha256")
            self.assertEqual(fixture.git("rev-parse", "--show-object-format"), "sha1")
            self.assertEqual((len(fixture.base), len(fixture.head)), (40, 40))
            self.assertEqual(fixture.prepare()["state"], "prepared")
            # A control without the explicit format proves this Git honors the default.
            fixture.repo = fixture.root / "default-format-control"
            fixture.repo.mkdir()
            fixture.git("init", "-q", "--template=")
            self.assertEqual(fixture.git("rev-parse", "--show-object-format"), "sha256")

    def test_sha256_candidate_prepares_and_rejects_dirty_bytes(self):
        self.repo = self.root / "sha256-repo"
        self.repo.mkdir()
        self.git("init", "-q", "--template=", "--object-format=sha256")
        (self.repo / ".git/fixture-attributes").write_text("")
        (self.repo / ".git/fixture-excludes").write_text("")
        content = self.repo / "content.txt"
        content.write_text("baseline\n")
        self.base = self.commit("Baseline")
        content.write_text("candidate\n")
        self.head = self.commit("Candidate")
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.assertEqual(self.git("rev-parse", "--show-object-format"), "sha256")
        self.assertEqual((len(self.base), len(self.head), len(tree)), (64, 64, 64))
        self.assertEqual(self.prepare()["state"], "prepared")
        bundle = self.ledger()["candidates"][0]["bundle"]
        self.assertEqual((bundle["base"], bundle["head"], bundle["tree"]),
                         (self.base, self.head, tree))
        clean_ledger = self.store / "ledger.json"
        before = clean_ledger.read_bytes()
        self.store = self.root / "sha256-dirty-review"
        original = content.read_bytes()
        try:
            content.write_bytes(original + b"uncommitted fixture change\n")
            code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
        finally:
            content.write_bytes(original)
        self.assertEqual((code, error), (1, {"error": "dirty_candidate"}))
        self.assertFalse(self.store.exists())
        self.assertEqual(clean_ledger.read_bytes(), before)

    def test_initial_prepare_size_bound_precedes_store_and_counts_newline(self):
        bundle = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        candidate = {"digest": REVIEW.digest(bundle), "bundle": bundle, "attempts": [],
                     "supersedes": None, "reason": "initial candidate"}
        ledger = {"version": 1, "active": candidate["digest"], "candidates": [candidate]}
        encoded = REVIEW.canonical(ledger) + b"\n"
        # Keep the real preparation/serialization path, with a smaller aggregate bound.
        # Every individual input fits; only the combined ledger (or final newline) fails.
        self.assertLess(self.inputs.stat().st_size, len(encoded) - 2)
        for artifact in bundle["artifacts"] + [bundle["diff"]]:
            self.assertLess(len(artifact["text"].encode()), len(encoded) - 2)
            self.assertLessEqual(len(artifact["text"].encode()), REVIEW.MAX_FILE)
        for name, limit in (("aggregate-overflow", len(encoded) - 2),
                            ("newline-overflow", len(encoded) - 1)):
            with self.subTest(boundary=name):
                self.store = self.root / name
                before = set(self.root.iterdir())
                with patch.object(REVIEW, "MAX_LEDGER", limit):
                    code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                        "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                self.assertEqual((code, error), (1, {"error": "ledger_too_large"}))
                self.assertEqual((self.store.exists(), (self.store / "lock").exists(),
                                  (self.store / "ledger.json").exists()), (False, False, False))
                self.assertEqual(set(self.root.iterdir()), before)
        self.store = self.root / "exact-size"
        with patch.object(REVIEW, "MAX_LEDGER", len(encoded)):
            self.assertEqual(self.prepare()["state"], "prepared")
        self.assertEqual((self.store / "ledger.json").read_bytes(), encoded)

    def test_explicit_version_one_input_is_rejected_but_implicit_v1_works(self):
        self.spec["version"] = 1
        self.save_inputs()
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
            "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
        self.assertEqual((code, error), (1, {"error": "invalid_schema"}))
        self.assertFalse(self.store.exists())
        self.spec.pop("version")
        self.save_inputs()
        self.assertEqual(self.prepare()["state"], "prepared")
        self.assertEqual(self.ledger()["version"], 1)

    def test_fifo_input_and_artifact_fail_without_creating_store(self):
        for path in (self.inputs, self.root / "supplied.txt"):
            with self.subTest(path=path.name), self.fifo_replacing(path):
                code, result = self.bounded_failure("prepare", "--repo", self.repo, "--base", self.base,
                    "--head", self.head, "--inputs", self.inputs, "--sanitized")
                self.assertEqual(code, 1)
                self.assertEqual(result["error"], "invalid_file")
                self.assertFalse(self.store.exists())

    def test_fifo_result_preserves_ledger_and_releases_lock(self):
        self.prepare()
        _, request = self.start()
        path = self.root / "result.json"
        path.write_text(json.dumps(self.result(request)))
        before = (self.store / "ledger.json").read_bytes()
        with self.fifo_replacing(path):
            code, result = self.bounded_failure("record", "--expect", self.candidate,
                "--result", path, "--sanitized")
            self.assertEqual(code, 1)
            self.assertEqual(result["error"], "invalid_file")
            self.assertEqual((self.store / "ledger.json").read_bytes(), before)
            self.assert_lock_released()
        self.assertEqual(self.call("resume")[1]["slots"][0]["state"], "in-flight")

    def test_fifo_tracked_and_store_files_fail_without_mutation_or_held_lock(self):
        self.approve_all()
        before = (self.store / "ledger.json").read_bytes()
        for path in (self.repo / "content.txt", self.store / "ledger.json", self.store / "lock"):
            with self.subTest(path=path.name), self.fifo_replacing(path) as backup:
                code, result = self.bounded_failure("status", "--require-ready")
                if path.parent == self.repo:
                    self.assertEqual(code, 2)
                    self.assertIn("invalid_file", [item["code"] for item in result["blockers"]])
                    self.assertNotEqual(result["state"], "ready")
                else:
                    self.assertEqual(code, 1)
                    self.assertEqual(result["error"], "invalid_file")
                ledger = backup if path.name == "ledger.json" else self.store / "ledger.json"
                self.assertEqual(ledger.read_bytes(), before)
                if path.name != "lock":
                    self.assert_lock_released()
            self.assertEqual((self.store / "ledger.json").read_bytes(), before)
            if path.name == "lock":
                # The replacement was refused before locking. Prove a fresh
                # controller cycle works on the restored lock and valid ledger.
                with REVIEW.locked(self.store, writing=True) as (_, ledger):
                    self.assertEqual(ledger["active"], self.candidate)
                    self.assertEqual(self.call("resume", success=False)[1]["error"], "store_busy")
                self.assert_lock_released()
            self.assertEqual(self.call("resume")[1]["state"], "ready")

    def test_tracked_file_size_boundary_blocks_readiness_and_oversized_preparation(self):
        asset = self.repo / "asset.bin"
        asset.write_bytes(b"\0" * REVIEW.MAX_FILE)
        self.base = self.commit("Tracked asset at the supported limit")
        (self.repo / "content.txt").write_text("next candidate\n")
        self.head = self.commit("Change small content only")
        self.assertEqual(self.approve_all()["state"], "ready")
        before = (self.store / "ledger.json").read_bytes()
        with asset.open("ab") as stream:
            stream.write(b"\0")
        code, result = self.bounded_failure("status", "--require-ready")
        self.assertEqual(code, 2)
        self.assertNotEqual(result["state"], "ready")
        self.assertIn("input_too_large", [item["code"] for item in result["blockers"]])
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assert_lock_released()
        self.head = self.commit("Clean committed asset above the supported limit")
        self.store = self.root / "oversized-review"
        code, result = self.bounded_failure("prepare", "--repo", self.repo, "--base", self.base,
            "--head", self.head, "--inputs", self.inputs, "--sanitized")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "input_too_large")
        self.assertFalse(self.store.exists())

    def test_ready_needs_two_independent_digest_bound_results(self):
        self.assertEqual(self.prepare()["state"], "prepared")
        _, first = self.start()
        self.assertEqual(self.record(self.result(first))["state"], "reviewing")
        _, second = self.start("second")
        self.assertEqual(first["bundle"], second["bundle"])
        self.assertNotIn("results", second)
        self.assertNotIn("attempts", second["bundle"])
        self.assertNotIn("synthetic terminal metadata", json.dumps(second))
        summary = self.record(self.result(second))
        self.assertEqual(summary["state"], "ready")
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("status", "--require-ready")[0], 0)

    def test_readiness_gate_not_status_exit_alone(self):
        self.prepare()
        self.assertEqual(self.call("status")[0], 0)
        code, report = self.call("status", "--require-ready", success=False, failure_code=2)
        self.assertEqual(code, 2)
        self.assertTrue(report["state"] == "prepared", "Not-ready status must remain a valid report")
        usage = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), "status"],
            cwd=self.root, env=self.environment, capture_output=True, timeout=15,
        )
        assert_no_fixture_output(self, usage.stdout + usage.stderr)
        self.assertEqual(usage.returncode, 2)
        self.assertTrue(usage.stdout == b"", "Usage errors must not emit a status report")
        self.assertTrue(
            usage.stderr.startswith(b"usage:") and b"required: --store" in usage.stderr,
            "Expected the missing-store argparse usage diagnostic",
        )
        before = (self.store / "ledger.json").read_bytes()
        help_result = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), "--help"],
            cwd=self.root, env=self.environment, capture_output=True, timeout=15,
        )
        assert_no_fixture_output(self, help_result.stdout + help_result.stderr)
        self.assertEqual(help_result.returncode, 0)
        self.assertTrue(help_result.stderr == b"", "Help unexpectedly wrote to stderr")
        self.assertTrue(help_result.stdout.startswith(b"usage:"), "Expected argparse help text")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(help_result.stdout)
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "Help changed the ledger")
        for slot in ("first", "second"):
            _, request = self.start(slot)
            self.record(self.result(request))
        code, ready = self.call("status", "--require-ready")
        self.assertEqual((code, ready["state"]), (0, "ready"))
        self.assertEqual(ready["blockers"], [])

    def test_deterministic_bundle_digest_and_sensitive_changes(self):
        first = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        second = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        self.assertEqual(REVIEW.digest(first), REVIEW.digest(second))
        for key, value in (("scope", "Different scope"), ("questions", ["Different question"]),
                           ("dependency_changes", "Changed pin")):
            changed = copy.deepcopy(first)
            changed[key] = value
            self.assertNotEqual(REVIEW.digest(first), REVIEW.digest(changed))
        for field in ("head", "base"):
            changed = copy.deepcopy(first)
            changed[field] = "0" * 40
            self.assertNotEqual(REVIEW.digest(first), REVIEW.digest(changed))
        changed = copy.deepcopy(first)
        changed["reviewers"][0]["requested_model"] = "different-model"
        self.assertNotEqual(REVIEW.digest(first), REVIEW.digest(changed))
        (self.root / "supplied.txt").write_text("New evidence\n")
        self.assertNotEqual(REVIEW.digest(first), REVIEW.digest(REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)))

    def test_supplied_evidence_is_copied_and_does_not_expire_with_scratch(self):
        self.prepare()
        (self.root / "supplied.txt").unlink()
        self.inputs.unlink()
        self.assertEqual(self.call("resume")[1]["state"], "prepared")
        _, request = self.start()
        self.assertIn("Implementer supplied", request["bundle"]["artifacts"][0]["text"])

    def test_actual_checks_remain_distinct_from_supplied_evidence(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        result["executed_checks"] = [{"command": "example-check", "cwd": "owned-fixture",
            "mode": "isolated", "revision": self.head, "outcome": "pass", "exit_code": 0,
            "writes": "owned scratch only", "evidence": "captured exit status",
            "provenance": "reviewer_reported"}]
        self.record(result)
        self.call("resume")
        recorded = self.ledger()["candidates"][0]["attempts"][0]["result"]
        self.assertEqual(recorded["executed_checks"], result["executed_checks"])
        self.assertEqual(recorded["supplied_evidence"], ["supplied-check"])
        self.assertEqual(recorded["limitations"], result["limitations"])

    def test_model_substitution_and_unverified_identity_block(self):
        for basis, model, reviewer, evidence in (
            ("requested_only", "example-model", "reviewer", "requested flag"),
            ("self_report", "example-model", "reviewer", "model-authored title"),
            ("harness_metadata", "other-model", "reviewer", "terminal metadata"),
            ("harness_metadata", None, "reviewer", "terminal metadata"),
            ("harness_metadata", "example-model", None, "terminal metadata"),
            ("harness_metadata", "example-model", "reviewer", ""),
        ):
            with self.subTest(basis=basis, model=model):
                ledger = self.synthetic_complete()
                result = ledger["candidates"][0]["attempts"][0]["result"]
                result["identity"] = {"basis": basis, "model": model, "reviewer": reviewer, "evidence": evidence}
                report = REVIEW.report(ledger)
                self.assertNotEqual(report["state"], "ready")
                self.assertIn("identity_unverified", [b["code"] for b in report["blockers"]])

    def synthetic_complete(self):
        bundle = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        candidate = {"digest": REVIEW.digest(bundle), "bundle": bundle, "attempts": [], "supersedes": None, "reason": "fixture"}
        for slot in bundle["reviewers"]:
            request = {"version": 1, "candidate": candidate["digest"], "slot": slot["id"],
                       "attempt": slot["id"], "adapter": slot["adapter"], "requested_model": slot["requested_model"]}
            result = self.result({"request": request, "request_digest": REVIEW.digest(request)})
            candidate["attempts"].append({"id": slot["id"], "request": request,
                "retry": None, "reason": "fixture", "result": result, "result_digest": REVIEW.digest(result)})
        return {"version": 1, "active": candidate["digest"], "candidates": [candidate]}

    def test_all_required_failure_modes_and_optional_blocking_findings(self):
        original = self.synthetic_complete()
        baseline = REVIEW.report(original)
        self.assertEqual(baseline["state"], "ready")
        self.assertEqual(baseline["blockers"], [])
        for verdict in ("failed", "cancelled", "interrupted", "changes-required"):
            with self.subTest(verdict=verdict):
                ledger = copy.deepcopy(original)
                ledger["candidates"][0]["attempts"][0]["result"]["verdict"] = verdict
                report = REVIEW.report(ledger)
                expected = [{"slot": "first", "code": "required_" + verdict}]
                if verdict == "changes-required":
                    expected.append({"slot": None, "code": "blocking_findings"})
                self.assertNotEqual(report["state"], "ready")
                self.assertEqual(report["state"], "changes-required" if verdict == "changes-required" else "reviewing")
                self.assertEqual(report["blockers"], expected)
        ledger = copy.deepcopy(original)
        ledger["candidates"][0]["bundle"]["reviewers"][1]["required"] = False
        baseline = REVIEW.report(ledger)
        self.assertEqual(baseline["state"], "ready")
        self.assertEqual(baseline["blockers"], [])
        ledger["candidates"][0]["attempts"][1]["result"]["findings"] = [
            {"id": "block", "severity": "blocking", "summary": "Fix required."}]
        report = REVIEW.report(ledger)
        self.assertEqual(report["state"], "changes-required")
        self.assertEqual(report["blockers"], [{"slot": None, "code": "blocking_findings"}])

    def test_v1_optional_terminal_failures_remain_ready(self):
        # V2 participating teams deliberately differ; its terminal/retry tests remain separate.
        self.spec["reviewers"][1]["required"] = False
        self.save_inputs()
        for verdict in ("failed", "cancelled", "interrupted"):
            with self.subTest(verdict=verdict):
                self.store = self.root / ("v1-optional-" + verdict)
                self.prepare()
                _, first = self.start()
                self.record(self.result(first))
                _, second = self.start("second")
                original = self.result(second, verdict)
                summary = self.record(original)
                expected_states = [("first", "pass"), ("second", verdict)]
                self.assertEqual(summary["state"], "ready")
                self.assertEqual(summary["blockers"], [])
                self.assertEqual([(s["id"], s["state"]) for s in summary["slots"]], expected_states)
                before = (self.store / "ledger.json").read_bytes()
                code, status = self.call("status", "--require-ready")
                self.assertEqual((code, status["state"]), (0, "ready"))
                self.assertEqual(status["blockers"], [])
                self.assertEqual([(s["id"], s["state"]) for s in status["slots"]], expected_states)
                self.assertFalse(status["publication_authority"])
                self.assertTrue(self.ledger()["candidates"][0]["attempts"][1]["result"] == original,
                                "Optional terminal result was not retained intact")
                self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                "Optional terminal status changed the ledger")
                self.assert_lock_released()

    def test_resume_retains_completed_review_without_starting_another(self):
        self.prepare()
        _, first = self.start()
        self.record(self.result(first))
        _, second = self.start("second")
        before = (self.store / "ledger.json").read_bytes()
        resumed = self.call("resume")[1]
        self.assertEqual([s["state"] for s in resumed["slots"]], ["pass", "in-flight"])
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        output = self.root / "recovered-request.json"
        self.call("resume", "--attempt", second["request"]["attempt"], "--output", output)
        self.assertEqual(json.loads(output.read_text()), second)
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)

    def test_request_reexport_refusals_preserve_ledger_output_and_lock(self):
        self.prepare()
        _, request = self.start()
        self.record(self.result(request))
        attempt = request["request"]["attempt"]

        def assert_refusal(command, args, name, diagnostic):
            output = self.root / ("refused-reexport-" + name + ".json")
            self.assertFalse(os.path.lexists(output), "Refusal output must start absent")
            before = (self.store / "ledger.json").read_bytes()
            code, error = self.call(command, *args, "--output", output, success=False)
            self.assertTrue((code, error) == (1, {"error": diagnostic}),
                            "Request re-export used an unexpected refusal diagnostic")
            self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                            "Rejected request re-export changed the ledger")
            self.assertFalse(os.path.lexists(output), "Rejected request re-export created output")
            self.assert_lock_released()

        assert_refusal("status", ("--attempt", attempt), "status", "attempt_required")
        assert_refusal("resume", ("--attempt", "missing-attempt"), "unknown", "unknown_attempt")
        before = (self.store / "ledger.json").read_bytes()
        recovered = self.root / "valid-reexport.json"
        self.call("resume", "--attempt", attempt, "--output", recovered)
        self.assertTrue(json.loads(recovered.read_text()) == request,
                        "Valid request re-export changed the original request")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Valid request re-export changed the ledger")
        self.assert_lock_released()

        previous = self.candidate
        self.spec["questions"].append("Does the successor preserve request-export isolation?")
        self.save_inputs()
        _, successor = self.call("supersede", "--expect", previous, "--reason", "New fixture question",
                                 "--repo", self.repo, "--base", self.base, "--head", self.head,
                                 "--inputs", self.inputs, "--sanitized")
        self.candidate = successor["candidate"]
        self.assertNotEqual(self.candidate, previous)
        # The old attempt exists and both candidates bind this same clean HEAD;
        # only its historical, non-active candidate makes this export ineligible.
        assert_refusal("resume", ("--candidate", previous, "--attempt", attempt),
                       "historical", "stale_candidate")

    def test_start_checkpoint_survives_failed_export(self):
        self.prepare()
        output = self.root / "occupied.json"
        output.write_text("owned sentinel")
        error = self.call("start", "--expect", self.candidate, "--slot", "first",
                          "--reason", "authorized", "--output", output, success=False)[1]
        self.assertEqual(error["error"], "invalid_input_or_local_operation")
        self.assertEqual(output.read_text(), "owned sentinel")
        pending = self.call("resume")[1]["slots"][0]
        self.assertEqual(pending["state"], "in-flight")
        self.call("resume", "--attempt", pending["attempt"], "--output", self.root / "recovered.json")

    def test_retry_is_explicit_and_cannot_reroll_a_pass(self):
        for verdict in ("failed", "cancelled", "interrupted"):
            with self.subTest(verdict=verdict):
                self.store = self.root / verdict
                self.prepare()
                _, request = self.start()
                self.record(self.result(request, verdict))
                output = self.root / ("not-created-" + verdict + ".json")
                self.assertFalse(os.path.lexists(output))
                error = self.call("start", "--expect", self.candidate, "--slot", "first", "--reason", "retry",
                                  "--output", output, success=False)[1]
                self.assertEqual(error["error"], "explicit_retry_required")
                self.assertFalse(os.path.lexists(output))
                _, retry = self.start(retry=request["request"]["attempt"])
                self.record(self.result(retry))
                error = self.call("start", "--expect", self.candidate, "--slot", "first", "--reason", "reroll",
                                  "--retry", retry["request"]["attempt"], "--output", output,
                                  success=False)[1]
                self.assertEqual(error["error"], "invalid_retry")
                self.assertFalse(os.path.lexists(output))
                self.assertEqual(len(self.ledger()["candidates"][0]["attempts"]), 2)

    def test_duplicate_result_and_cross_slot_or_candidate_are_refused(self):
        self.prepare()
        _, request = self.start()
        for field, value in (("candidate", "0" * 64), ("slot", "second"),
                             ("request_digest", "0" * 64), ("adapter", "other")):
            result = self.result(request)
            result[field] = value
            self.assertEqual(self.record(result, success=False)["error"], "result_binding_mismatch")
            self.assert_lock_released()
        result = self.result(request)
        self.record(result)
        self.assertEqual(self.record(result, success=False)["error"], "result_already_recorded")
        self.assert_lock_released()

    def test_one_runtime_session_cannot_fill_independent_slots(self):
        self.prepare()
        _, first = self.start()
        first_result = self.result(first)
        self.record(first_result)
        _, second = self.start("second")
        second_result = self.result(second)
        second_result["run_id"] = first_result["run_id"]
        self.assertEqual(self.record(second_result, success=False)["error"], "duplicate_identity")
        self.assert_lock_released()
        self.assertIsNone(self.ledger()["candidates"][0]["attempts"][1]["result"])

    def test_v1_run_id_identifier_boundaries(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        original["run_id"] = "r" * 128
        candidate = self.ledger()["candidates"][0]
        REVIEW.result_check(original, candidate["attempts"][0], candidate)
        before = (self.store / "ledger.json").read_bytes()
        for name, value, expected in (("slash", "fixture/run", "invalid_identifier"),
                                      ("leading-hyphen", "-fixture", "invalid_identifier"),
                                      ("embedded-space", "fixture run", "invalid_identifier"),
                                      ("too-long", "r" * 129, "invalid_string")):
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                invalid["run_id"] = value
                self.assertEqual(self.record(invalid, success=False)["error"], expected)
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                self.assert_lock_released()
        self.record(original)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)
        _, second = self.start("second")
        punctuated = self.result(second)
        punctuated["run_id"] = "a.b_c-d"
        self.record(punctuated)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][1]["result"], punctuated)

    def test_new_candidate_invalidates_approvals_and_preserves_history(self):
        self.approve_all()
        old = self.candidate
        self.spec["questions"].append("Also check the evidence distinction.")
        self.save_inputs()
        _, fresh = self.call("supersede", "--expect", old, "--reason", "expanded question",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")
        self.assertNotEqual(fresh["candidate"], old)
        self.assertEqual(fresh["state"], "prepared")
        old_report = self.call("status", "--candidate", old)[1]
        self.assertEqual(old_report["state"], "superseded")
        self.assertEqual([s["state"] for s in old_report["slots"]], ["pass", "pass"])
        error = self.call("start", "--expect", old, "--slot", "first", "--reason", "stale",
                          "--output", self.root / "stale.json", success=False)[1]
        self.assertEqual(error["error"], "stale_candidate")

    def test_changed_commit_same_tree_and_dirty_work_prevent_ready(self):
        self.approve_all()
        self.git("commit", "--allow-empty", "-q", "-m", "Different commit, same tree")
        report = self.call("status")[1]
        self.assertIn("candidate_moved", [b["code"] for b in report["blockers"]])
        self.assertNotEqual(report["state"], "ready")

    def test_modified_staged_untracked_and_ignored_files_rejected(self):
        self.prepare()
        for name in ("content.txt", "untracked.txt", "ignored.txt"):
            path = self.repo / name
            original = path.read_bytes() if path.exists() else None
            path.write_text("changed\n")
            if name == "ignored.txt":
                (self.repo / ".git/info").mkdir(exist_ok=True)
                (self.repo / ".git/info/exclude").write_text("ignored.txt\n")
            self.assertIn("dirty_candidate", [b["code"] for b in self.call("status")[1]["blockers"]])
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)
        (self.repo / "content.txt").write_text("staged\n")
        self.git("add", "content.txt")
        (self.repo / "content.txt").write_text("candidate\n")
        self.assertIn("dirty_candidate", [b["code"] for b in self.call("status")[1]["blockers"]])

    def test_missing_tracked_file_blocks_readiness_without_ledger_mutation(self):
        self.assertEqual(self.approve_all()["state"], "ready")
        before = (self.store / "ledger.json").read_bytes()
        path = self.repo / "content.txt"
        backup = self.root / "missing-content.txt"
        path.rename(backup)
        try:
            code, report = self.call("status", "--require-ready", success=False, failure_code=2)
            self.assertEqual(code, 2)
            self.assertEqual(report["state"], "reviewing")
            self.assertTrue(report["blockers"] == [{"slot": None, "code": "dirty_candidate"}],
                            "missing tracked content did not produce the exact dirty blocker")
            self.assertTrue((self.store / "ledger.json").read_bytes() == before, "missing-file check changed ledger")
            self.assert_lock_released()
        finally:
            backup.rename(path)  # Restore only the tracked file owned by this fixture.
        self.assertEqual(self.call("status", "--require-ready")[1]["state"], "ready")

    def test_unmerged_index_stages_block_readiness_without_ledger_mutation(self):
        ours = self.head
        (self.repo / "content.txt").write_text("incoming fixture content\n")
        self.head = self.commit("Incoming fixture tree")
        self.assertEqual(self.approve_all()["state"], "ready")
        before = (self.store / "ledger.json").read_bytes()
        content = (self.repo / "content.txt").read_bytes()
        try:
            # Merge three owned trees in the index only; never merge a user worktree.
            self.git("read-tree", ours)
            self.git("read-tree", "-i", "-m", self.base, ours, self.head)
            entries = self.git("ls-files", "--stage", "--", "content.txt").splitlines()
            stages = {row.split("\t", 1)[0].split()[2] for row in entries}
            self.assertTrue(len(entries) == 3 and stages == {"1", "2", "3"},
                            "fixture did not create real stage-1/2/3 entries")
            self.assertTrue((self.repo / "content.txt").read_bytes() == content,
                            "index-only fixture merge changed worktree content")
            code, report = self.call("status", "--require-ready", success=False, failure_code=2)
            self.assertEqual(code, 2)
            self.assertEqual(report["state"], "reviewing")
            self.assertTrue(report["blockers"] == [{"slot": None, "code": "dirty_candidate"}],
                            "unmerged index did not produce the exact dirty blocker")
            self.assertTrue((self.store / "ledger.json").read_bytes() == before, "unmerged-index check changed ledger")
            self.assert_lock_released()
        finally:
            self.git("read-tree", self.head)  # Restore this fixture's index, not its worktree.
        self.assertEqual(self.call("status", "--require-ready")[1]["state"], "ready")

    def test_tracked_file_and_directory_symlinks_compare_link_text_without_following(self):
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                target = self.root / (kind + "-target")
                if kind == "directory":
                    target.mkdir()
                content = target / "content.txt" if kind == "directory" else target
                content.write_text("original target content\n")
                link = self.repo / (kind + "-link")
                text = "../" + target.name
                link.symlink_to(text, target_is_directory=(kind == "directory"))
                self.head = self.commit("Track owned " + kind + " symlink")
                self.assertTrue(self.git("ls-tree", "HEAD", link.name).startswith("120000 blob "),
                                "fixture did not commit a symlink entry")
                self.store = self.root / ("review-" + kind)
                self.assertEqual(self.approve_all()["state"], "ready")
                before = (self.store / "ledger.json").read_bytes()
                self.assertTrue(os.readlink(link) == text, "preparation changed fixture link text")
                self.assertTrue(content.read_text() == "original target content\n", "preparation changed target content")
                content.write_text("changed target content\n")
                self.assertEqual(self.call("status", "--require-ready")[1]["state"], "ready")
                try:
                    link.unlink()  # Both link spellings stay within this owned fixture.
                    link.symlink_to(".././" + target.name, target_is_directory=(kind == "directory"))
                    self.assertTrue(os.readlink(link) == ".././" + target.name,
                                    "fixture did not change the raw link text")
                    self.assertTrue(link.resolve() == target, "changed link no longer names the same owned target")
                    code, report = self.call("status", "--require-ready", success=False, failure_code=2)
                    self.assertEqual(code, 2)
                    self.assertTrue(report["blockers"] == [{"slot": None, "code": "dirty_candidate"}],
                                    "changed link text did not produce the exact dirty blocker")
                    self.assertTrue((self.store / "ledger.json").read_bytes() == before, "symlink checks changed ledger")
                    self.assert_lock_released()
                finally:
                    link.unlink(missing_ok=True)
                    link.symlink_to(text, target_is_directory=(kind == "directory"))
                self.assertTrue(content.read_text() == "changed target content\n", "source check changed target content")
                self.assertEqual(self.call("status", "--require-ready")[1]["state"], "ready")

    def test_filters_fsmonitor_external_diff_and_pager_are_not_run(self):
        sentinel = self.root / "should-not-exist"
        command = "touch " + str(sentinel)
        self.git("config", "filter.fixture.clean", command)
        self.git("config", "filter.fixture.smudge", command)
        self.git("config", "core.fsmonitor", command)
        self.git("config", "diff.external", command)
        self.git("config", "core.pager", command)
        (self.repo / ".git/info").mkdir(exist_ok=True)
        (self.repo / ".git/info/attributes").write_text("* filter=fixture diff=fixture\n")
        self.git("config", "diff.fixture.textconv", command)
        self.prepare()
        self.call("status")
        self.assertFalse(sentinel.exists())

    def test_canonical_regular_mode_depends_only_on_owner_execute(self):
        path = self.repo / "mode.txt"
        path.write_text("Canonical Git mode fixture.\n")
        self.git("config", "core.fileMode", "false")
        for index_mode, correct, incorrect in (("100644", 0o451, 0o551),
                                               ("100755", 0o755, 0o655)):
            path.chmod(0o644)
            self.git("add", "mode.txt")
            self.git("update-index", "--chmod=" + ("+x" if index_mode == "100755" else "-x"), "mode.txt")
            self.git("commit", "-q", "-m", "Record canonical " + index_mode)
            self.head = self.git("rev-parse", "HEAD")
            self.assertTrue(self.git("ls-tree", "HEAD", "mode.txt").startswith(index_mode + " blob "))
            self.assertEqual(correct ^ incorrect, 0o100)
            for mode, accepted in ((correct, True), (incorrect, False)):
                with self.subTest(index_mode=index_mode, mode=oct(mode), accepted=accepted):
                    self.store = self.root / (index_mode + "-" + oct(mode))
                    path.chmod(mode)
                    self.assertEqual(path.stat().st_mode & 0o777, mode)
                    self.assertTrue(mode & 0o011)  # Both pairs retain group/other execute bits.
                    if accepted:
                        self.assertEqual(self.prepare()["state"], "prepared")
                    else:
                        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                            "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                        self.assertEqual((code, error), (1, {"error": "dirty_candidate"}))
                        self.assertFalse(self.store.exists())

    def test_repository_routing_refused_and_security_context_retained(self):
        expected = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"}
        self.assertTrue(REVIEW.ROUTING == expected, "The seven-name Git routing contract changed")
        for name in sorted(expected):
            with self.subTest(variable=name):
                with patch.dict(os.environ, {name: str(self.root / "outside")}), patch.object(
                    subprocess, "run",
                ) as runner:
                    code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                        "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                self.assertTrue((code, error) == (1, {"error": "git_routing_override"}),
                                "Expected the static Git-routing refusal")
                self.assertFalse(runner.called, "Routing refusal must precede Git execution")
                self.assertFalse(self.store.exists())
        context = {"HTTPS_PROXY": "https://proxy.example.invalid", "SSL_CERT_FILE": "synthetic-ca",
                   "HARNESS_SECURITY_CONTEXT": "preserve"}
        with patch.dict(os.environ, context), patch.object(subprocess, "run", wraps=subprocess.run) as runner:
            self.prepare()
        for call in runner.call_args_list:
            for key, value in context.items():
                self.assertEqual(call.kwargs["env"][key], value)
        self.assertFalse(any("fetch" in call.args[0] for call in runner.call_args_list))

    def test_local_partial_clone_configuration_is_rejected_without_store(self):
        for key, value in (("remote.example.promisor", "true"), ("extensions.partialclone", "example")):
            with self.subTest(configuration=key):
                self.store = self.root / ("partial-clone-" + key)
                self.git("config", key, value)
                try:
                    with patch.object(REVIEW, "git", wraps=REVIEW.git) as reader:
                        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                            "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                    self.assertTrue((code, error) == (1, {"error": "partial_clone_unsupported"}),
                                    "Expected the static local partial-clone refusal")
                    operations = [call.args[1] for call in reader.call_args_list]
                    self.assertTrue("partial" in operations and "diff" not in operations,
                                    "Local partial-clone refusal must precede diff generation")
                    self.assertFalse(self.store.exists())
                finally:
                    self.git("config", "--unset", key)
                with patch.object(REVIEW, "git", wraps=REVIEW.git) as reader:
                    self.assertEqual(self.prepare()["state"], "prepared")
                self.assertTrue(any(call.args[1] == "diff" for call in reader.call_args_list),
                                "The otherwise-identical valid prepare must generate its diff")

    def test_symlink_traversal_permissions_and_inside_repo_outputs(self):
        self.spec["artifacts"][0]["path"] = "../outside.txt"
        self.save_inputs()
        error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                          "--inputs", self.inputs, "--sanitized", success=False)[1]
        self.assertEqual(error["error"], "unsafe_artifact_path")
        self.spec["artifacts"][0]["path"] = "link.txt"
        (self.root / "link.txt").symlink_to(self.root / "supplied.txt")
        self.save_inputs()
        error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                          "--inputs", self.inputs, "--sanitized", success=False)[1]
        self.assertEqual(error["error"], "symlink_path")
        self.spec["artifacts"][0]["path"] = "supplied.txt"
        self.save_inputs()
        self.prepare()
        error = self.call("start", "--expect", self.candidate, "--slot", "first", "--reason", "test",
                          "--output", self.repo / "request.json", success=False)[1]
        self.assertEqual(error["error"], "output_inside_repository")
        self.assertFalse(os.path.lexists(self.repo / "request.json"))
        self.assert_lock_released()
        (self.store / "ledger.json").chmod(0o644)
        self.assertEqual(self.call("status", success=False)[1]["error"], "unsafe_permissions")
        self.assert_lock_released()

    def test_closed_schema_duplicate_keys_and_secret_errors(self):
        value = credential_shapes()[0][1]
        for text, expected in (('{"scope":"first","scope":"second"}', "duplicate_json_key"),
                               ('{"value": NaN}', "invalid_json_number"),
                               (json.dumps({"scope": value}), "invalid_schema")):
            self.inputs.write_text(text)
            code, error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                                    "--inputs", self.inputs, "--sanitized", success=False)
            self.assertEqual((code, error), (1, {"error": expected}))
        self.spec["scope"] = value
        self.save_inputs()
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                                "--inputs", self.inputs, "--sanitized", success=False)
        self.assertEqual((code, error), (1, {"error": "sensitive_input"}))
        self.assertFalse(self.store.exists())

    def test_crlf_attachment_is_rejected_and_lf_attachment_is_preserved(self):
        artifact = self.root / "supplied.txt"
        original = b"Supplied fixture evidence.\r\nSecond line.\r\n"
        artifact.write_bytes(original)
        REVIEW.clean_candidate(self.repo, self.head, "none")
        before = set(self.root.iterdir())
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
            "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
        self.assertTrue((code, error) == (1, {"error": "invalid_string"}),
                        "CRLF attachment refusal used an unexpected diagnostic")
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(self.store.exists())
        self.assertTrue(artifact.read_bytes() == original, "attachment refusal changed supplied bytes")
        accepted = original.replace(b"\r\n", b"\n")
        artifact.write_bytes(accepted)
        self.assertEqual(self.prepare()["state"], "prepared")
        stored = self.ledger()["candidates"][0]["bundle"]["artifacts"][0]
        self.assertTrue(stored["text"].encode() == accepted, "LF attachment was not retained exactly")
        self.assertEqual(stored["sha256"], hashlib.sha256(accepted).hexdigest())

    def test_raw_committed_cr_diff_is_rejected_and_lf_diff_is_preserved(self):
        path = self.repo / "control-character.txt"
        info = self.repo / ".git/info"
        info.mkdir(exist_ok=True)
        # Only this owned fixture file: preserve raw bytes and require a text diff.
        (info / "attributes").write_text(path.name + " -text diff\n")
        for name, content, accepted in (
            ("carriage-return", b"first\rsecond\n", False),
            ("line-feed", b"first\nsecond\n", True),
        ):
            with self.subTest(case=name):
                self.store = self.root / ("diff-" + name)
                path.write_bytes(content)
                self.head = self.commit("Commit raw control-character fixture")
                self.assertTrue(self.git("hash-object", "--no-filters", str(path)) ==
                                self.git("rev-parse", self.head + ":" + path.name),
                                "committed blob does not match the raw fixture bytes")
                REVIEW.clean_candidate(self.repo, self.head, "none")
                difference = REVIEW.git(self.repo, "diff", self.base, self.head).decode("utf-8")
                self.assertTrue(("\r" in difference) == (not accepted),
                                "generated diff did not retain the intended character boundary")
                if accepted:
                    self.assertEqual(self.prepare()["state"], "prepared")
                    stored = self.ledger()["candidates"][0]["bundle"]["diff"]
                    self.assertTrue(stored["text"] == difference, "LF diff was not retained exactly")
                    self.assertEqual(stored["sha256"], hashlib.sha256(difference.encode()).hexdigest())
                else:
                    before = set(self.root.iterdir())
                    code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                        "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                    self.assertTrue((code, error) == (1, {"error": "invalid_string"}),
                                    "CR diff refusal used an unexpected diagnostic")
                    self.assertEqual(set(self.root.iterdir()), before)
                    self.assertFalse(self.store.exists())
                self.assertTrue(path.read_bytes() == content, "diff capture changed the raw fixture")

    def test_sentinel_source_diff_is_bound_without_redaction(self):
        source = Path(__file__).read_bytes()
        fixture = self.repo / "unexecuted-test-source.py"
        fixture.write_bytes(source)
        self.head = self.commit("Capture unexecuted fixture source")
        ambient = self.root / "ambient-attributes"
        ambient_bytes = (fixture.name + " -diff\n").encode()
        ambient.write_bytes(ambient_bytes)
        inherited = dict(os.environ)
        assignment = "core.attributesFile=" + str(ambient)
        # Append one fixture setting; retain all inherited config/security channels.
        parameters = inherited.get("GIT_CONFIG_PARAMETERS", "")
        parameters += (" " if parameters else "") + "'" + assignment.replace("'", "'\\''") + "'"
        with patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": parameters}):
            binary = REVIEW.git(self.repo, "diff", self.base, self.head)
            self.assertTrue(b"GIT binary patch" in binary,
                            "ambient attributes did not exercise the binary-diff counterexample")
            self.assertFalse(b"SYNTHETIC_REVIEW_SECRET_" in binary,
                             "binary counterexample unexpectedly exposed the source marker")
            info = self.repo / ".git/info"
            info.mkdir(exist_ok=True)
            (info / "attributes").write_text(fixture.name + " diff\n")
            with patch.object(subprocess, "run", wraps=subprocess.run) as runner:
                expected = REVIEW.git(self.repo, "diff", self.base, self.head).decode("utf-8")
                self.assertTrue("SYNTHETIC_REVIEW_SECRET_" in expected, "fixture diff must contain the test marker")
                self.assertEqual(self.prepare()["state"], "prepared")
            expected_environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
            for invocation in runner.call_args_list:
                self.assertTrue(invocation.kwargs["env"] == expected_environment,
                                "controller discarded or changed inherited fixture security context")
        self.assertTrue(dict(os.environ) == inherited, "attribute fixture did not restore inherited settings")
        self.assertTrue(ambient.read_bytes() == ambient_bytes, "capture changed the ambient-attribute fixture")
        bundle = self.ledger()["candidates"][0]["bundle"]
        self.assertTrue(bundle["diff"]["text"] == expected, "captured fixture diff was changed")
        self.assertEqual(bundle["diff"]["sha256"], hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(bundle["head"], self.head)
        self.assertTrue(fixture.read_bytes() == source, "fixture source changed during capture")

    def test_generated_credential_in_bound_diff_is_rejected(self):
        value = credential_shapes()[0][1]
        (self.repo / "credential-fixture.txt").write_text(value + "\n")
        self.head = self.commit("Add generated pattern fixture")
        difference = REVIEW.git(self.repo, "diff", self.base, self.head).decode("utf-8")
        self.assertTrue(value in difference, "fixture diff must contain the generated pattern")
        before = set(self.root.iterdir())
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                                "--inputs", self.inputs, "--sanitized", success=False)
        self.assertEqual((code, error), (1, {"error": "sensitive_input"}))
        self.assertFalse(self.store.exists())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_artifact_and_result_tampering_fail_closed(self):
        self.approve_all()
        original = (self.store / "ledger.json").read_bytes()
        ledger = self.ledger()
        ledger["candidates"][0]["bundle"]["artifacts"][0]["text"] = "tampered"
        (self.store / "ledger.json").write_text(json.dumps(ledger))
        self.assertEqual(self.call("status", success=False)[1]["error"], "artifact_changed")
        self.assert_lock_released()
        (self.store / "ledger.json").write_bytes(original)
        ledger = self.ledger()
        ledger["candidates"][0]["attempts"][0]["result"]["verdict"] = "failed"
        (self.store / "ledger.json").write_text(json.dumps(ledger))
        self.assertEqual(self.call("resume", success=False)[1]["error"], "result_digest_mismatch")
        self.assert_lock_released()

    def test_atomic_checkpoint_failures_preserve_old_state(self):
        self.prepare()
        before = (self.store / "ledger.json").read_bytes()
        with patch.object(os, "replace", side_effect=OSError(credential_shapes()[0][1])):
            error = self.call("start", "--expect", self.candidate, "--slot", "first", "--reason", "test",
                              "--output", self.root / "request.json", success=False)[1]
            self.assertEqual(error["error"], "invalid_input_or_local_operation")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(list(self.store.glob(".checkpoint-*")), [])
        self.assertEqual(self.call("resume")[1]["state"], "prepared")

    def test_lock_contention_and_completed_start_never_imply_review_result(self):
        self.prepare()
        with (self.store / "lock").open("rb") as stream:
            REVIEW.fcntl.flock(stream, REVIEW.fcntl.LOCK_EX)
            self.assertEqual(self.call("resume", success=False)[1]["error"], "store_busy")
        self.assertEqual(self.call("resume")[1]["state"], "prepared")
        # The controller process exits normally, without a reviewer result.
        request = self.root / "child-request.json"
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "start", "--store", str(self.store),
            "--expect", self.candidate, "--slot", "first", "--reason", "fake interrupted reviewer", "--output", str(request)],
            capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, "owned controller start failed")
        self.assertEqual(self.call("resume")[1]["slots"][0]["state"], "in-flight")

    def test_sigterm_entrypoint_uses_static_reconciliation_error(self):
        self.prepare()
        before = (self.store / "ledger.json").read_bytes()
        marker = self.root / "git-boundary-reached"
        request = self.root / "never-exported.json"

        def assert_no_request_output(path):
            self.assertFalse(os.path.lexists(path), "SIGTERM fixture created request output")

        assert_no_request_output(request)

        program = (
            "import os, pathlib, runpy, signal, subprocess, sys\n"
            "script, marker = sys.argv[1:3]\n"
            "sys.argv = [script, *sys.argv[3:]]\n"
            "def interrupt(command, *args, **kwargs):\n"
            " assert command[1:4] == ['--no-pager', '--no-optional-locks', '--no-replace-objects']\n"
            " with pathlib.Path(marker).open('x') as stream:\n"
            "  stream.write('git boundary reached')\n"
            " os.kill(os.getpid(), signal.SIGTERM)\n"
            " raise AssertionError('self-signal did not interrupt controller')\n"
            "subprocess.run = interrupt\n"
            "runpy.run_path(script, run_name='__main__')\n"
        )
        # Only the owned child patches run; the real entrypoint installs its handler.
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", program, str(SCRIPT), str(marker),
                 "start", "--store", str(self.store), "--expect", self.candidate,
                 "--slot", "first", "--reason", "owned self-interruption fixture", "--output", str(request)],
                cwd=self.root, env=self.environment, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            # run() kills and reaps its own direct child before raising.
            self.fail("owned SIGTERM fixture did not finish")
        assert_no_fixture_output(self, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 130)
        self.assertTrue(result.stdout == b"", "SIGTERM fixture wrote unexpected stdout")
        self.assertTrue(result.stderr == b'{"error":"interrupted_reconcile_store"}\n',
                        "SIGTERM fixture did not emit the exact static diagnostic")
        self.assertTrue(marker.is_file(), "child did not reach the Git boundary")
        self.assertTrue(marker.read_text() == "git boundary reached", "unexpected Git boundary marker")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "SIGTERM fixture changed the ledger")
        assert_no_request_output(request)
        self.assert_lock_released()

        # Exercise the same oracle without starting another controller child.
        dangling = self.root / "dangling-request-control.json"
        dangling.symlink_to("missing-request-control-target")
        self.assertTrue(dangling.is_symlink() and not dangling.exists(),
                        "Owned request-output control must be a dangling symlink")
        with self.assertRaises(AssertionError) as caught:
            assert_no_request_output(dangling)
        self.assertTrue(str(caught.exception).endswith("SIGTERM fixture created request output"),
                        "Dangling output failed outside its absence oracle")

    def test_sigkill_after_start_preserves_pending_attempt_and_releases_lock(self):
        self.prepare()
        marker = self.root / "checkpoint-reached"
        program = (
            "import importlib.util, pathlib, sys, time\n"
            "spec=importlib.util.spec_from_file_location('review', sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
            "original=module.checkpoint\n"
            "def pause(store, ledger):\n"
            " original(store, ledger)\n"
            " pathlib.Path(sys.argv[2]).write_text('checkpoint reached')\n"
            " time.sleep(30)\n"
            "module.checkpoint=pause\n"
            "module.main(sys.argv[3:])\n"
        )
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", program, str(SCRIPT), str(marker),
            "start", "--store", str(self.store), "--expect", self.candidate, "--slot", "first",
            "--reason", "owned interruption fixture", "--output", str(self.root / "never-exported.json")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "child did not reach checkpoint")
            process.send_signal(signal.SIGKILL)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            report = self.call("resume")[1]
            self.assertEqual(report["slots"][0]["state"], "in-flight")
            self.assertNotEqual(report["state"], "ready")
            self.assertFalse(os.path.lexists(self.root / "never-exported.json"))
        finally:
            if process.poll() is None:
                process.kill()  # Only this fixture's direct child.
            process.communicate(timeout=5)

    def test_gitlinks_require_explicit_scope_and_bind_unavailable_pin_objects(self):
        pin = "1" * 40
        self.git("update-index", "--add", "--cacheinfo", "160000," + pin + ",dependencies/example")
        self.git("commit", "-q", "-m", "Add pin without nested objects")
        self.head = self.git("rev-parse", "HEAD")
        error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                          "--inputs", self.inputs, "--sanitized", success=False)[1]
        self.assertEqual(error["error"], "gitlink_scope_required")
        self.spec["gitlink_scope"] = "pins-only"
        self.spec["dependency_changes"] = "Added dependency pin; nested source not supplied or reviewed."
        self.save_inputs()
        report = self.prepare()
        self.assertEqual(report["gitlinks"], [{"path": "dependencies/example", "base": None, "head": pin}])
        self.assertEqual(report["scope_limits"], [REVIEW.PIN_LIMIT])
        _, request = self.start()
        self.assertEqual(request["bundle"]["scope_limits"], report["scope_limits"])
        self.assertEqual(request["bundle"]["gitlinks"], report["gitlinks"])
        original = self.candidate
        self.git("update-index", "--cacheinfo", "160000," + "2" * 40 + ",dependencies/example")
        self.assertIn("dirty_candidate", [b["code"] for b in self.call("status")[1]["blockers"]])
        self.git("commit", "-q", "-m", "Change pin")
        self.head = self.git("rev-parse", "HEAD")
        _, report = self.call("supersede", "--expect", original, "--reason", "changed dependency pin",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")
        self.assertNotEqual(report["candidate"], original)
        self.assertEqual(report["state"], "prepared")
        self.assertEqual(report["gitlinks"][0]["head"], "2" * 40)

    def test_initialized_gitlink_head_mismatch_blocks_but_dirty_content_is_out_of_scope(self):
        nested = self.repo / "dependency"
        nested.mkdir()
        parent = self.repo
        self.repo = nested
        self.git("init", "-q", "--template=", "--object-format=sha1")
        (nested / "nested.txt").write_text("nested baseline")
        first = self.commit("Nested baseline")
        (nested / "nested.txt").write_text("nested revision")
        second = self.commit("Nested revision")
        self.repo = parent
        self.git("update-index", "--add", "--cacheinfo", "160000," + first + ",dependency")
        self.git("commit", "-q", "-m", "Add mismatching initialized pin")
        self.head = self.git("rev-parse", "HEAD")
        self.spec["gitlink_scope"] = "pins-only"
        self.save_inputs()
        error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                          "--inputs", self.inputs, "--sanitized", success=False)[1]
        self.assertEqual(error["error"], "gitlink_head_mismatch")
        self.git("update-index", "--cacheinfo", "160000," + second + ",dependency")
        self.git("commit", "-q", "-m", "Match initialized pin")
        self.head = self.git("rev-parse", "HEAD")
        (nested / "nested.txt").write_text("dirty and explicitly unreviewed")
        report = self.prepare()
        self.assertEqual(report["state"], "prepared")
        self.assertEqual(report["scope_limits"], [REVIEW.PIN_LIMIT])

    def test_missing_and_partial_stores_are_not_success(self):
        self.assertEqual(self.call("resume", success=False)[1]["error"], "missing_path")
        self.store.mkdir(mode=0o700)
        (self.store / "lock").touch(mode=0o600)
        self.assertEqual(self.call("resume", success=False)[1]["error"], "missing_path")

    def test_pin_only_initialized_missing_objects_do_not_trigger_fetch(self):
        pin = "3" * 40
        nested = self.repo / "dependency"
        nested.mkdir()
        parent = self.repo
        self.repo = nested
        self.git("init", "-q", "--template=", "--object-format=sha1")
        (nested / ".git/HEAD").write_text(pin + "\n")
        self.git("config", "remote.example.promisor", "true")
        self.repo = parent
        self.git("update-index", "--add", "--cacheinfo", "160000," + pin + ",dependency")
        self.git("commit", "-q", "-m", "Add pin with unavailable nested object")
        self.head = self.git("rev-parse", "HEAD")
        self.spec["gitlink_scope"] = "pins-only"
        self.save_inputs()
        with patch.object(subprocess, "run", wraps=subprocess.run) as runner:
            report = self.prepare()
        self.assertEqual(report["gitlinks"][0]["head"], pin)
        self.assertEqual(report["scope_limits"], [REVIEW.PIN_LIMIT])
        self.assertFalse(any("fetch" in call.args[0] for call in runner.call_args_list))

    def test_disposition_rejects_genuine_v1_workflow(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        result["findings"] = [{"id": "issue", "severity": "blocking", "summary": "Fixture correction required."}]
        self.record(result)
        disposition = {
            "version": 2, "candidate": self.candidate, "id": "request-fix", "actor": "fixture-coordinator",
            "authority": "Explicit fixture decision, not authenticated authority.",
            "decisions": [{"attempt": result["attempt"], "finding": "issue", "decision": "request-fix",
                           "rationale": "Correct the recorded fixture finding."}],
            "provenance": {"importer": "fixture-operator", "source": "fixture-decision",
                           "source_sha256": REVIEW.sha(b"decision")},
        }
        path = self.root / "decision.json"
        path.write_text(json.dumps(disposition))
        before = (self.store / "ledger.json").read_bytes()
        code, error = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                "--sanitized", success=False)
        self.assertEqual(code, 1)
        self.assertEqual(error["error"], "team_review_required")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.ledger()["version"], 1)
        self.assertEqual(self.ledger()["candidates"][0]["bundle"]["version"], 1)
        self.assert_lock_released()

    def test_prelaunch_interruption_can_be_recorded_without_inventing_runtime_identity(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "interrupted")
        result["run_id"] = None
        result["identity"] = {"basis": "unknown", "reviewer": None, "model": None, "evidence": ""}
        report = self.record(result)
        self.assertNotEqual(report["state"], "ready")
        _, retry = self.start(retry=request["request"]["attempt"])
        invalid = self.result(retry)
        invalid["run_id"] = None
        self.assertEqual(self.record(invalid, success=False)["error"], "missing_completed_run")
        self.assert_lock_released()

    def test_replaced_result_survives_lost_success_response(self):
        self.prepare()
        _, request = self.start()
        original = os.replace

        def replaced_then_failed(source, target):
            original(source, target)
            raise OSError(credential_shapes()[0][1])

        with patch.object(os, "replace", side_effect=replaced_then_failed):
            self.assertEqual(self.record(self.result(request), success=False)["error"],
                             "invalid_input_or_local_operation")
        self.assert_lock_released()
        self.assertEqual(self.call("resume")[1]["slots"][0]["state"], "pass")
        self.assertEqual(self.record(self.result(request), success=False)["error"], "result_already_recorded")
        self.assert_lock_released()

    def test_supersede_failure_preserves_prior_history_atomically(self):
        self.approve_all()
        original = (self.store / "ledger.json").read_bytes()
        self.spec["questions"].append("New scope")
        self.save_inputs()
        with patch.object(os, "replace", side_effect=OSError("synthetic interruption")):
            error = self.call("supersede", "--expect", self.candidate, "--reason", "new question",
                "--repo", self.repo, "--base", self.base, "--head", self.head,
                "--inputs", self.inputs, "--sanitized", success=False)[1]
            self.assertEqual(error["error"], "invalid_input_or_local_operation")
        self.assert_lock_released()
        self.assertEqual((self.store / "ledger.json").read_bytes(), original)
        self.assertEqual(len(self.ledger()["candidates"]), 1)

    def test_unknown_fields_types_and_unsupported_versions_are_rejected(self):
        original = self.synthetic_complete()
        mutations = (
            (lambda value: value.update(extra="unknown"), "invalid_schema"),
            (lambda value: value.update(version=True), "unsupported_version"),
            (lambda value: value.update(version=2), "unsupported_version"),
            (lambda value: value.update(active="0" * 64), "invalid_active_candidate"),
            (lambda value: value["candidates"][0]["attempts"][0]["result"].update(extra="unknown"), "invalid_schema"),
            (lambda value: value["candidates"][0]["bundle"].update(
                scope_limits=[] if value["candidates"][0]["bundle"]["scope_limits"] else ["invented"]),
             "invalid_scope_limits"),
        )
        for mutation, expected in mutations:
            value = copy.deepcopy(original)
            mutation(value)
            with self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                REVIEW.validate_ledger(value)

    def test_malformed_ledger_candidate_shapes_preserve_bytes_lock_and_readiness(self):
        self.assertEqual(ReviewTests.approve_all(self)["state"], "ready")
        original_bytes = (self.store / "ledger.json").read_bytes()
        original = self.ledger()
        candidate = original["candidates"][0]
        bundle = candidate["bundle"]
        self.assertEqual((original["version"], bundle["version"]), (self.spec.get("version", 1),) * 2)
        wrong_envelope = copy.deepcopy(candidate)
        if bundle["version"] == 1:
            wrong_envelope["dispositions"] = []
        else:
            wrong_envelope.pop("dispositions")
        cases = (
            ("null-candidate", None, "invalid_schema"),
            ("list-candidate", [], "invalid_schema"),
            ("string-candidate", "malformed candidate", "invalid_schema"),
            ("missing-bundle", {k: v for k, v in candidate.items() if k != "bundle"}, "invalid_schema"),
            ("null-bundle", {**candidate, "bundle": None}, "unsupported_version"),
            ("list-bundle", {**candidate, "bundle": []}, "unsupported_version"),
            ("string-bundle", {**candidate, "bundle": "malformed bundle"}, "unsupported_version"),
            ("missing-version", {**candidate, "bundle": {k: v for k, v in bundle.items() if k != "version"}},
             "unsupported_version"),
            ("boolean-version", {**candidate, "bundle": {**bundle, "version": True}}, "unsupported_version"),
            ("string-version", {**candidate, "bundle": {**bundle, "version": "2"}}, "unsupported_version"),
            ("unsupported-version", {**candidate, "bundle": {**bundle, "version": 4}}, "unsupported_version"),
            ("unsupported-version-missing-repository",
             {**candidate, "bundle": {**{k: v for k, v in bundle.items() if k != "repository"}, "version": 4}},
             "unsupported_version"),
            ("missing-repository", {**candidate, "bundle": {k: v for k, v in bundle.items() if k != "repository"}},
             "invalid_schema"),
            ("null-repository", {**candidate, "bundle": {**bundle, "repository": None}}, "invalid_schema"),
            ("missing-attempts", {k: v for k, v in candidate.items() if k != "attempts"}, "invalid_schema"),
            ("extra-envelope-field", {**candidate, "extra": "unexpected"}, "invalid_schema"),
            ("wrong-version-envelope", wrong_envelope, "invalid_schema"),
        )
        for name, replacement, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                invalid["candidates"][0] = copy.deepcopy(replacement)
                # Shape validation precedes digest checks; retain the original bindings.
                ReviewTests.assert_ledger_shape_refusal(self, original_bytes, invalid, expected)

    def test_git_errors_and_timeout_do_not_echo_diagnostics(self):
        value = credential_shapes()[0][1].encode()
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 17, b"", value)):
            error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                              "--inputs", self.inputs, "--sanitized", success=False)[1]
            self.assertEqual(error["error"], "git_read_failed")
        # This command is only an in-memory exception field, never executed.
        failure = subprocess.TimeoutExpired(cmd=value.decode(), timeout=30, output=value, stderr=value)
        self.assertTrue(value.decode() in str(failure), "timeout exception must contain fixture text")
        with patch.object(subprocess, "run", side_effect=failure):
            error = self.call("prepare", "--repo", self.repo, "--base", self.base, "--head", self.head,
                              "--inputs", self.inputs, "--sanitized", success=False)[1]
            self.assertEqual(error["error"], "invalid_input_or_local_operation")
        self.assertFalse(self.store.exists())


class TeamReviewTests(unittest.TestCase):
    # Reuse the same owned Git fixtures without rerunning/inheriting v1 tests.
    git = ReviewTests.git
    commit = ReviewTests.commit
    save_inputs = ReviewTests.save_inputs
    call = ReviewTests.call
    prepare = ReviewTests.prepare
    start = ReviewTests.start
    record = ReviewTests.record
    ledger = ReviewTests.ledger

    def setUp(self):
        ReviewTests.setUp(self)
        self.enable_teams()

    def test_v2_malformed_ledger_candidate_shapes_preserve_bytes_lock_and_readiness(self):
        ReviewTests.test_malformed_ledger_candidate_shapes_preserve_bytes_lock_and_readiness(self)

    def enable_teams(self):
        self.spec["version"] = 2
        self.spec["team_review"] = {
            "checklist": [{"id": "integration", "description": "Assess the complete integration contract.",
                           "paths": ["content.txt"], "lead_required": True}],
            "severity_scale": ["P0", "P1", "P2"], "max_researchers": 3, "accepted_risk": True,
        }
        for slot in self.spec["reviewers"]:
            slot.update(model_selection={"family": "fixture-family", "basis": "Explicit fixture selection, not availability evidence.",
                                         "source": "fixture-selection", "source_sha256": REVIEW.sha(b"selection")},
                        identity_requirement="launch-selection", researcher_model_overrides=[])
        self.save_inputs()

    def result(self, request, verdict="pass"):
        result = ReviewTests.result(self, request, verdict)
        result.update(version=2, run_id="/fixture/lead/" + result["attempt"], researchers=[],
                      launch={"model": "example-model", "source": "fixture-launch", "source_sha256": REVIEW.sha(b"launch")})
        result["identity"]["model"] = None  # Unknown served identity is explicit.
        result["coverage"] = [{"obligation": row["id"], "state": "reviewed", "owners": ["lead"],
                               "evidence": [{"owner": "lead", **{k: result["provenance"][k] for k in ("source", "source_sha256")}}]}
                              for row in request["bundle"]["coverage_requirements"]]
        return result

    def researcher(self, result, name="researcher"):
        # Native task paths can repeat across parent sessions. Preserve both
        # observed components in a qualified identity, not an aggregate run.
        child = {"id": name, "adapter": "fake-native", "run_id": result["run_id"] + "::/root/" + name,
                 "parent_run_id": result["run_id"], "status": "complete",
                 "identity": copy.deepcopy(result["identity"]), "launch": copy.deepcopy(result["launch"]),
                 "report": {"source": "fixture-report-" + name, "source_sha256": REVIEW.sha(name.encode())}}
        result["researchers"].append(child)
        return child

    def delegate_file(self, result, child):
        result["coverage"][0].update(owners=[child["id"]], evidence=[{"owner": child["id"], **child["report"]}])

    def finding(self):
        return {"id": "issue", "kind": "defect", "severity": "P2", "confidence": "high", "recommendation": "address-before-merge",
                "summary": "A fixture contract needs correction.", "rationale": "The supplied fixture demonstrates a mismatch.",
                "evidence": ["fixture assertion"], "assumptions": [], "requested_outcome": "Correct the fixture contract.",
                "assessments": [], "classification_reason": ""}

    def validate_result(self, result):
        candidate = self.ledger()["candidates"][-1]
        attempt = next(a for a in candidate["attempts"] if a["id"] == result["attempt"])
        REVIEW.result_check(result, attempt, candidate)

    def decision(self, result, decision="accept-risk", name="decision", authorized=False, success=True,
                 finding_id="issue"):
        record = {"version": 2, "candidate": self.candidate, "id": name, "actor": "fixture-coordinator",
                  "authority": "Explicit fixture authorization; not authenticated by the controller.",
                  "decisions": [{"attempt": result["attempt"], "finding": finding_id, "decision": decision,
                                 "rationale": "Recorded fixture decision, not a code change."}],
                  "provenance": {"importer": "fixture-operator", "source": "fixture-decision", "source_sha256": REVIEW.sha(b"decision")}}
        path = self.root / "decision.json"
        path.write_text(json.dumps(record))
        flags = ["--authorize-accepted-risk"] if authorized else []
        return self.call("disposition", "--expect", self.candidate, "--disposition", path, "--sanitized", *flags, success=success)[1]

    def test_v2_malformed_requirement_rows_use_static_schema_error(self):
        self.assertEqual(ReviewTests.approve_all(self)["state"], "ready")
        path = self.store / "ledger.json"
        original_bytes = path.read_bytes()
        original = self.ledger()
        rows = original["candidates"][0]["bundle"]["coverage_requirements"]
        file_index = next(i for i, row in enumerate(rows) if row["kind"] == "changed-file")
        checklist_index = next(i for i, row in enumerate(rows) if row["kind"] == "checklist")
        cases = (
            ("null-row", file_index, None),
            ("list-row", file_index, []),
            ("string-row", file_index, "malformed requirement"),
            ("file-without-id", file_index, {k: v for k, v in rows[file_index].items() if k != "id"}),
            ("checklist-without-id", checklist_index,
             {k: v for k, v in rows[checklist_index].items() if k != "id"}),
        )
        for name, index, replacement in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                invalid["candidates"][0]["bundle"]["coverage_requirements"][index] = replacement
                # Bundle shape validation precedes its digest check; do not forge a new binding.
                malformed = REVIEW.canonical(invalid) + b"\n"
                try:
                    path.write_bytes(malformed)
                    output, errors = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = REVIEW.main(["status", "--store", str(self.store), "--require-ready"])
                    assert_no_fixture_output(self, output.getvalue() + errors.getvalue())
                    self.assertTrue(code == 1 and output.getvalue() == "" and
                                    json.loads(errors.getvalue()) == {"error": "invalid_schema"},
                                    "Malformed requirement did not produce the static schema diagnostic")
                    self.assertTrue(path.read_bytes() == malformed, "Rejected ledger bytes were changed")
                    ReviewTests.assert_lock_released(self)
                finally:
                    path.write_bytes(original_bytes)  # Restore only this owned fixture ledger.
                code, recovered = self.call("status", "--require-ready")
                self.assertEqual((code, recovered["state"]), (0, "ready"))
                self.assertEqual(recovered["blockers"], [])
                self.assertTrue(path.read_bytes() == original_bytes, "Recovery changed the original records")

    def test_unchanged_non_utf8_symlink_target_prepares_and_rejects_dirty_target(self):
        link = self.repo / "byte-link"
        link_path = os.fsencode(link)
        target = b"fixture-target-" + bytes([0xff])
        os.symlink(target, link_path)
        self.base = self.commit("Track byte-target symlink in base")
        (self.repo / "content.txt").write_text("Separate candidate content change\n")
        self.head = self.commit("Retain byte-target symlink unchanged")
        self.spec["team_review"]["checklist"][0]["paths"].append(link.name)
        self.save_inputs()
        blob = b"blob " + str(len(target)).encode() + b"\0" + target
        expected_oid = hashlib.sha1(blob).hexdigest()  # The owned fixture is explicitly SHA1.
        for revision in (self.base, self.head):
            self.assertTrue(self.git("ls-tree", revision, "--", link.name).startswith("120000 blob "),
                            "Fixture did not commit a symlink entry")
            self.assertTrue(self.git("rev-parse", revision + ":" + link.name) == expected_oid,
                            "Committed link blob did not preserve the raw target bytes")
        summary = ReviewTests.approve_all(self)
        self.assertEqual(summary["state"], "ready")
        self.assertTrue(summary["clean_review_ready"])
        requirement = next(row for row in self.ledger()["candidates"][0]["bundle"]["coverage_requirements"]
                           if row.get("path") == link.name)
        self.assertTrue(requirement["kind"] == "supporting-file" and
                        requirement["base"] == requirement["head"] == ["120000", expected_oid],
                        "Unchanged link was not retained as an exact supporting-file obligation")
        before = (self.store / "ledger.json").read_bytes()
        self.assertTrue(os.readlink(link_path) == target, "Preparation changed raw link bytes")
        self.assertEqual(self.call("status", "--require-ready")[1]["state"], "ready")
        try:
            link.unlink()
            changed = b"fixture-target-" + bytes([0xfe])
            os.symlink(changed, link_path)
            self.assertTrue(os.readlink(link_path) == changed, "Fixture did not change the raw target")
            code, report = self.call("status", "--require-ready", success=False, failure_code=2)
            self.assertEqual((code, report["state"]), (2, "reviewing"))
            self.assertTrue(report["blockers"] == [{"slot": None, "code": "dirty_candidate"}],
                            "Changed target did not produce the exact dirty blocker")
            self.assertFalse(report["clean_review_ready"])
            self.assertTrue((self.store / "ledger.json").read_bytes() == before, "Dirty check changed the ledger")
            ReviewTests.assert_lock_released(self)
        finally:
            link.unlink(missing_ok=True)
            os.symlink(target, link_path)  # Restore this fixture's original raw target.
        recovered = self.call("status", "--require-ready")[1]
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(recovered["blockers"], [])
        self.assertTrue(recovered["clean_review_ready"])
        self.assertTrue(os.readlink(link_path) == target, "Recovery changed raw link bytes")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "Recovery changed the ledger")

    def test_changed_non_utf8_symlink_diff_remains_unsupported(self):
        link = self.repo / "byte-link"
        link_path = os.fsencode(link)
        os.symlink(b"fixture-target-ascii", link_path)
        self.base = self.commit("Track UTF8 link target in base")
        link.unlink()
        target = b"fixture-target-" + bytes([0xff])
        os.symlink(target, link_path)
        self.head = self.commit("Change tracked link to non-UTF8 target")
        info = self.repo / ".git/info"
        info.mkdir(exist_ok=True)
        # Limit the textual-diff control to this owned fixture path.
        (info / "attributes").write_text(link.name + " -text diff\n")
        REVIEW.clean_candidate(self.repo, self.head, "none")
        difference = REVIEW.git(self.repo, "diff", self.base, self.head)
        self.assertTrue(target in difference, "Generated diff omitted the changed raw target")
        with self.assertRaises(UnicodeDecodeError):
            difference.decode("utf-8")
        before = set(self.root.iterdir())
        code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
            "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
        self.assertTrue((code, error) == (1, {"error": "invalid_input_or_local_operation"}),
                        "Non-UTF8 generated diff used an unexpected diagnostic")
        self.assertFalse(self.store.exists())
        self.assertTrue(set(self.root.iterdir()) == before, "Rejected diff created fixture outputs")
        self.assertTrue(os.readlink(link_path) == target, "Rejected capture changed raw link bytes")
        self.assertTrue(self.git("rev-parse", "HEAD") == self.head, "Rejected capture changed the candidate")

    def test_v2_closed_schemas_and_enums_reject_without_mutation(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        child = self.researcher(result)
        finding = self.finding()
        finding["assessments"] = [{"researcher": child["id"], "finding": "child-issue",
            **{key: finding[key] for key in ("kind", "severity", "confidence", "recommendation", "rationale")}}]
        result["findings"] = [finding]
        self.validate_result(result)
        cases = (
            ("result-schema", lambda r: r.update(extra="unknown"), "invalid_schema"),
            ("researcher-schema", lambda r: r["researchers"][0].update(extra="unknown"), "invalid_schema"),
            ("coverage-schema", lambda r: r["coverage"][0].update(extra="unknown"), "invalid_schema"),
            ("finding-schema", lambda r: r["findings"][0].update(extra="unknown"), "invalid_schema"),
            ("assessment-schema", lambda r: r["findings"][0]["assessments"][0].update(extra="unknown"), "invalid_schema"),
            ("result-version", lambda r: r.update(version=1), "unsupported_version"),
            ("researcher-status", lambda r: r["researchers"][0].update(status="finished"), "invalid_researcher_status"),
            ("coverage-state", lambda r: r["coverage"][0].update(state="complete"), "invalid_coverage_state"),
        )
        before = (self.store / "ledger.json").read_bytes()
        path = self.root / "result.json"
        for name, change, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(result)
                change(invalid)
                with self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                    self.validate_result(invalid)
                path.write_text(json.dumps(invalid))
                code, error = self.call("record", "--expect", self.candidate, "--result", path,
                                        "--sanitized", success=False)
                self.assertTrue((code, error) == (1, {"error": expected}),
                                "Closed v2 result validation used an unexpected diagnostic")
                self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                "Rejected v2 result changed the ledger")
                ReviewTests.assert_lock_released(self)
        self.record(result)
        self.assertTrue(self.ledger()["candidates"][0]["attempts"][0]["result"] == result,
                        "Valid v2 result was not retained intact")

        self.decision(result, decision="request-fix", name="seed")
        candidate = self.ledger()["candidates"][0]
        previous = copy.deepcopy(candidate["dispositions"])
        disposition = copy.deepcopy(previous[0]["record"])
        disposition["id"] = "next"
        REVIEW.disposition_check(disposition, candidate, False)
        invalid = copy.deepcopy(disposition)
        invalid["extra"] = "unknown"
        with self.assertRaisesRegex(REVIEW.Stop, "^invalid_schema$"):
            REVIEW.disposition_check(invalid, candidate, False)
        path = self.root / "decision.json"
        path.write_text(json.dumps(invalid))
        before = (self.store / "ledger.json").read_bytes()
        code, error = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                "--sanitized", success=False)
        self.assertTrue((code, error) == (1, {"error": "invalid_schema"}),
                        "Closed v2 disposition validation used an unexpected diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Rejected v2 disposition changed the ledger")
        ReviewTests.assert_lock_released(self)
        path.write_text(json.dumps(disposition))
        self.call("disposition", "--expect", self.candidate, "--disposition", path, "--sanitized")
        stored = self.ledger()["candidates"][0]
        self.assertEqual(len(stored["dispositions"]), 2)
        self.assertTrue(stored["dispositions"][:-1] == previous,
                        "Valid disposition changed the prior records")
        self.assertTrue(stored["dispositions"][-1]["record"] == disposition,
                        "Valid disposition was not retained intact")
        self.assertTrue(stored["attempts"][0]["result"] == result,
                        "Disposition recording changed the retained result")

    def test_disposition_requires_sanitized_acknowledgment_without_mutation(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        result["findings"] = [self.finding()]
        self.record(result)
        self.decision(result, decision="request-fix", name="seed")
        record = copy.deepcopy(self.ledger()["candidates"][0]["dispositions"][0]["record"])
        record["id"] = "next"
        path = self.root / "decision.json"
        path.write_text(json.dumps(record))
        before = (self.store / "ledger.json").read_bytes()
        args = ("--expect", self.candidate, "--disposition", path)
        code, error = self.call("disposition", *args, success=False)
        self.assertTrue((code, error) == (1, {"error": "sanitization_acknowledgment_required"}),
                        "missing disposition acknowledgment used an unexpected diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before, "rejected disposition changed ledger")
        self.assertEqual(self.ledger()["active"], self.candidate)
        ReviewTests.assert_lock_released(self)
        self.call("disposition", *args, "--sanitized")
        self.assertTrue(self.ledger()["candidates"][0]["dispositions"][-1]["record"] == record,
                        "acknowledged disposition did not retain the valid record")

    def test_disposition_on_retained_superseded_candidate_is_rejected(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        result["findings"] = [self.finding()]
        self.record(result)
        self.decision(result, decision="request-fix", name="before-supersession")
        previous = copy.deepcopy(self.ledger()["candidates"][0])
        old_digest = self.candidate
        self.assertEqual([entry["record"]["id"] for entry in previous["dispositions"]],
                         ["before-supersession"])
        record = copy.deepcopy(previous["dispositions"][0]["record"])
        record["id"] = "after-supersession"
        REVIEW.disposition_check(record, previous, False)  # Valid against the real prior finding.
        self.spec["questions"].append("Assess the newly selected successor scope.")
        self.save_inputs()
        summary = self.call("supersede", "--expect", old_digest, "--reason", "New fixture scope",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")[1]
        self.candidate = summary["candidate"]
        self.assertNotEqual(self.candidate, old_digest)
        self.assertEqual(summary["state"], "prepared")
        path = self.root / "decision.json"
        path.write_text(json.dumps(record))
        before = (self.store / "ledger.json").read_bytes()
        code, error = self.call("disposition", "--expect", old_digest, "--disposition", path,
                                "--sanitized", success=False)
        self.assertTrue((code, error) == (1, {"error": "stale_candidate"}),
                        "retained stale candidate used an unexpected disposition diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "stale disposition changed the ledger")
        ledger = self.ledger()
        self.assertEqual(ledger["active"], self.candidate)
        self.assertEqual(len(ledger["candidates"]), 2)
        self.assertTrue(ledger["candidates"][0] == previous, "supersession changed prior decisions or findings")
        self.assertEqual(ledger["candidates"][1]["attempts"], [])
        self.assertEqual(ledger["candidates"][1]["dispositions"], [])
        self.assertEqual(self.call("status", "--candidate", old_digest)[1]["state"], "superseded")
        ReviewTests.assert_lock_released(self)

    def test_v2_run_id_character_boundaries(self):
        for role in ("lead", "child"):
            with self.subTest(role=role):
                self.store = self.root / ("run-id-" + role)
                self.prepare()
                _, request = self.start()
                original = self.result(request)
                actor = original
                if role == "child":
                    actor = self.researcher(original)
                    self.delegate_file(original, actor)
                # The lead-only case cannot be rejected by a child's parent-width guard.
                actor["run_id"] = "/" + "r" * 511
                self.assertEqual(len(actor["run_id"]), 512)
                self.validate_result(original)
                invalid = copy.deepcopy(original)
                invalid_actor = invalid if role == "lead" else invalid["researchers"][0]
                invalid_actor["run_id"] += "r"
                self.assertEqual(len(invalid_actor["run_id"]), 513)
                before = (self.store / "ledger.json").read_bytes()
                self.assertEqual(self.record(invalid, success=False)["error"], "invalid_string")
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
                self.record(original)
                self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)

    def test_complete_independent_teams_unknown_backend_and_lead_checklist(self):
        self.prepare()
        for slot in ("first", "second"):
            _, request = self.start(slot)
            result = self.result(request)
            child = self.researcher(result)
            self.delegate_file(result, child)
            summary = self.record(result)
        self.assertEqual(summary["state"], "ready")
        self.assertTrue(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("resume", "--require-ready")[0], 0)

    def test_each_lead_and_child_identity_clause_blocks_pass_independently(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        self.researcher(original)  # Qualified nonowner; coverage stays lead-owned.
        self.validate_result(original)
        before = (self.store / "ledger.json").read_bytes()
        cases = (
            ("basis", "operator_attestation"),
            ("basis", "self_report"),
            ("basis", "requested_only"),
            ("basis", "unknown"),
            ("reviewer", None),
            ("evidence", ""),
            ("launch", None),
        )
        for role in ("lead", "child"):
            for field, value in cases:
                with self.subTest(role=role, field=field, value=value):
                    result = copy.deepcopy(original)
                    actor = result if role == "lead" else result["researchers"][0]
                    if field == "launch":
                        actor["launch"] = value
                    else:
                        actor["identity"][field] = value
                    self.assertTrue(all(row["state"] == "reviewed" and
                                        row["owners"] == ["lead"]
                                        for row in result["coverage"]))
                    rejected = self.record(result, success=False)
                    expected = ("unsubstantiated_coverage" if role == "lead"
                                else "incomplete_team_pass")
                    self.assertEqual(rejected["error"], expected)
                    self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                    ReviewTests.assert_lock_released(self)
        self.record(original)

    def test_pass_without_native_run_id_is_incomplete_and_not_recorded(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        policy = next(p for p in request["bundle"]["reviewers"]
                      if p["id"] == original["slot"])
        self.validate_result(original)
        self.assertTrue(REVIEW.team_complete(original, policy))
        before = (self.store / "ledger.json").read_bytes()
        result = copy.deepcopy(original)
        result["run_id"] = None  # Sole difference from an otherwise valid PASS.
        self.assertFalse(REVIEW.team_complete(result, policy))
        rejected = self.record(result, success=False)
        self.assertEqual(rejected["error"], "unsubstantiated_coverage")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        ReviewTests.assert_lock_released(self)
        self.record(original)

    def test_null_lead_cannot_declare_a_child_without_a_valid_parent(self):
        self.prepare()
        _, request = self.start()
        observed = self.result(request, "interrupted")
        self.researcher(observed)
        for row in observed["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.validate_result(observed)
        unobserved = copy.deepcopy(observed)
        unobserved.update(run_id=None, launch=None, researchers=[])
        unobserved["identity"] = {"basis": "unknown", "reviewer": None, "model": None, "evidence": ""}
        self.validate_result(unobserved)  # The null lead is valid until a child is declared.
        before = (self.store / "ledger.json").read_bytes()
        for name, parent, diagnostic in (
            ("null-parent", None, "invalid_string"),
            ("observed-string-parent", observed["run_id"], "invalid_researcher_parent"),
        ):
            with self.subTest(case=name):
                invalid = copy.deepcopy(unobserved)
                invalid["researchers"] = copy.deepcopy(observed["researchers"])
                invalid["researchers"][0]["parent_run_id"] = parent
                self.assertTrue(self.record(invalid, success=False) == {"error": diagnostic},
                                "null-lead child refusal used an unexpected diagnostic")
                self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                "null-lead child refusal changed the ledger")
                ReviewTests.assert_lock_released(self)
        self.record(observed)
        self.assertTrue(self.ledger()["candidates"][0]["attempts"][0]["result"] == observed,
                        "valid observed lead and child were not retained")
        ReviewTests.assert_lock_released(self)

    def test_qualified_failed_children_allow_pass_after_complete_reassignment(self):
        self.prepare()
        for slot, status in (("first", "failed"), ("second", "interrupted")):
            _, request = self.start(slot)
            result = self.result(request)
            child = self.researcher(result, "original")
            self.delegate_file(result, child)
            child.update(status=status, report=None)
            replacement = self.researcher(result, "replacement")
            self.delegate_file(result, replacement)
            result["limitations"].append("Original child " + status + "; its work was reassigned.")
            summary = self.record(result)
            stored = self.ledger()["candidates"][0]["attempts"][-1]["result"]
            self.assertEqual(stored["researchers"][0], child)
            self.assertTrue(all("original" not in row["owners"] for row in stored["coverage"]))
        self.assertEqual(summary["state"], "ready")
        self.assertTrue(summary["clean_review_ready"])
        self.assertEqual(self.call("status", "--require-ready")[0], 0)

    def test_nonowning_failed_children_with_unknown_or_conflicting_identity_block(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        self.researcher(original)
        before = (self.store / "ledger.json").read_bytes()
        for status in ("failed", "interrupted"):
            for identity in ("unknown", "contradictory"):
                with self.subTest(status=status, identity=identity):
                    result = copy.deepcopy(original)
                    child = result["researchers"][0]
                    child.update(status=status, report=None)
                    if identity == "unknown":
                        child.update(launch=None, identity={"basis": "unknown", "reviewer": None,
                                                          "model": None, "evidence": ""})
                    else:
                        child["identity"]["model"] = "different-served-model"
                    self.assertTrue(all(row["state"] == "reviewed" and row["owners"] == ["lead"]
                                        for row in result["coverage"]))
                    rejected = self.record(result, success=False)
                    self.assertEqual(rejected["error"], "incomplete_team_pass")
                    self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                    ReviewTests.assert_lock_released(self)
        result["verdict"] = "interrupted"
        summary = self.record(result)
        self.assertIn("team_incomplete", [item["code"] for item in summary["blockers"]])
        self.assertEqual(summary["state"], "reviewing")
        self.assertEqual(summary["blockers"], [
            {"slot": "first", "code": "required_interrupted"},
            {"slot": "first", "code": "team_incomplete"},
            {"slot": "second", "code": "required_not-started"},
        ])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)

    def test_different_child_adapter_retains_lead_identity_policy(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        child = self.researcher(original)
        child.update(adapter="fixture-other-adapter", status="failed", report=None)
        self.assertNotEqual(child["adapter"], original["adapter"])
        self.assertTrue(all(row["state"] == "reviewed" and row["owners"] == ["lead"]
                            for row in original["coverage"]))
        self.validate_result(original)
        before = (self.store / "ledger.json").read_bytes()
        for name, field, value in (("unknown-basis", "basis", "unknown"),
                                    ("contradictory-model", "model", "fixture-different-served")):
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                invalid["researchers"][0]["identity"][field] = value
                self.assertEqual(self.record(invalid, success=False)["error"], "incomplete_team_pass")
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
        self.record(original)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)
        _, second = self.start("second")
        summary = self.record(self.result(second))
        self.assertEqual(summary["state"], "ready")
        self.assertEqual(summary["blockers"], [])

    def test_tree_derived_added_deleted_mode_and_support_obligations(self):
        for name in ("removed.txt", "renamed.txt", "mode.txt", "support.txt"):
            (self.repo / name).write_text(name + "\n")
        self.base = self.commit("Baseline coverage fixtures")
        (self.repo / "removed.txt").unlink()
        (self.repo / "renamed.txt").rename(self.repo / "new-name.txt")
        (self.repo / "mode.txt").chmod(0o755)
        self.git("update-index", "--chmod=+x", "mode.txt")
        (self.repo / "added.txt").write_text("new\n")
        self.head = self.commit("Change coverage fixtures")
        self.spec["team_review"]["checklist"][0]["paths"] = ["support.txt"]
        self.save_inputs()
        self.prepare()
        rows = {r["path"]: r for r in self.ledger()["candidates"][0]["bundle"]["coverage_requirements"] if "path" in r}
        self.assertEqual(set(rows), {"added.txt", "removed.txt", "renamed.txt", "new-name.txt", "mode.txt", "support.txt"})
        self.assertIsNone(rows["removed.txt"]["head"])
        self.assertIsNone(rows["added.txt"]["base"])
        self.assertEqual(rows["mode.txt"]["base"][0], "100644")
        self.assertEqual(rows["mode.txt"]["head"][0], "100755")
        self.assertEqual(rows["support.txt"]["kind"], "supporting-file")
        candidate = self.ledger()["candidates"][0]
        candidate["bundle"]["coverage_requirements"].pop(0)
        candidate["digest"] = REVIEW.digest(candidate["bundle"])
        with self.assertRaisesRegex(REVIEW.Stop, "^coverage_requirements_changed$"):
            REVIEW.check_source(candidate)

    def test_gitlink_change_derives_exact_file_obligation(self):
        first, second = "1" * 40, "2" * 40
        self.git("update-index", "--add", "--cacheinfo", "160000," + first + ",dependencies/example")
        self.git("commit", "-q", "-m", "Baseline dependency pin")
        self.base = self.git("rev-parse", "HEAD")
        self.git("update-index", "--cacheinfo", "160000," + second + ",dependencies/example")
        self.git("commit", "-q", "-m", "Change dependency pin")
        self.head = self.git("rev-parse", "HEAD")
        self.spec["gitlink_scope"] = "pins-only"
        self.spec["dependency_changes"] = "Changed pin only; nested source is unavailable and not reviewed."
        self.save_inputs()
        self.prepare()
        bundle = self.ledger()["candidates"][0]["bundle"]
        rows = {row["path"]: row for row in bundle["coverage_requirements"] if "path" in row}
        self.assertEqual(set(rows), {"content.txt", "dependencies/example"})
        self.assertEqual(rows["dependencies/example"], {
            "id": "file-5a286e37f3abb9d3def4bf77", "kind": "changed-file", "path": "dependencies/example",
            "base": ["160000", first], "head": ["160000", second],
        })
        self.assertEqual(bundle["gitlinks"], [{"path": "dependencies/example", "base": first, "head": second}])
        self.assertEqual(bundle["scope_limits"], [REVIEW.PIN_LIMIT])

    def test_closed_team_policy_rejects_missing_floor_and_invalid_fields(self):
        original = copy.deepcopy(self.spec)
        changes = (
            (lambda s: s["team_review"].update(checklist=[]), "missing_checklist"),
            (lambda s: s["team_review"].update(max_researchers=True), "invalid_researcher_limit"),
            (lambda s: s["team_review"].update(max_researchers=129), "invalid_researcher_limit"),
            (lambda s: s["team_review"]["checklist"][0].update(id="file-reserved"), "reserved_obligation_id"),
            (lambda s: s["team_review"]["checklist"][0].update(paths=["absent.txt"]), "unknown_checklist_path"),
            (lambda s: s["reviewers"][0].update(requested_model=None), "invalid_string"),
            (lambda s: s["reviewers"][0].update(identity_requirement="requested-only"), "invalid_identity_requirement"),
            (lambda s: s["reviewers"][0]["model_selection"].update(extra=True), "invalid_schema"),
            (lambda s: s.update(version=True), "unsupported_version"),
        )
        for index, (change, expected) in enumerate(changes):
            with self.subTest(index=index):
                self.spec = copy.deepcopy(original)
                change(self.spec)
                self.save_inputs()
                with self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                    REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)

    def test_v2_policy_model_and_scale_boundaries(self):
        model = "fixture-" + "m" * 248
        family = "fixture-" + "f" * 120
        overrides = ["fixture-model-" + str(index) for index in range(32)]
        scale = ["fixture-P" + str(index) for index in range(32)]
        self.assertEqual(len(model), 256)
        self.assertEqual(len(family), 128)
        validators = {
            "reviewers": lambda spec: REVIEW.policy_check(spec["reviewers"], version=2),
            "team_review": lambda spec: REVIEW.team_policy_check(spec["team_review"]),
        }
        cases = (
            ("requested-model", "reviewers", lambda s, v: s["reviewers"][0].update(requested_model=v), model,
             (("too-long", model + "m", "invalid_string"),)),
            ("family", "reviewers", lambda s, v: s["reviewers"][0]["model_selection"].update(family=v), family,
             (("too-long", family + "f", "invalid_string"),
              ("leading-hyphen", "-fixture", "invalid_identifier"),
              ("embedded-space", "fixture family", "invalid_identifier"))),
            ("overrides", "reviewers", lambda s, v: s["reviewers"][0].update(researcher_model_overrides=v), overrides,
             (("too-many", overrides + ["fixture-model-32"], "invalid_list"),
              ("duplicate", [overrides[0], overrides[0]], "duplicate_identity"))),
            ("override-entry", "reviewers", lambda s, v: s["reviewers"][0].update(researcher_model_overrides=v), [model],
             (("too-long", [model + "m"], "invalid_string"),)),
            ("severity-scale", "team_review", lambda s, v: s["team_review"].update(severity_scale=v), scale,
             (("too-many", scale + ["fixture-P32"], "invalid_list"),
              ("empty", [], "missing_severity_scale"),
              ("duplicate", [scale[0], scale[0]], "duplicate_identity"))),
        )
        for name, validator, change, accepted, rejected in cases:
            with self.subTest(field=name):
                baseline = copy.deepcopy(self.spec)
                change(baseline, accepted)
                REVIEW.policy_check(baseline["reviewers"], version=2)
                REVIEW.team_policy_check(baseline["team_review"])
                for case, value, expected in rejected:
                    with self.subTest(case=case):
                        invalid = copy.deepcopy(baseline)
                        change(invalid, value)
                        unaffected = "team_review" if validator == "reviewers" else "reviewers"
                        validators[unaffected](invalid)
                        with self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                            validators[validator](invalid)

    def test_v2_prepare_cli_override_entry_length_boundary(self):
        original = copy.deepcopy(self.spec)
        model = "fixture-" + "m" * 248
        self.assertEqual(len(model), 256)

        def input_source_snapshot():
            # Raw index bytes are intentional: both self.git() and REVIEW.git()
            # use --no-optional-locks to prevent optional index refreshes by
            # these read-only probes.
            return (self.inputs.read_bytes(), (self.root / "supplied.txt").read_bytes(),
                    (self.repo / "content.txt").read_bytes(), (self.repo / ".git/index").read_bytes(),
                    self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"))

        for length, value in ((256, model), (257, model + "m")):
            with self.subTest(length=length):
                self.assertEqual(len(value), length)
                self.spec = copy.deepcopy(original)
                self.spec["reviewers"][0]["researcher_model_overrides"] = [value]
                self.save_inputs()
                self.store = self.root / ("review-override-" + str(length))
                self.assertFalse(os.path.lexists(self.store))
                before = input_source_snapshot()
                self.assertEqual(before[-2:], (self.head, ""))
                members = set(self.root.iterdir())
                result = subprocess.run(
                    [sys.executable, "-I", "-B", str(SCRIPT), "prepare", "--store", str(self.store),
                     "--repo", str(self.repo), "--base", self.base, "--head", self.head,
                     "--inputs", str(self.inputs), "--sanitized"],
                    cwd=self.root, env=self.environment, stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=15,
                )
                assert_no_fixture_output(self, result.stdout + result.stderr)
                self.assertTrue(input_source_snapshot() == before, "CLI prepare changed fixture inputs/source")
                if length == 256:
                    self.assertEqual(result.returncode, 0, "valid override entry did not prepare")
                    self.assertTrue(result.stderr == b"", "valid prepare emitted an error")
                    summary = json.loads(result.stdout)
                    self.assertEqual(summary["state"], "prepared")
                    ledger = self.ledger()
                    self.assertEqual(ledger["active"], summary["candidate"])
                    bundle = ledger["candidates"][0]["bundle"]
                    self.assertEqual(bundle["reviewers"], self.spec["reviewers"])
                else:
                    self.assertTrue(result.returncode == 1 and result.stdout == b"" and
                                    result.stderr == b'{"error": "invalid_string"}\n',
                                    "oversized override entry did not produce the exact CLI refusal")
                    self.assertFalse(os.path.lexists(self.store))
                    self.assertTrue(set(self.root.iterdir()) == members, "rejected prepare allocated an output")

    def test_v2_lead_and_child_identity_string_boundaries(self):
        model = "fixture-" + "m" * 248
        reviewer = "fixture-" + "r" * 248
        self.assertEqual(len(model), 256)
        self.assertEqual(len(reviewer), 256)
        for slot in self.spec["reviewers"]:
            slot["requested_model"] = model
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        original["launch"]["model"] = model
        original["identity"].update(reviewer=reviewer, model=model)
        self.researcher(original)
        self.validate_result(original)
        before = (self.store / "ledger.json").read_bytes()
        for role in ("lead", "child"):
            for field in ("reviewer", "model"):
                with self.subTest(role=role, field=field):
                    invalid = copy.deepcopy(original)
                    actor = invalid if role == "lead" else invalid["researchers"][0]
                    actor["identity"][field] += "x"
                    self.assertEqual(len(actor["identity"][field]), 257)
                    self.assertEqual(self.record(invalid, success=False)["error"], "invalid_string")
                    self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                    ReviewTests.assert_lock_released(self)
        self.record(original)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)

    def test_missing_unresolved_forged_or_duplicate_coverage_cannot_pass(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        changes = (
            (lambda r: r["coverage"].pop(), "missing_coverage"),
            (lambda r: r["coverage"].append(copy.deepcopy(r["coverage"][0])), "duplicate_identity"),
            (lambda r: r["coverage"][0].update(obligation="unknown"), "unknown_obligation"),
            (lambda r: r["coverage"][0].update(state="unresolved"), "incomplete_team_pass"),
            (lambda r: r["coverage"][0].update(state="assigned"), "incomplete_team_pass"),
            (lambda r: r["coverage"][0].update(owners=[], evidence=[]), "unsubstantiated_coverage"),
            (lambda r: r["coverage"][0].update(owners=["other"]), "unknown_coverage_owner"),
            (lambda r: r["coverage"][0]["evidence"][0].update(source_sha256="0" * 64), "coverage_report_mismatch"),
            (lambda r: r["coverage"][0]["evidence"][0].update(owner="other"), "invalid_coverage_evidence_owner"),
        )
        for index, (change, expected) in enumerate(changes):
            with self.subTest(index=index), self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                result = copy.deepcopy(original)
                change(result)
                self.validate_result(result)
        self.record(original)
        _, second = self.start("second")
        incomplete = self.result(second, "changes-required")
        incomplete["coverage"][0].update(state="unresolved", owners=[], evidence=[])
        incomplete["findings"] = [self.finding()]
        summary = self.record(incomplete)
        self.assertFalse(summary["clean_review_ready"])
        self.assertIn("team_incomplete", [item["code"] for item in summary["blockers"]])

    def test_multi_owner_coverage_requires_each_owner_and_allows_shared_reports(self):
        self.prepare()
        for slot, shared in (("first", False), ("second", True)):
            _, request = self.start(slot)
            original = self.result(request)
            child = self.researcher(original)
            lead_report = {key: original["provenance"][key] for key in ("source", "source_sha256")}
            if shared:
                child["report"] = copy.deepcopy(lead_report)
            original["coverage"][0].update(
                owners=["lead", child["id"]],
                evidence=[{"owner": "lead", **lead_report}, {"owner": child["id"], **child["report"]}])
            self.validate_result(original)
            if not shared:
                changes = (
                    ("missing-second-evidence", lambda r: r["coverage"][0]["evidence"].pop(),
                     "unsubstantiated_coverage"),
                    ("failed-second-owner", lambda r: r["researchers"][0].update(status="failed"),
                     "unsubstantiated_coverage"),
                    ("duplicate-owner", lambda r: r["coverage"][0]["owners"].append("lead"),
                     "duplicate_identity"),
                    ("duplicate-evidence-owner", lambda r: r["coverage"][0]["evidence"].append(
                        copy.deepcopy(r["coverage"][0]["evidence"][1])), "duplicate_identity"),
                )
                before = (self.store / "ledger.json").read_bytes()
                for name, change, expected in changes:
                    with self.subTest(case=name):
                        invalid = copy.deepcopy(original)
                        change(invalid)
                        self.assertEqual(self.record(invalid, success=False)["error"], expected)
                        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                        ReviewTests.assert_lock_released(self)
            else:
                self.assertEqual(child["report"], lead_report)
                self.assertNotEqual(original["coverage"][0]["evidence"][0]["owner"],
                                    original["coverage"][0]["evidence"][1]["owner"])
            summary = self.record(original)
            self.assertEqual(self.ledger()["candidates"][0]["attempts"][-1]["result"], original)
        self.assertEqual(summary["state"], "ready")
        self.assertEqual(summary["blockers"], [])
        self.assertEqual(self.call("status", "--require-ready")[0], 0)

    def test_coverage_evidence_matches_named_owner_and_requires_report(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        child = self.researcher(original)
        self.delegate_file(original, child)
        self.validate_result(original)
        lead_report = {key: original["provenance"][key] for key in ("source", "source_sha256")}
        self.assertNotEqual(child["report"], lead_report)
        wrong_report = copy.deepcopy(original)
        wrong_report["coverage"][0]["evidence"][0].update(lead_report)
        failed = copy.deepcopy(original)
        failed["verdict"] = "interrupted"
        failed["researchers"][0]["status"] = "failed"
        failed["coverage"][0]["state"] = "unresolved"
        self.validate_result(failed)
        no_report = copy.deepcopy(failed)
        no_report["researchers"][0]["report"] = None
        before = (self.store / "ledger.json").read_bytes()
        for name, invalid in (("lead-reference-for-child", wrong_report), ("null-report", no_report)):
            with self.subTest(case=name):
                self.assertEqual(self.record(invalid, success=False)["error"], "coverage_report_mismatch")
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
        no_evidence = copy.deepcopy(no_report)
        no_evidence["coverage"][0]["evidence"] = []
        self.validate_result(no_evidence)  # Unresolved reportless work can still be preserved honestly.
        self.record(original)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)

    def test_researcher_parent_identity_report_and_lead_assessment_are_checked(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        child = self.researcher(original)
        self.delegate_file(original, child)
        changes = (
            (lambda r: r["researchers"][0].update(id="lead"), "invalid_researcher_id"),
            (lambda r: r["researchers"][0].update(parent_run_id="/fixture/grandchild"), "invalid_researcher_parent"),
            (lambda r: r["researchers"][0].update(run_id=r["run_id"]), "duplicate_identity"),
            (lambda r: r["researchers"][0].update(report=None), "missing_researcher_report"),
            (lambda r: r["researchers"][0].update(status="interrupted"), "unsubstantiated_coverage"),
            (lambda r: r["researchers"][0]["identity"].update(model="different-served-model"), "unsubstantiated_coverage"),
            (lambda r: r["researchers"][0]["launch"].update(model="unapproved-small-model"), "researcher_model_mismatch"),
            (lambda r: r["coverage"][-1].update(owners=["researcher"], evidence=copy.deepcopy(r["coverage"][0]["evidence"])), "lead_assessment_required"),
            (lambda r: r["researchers"].append(copy.deepcopy(r["researchers"][0])), "invalid_researcher_id"),
            (lambda r: r["launch"].update(model="different-selected-model"), "launch_model_mismatch"),
            (lambda r: r["identity"].update(model="different-served-model"), "unsubstantiated_coverage"),
        )
        for index, (change, expected) in enumerate(changes):
            with self.subTest(index=index), self.assertRaisesRegex(REVIEW.Stop, "^" + expected + "$"):
                result = copy.deepcopy(original)
                change(result)
                self.validate_result(result)
        self.validate_result(original)

    def test_checklist_delegation_respects_lead_required_flag(self):
        self.spec["team_review"]["checklist"].append({"id": "delegated", "description": "Delegable fixture check.",
                                                    "paths": ["content.txt"], "lead_required": False})
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        child = self.researcher(result)
        self.delegate_file(result, child)
        delegated = next(row for row in result["coverage"] if row["obligation"] == "delegated")
        delegated.update(owners=[child["id"]], evidence=[{"owner": child["id"], **child["report"]}])
        self.validate_result(result)
        invalid = copy.deepcopy(result)
        required = next(row for row in invalid["coverage"] if row["obligation"] == "integration")
        required.update(owners=[child["id"]], evidence=copy.deepcopy(delegated["evidence"]))
        with self.assertRaisesRegex(REVIEW.Stop, "^lead_assessment_required$"):
            self.validate_result(invalid)
        self.record(result)

    def test_cross_team_child_run_reuse_is_rejected_without_recording(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first)
        child = self.researcher(result)
        self.record(result)
        _, second = self.start("second")
        other = self.result(second)
        replacement = self.researcher(other)
        fresh_run_id = replacement["run_id"]
        self.assertNotEqual(fresh_run_id, child["run_id"])
        replacement["run_id"] = child["run_id"]
        self.validate_result(other)  # Locally valid; only retained cross-team history conflicts.
        before = (self.store / "ledger.json").read_bytes()
        self.assertTrue(self.record(other, success=False) == {"error": "duplicate_identity"},
                        "Cross-team child reuse must produce the static duplicate-identity refusal")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Rejected cross-team child reuse changed the ledger")
        ReviewTests.assert_lock_released(self)
        replacement["run_id"] = fresh_run_id
        self.validate_result(other)
        summary = self.record(other)
        self.assertTrue([attempt["result"] for attempt in self.ledger()["candidates"][0]["attempts"]]
                        == [result, other], "Fresh recording must retain both complete team results")
        self.assertTrue(summary["clean_review_ready"])
        self.assertEqual(summary["blockers"], [])

    def test_distinct_adapters_may_report_the_same_native_run_id(self):
        for slot, adapter in zip(self.spec["reviewers"], ("fixture-adapter-one", "fixture-adapter-two")):
            slot["adapter"] = adapter
        self.save_inputs()
        self.prepare()
        for slot in ("first", "second"):
            _, request = self.start(slot)
            result = self.result(request)
            result["run_id"] = "/fixture/shared-native-run"
            summary = self.record(result)
        results = [attempt["result"] for attempt in self.ledger()["candidates"][0]["attempts"]]
        self.assertEqual([result["run_id"] for result in results], ["/fixture/shared-native-run"] * 2)
        self.assertEqual([result["adapter"] for result in results],
                         ["fixture-adapter-one", "fixture-adapter-two"])
        self.assertTrue(summary["clean_review_ready"])
        self.assertEqual(summary["blockers"], [])
        self.assertEqual(self.call("status", "--require-ready")[0], 0)

    def test_distinct_child_adapters_may_report_the_same_native_run_id(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        first = self.researcher(result, "first-child")
        second = self.researcher(result, "second-child")
        first.update(adapter="fixture-adapter-one", run_id="/fixture/shared-child-run")
        second.update(adapter="fixture-adapter-two", run_id=first["run_id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["adapter"], second["adapter"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.validate_result(result)
        self.record(result)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)

    def test_distinct_children_cannot_share_one_runtime_identity(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request)
        self.researcher(original, "first-child")
        self.researcher(original, "second-child")
        self.validate_result(original)
        invalid = copy.deepcopy(original)
        first, second = invalid["researchers"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["adapter"], second["adapter"])
        second["run_id"] = first["run_id"]
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(invalid, success=False)["error"], "duplicate_identity")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        ReviewTests.assert_lock_released(self)
        self.record(original)

    def test_child_runtime_identity_cannot_be_reused_on_same_slot_retry(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first, "interrupted")
        child = self.researcher(result)
        self.record(result)
        original = copy.deepcopy(self.ledger()["candidates"][0]["attempts"][0])
        _, retry = self.start(retry=result["attempt"])
        other = self.result(retry)
        replacement = self.researcher(other)
        fresh_run_id = replacement["run_id"]
        self.assertNotEqual(other["run_id"], result["run_id"])
        self.assertNotEqual(replacement["parent_run_id"], child["parent_run_id"])
        self.assertEqual(replacement["parent_run_id"], other["run_id"])
        self.assertEqual(replacement["adapter"], child["adapter"])
        replacement["run_id"] = child["run_id"]
        self.validate_result(other)  # Locally valid; only the earlier attempt collides.
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(other, success=False)["error"], "duplicate_identity")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0], original)
        ReviewTests.assert_lock_released(self)
        replacement["run_id"] = fresh_run_id
        self.record(other)
        attempts = self.ledger()["candidates"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], original)
        self.assertEqual(attempts[1]["retry"], original["id"])
        self.assertEqual(attempts[1]["result"], other)

    def test_child_runtime_identity_cannot_be_reused_across_candidates(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first)
        child = self.researcher(result)
        self.record(result)
        old = self.ledger()["candidates"][0]
        old_digest = self.candidate
        (self.repo / "content.txt").write_text("successor candidate\n")
        self.head = self.commit("Successor candidate")
        summary = self.call("supersede", "--expect", old_digest, "--reason", "Changed fixture source",
                            "--repo", self.repo, "--base", self.base, "--head", self.head,
                            "--inputs", self.inputs, "--sanitized")[1]
        self.candidate = summary["candidate"]
        self.assertNotEqual(self.candidate, old_digest)
        self.assertNotEqual(self.head, old["bundle"]["head"])
        self.assertEqual(len(self.ledger()["candidates"]), 2)
        self.assertEqual(self.ledger()["candidates"][0], old)
        _, second = self.start()
        other = self.result(second)
        replacement = self.researcher(other)
        fresh_run_id = replacement["run_id"]
        self.assertNotEqual(replacement["parent_run_id"], child["parent_run_id"])
        replacement["run_id"] = child["run_id"]
        self.validate_result(other)  # Locally valid; only retained history collides.
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(other, success=False)["error"], "duplicate_identity")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.ledger()["candidates"][0], old)
        ReviewTests.assert_lock_released(self)
        replacement["run_id"] = fresh_run_id
        self.record(other)

    def test_explicit_child_override_and_strict_served_identity(self):
        for slot in self.spec["reviewers"]:
            slot["identity_requirement"] = "served-model"
            slot["researcher_model_overrides"] = ["explicit-specialist"]
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        with self.assertRaisesRegex(REVIEW.Stop, "^unsubstantiated_coverage$"):
            self.validate_result(result)
        result["identity"]["model"] = "example-model"
        child = self.researcher(result)
        child["identity"]["model"] = child["launch"]["model"] = "explicit-specialist"
        self.delegate_file(result, child)
        self.validate_result(result)
        child["identity"]["model"] = None
        with self.assertRaisesRegex(REVIEW.Stop, "^unsubstantiated_coverage$"):
            self.validate_result(result)

    def test_child_only_model_override_is_not_valid_for_lead(self):
        override = "fixture-child-specialist"
        for slot in self.spec["reviewers"]:
            slot["researcher_model_overrides"] = [override]
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        child = self.researcher(result)
        child["launch"]["model"] = override
        self.delegate_file(result, child)
        self.assertIsNone(result["identity"]["model"])
        self.assertIsNone(child["identity"]["model"])
        self.validate_result(result)
        invalid = copy.deepcopy(result)
        invalid["launch"]["model"] = override  # Sole mutation; no served-model contradiction masks this guard.
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(invalid, success=False)["error"], "launch_model_mismatch")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        ReviewTests.assert_lock_released(self)
        self.record(result)

    def test_max_researchers_counts_declared_runs_not_live_concurrency(self):
        self.spec["team_review"]["max_researchers"] = 1
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        self.researcher(result, "one")
        self.validate_result(result)
        invalid = copy.deepcopy(result)
        self.researcher(invalid, "two")
        with self.assertRaisesRegex(REVIEW.Stop, "^invalid_list$"):
            self.validate_result(invalid)
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(invalid, success=False), {"error": "invalid_list"})
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "researcher-limit refusal changed the ledger")
        ReviewTests.assert_lock_released(self)
        self.record(result)
        self.assertTrue(self.ledger()["candidates"][0]["attempts"][0]["result"] == result,
                        "one-child result was not retained at the configured limit")

    def test_coverage_allows_129_owners_and_evidence_but_not_130(self):
        self.spec["team_review"]["max_researchers"] = 128
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        coverage = result["coverage"][0]
        for index in range(128):
            child = self.researcher(result, "owner-" + str(index))
            coverage["owners"].append(child["id"])
            coverage["evidence"].append({"owner": child["id"], **child["report"]})
        self.assertEqual(len(result["researchers"]), 128)
        self.assertEqual(len(coverage["owners"]), 129)
        self.assertEqual(len(set(coverage["owners"])), 129)
        self.assertEqual(len(coverage["evidence"]), 129)
        self.assertEqual({row["owner"] for row in coverage["evidence"]}, set(coverage["owners"]))
        self.validate_result(result)
        before = (self.store / "ledger.json").read_bytes()
        for field in ("owners", "evidence"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(result)
                row = invalid["coverage"][0]
                # Lead + 128 children exhaust the valid owner pool. Repeating a
                # known owner pins the cap-before-uniqueness diagnostic order.
                row[field].append(copy.deepcopy(row[field][0]))
                self.assertEqual(len(row[field]), 130)
                other = "evidence" if field == "owners" else "owners"
                self.assertEqual(len(row[other]), 129)
                self.assertEqual(self.record(invalid, success=False), {"error": "invalid_list"})
                self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                "coverage-limit refusal changed the ledger")
                ReviewTests.assert_lock_released(self)
        self.record(result)
        self.assertTrue(self.ledger()["candidates"][0]["attempts"][0]["result"] == result,
                        "129-owner/evidence result was not retained")

    def test_assessments_allow_128_researchers_but_not_129_entries(self):
        self.spec["team_review"]["max_researchers"] = 128
        self.save_inputs()
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        finding = self.finding()
        for index in range(128):
            child = self.researcher(result, "researcher-" + str(index))
            finding["assessments"].append({"researcher": child["id"], "finding": "child-issue",
                **{key: finding[key] for key in ("kind", "severity", "confidence", "recommendation", "rationale")}})
        result["findings"] = [finding]
        self.assertEqual(len(result["researchers"]), 128)
        self.assertEqual(len({child["id"] for child in result["researchers"]}), 128)
        self.assertEqual(len(finding["assessments"]), 128)
        self.validate_result(result)
        invalid = copy.deepcopy(result)
        extra = copy.deepcopy(invalid["findings"][0]["assessments"][0])
        extra["finding"] = "second-child-issue"
        invalid["findings"][0]["assessments"].append(extra)
        references = [(row["researcher"], row["finding"]) for row in invalid["findings"][0]["assessments"]]
        self.assertEqual(len(invalid["researchers"]), 128)
        self.assertEqual(len(references), 129)
        self.assertEqual(len(set(references)), 129)  # Neither researcher count nor duplicate references causes rejection.
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(invalid, success=False)["error"], "invalid_list")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        ReviewTests.assert_lock_released(self)
        self.record(result)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)

    def test_lead_classification_preserves_proposal_and_requires_material_reason(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        self.researcher(result)
        finding = self.finding()
        proposal = {key: finding[key] for key in ("kind", "severity", "confidence", "recommendation", "rationale")}
        proposal.update(researcher="researcher", finding="child-issue")
        finding["assessments"] = [proposal]
        result["findings"] = [finding]
        self.validate_result(result)
        before = (self.store / "ledger.json").read_bytes()
        reconciled = {}
        for key, value in (("kind", "suggestion"), ("severity", "P0"),
                           ("confidence", "medium"), ("recommendation", "follow-up")):
            with self.subTest(dimension=key):
                changed = copy.deepcopy(result)
                changed["findings"][0]["assessments"][0][key] = value
                self.assertEqual(self.record(changed, success=False)["error"], "missing_classification_reason")
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
                changed["findings"][0]["classification_reason"] = "Lead checked the contract and explained the " + key + " difference."
                self.validate_result(changed)
                reconciled[key] = changed
        self.record(reconciled["severity"])
        stored = self.ledger()["candidates"][0]["attempts"][0]["result"]["findings"][0]
        self.assertEqual(stored["assessments"][0]["severity"], "P0")
        self.assertEqual(stored["severity"], "P2")

    def test_explained_optional_lead_classification_preserves_defect_block_proposal(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        child = self.researcher(result)
        finding = self.finding()
        proposal = {key: finding[key] for key in
                    ("kind", "severity", "confidence", "recommendation", "rationale")}
        proposal.update(researcher=child["id"], finding="child-issue", recommendation="block")
        finding.update(kind="suggestion", severity=None, recommendation="optional", assessments=[proposal],
                       classification_reason="Lead verified the alleged defect is outside the agreed contract; the remaining idea is optional.")
        result["findings"] = [finding]
        self.validate_result(result)
        invalid = copy.deepcopy(result)
        invalid["findings"][0]["classification_reason"] = ""
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.record(invalid, success=False), {"error": "missing_classification_reason"})
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "unexplained reclassification changed the ledger")
        ReviewTests.assert_lock_released(self)
        self.record(result)
        _, second = self.start("second")
        summary = self.record(self.result(second))
        self.assertEqual(summary["state"], "ready")
        self.assertEqual(summary["blockers"], [])
        self.assertTrue(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        code, status = self.call("status", "--require-ready")
        self.assertEqual(code, 0)
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["blockers"], [])
        self.assertTrue(status["clean_review_ready"])
        stored = self.ledger()["candidates"][0]["attempts"][0]["result"]
        self.assertTrue(stored == result, "explained lead result or original proposal was changed")
        self.assertEqual(stored["findings"][0]["assessments"], [proposal])
        self.assertEqual((proposal["kind"], proposal["recommendation"]), ("defect", "block"))
        self.assertEqual((stored["findings"][0]["kind"], stored["findings"][0]["recommendation"]),
                         ("suggestion", "optional"))

    def test_assessment_references_and_classification_are_independently_validated(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        child = self.researcher(result)
        child["status"] = "interrupted"  # A report can survive interruption; no coverage is assigned to this child.
        finding = self.finding()
        finding["assessments"] = [{"researcher": child["id"], "finding": "child-issue",
                                   **{key: finding[key] for key in
                                      ("kind", "severity", "confidence", "recommendation", "rationale")}}]
        result["findings"] = [finding]
        self.assertTrue(all(row["owners"] == ["lead"] for row in result["coverage"]))
        self.assertEqual(finding["classification_reason"], "")
        self.validate_result(result)
        cases = [
            ("unknown researcher", lambda r: r["findings"][0]["assessments"][0].update(researcher="unknown"),
             "unknown_researcher_assessment"),
            ("lead reference", lambda r: r["findings"][0]["assessments"][0].update(researcher="lead"),
             "unknown_researcher_assessment"),
            ("reportless researcher", lambda r: r["researchers"][0].update(report=None),
             "unknown_researcher_assessment"),
            ("duplicate reference", lambda r: r["findings"][0]["assessments"].append(
                copy.deepcopy(r["findings"][0]["assessments"][0])), "duplicate_identity"),
        ]
        for target in ("finding", "assessment"):
            for key, value, code in (("kind", "observation", "invalid_finding"),
                                     ("severity", "P3", "invalid_severity"),
                                     ("severity", None, "invalid_severity"),
                                     ("confidence", "certain", "invalid_confidence"),
                                     ("recommendation", "approve", "invalid_recommendation")):
                def change(record, target=target, key=key, value=value):
                    row = record["findings"][0]
                    if target == "assessment":
                        row = row["assessments"][0]
                    row[key] = value
                cases.append((f"{target} {key} {value}", change, code))
        before = (self.store / "ledger.json").read_bytes()
        for name, change, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(result)
                change(invalid)
                self.assertEqual(self.record(invalid, success=False)["error"], expected)
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
        self.record(result)
        stored = self.ledger()["candidates"][0]["attempts"][0]["result"]["findings"][0]
        self.assertEqual(stored, finding)
        self.assertEqual(stored["classification_reason"], "")

    def test_one_original_assessment_can_support_two_split_lead_findings(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        child = self.researcher(result)
        first = self.finding()
        first["assessments"] = [{"researcher": child["id"], "finding": "child-issue",
                                 **{key: first[key] for key in
                                    ("kind", "severity", "confidence", "recommendation", "rationale")}}]
        first["classification_reason"] = "Lead split one original proposal into two separately actionable components."
        second = copy.deepcopy(first)
        second.update(id="second-issue", summary="The second component of the same original fixture proposal.")
        result["findings"] = [first, second]
        self.validate_result(result)
        summary = self.record(result)
        stored = self.ledger()["candidates"][0]["attempts"][0]["result"]["findings"]
        self.assertEqual(stored, [first, second])
        self.assertEqual(len({finding["id"] for finding in stored}), 2)
        references = [(assessment["researcher"], assessment["finding"])
                      for finding in stored for assessment in finding["assessments"]]
        self.assertEqual(references, [(child["id"], "child-issue")] * 2)
        self.assertEqual(len(set(references)), 1)  # Two lead findings do not create a second original proposal.
        self.assertEqual(summary["state"], "changes-required")
        self.assertFalse(summary["clean_review_ready"])

    def test_defects_require_evidence_but_assumptions_can_be_empty(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        finding = self.finding()
        result["findings"] = [finding]
        finding["evidence"] = []
        with self.assertRaisesRegex(REVIEW.Stop, "^missing_defect_evidence$"):
            self.validate_result(result)
        finding["evidence"] = ["Durable fixture source observation"]
        self.assertEqual(finding["assumptions"], [])
        self.validate_result(result)

    def test_suggestion_is_not_automatically_a_defect_or_waived_by_severity(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request)
        finding = self.finding()
        result["findings"] = [finding]
        with self.assertRaisesRegex(REVIEW.Stop, "^actionable_findings_require_changes$"):
            self.validate_result(result)
        finding.update(kind="suggestion", severity=None, recommendation="optional")
        self.validate_result(result)
        finding["recommendation"] = "follow-up"
        with self.assertRaisesRegex(REVIEW.Stop, "^actionable_findings_require_changes$"):
            self.validate_result(result)
        finding["recommendation"] = "optional"
        self.record(result)
        _, second = self.start("second")
        summary = self.record(self.result(second))
        self.assertEqual(summary["state"], "ready")
        self.assertTrue(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertEqual(summary["blockers"], [])
        code, status = self.call("status", "--require-ready")
        self.assertEqual(code, 0)
        self.assertTrue(status["clean_review_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"]["findings"], [finding])

    def test_accepted_risk_is_separate_explicit_append_only_and_not_ready(self):
        self.prepare()
        _, request = self.start()
        first = self.result(request, "changes-required")
        first["findings"] = [self.finding()]
        self.record(first)
        _, request = self.start("second")
        self.record(self.result(request))
        self.assertEqual(self.decision(first, authorized=False, success=False)["error"], "accepted_risk_not_authorized")
        for name in ("request-fix", "dispute", "defer"):
            summary = self.decision(first, decision=name, name=name)
            self.assertEqual(summary["state"], "changes-required")
        summary = self.decision(first, name="accept", authorized=True)
        self.assertEqual(summary["state"], "accepted-with-findings")
        self.assertTrue(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["coordinator_authority_authenticated"])
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        ledger = self.ledger()
        self.assertEqual(ledger["candidates"][0]["attempts"][0]["result"], first)
        dispositions = ledger["candidates"][0]["dispositions"]
        self.assertEqual(len(dispositions), 4)
        self.assertEqual([entry["accepted_risk_authorized"] for entry in dispositions], [False, False, False, True])
        for entry in dispositions:
            self.assertEqual(set(entry), {"record", "record_digest", "accepted_risk_authorized"})
            self.assertEqual(entry["record_digest"], hashlib.sha256(REVIEW.canonical(entry["record"])).hexdigest())
        self.assertEqual(summary["dispositions"], [
            {"id": entry["record"]["id"], "sha256": entry["record_digest"]} for entry in dispositions])
        self.assertTrue(all(set(entry) == {"id", "sha256"} for entry in summary["dispositions"]))
        self.assertNotIn("accepted_risk_authorized", summary)
        tampered = copy.deepcopy(ledger)
        tampered["candidates"][0]["dispositions"][-1]["record_digest"] = "0" * 64
        with self.assertRaisesRegex(REVIEW.Stop, "^disposition_digest_mismatch$"):
            REVIEW.validate_ledger(tampered)
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.decision(first, name="accept", authorized=True, success=False)["error"],
                         "duplicate_identity")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.decision(first, decision="request-fix", name="reconsider")["state"], "changes-required")

    def test_acceptance_requires_every_actionable_finding(self):
        self.prepare()
        _, request = self.start()
        first = self.result(request, "changes-required")
        second_finding = self.finding()
        second_finding["id"] = "second-issue"
        first["findings"] = [self.finding(), second_finding]
        self.record(first)
        _, request = self.start("second")
        self.record(self.result(request))
        original_attempts = copy.deepcopy(self.ledger()["candidates"][0]["attempts"])
        blockers = [{"slot": None, "code": "recorded_findings"}]
        summary = self.decision(first, name="accept-first", authorized=True)
        self.assertTrue(all(slot["complete"] for slot in summary["slots"]))
        self.assertEqual([slot["state"] for slot in summary["slots"]], ["changes-required", "pass"])
        self.assertEqual(summary["blockers"], blockers)
        self.assertEqual(summary["state"], "changes-required")
        self.assertFalse(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        summary = self.decision(first, name="accept-second", authorized=True, finding_id="second-issue")
        self.assertEqual(summary["blockers"], blockers)
        self.assertEqual(summary["state"], "accepted-with-findings")
        self.assertTrue(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        candidate = self.ledger()["candidates"][0]
        self.assertEqual(candidate["attempts"], original_attempts)
        self.assertEqual(candidate["attempts"][0]["result"], first)
        dispositions = candidate["dispositions"]
        self.assertEqual([entry["record"]["id"] for entry in dispositions], ["accept-first", "accept-second"])
        self.assertEqual([entry["accepted_risk_authorized"] for entry in dispositions], [True, True])
        self.assertEqual([[(row["attempt"], row["finding"], row["decision"])
                           for row in entry["record"]["decisions"]] for entry in dispositions],
                         [[(first["attempt"], "issue", "accept-risk")],
                          [(first["attempt"], "second-issue", "accept-risk")]])

    def test_disposition_rejects_independent_binding_vocabulary_and_source_errors(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        result["findings"] = [self.finding()]
        self.record(result)
        self.decision(result, decision="request-fix", name="seed")
        baseline = copy.deepcopy(self.ledger()["candidates"][0]["dispositions"][0]["record"])
        baseline["id"] = "next"
        stale = "0" * 64
        self.assertNotEqual(stale, self.candidate)
        cases = (
            ("candidate", lambda r: r.update(candidate=stale), self.candidate, False, "disposition_binding_mismatch"),
            ("version", lambda r: r.update(version=1), self.candidate, False, "disposition_binding_mismatch"),
            ("decision", lambda r: r["decisions"][0].update(decision="approve"), self.candidate, False, "invalid_decision"),
            ("empty decisions", lambda r: r.update(decisions=[]), self.candidate, False, "missing_decisions"),
            ("stale expectation", lambda r: None, stale, False, "stale_candidate"),
            ("dirty source", lambda r: None, self.candidate, True, "dirty_candidate"),
        )
        path = self.root / "decision.json"
        content = self.repo / "content.txt"
        original_content = content.read_bytes()
        before = (self.store / "ledger.json").read_bytes()
        for name, change, expect, dirty, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(baseline)
                change(invalid)
                path.write_text(json.dumps(invalid))
                try:
                    if dirty:
                        content.write_bytes(original_content + b"uncommitted fixture change\n")
                    code, error = self.call("disposition", "--expect", expect, "--disposition", path,
                                            "--sanitized", success=False)
                finally:
                    if dirty:
                        content.write_bytes(original_content)
                self.assertEqual(code, 1)
                self.assertEqual(error["error"], expected)
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)
        path.write_text(json.dumps(baseline))
        self.call("disposition", "--expect", self.candidate, "--disposition", path, "--sanitized")
        stored = self.ledger()["candidates"][0]["dispositions"]
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[-1]["record"], baseline)

    def test_disposition_allows_100_distinct_known_decisions_but_not_101(self):
        self.prepare()
        _, request = self.start()
        first = self.result(request, "changes-required")
        first["findings"] = [{**self.finding(), "id": "issue-" + str(index)} for index in range(100)]
        self.record(first)
        _, request = self.start("second")
        second = self.result(request, "changes-required")
        second["findings"] = [self.finding()]
        self.record(second)
        self.decision(first, decision="request-fix", name="seed", finding_id="issue-0")
        candidate = self.ledger()["candidates"][0]
        baseline = copy.deepcopy(candidate["dispositions"][0]["record"])
        baseline["id"] = "at-limit"
        baseline["decisions"] = [{"attempt": first["attempt"], "finding": finding["id"],
                                  "decision": "request-fix", "rationale": "Correct the known fixture finding."}
                                 for finding in first["findings"]]
        self.assertEqual(len(baseline["decisions"]), 100)
        REVIEW.disposition_check(baseline, candidate, False)
        invalid = copy.deepcopy(baseline)
        invalid["decisions"].append({"attempt": second["attempt"], "finding": "issue",
                                     "decision": "request-fix", "rationale": "Correct the additional known fixture finding."})
        references = [(row["attempt"], row["finding"]) for row in invalid["decisions"]]
        known = {(attempt["id"], finding["id"]) for attempt in candidate["attempts"]
                 for finding in attempt["result"]["findings"]}
        self.assertEqual(len(references), 101)
        self.assertEqual(len(set(references)), 101)
        self.assertEqual(set(references), known)  # No duplicate or unknown reference masks the list cap.
        path = self.root / "decision.json"
        path.write_text(json.dumps(invalid))
        before = (self.store / "ledger.json").read_bytes()
        code, error = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                "--sanitized", success=False)
        self.assertEqual((code, error), (1, {"error": "invalid_list"}))
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "decision-limit refusal changed the ledger")
        ReviewTests.assert_lock_released(self)
        path.write_text(json.dumps(baseline))
        self.call("disposition", "--expect", self.candidate, "--disposition", path, "--sanitized")
        stored = self.ledger()["candidates"][0]
        self.assertTrue(stored["attempts"] == candidate["attempts"], "disposition changed original findings")
        self.assertEqual(len(stored["dispositions"]), 2)
        self.assertTrue(stored["dispositions"][0] == candidate["dispositions"][0],
                        "boundary disposition changed the prior record")
        self.assertTrue(stored["dispositions"][1]["record"] == baseline,
                        "100-decision record was not retained")

    def test_interrupted_findings_survive_pass_retry_and_explicit_risk_acceptance(self):
        self.prepare()
        _, request = self.start()
        interrupted = self.result(request, "interrupted")
        interrupted["findings"] = [self.finding()]
        interrupted["coverage"][0].update(state="unresolved", owners=[], evidence=[])
        self.record(interrupted)
        original = copy.deepcopy(self.ledger()["candidates"][0]["attempts"][0])
        _, retry = self.start(retry=interrupted["attempt"])
        self.record(self.result(retry))
        _, second = self.start("second")
        summary = self.record(self.result(second))
        self.assertTrue(all(slot["state"] == "pass" and slot["complete"] for slot in summary["slots"]))
        self.assertEqual(summary["state"], "changes-required")
        self.assertEqual(summary["blockers"], [{"slot": None, "code": "recorded_findings"}])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        summary = self.decision(interrupted, name="retained-risk", authorized=True)
        self.assertEqual(summary["state"], "accepted-with-findings")
        self.assertEqual(summary["blockers"], [{"slot": None, "code": "recorded_findings"}])
        self.assertTrue(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["publication_authority"])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        attempts = self.ledger()["candidates"][0]["attempts"]
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], original)
        self.assertEqual([attempt["result"]["findings"] for attempt in attempts[1:]], [[], []])

    def test_accepted_earlier_findings_do_not_erase_latest_rejection(self):
        self.prepare()
        _, request = self.start()
        interrupted = self.result(request, "interrupted")
        interrupted["findings"] = [self.finding()]
        interrupted["coverage"][0].update(state="unresolved", owners=[], evidence=[])
        self.record(interrupted)
        original = copy.deepcopy(self.ledger()["candidates"][0]["attempts"][0])
        _, retry = self.start(retry=interrupted["attempt"])
        self.record(self.result(retry, "changes-required"))
        _, second = self.start("second")
        self.record(self.result(second))
        original_attempts = copy.deepcopy(self.ledger()["candidates"][0]["attempts"])
        summary = self.decision(interrupted, name="earlier-risk", authorized=True)
        blockers = [{"slot": "first", "code": "unresolved_review_verdict"},
                    {"slot": None, "code": "recorded_findings"}]
        self.assertTrue(all(slot["complete"] for slot in summary["slots"]))
        self.assertEqual([slot["state"] for slot in summary["slots"]], ["changes-required", "pass"])
        self.assertEqual(summary["blockers"], blockers)
        self.assertEqual(summary["state"], "changes-required")
        self.assertFalse(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["publication_authority"])
        code, status = self.call("status", "--require-ready", success=False, failure_code=2)
        self.assertEqual(code, 2)
        self.assertEqual(status["blockers"], blockers)
        candidate = self.ledger()["candidates"][0]
        self.assertEqual(candidate["attempts"], original_attempts)
        self.assertEqual(len(candidate["attempts"]), 3)
        self.assertEqual(candidate["attempts"][0], original)
        self.assertEqual([attempt["result"]["findings"] for attempt in candidate["attempts"][1:]], [[], []])
        dispositions = candidate["dispositions"]
        self.assertEqual(len(dispositions), 1)
        self.assertTrue(dispositions[0]["accepted_risk_authorized"])
        self.assertEqual([(row["attempt"], row["finding"], row["decision"])
                          for row in dispositions[0]["record"]["decisions"]],
                         [(interrupted["attempt"], "issue", "accept-risk")])

    def test_acceptance_cannot_override_missing_coverage_identity_or_policy(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first, "changes-required")
        result["findings"] = [self.finding()]
        result["coverage"][0].update(state="unresolved", owners=[], evidence=[])
        self.record(result)
        _, second = self.start("second")
        self.record(self.result(second))
        summary = self.decision(result, authorized=True)
        self.assertFalse(summary["accepted_with_findings"])
        self.assertEqual(summary["state"], "changes-required")
        self.assertEqual(summary["blockers"], [
            {"slot": "first", "code": "team_incomplete"},
            {"slot": None, "code": "recorded_findings"},
        ])
        self.assertFalse(summary["clean_review_ready"])
        candidate = self.ledger()["candidates"][0]
        record = candidate["dispositions"][0]["record"]
        candidate["bundle"]["team_review"]["accepted_risk"] = False
        with self.assertRaisesRegex(REVIEW.Stop, "^accepted_risk_not_authorized$"):
            REVIEW.disposition_check(record, candidate, True)
        candidate["bundle"]["team_review"]["accepted_risk"] = True
        record["decisions"][0]["finding"] = "unknown"
        with self.assertRaisesRegex(REVIEW.Stop, "^unknown_disposition_finding$"):
            REVIEW.disposition_check(record, candidate, True)

    def test_acceptance_cannot_override_unqualified_nonowner_child_identity(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        result["findings"] = [self.finding()]
        child = self.researcher(result)
        child.update(status="failed", report=None, launch=None,
                     identity={"basis": "unknown", "reviewer": None, "model": None, "evidence": ""})
        self.assertTrue(all(row["state"] == "reviewed" and row["owners"] == ["lead"]
                            for row in result["coverage"]))
        self.record(result)
        _, second = self.start("second")
        self.record(self.result(second))
        summary = self.decision(result, authorized=True)
        self.assertIn("team_incomplete", [item["code"] for item in summary["blockers"]])
        self.assertEqual(summary["state"], "changes-required")
        self.assertEqual(summary["blockers"], [
            {"slot": "first", "code": "team_incomplete"},
            {"slot": None, "code": "recorded_findings"},
        ])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)

    def test_changes_required_without_actionable_findings_is_terminal_and_not_retryable(self):
        for findings in ("empty", "optional-only"):
            for completeness in ("complete", "coverage-incomplete", "identity-incomplete"):
                with self.subTest(findings=findings, completeness=completeness):
                    self.store = self.root / (findings + "-" + completeness)
                    self.prepare()
                    _, second = self.start("second")
                    self.record(self.result(second))
                    _, request = self.start()
                    result = self.result(request, "changes-required")
                    if findings == "optional-only":
                        suggestion = self.finding()
                        suggestion.update(kind="suggestion", severity=None, recommendation="optional")
                        result["findings"] = [suggestion]
                    if completeness == "coverage-incomplete":
                        result["coverage"][0].update(state="unresolved", owners=[], evidence=[])
                    elif completeness == "identity-incomplete":
                        # Lead coverage is complete; only the nonowner child's identity is unqualified.
                        child = self.researcher(result)
                        child.update(status="failed", report=None, launch=None,
                                     identity={"basis": "unknown", "reviewer": None,
                                               "model": None, "evidence": ""})
                        self.assertTrue(all(row["state"] == "reviewed" for row in result["coverage"]))
                    self.assertFalse(any(REVIEW.actionable(finding) for finding in result["findings"]))
                    summary = self.record(result)
                    expected = ([] if completeness == "complete" else
                                [{"slot": "first", "code": "team_incomplete"}])
                    expected.append({"slot": "first", "code": "unresolved_review_verdict"})
                    self.assertEqual(summary["blockers"], expected)
                    self.assertEqual(summary["state"], "changes-required")
                    self.assertEqual(summary["slots"][0]["complete"], completeness == "complete")
                    self.assertFalse(summary["clean_review_ready"])
                    self.assertFalse(summary["accepted_with_findings"])
                    code, status = self.call("status", "--require-ready", success=False, failure_code=2)
                    self.assertEqual(code, 2)
                    self.assertEqual(status["state"], "changes-required")
                    before = (self.store / "ledger.json").read_bytes()
                    for explicit in (False, True):
                        with self.subTest(explicit_retry=explicit):
                            output = self.root / (findings + "-" + completeness + "-" + str(explicit) + ".json")
                            self.assertFalse(os.path.lexists(output))
                            flags = ["--retry", result["attempt"]] if explicit else []
                            code, rejected = self.call("start", "--expect", self.candidate,
                                "--slot", "first", "--reason", "Attempt to reroll a terminal rejection",
                                "--output", output, *flags, success=False)
                            self.assertEqual(code, 1)
                            self.assertEqual(rejected["error"],
                                             "invalid_retry" if explicit else "explicit_retry_required")
                            self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                            self.assertFalse(os.path.lexists(output))
                            ReviewTests.assert_lock_released(self)

    def test_required_and_optional_participating_incomplete_and_unresolved_verdicts_block(self):
        for required in (True, False):
            with self.subTest(required=required):
                self.store = self.root / ("required" if required else "optional")
                self.spec["reviewers"][1]["required"] = required
                self.save_inputs()
                self.prepare()
                _, first = self.start()
                summary = self.record(self.result(first))
                self.assertEqual(summary["state"], "reviewing" if required else "ready")
                started, second = self.start("second")
                prefix = "required_" if required else "participating_"
                self.assertEqual(started["blockers"], [{"slot": "second", "code": prefix + "in-flight"}])
                incomplete = self.result(second, "interrupted")
                incomplete["coverage"][0].update(state="unresolved", owners=[], evidence=[])
                summary = self.record(incomplete)
                self.assertEqual(summary["blockers"], [
                    {"slot": "second", "code": prefix + "interrupted"},
                    {"slot": "second", "code": "team_incomplete"},
                ])
                self.assertFalse(summary["clean_review_ready"])
                _, retry = self.start("second", retry=second["request"]["attempt"])
                summary = self.record(self.result(retry, "changes-required"))
                self.assertEqual(summary["blockers"], [{"slot": "second", "code": "unresolved_review_verdict"}])
                self.assertEqual(summary["state"], "changes-required")
                self.assertFalse(summary["clean_review_ready"])
                self.assertFalse(summary["accepted_with_findings"])

    def test_required_and_optional_terminal_failures_require_explicit_retry(self):
        for required in (True, False):
            for verdict in ("failed", "cancelled", "interrupted"):
                with self.subTest(required=required, verdict=verdict):
                    name = ("required-" if required else "optional-") + verdict
                    self.store = self.root / name
                    self.spec["reviewers"][1]["required"] = required
                    self.save_inputs()
                    self.prepare()
                    _, first = self.start()
                    self.record(self.result(first))
                    _, second = self.start("second")
                    original = self.result(second, verdict)
                    summary = self.record(original)
                    prefix = "required_" if required else "participating_"
                    blockers = [{"slot": "second", "code": prefix + verdict}]
                    self.assertEqual(summary["state"], "reviewing")
                    self.assertEqual(summary["blockers"], blockers)
                    self.assertTrue(all(slot["complete"] for slot in summary["slots"]))
                    self.assertFalse(summary["clean_review_ready"])
                    self.assertFalse(summary["accepted_with_findings"])
                    code, status = self.call("status", "--require-ready", success=False, failure_code=2)
                    self.assertEqual(code, 2)
                    self.assertEqual(status["blockers"], blockers)
                    prior = copy.deepcopy(self.ledger()["candidates"][0]["attempts"])
                    before = (self.store / "ledger.json").read_bytes()
                    output = self.root / ("unacknowledged-retry-" + name + ".json")
                    code, error = self.call("start", "--expect", self.candidate, "--slot", "second",
                        "--reason", "Retry the terminal fixture result", "--output", output, success=False)
                    self.assertEqual((code, error), (1, {"error": "explicit_retry_required"}))
                    self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                    "unacknowledged retry changed the ledger")
                    self.assertFalse(os.path.lexists(output))
                    ReviewTests.assert_lock_released(self)
                    _, retry = self.start("second", retry=original["attempt"])
                    recovered = self.result(retry)
                    summary = self.record(recovered)
                    self.assertEqual(summary["state"], "ready")
                    self.assertEqual(summary["blockers"], [])
                    self.assertTrue(summary["clean_review_ready"])
                    self.assertFalse(summary["accepted_with_findings"])
                    code, status = self.call("status", "--require-ready")
                    self.assertEqual(code, 0)
                    self.assertEqual(status["state"], "ready")
                    self.assertEqual(status["blockers"], [])
                    self.assertTrue(status["clean_review_ready"])
                    attempts = self.ledger()["candidates"][0]["attempts"]
                    self.assertEqual(len(attempts), 3)
                    self.assertTrue(attempts[:2] == prior, "retry changed prior terminal records")
                    self.assertTrue(attempts[1]["result"] == original, "original failure result was not retained")
                    self.assertEqual(attempts[2]["retry"], original["attempt"])
                    self.assertTrue(attempts[2]["result"] == recovered, "successful retry result was not retained")

    def test_interruption_without_invented_launch_and_explicit_retry(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first, "interrupted")
        result.update(run_id=None, launch=None)
        result["identity"] = {"basis": "unknown", "reviewer": None, "model": None, "evidence": ""}
        for row in result["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.record(result)
        _, retry = self.start(retry=first["request"]["attempt"])
        self.assertNotEqual(first["request"]["attempt"], retry["request"]["attempt"])
        self.record(self.result(retry))
        self.assertEqual(len(self.ledger()["candidates"][0]["attempts"]), 2)

    def test_supersede_promotes_v1_without_rewriting_history_or_carrying_approval(self):
        self.spec.pop("version")
        self.spec.pop("team_review")
        for slot in self.spec["reviewers"]:
            for key in ("model_selection", "identity_requirement", "researcher_model_overrides"):
                slot.pop(key)
        self.save_inputs()
        self.prepare()
        for slot in ("first", "second"):
            _, request = self.start(slot)
            self.record(ReviewTests.result(self, request))
        old = self.ledger()["candidates"][0]
        old_digest = self.candidate
        self.enable_teams()
        summary = self.call("supersede", "--expect", old_digest, "--reason", "Explicit team policy version",
                            "--repo", self.repo, "--base", self.base, "--head", self.head,
                            "--inputs", self.inputs, "--sanitized")[1]
        self.assertEqual(summary["state"], "prepared")
        ledger = self.ledger()
        self.assertEqual(ledger["version"], 2)
        self.assertEqual(ledger["candidates"][0], old)
        self.assertEqual(self.call("status", "--candidate", old_digest)[1]["state"], "superseded")
        self.assertEqual(ledger["candidates"][1]["attempts"], [])

    def test_supersede_cannot_downgrade_or_mutate_history_but_separate_v1_store_works(self):
        self.prepare()
        _, request = self.start()
        self.record(self.result(request))
        before = (self.store / "ledger.json").read_bytes()
        self.spec.pop("version")
        self.spec.pop("team_review")
        for slot in self.spec["reviewers"]:
            for key in ("model_selection", "identity_requirement", "researcher_model_overrides"):
                slot.pop(key)
        self.save_inputs()
        code, result = self.call("supersede", "--expect", self.candidate, "--reason", "Old input template",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized", success=False)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "version_downgrade")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        ReviewTests.assert_lock_released(self)
        self.assertEqual(self.call("status")[1]["candidate"], self.candidate)
        self.store = self.root / "separate-v1-review"
        self.assertEqual(self.prepare()["state"], "prepared")
        self.assertEqual(self.ledger()["version"], 1)

    def test_existing_mixed_history_loads_but_new_supersession_uses_highest_version(self):
        self.prepare()
        ledger = self.ledger()
        old = copy.deepcopy(ledger["candidates"][0])
        self.spec.pop("version")
        self.spec.pop("team_review")
        for slot in self.spec["reviewers"]:
            for key in ("model_selection", "identity_requirement", "researcher_model_overrides"):
                slot.pop(key)
        self.save_inputs()
        bundle = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        historical = {"digest": REVIEW.digest(bundle), "bundle": bundle, "attempts": [],
                      "supersedes": old["digest"], "reason": "Previously valid v1 successor fixture"}
        ledger["candidates"].append(historical)
        ledger["active"] = historical["digest"]
        REVIEW.validate_ledger(ledger)
        # Model an existing valid history, not a newly authorized downgrade.
        (self.store / "ledger.json").write_bytes(REVIEW.canonical(ledger) + b"\n")
        before = (self.store / "ledger.json").read_bytes()
        self.assertEqual(self.call("status")[1]["candidate"], historical["digest"])
        self.assertEqual(self.call("status", "--candidate", old["digest"])[1]["state"], "superseded")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.spec["questions"].append("A distinct prospective v1 input")
        self.save_inputs()
        code, result = self.call("supersede", "--expect", historical["digest"], "--reason", "Another old template",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized", success=False)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "version_downgrade")
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.ledger()["candidates"][0], old)
        ReviewTests.assert_lock_released(self)

    def test_malformed_later_mixed_history_candidate_preserves_ready_store(self):
        self.prepare()
        ledger = self.ledger()
        old = copy.deepcopy(ledger["candidates"][0])
        self.spec.pop("version")
        self.spec.pop("team_review")
        for slot in self.spec["reviewers"]:
            for key in ("model_selection", "identity_requirement", "researcher_model_overrides"):
                slot.pop(key)
        self.save_inputs()
        bundle = REVIEW.prepare_bundle(self.repo, self.base, self.head, self.inputs)
        historical = {"digest": REVIEW.digest(bundle), "bundle": bundle, "attempts": [],
                      "supersedes": old["digest"], "reason": "Previously valid v1 successor fixture"}
        ledger["candidates"].append(historical)
        ledger["active"] = historical["digest"]
        REVIEW.validate_ledger(ledger)
        # Reuse the retained mixed-history shape, not a newly authorized downgrade.
        path = self.store / "ledger.json"
        path.write_bytes(REVIEW.canonical(ledger) + b"\n")
        self.candidate = historical["digest"]
        for slot in ("first", "second"):
            _, request = self.start(slot)
            summary = self.record(ReviewTests.result(self, request))
        self.assertEqual((self.ledger()["version"], summary["state"], summary["blockers"]), (2, "ready", []))
        original_bytes, original = path.read_bytes(), self.ledger()
        later = original["candidates"][1]
        self.assertEqual([item["bundle"]["version"] for item in original["candidates"]], [2, 1])
        cases = (
            ("later-null-candidate", None, "invalid_schema"),
            ("later-missing-bundle", {k: v for k, v in later.items() if k != "bundle"}, "invalid_schema"),
            ("later-null-bundle", {**later, "bundle": None}, "unsupported_version"),
        )
        for name, replacement, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                invalid["candidates"][1] = copy.deepcopy(replacement)
                ReviewTests.assert_ledger_shape_refusal(self, original_bytes, invalid, expected)
        self.assertTrue(self.ledger()["candidates"][0] == old, "Shape refusals changed earlier history")


class WorksetReviewTests(unittest.TestCase):
    """Opt-in release decisions using only the existing owned Git fixtures."""

    git = ReviewTests.git
    commit = ReviewTests.commit
    save_inputs = ReviewTests.save_inputs
    call = ReviewTests.call
    prepare = ReviewTests.prepare
    start = ReviewTests.start
    record = ReviewTests.record
    ledger = ReviewTests.ledger
    finding = TeamReviewTests.finding
    researcher = TeamReviewTests.researcher
    validate_result = TeamReviewTests.validate_result

    def setUp(self):
        ReviewTests.setUp(self)
        TeamReviewTests.enable_teams(self)
        self.spec["team_review"]["release_policy"] = "blocking-only"
        self.save_inputs()

    def result(self, request, verdict="pass"):
        result = TeamReviewTests.result(self, request, verdict)
        result["version"] = request["bundle"]["version"]
        return result

    def decision(self, result, decision="nonblocking", name="decision", finding="issue", authorized=False, success=True):
        row = {"attempt": result["attempt"], "finding": finding, "decision": decision,
               "rationale": "Coordinator verified the fixture mismatch has no serious release impact; preserve it as follow-up work."}
        if decision == "nonblocking":
            row["follow_up"] = {"issue": "https://example.invalid/fixture/issues/7",
                                "source": "owned synthetic Issue receipt",
                                "source_sha256": REVIEW.sha(b"fixture Issue receipt")}
        record = {"version": self.spec["version"], "candidate": self.candidate, "id": name,
                  "actor": "fixture-coordinator", "authority": "Explicit owned-fixture workset policy.",
                  "decisions": [row], "provenance": {"importer": "fixture-operator",
                  "source": "owned fixture classification", "source_sha256": REVIEW.sha(b"classification")}}
        path = self.root / "decision.json"
        path.write_text(json.dumps(record))
        flags = ["--authorize-accepted-risk"] if authorized else []
        summary = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                            "--sanitized", *flags, success=success)[1]
        return summary, record

    def reviewed_pair(self, findings=None, verdict="changes-required"):
        self.prepare()
        _, first = self.start()
        result = self.result(first, verdict)
        result["findings"] = [self.finding()] if findings is None else findings
        self.record(result)
        _, second = self.start("second")
        self.record(self.result(second))
        return result

    def assert_release_gate(self, ready):
        for command in ("status", "resume"):
            code, summary = self.call(command, "--require-release-ready", success=ready, failure_code=2)
            self.assertEqual(code, 0 if ready else 2)
            self.assertEqual(summary["release_ready"], ready)
            self.assertFalse(summary["publication_authority"])
        return summary

    def test_workset_policy_is_explicit_bound_and_preserves_legacy_history(self):
        self.spec["team_review"].pop("release_policy")
        self.save_inputs()
        old_result = self.reviewed_pair()
        old = copy.deepcopy(self.ledger()["candidates"][0])
        before = (self.store / "ledger.json").read_bytes()
        self.assertNotIn("release_ready", self.call("status")[1])
        summary = self.assert_release_gate(False)
        self.assertEqual(summary["release_state"], "not-enabled")
        self.assertEqual(summary["release_blockers"], [{"slot": None, "code": "release_policy_not_enabled"}])
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.assertEqual(self.decision(old_result, success=False)[0], {"error": "invalid_decision"})
        self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        self.spec["team_review"]["release_policy"] = "blocking-only"
        self.save_inputs()
        summary = self.call("supersede", "--expect", self.candidate, "--reason", "Explicit new workset policy",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")[1]
        self.assertNotEqual(summary["candidate"], old["digest"])
        self.assertEqual(self.ledger()["candidates"][0], old)
        self.assertEqual(self.ledger()["candidates"][1]["attempts"], [])
        self.assertFalse(summary["release_ready"])

    def test_workset_policy_rejects_unknown_values_without_creating_store(self):
        for value in (None, True, "clean", "all-findings-accepted", {}):
            with self.subTest(value=value):
                self.spec["team_review"]["release_policy"] = value
                self.save_inputs()
                code, error = self.call("prepare", "--repo", self.repo, "--base", self.base,
                    "--head", self.head, "--inputs", self.inputs, "--sanitized", success=False)
                self.assertEqual((code, error), (1, {"error": "invalid_release_policy"}))
                self.assertFalse(self.store.exists())

    def test_workset_nonblocking_issue_opens_only_the_distinct_release_gate(self):
        first = self.reviewed_pair()
        original = copy.deepcopy(self.ledger()["candidates"][0]["attempts"])
        self.assertFalse(self.assert_release_gate(False)["clean_review_ready"])
        summary, record = self.decision(first)
        self.assertTrue(summary["release_ready"])
        self.assertEqual(summary["release_state"], "ready-with-followups")
        self.assertEqual(summary["state"], "changes-required")
        self.assertFalse(summary["clean_review_ready"])
        self.assertFalse(summary["accepted_with_findings"])
        self.assertEqual(summary["release_blockers"], [])
        self.assertEqual(summary["follow_ups"], [{"attempt": first["attempt"], "finding": "issue",
                                                **record["decisions"][0]["follow_up"]}])
        self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
        self.assert_release_gate(True)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"], original)
        self.assertFalse(summary["coordinator_authority_authenticated"])

    def test_workset_blocking_is_not_inferred_from_severity_or_old_decisions(self):
        first = self.reviewed_pair()
        for index, decision in enumerate(("request-fix", "defer", "dispute", "accept-risk")):
            with self.subTest(decision=decision):
                summary, _ = self.decision(first, decision, "legacy-" + str(index), authorized=decision == "accept-risk")
                self.assertFalse(summary["release_ready"])
                self.assertEqual(summary["release_blockers"][-1]["code"], "unresolved_blocking_finding")
        self.assertEqual(first["findings"][0]["severity"], "P2")
        summary, _ = self.decision(first, name="explicit-reassessment")
        self.assertTrue(summary["release_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], first)

    def test_workset_false_positive_is_explicit_and_does_not_require_an_issue(self):
        first = self.reviewed_pair()
        summary, record = self.decision(first, "false-positive")
        self.assertNotIn("follow_up", record["decisions"][0])
        self.assertTrue(summary["release_ready"])
        self.assertEqual(summary["release_state"], "ready")
        self.assertEqual(summary["follow_ups"], [])
        self.assertFalse(summary["clean_review_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], first)

    def test_workset_followup_and_optional_findings_need_explicit_references(self):
        for index, recommendation in enumerate(("follow-up", "optional")):
            with self.subTest(recommendation=recommendation):
                self.store = self.root / ("recommendation-" + str(index))
                finding = self.finding()
                finding.update(kind="suggestion", severity="P0", recommendation=recommendation)
                first = self.reviewed_pair([finding], "pass" if recommendation == "optional" else "changes-required")
                summary = self.assert_release_gate(False)
                self.assertEqual(summary["release_blockers"][-1]["code"], "finding_disposition_required")
                summary, _ = self.decision(first)
                self.assertTrue(summary["release_ready"])
                self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], first)

    def test_workset_decision_validation_preserves_store_on_missing_facts(self):
        first = self.reviewed_pair()
        _, baseline = self.decision(first)
        baseline["id"] = "next"
        path = self.root / "decision.json"
        before = (self.store / "ledger.json").read_bytes()
        cases = (
            ("missing-followup", lambda r: r["decisions"][0].pop("follow_up"), "invalid_schema"),
            ("empty-issue", lambda r: r["decisions"][0]["follow_up"].update(issue=""), "invalid_string"),
            ("space-issue", lambda r: r["decisions"][0]["follow_up"].update(issue=" "), "invalid_follow_up"),
            ("empty-source", lambda r: r["decisions"][0]["follow_up"].update(source=""), "invalid_string"),
            ("bad-hash", lambda r: r["decisions"][0]["follow_up"].update(source_sha256="invalid"), "invalid_digest"),
            ("empty-rationale", lambda r: r["decisions"][0].update(rationale=""), "invalid_string"),
            ("space-rationale", lambda r: r["decisions"][0].update(rationale=" "), "missing_release_rationale"),
            ("unknown-finding", lambda r: r["decisions"][0].update(finding="absent"), "unknown_disposition_finding"),
            ("stale-candidate", lambda r: r.update(candidate="0" * 64), "disposition_binding_mismatch"),
        )
        for name, change, expected in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(baseline)
                change(invalid)
                path.write_text(json.dumps(invalid))
                code, error = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                       "--sanitized", success=False)
                self.assertEqual((code, error), (1, {"error": expected}))
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
                ReviewTests.assert_lock_released(self)

    def test_workset_latest_decision_can_reopen_a_blocker_without_erasing_history(self):
        first = self.reviewed_pair()
        self.decision(first)
        original = copy.deepcopy(self.ledger()["candidates"][0])
        summary, _ = self.decision(first, "request-fix", "reopen")
        self.assertFalse(summary["release_ready"])
        self.assertEqual(summary["release_blockers"][-1]["code"], "unresolved_blocking_finding")
        current = self.ledger()["candidates"][0]
        self.assertEqual(current["attempts"], original["attempts"])
        self.assertEqual(current["dispositions"][:-1], original["dispositions"])

    def test_workset_all_retained_findings_need_decisions_even_after_retry(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request, "interrupted")
        original["findings"] = [self.finding()]
        self.record(original)
        _, retry = self.start(retry=original["attempt"])
        current = self.result(retry, "changes-required")
        finding = self.finding()
        finding["id"] = "new-issue"
        current["findings"] = [finding]
        self.record(current)
        _, second = self.start("second")
        self.record(self.result(second))
        summary, _ = self.decision(current, finding="new-issue")
        self.assertFalse(summary["release_ready"])
        self.assertEqual(summary["release_blockers"][-1]["attempt"], original["attempt"])
        summary, _ = self.decision(original, name="retained")
        self.assertTrue(summary["release_ready"])
        self.assertEqual(len(summary["follow_ups"]), 2)
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], original)

    def test_workset_empty_latest_rejection_cannot_borrow_earlier_decision(self):
        self.prepare()
        _, request = self.start()
        original = self.result(request, "interrupted")
        original["findings"] = [self.finding()]
        self.record(original)
        _, retry = self.start(retry=original["attempt"])
        self.record(self.result(retry, "changes-required"))
        _, second = self.start("second")
        self.record(self.result(second))
        summary, _ = self.decision(original)
        self.assertFalse(summary["release_ready"])
        self.assertEqual(summary["release_blockers"], [{"slot": "first", "code": "unresolved_review_verdict"}])

    def test_workset_optional_only_rejection_can_be_explicitly_resolved(self):
        finding = self.finding()
        finding.update(kind="suggestion", severity=None, recommendation="optional")
        first = self.reviewed_pair([finding])
        self.assertIn("unresolved_review_verdict", [b["code"] for b in self.assert_release_gate(False)["release_blockers"]])
        summary, _ = self.decision(first)
        self.assertTrue(summary["release_ready"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertEqual(summary["slots"][0]["state"], "changes-required")

    def test_workset_missing_coverage_identity_or_participating_review_stays_blocked(self):
        for gap in ("coverage", "identity", "failed", "cancelled", "interrupted", "in-flight", "not-started"):
            with self.subTest(gap=gap):
                self.store = self.root / ("gap-" + gap)
                self.prepare()
                _, first = self.start()
                result = self.result(first, "changes-required")
                result["findings"] = [self.finding()]
                if gap == "coverage":
                    result["coverage"][0].update(state="unresolved", owners=[], evidence=[])
                if gap == "identity":
                    child = self.researcher(result)
                    child.update(status="failed", report=None, launch=None,
                                 identity={"basis": "unknown", "reviewer": None, "model": None, "evidence": ""})
                self.record(result)
                if gap != "not-started":
                    _, second = self.start("second")
                    if gap != "in-flight":
                        self.record(self.result(second, gap if gap in {"failed", "cancelled", "interrupted"} else "pass"))
                summary, _ = self.decision(result)
                self.assertFalse(summary["release_ready"])
                self.assertTrue(summary["release_blockers"])

    def test_workset_source_change_blocks_even_after_explicit_dispositions(self):
        first = self.reviewed_pair()
        self.decision(first)
        before = (self.store / "ledger.json").read_bytes()
        path = self.repo / "content.txt"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"unreviewed fixture change\n")
            summary = self.assert_release_gate(False)
            self.assertEqual(summary["release_blockers"], [{"slot": None, "code": "dirty_candidate"}])
            self.assertEqual((self.store / "ledger.json").read_bytes(), before)
        finally:
            path.write_bytes(original)
        self.assert_release_gate(True)

    def test_workset_unknown_confidence_is_not_invented_or_allowed_for_blocking_records(self):
        self.prepare()
        _, request = self.start()
        result = self.result(request, "changes-required")
        child = self.researcher(result)
        finding = self.finding()
        finding.update(confidence=None, recommendation="follow-up")
        finding["assessments"] = [{"researcher": child["id"], "finding": "original",
                                  **{key: finding[key] for key in
                                     ("kind", "severity", "confidence", "recommendation", "rationale")}}]
        result["findings"] = [finding]
        result["limitations"].append("Original confidence was not supplied; null preserves unknown, not an inferred classification.")
        self.validate_result(result)
        for location in ("lead", "researcher"):
            for recommendation in ("block", "address-before-merge"):
                with self.subTest(location=location, recommendation=recommendation):
                    changed = copy.deepcopy(result)
                    row = changed["findings"][0] if location == "lead" else changed["findings"][0]["assessments"][0]
                    row["recommendation"] = recommendation
                    with self.assertRaisesRegex(REVIEW.Stop, "^invalid_confidence$"):
                        self.validate_result(changed)
        missing = copy.deepcopy(result)
        missing["findings"][0].pop("confidence")
        with self.assertRaisesRegex(REVIEW.Stop, "^invalid_schema$"):
            self.validate_result(missing)
        self.record(result)
        _, second = self.start("second")
        self.record(self.result(second))
        self.assertTrue(self.decision(result)[0]["release_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)
        candidate = copy.deepcopy(self.ledger()["candidates"][0])
        candidate["bundle"]["team_review"].pop("release_policy")
        with self.assertRaisesRegex(REVIEW.Stop, "^invalid_confidence$"):
            REVIEW.result_check(result, candidate["attempts"][0], candidate)

    def test_workset_check_facts_are_preserved_not_blanket_release_failures(self):
        self.prepare()
        _, first = self.start()
        result = self.result(first)
        result["executed_checks"] = [{"command": "synthetic exploratory check", "cwd": "owned fixture",
            "mode": "isolated", "revision": self.head, "outcome": outcome, "exit_code": code,
            "writes": "None", "evidence": "Nonessential exploratory fixture outcome; no command executed by this fake reviewer.",
            "provenance": "reviewer_reported"}
            for outcome, code in (("fail", 1), ("interrupted", None), ("unknown", None), ("skipped", None))]
        self.record(result)
        _, second = self.start("second")
        summary = self.record(self.result(second))
        self.assertTrue(summary["release_ready"])
        self.assertEqual(self.ledger()["candidates"][0]["attempts"][0]["result"], result)

    def test_workset_v3_keeps_continuation_contract_and_clean_pass_rules(self):
        self.spec["version"] = 3
        self.save_inputs()
        first = self.reviewed_pair()
        self.assertIsNone(self.ledger()["candidates"][0]["attempts"][0]["request"]["continuation"])
        self.assertTrue(self.decision(first)[0]["release_ready"])
        changed = copy.deepcopy(first)
        changed["verdict"] = "pass"
        with self.assertRaisesRegex(REVIEW.Stop, "^actionable_findings_require_changes$"):
            self.validate_result(changed)

    def test_workset_delta_successor_needs_new_current_owner_endorsements(self):
        first = self.reviewed_pair()
        self.decision(first)
        original = copy.deepcopy(self.ledger()["candidates"][0])
        (self.repo / "content.txt").write_text("batched fixture correction\n")
        self.head = self.commit("Correct the fixture workset")
        manifest = {"prior_candidate": original["digest"], "prior_head": original["bundle"]["head"],
                    "final_head": self.head, "delta": "batched content correction",
                    "evidence": [{"slot": a["request"]["slot"], "result_digest": a["result_digest"]}
                                 for a in original["attempts"]]}
        (self.root / "delta.json").write_text(json.dumps(manifest))
        self.spec["artifacts"].append({"name": "delta-evidence", "path": "delta.json"})
        self.save_inputs()
        summary = self.call("supersede", "--expect", self.candidate, "--reason", "Batched correction, current-owner delta review",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")[1]
        self.candidate = summary["candidate"]
        self.assertFalse(summary["release_ready"])
        self.assertEqual(summary["head"], self.head)
        successor = self.ledger()["candidates"][-1]
        self.assertEqual(successor["bundle"]["base"], self.base)
        self.assertEqual([r["id"] for r in successor["bundle"]["coverage_requirements"]],
                         [r["id"] for r in original["bundle"]["coverage_requirements"]])
        self.assertEqual(successor["attempts"], [])
        for slot in ("first", "second"):
            _, request = self.start(slot)
            current = self.result(request)
            current["limitations"].append("Current owner endorses this final head using its prior same-team evidence and assessed delta; not a fresh whole-source reread.")
            if slot == "first":
                reused = copy.deepcopy(current)
                reused["run_id"] = first["run_id"]
                before = (self.store / "ledger.json").read_bytes()
                self.assertEqual(self.record(reused, success=False)["error"], "duplicate_identity")
                self.assertEqual((self.store / "ledger.json").read_bytes(), before)
            summary = self.record(current)
            self.assertEqual(summary["release_ready"], slot == "second")
        self.assertEqual(self.ledger()["candidates"][0], original)
        historical = self.call("status", "--candidate", original["digest"])[1]
        self.assertFalse(historical["release_ready"])
        self.assertIn("superseded_candidate", [b["code"] for b in historical["release_blockers"]])


class ContinuationReviewTests(unittest.TestCase):
    # Reuse owned fixtures, not TestCase inheritance or a second environment policy.
    git = ReviewTests.git
    commit = ReviewTests.commit
    save_inputs = ReviewTests.save_inputs
    call = ReviewTests.call
    prepare = ReviewTests.prepare
    start = ReviewTests.start
    record = ReviewTests.record
    ledger = ReviewTests.ledger
    researcher = TeamReviewTests.researcher
    delegate_file = TeamReviewTests.delegate_file
    finding = TeamReviewTests.finding
    validate_result = TeamReviewTests.validate_result

    def setUp(self):
        ReviewTests.setUp(self)
        TeamReviewTests.enable_teams(self)
        self.spec["version"] = 3
        self.save_inputs()
        self.authorization = {
            "actor": "fixture-operator", "authority": "Explicit fixture continuation authorization.",
            "limits": "Same native lead, synthesis only; no new researchers or provider authority.",
            "source": "fixture-authorization", "source_sha256": REVIEW.sha(b"authorization"),
        }
        self.authorization_path = self.root / "authorization.json"
        self.authorization_path.write_text(json.dumps(self.authorization))
        self.authorization_path.chmod(0o600)
        self.output_number = 0

    def result(self, request, verdict="pass"):
        result = TeamReviewTests.result(self, request, verdict)
        result["version"] = request["request"]["version"]
        if request["request"].get("continuation") is not None:
            predecessor = request["continuation_history"][-1]["result"]
            for key in ("run_id", "identity", "launch", "researchers"):
                result[key] = copy.deepcopy(predecessor[key])
            result["provenance"] = {
                "importer": "fixture-operator", "source": "fixture-synthesis-" + result["attempt"],
                "source_sha256": REVIEW.sha(("synthesis-" + result["attempt"]).encode()),
            }
            for row in result["coverage"]:
                row["evidence"] = [{"owner": "lead", **{key: result["provenance"][key]
                                                       for key in ("source", "source_sha256")}}]
        return result

    def attempt(self, identity):
        return next(attempt for attempt in self.ledger()["candidates"][-1]["attempts"]
                    if attempt["id"] == identity)

    def interrupted_team(self, children=2, slot="first"):
        _, request = self.start(slot)
        self.assertTrue(request["request"]["continuation"] is None
                        and request["continuation_history"] == [],
                        "Initial v3 request unexpectedly carried prior work")
        result = self.result(request, "interrupted")
        for index in range(children):
            self.researcher(result, "child-" + str(index))
        for row in result["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.record(result)
        return result

    def continuation_args(self, origin, slot="first"):
        self.output_number += 1
        output = self.root / ("continuation-" + str(self.output_number) + ".json")
        return output, ["--expect", self.candidate, "--slot", slot,
                        "--reason", "Explicit fixture lead synthesis",
                        "--continue-from", origin, "--authorization", self.authorization_path,
                        "--sanitized", "--output", output]

    def continue_from(self, origin, slot="first"):
        output, args = self.continuation_args(origin, slot)
        _, summary = self.call("start", *args)
        return summary, json.loads(output.read_text())

    def assert_start_refusal(self, args, output, diagnostic):
        before = (self.store / "ledger.json").read_bytes()
        code, result = self.call("start", *args, success=False)
        self.assertTrue(code == 1 and result == {"error": diagnostic},
                        "Continuation start did not use its expected static diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Rejected continuation changed the ledger")
        self.assertFalse(os.path.lexists(output), "Rejected continuation created an export")
        ReviewTests.assert_lock_released(self)

    def assert_record_refusal(self, result, diagnostic):
        before = (self.store / "ledger.json").read_bytes()
        self.assertTrue(self.record(result, success=False) == {"error": diagnostic},
                        "Continuation record did not use its expected static diagnostic")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Rejected continuation result changed the ledger")
        ReviewTests.assert_lock_released(self)

    def test_same_session_continuation_reconciles_unresolved_floor_without_peer_history(self):
        self.spec["team_review"]["max_researchers"] = 2
        self.save_inputs()
        self.prepare()
        _, peer = self.start("second")
        self.record(self.result(peer))
        origin = self.interrupted_team()
        previous = copy.deepcopy(self.ledger()["candidates"][0]["attempts"])
        retained = self.attempt(origin["attempt"])
        self.assertTrue(all(row["state"] == "unresolved" for row in origin["coverage"]),
                        "Continuation fixture must begin without completed lead reconciliation")
        summary, request = self.continue_from(origin["attempt"])
        self.assertTrue(request["continuation_history"] == [retained],
                        "Continuation export must contain only its linked team history")
        self.assertTrue(request["request"]["continuation"] == {
            "attempt": retained["id"], "result_digest": retained["result_digest"],
            "authorization": self.authorization,
        }, "Continuation request lost the exact origin or authorization")
        self.assertIsNone(self.attempt(request["request"]["attempt"])["retry"])
        self.assertEqual(request["request_digest"], REVIEW.digest(request["request"]))
        self.assertNotEqual(request["request_digest"], origin["request_digest"])
        self.assertEqual(summary["slots"][0]["continuation_of"], retained["id"])
        self.assertIsNone(summary["slots"][1]["continuation_of"])
        self.assertFalse(summary["clean_review_ready"])
        result = self.result(request)
        result["launch"] = {
            "model": origin["launch"]["model"], "source": "fixture-resumed-launch",
            "source_sha256": REVIEW.sha(b"resumed launch"),
        }
        self.assertTrue(result["launch"]["source"] != origin["launch"]["source"]
                        and result["launch"]["source_sha256"] != origin["launch"]["source_sha256"],
                        "Continuation must exercise new launch evidence for the same selected model")
        self.delegate_file(result, result["researchers"][0])
        self.assertNotEqual(result["attempt"], origin["attempt"])
        self.assertEqual(result["run_id"], origin["run_id"])
        self.assertTrue(REVIEW.canonical(result["researchers"]) == REVIEW.canonical(origin["researchers"]),
                        "Continued synthesis changed the original researcher records")
        invalid = copy.deepcopy(result)
        invalid["launch"]["model"] = "different-selected-model"
        self.assert_record_refusal(invalid, "launch_model_mismatch")
        summary = self.record(result)
        self.assertTrue(summary["state"] == "ready" and summary["clean_review_ready"]
                        and summary["blockers"] == [] and not summary["publication_authority"],
                        "Complete continuation did not retain the clean, non-publishing gate")
        self.assertEqual(self.call("status", "--require-ready")[0], 0)
        attempts = self.ledger()["candidates"][0]["attempts"]
        self.assertTrue(attempts[:-1] == previous, "Continuation rewrote earlier attempts")
        self.assertTrue(attempts[-1]["result"]["launch"] == result["launch"],
                        "Continuation did not retain its new launch evidence exactly")
        self.assertEqual(attempts[-1]["result_digest"], REVIEW.digest(result))
        before = (self.store / "ledger.json").read_bytes()
        self.call("resume")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Plain resume created or changed continuation work")

    def test_continuation_requires_sanitized_explicit_closed_authorization(self):
        self.prepare()
        origin = self.interrupted_team()
        for name, diagnostic in (("authorization", "continuation_authorization_required"),
                                  ("sanitized", "sanitization_acknowledgment_required"),
                                  ("continue-from", "continuation_not_requested")):
            with self.subTest(missing=name):
                output, args = self.continuation_args(origin["attempt"])
                position = args.index("--" + name)
                del args[position:position + (1 if name == "sanitized" else 2)]
                self.assert_start_refusal(args, output, diagnostic)
        cases = (
            ("missing-field", lambda value: value.pop("limits"), "invalid_schema"),
            ("extra-field", lambda value: value.update(extra=True), "invalid_schema"),
            ("empty-actor", lambda value: value.update(actor=""), "invalid_string"),
            ("empty-authority", lambda value: value.update(authority=""), "invalid_string"),
            ("empty-limits", lambda value: value.update(limits=""), "invalid_string"),
            ("empty-source", lambda value: value.update(source=""), "invalid_string"),
            ("bad-digest", lambda value: value.update(source_sha256="not-a-digest"), "invalid_digest"),
            ("sensitive-actor", lambda value: value.update(actor=credential_shapes()[0][1]), "sensitive_input"),
        )
        for name, change, diagnostic in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(self.authorization)
                change(invalid)
                self.authorization_path.write_text(json.dumps(invalid))
                output, args = self.continuation_args(origin["attempt"])
                self.assert_start_refusal(args, output, diagnostic)
        self.authorization_path.write_text(json.dumps(self.authorization))
        output, args = self.continuation_args(origin["attempt"])
        before = (self.store / "ledger.json").read_bytes()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                REVIEW.main(["start", "--store", str(self.store), *map(str, args),
                             "--retry", origin["attempt"]])
        assert_no_fixture_output(self, stdout.getvalue() + stderr.getvalue())
        self.assertTrue(caught.exception.code == 2 and stdout.getvalue() == ""
                        and "not allowed with argument" in stderr.getvalue(),
                        "Retry and continuation must be mutually exclusive CLI choices")
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "CLI usage refusal changed the ledger")
        self.assertFalse(os.path.lexists(output))
        ReviewTests.assert_lock_released(self)
        _, request = self.continue_from(origin["attempt"])
        self.assertTrue(request["request"]["continuation"]["authorization"] == self.authorization,
                        "Valid explicit authorization was not retained")

    def test_continuation_requires_latest_interrupted_observed_qualified_lead(self):
        for state in ("pending", "failed", "cancelled", "pass", "changes-required",
                      "null-run", "unqualified", "interrupted"):
            with self.subTest(state=state):
                self.store = self.root / ("eligibility-" + state)
                self.prepare()
                _, request = self.start()
                result = self.result(request, state if state in REVIEW.TERMINAL else "interrupted")
                if state in {"null-run", "unqualified"}:
                    for row in result["coverage"]:
                        row.update(state="unresolved", owners=[], evidence=[])
                    result["identity"] = {"basis": "unknown", "reviewer": None, "model": None, "evidence": ""}
                    result["launch"] = None
                    if state == "null-run":
                        result["run_id"] = None
                if state != "pending":
                    self.record(result)
                output, args = self.continuation_args(result["attempt"])
                if state == "interrupted":
                    self.call("start", *args)
                    self.assertTrue(output.is_file(), "Eligible interrupted lead did not export continuation")
                    output, args = self.continuation_args(result["attempt"])
                self.assert_start_refusal(args, output, "invalid_continuation")
        self.store = self.root / "wrong-origin"
        self.prepare()
        origin = self.interrupted_team()
        for identity, slot in (("unknown-attempt", "first"), (origin["attempt"], "second")):
            output, args = self.continuation_args(identity, slot)
            self.assert_start_refusal(args, output, "invalid_continuation")
        self.continue_from(origin["attempt"])

    def test_continuation_refuses_changed_candidate_and_dirty_source(self):
        self.prepare()
        origin = self.interrupted_team()
        old = copy.deepcopy(self.ledger()["candidates"][0])
        content = (self.repo / "content.txt").read_bytes()
        (self.repo / "content.txt").write_bytes(content + b"dirty\n")
        output, args = self.continuation_args(origin["attempt"])
        self.assert_start_refusal(args, output, "dirty_candidate")
        (self.repo / "content.txt").write_bytes(content)  # Only this owned fixture file.
        self.spec["questions"].append("Genuinely expanded fixture review scope.")
        self.save_inputs()
        summary = self.call("supersede", "--expect", self.candidate, "--reason", "Expanded scope",
            "--repo", self.repo, "--base", self.base, "--head", self.head,
            "--inputs", self.inputs, "--sanitized")[1]
        output, args = self.continuation_args(origin["attempt"])
        self.assert_start_refusal(args, output, "stale_candidate")
        self.candidate = summary["candidate"]
        output, args = self.continuation_args(origin["attempt"])
        self.assert_start_refusal(args, output, "invalid_continuation")
        self.assertTrue(self.ledger()["candidates"][0] == old, "Supersession changed retained origin")
        _, fresh = self.start()
        self.assertTrue(fresh["continuation_history"] == [] and fresh["request"]["continuation"] is None,
                        "Successor candidate inherited prior review work")
        self.record(self.result(fresh))

    def test_continuation_retains_exact_researchers_and_actual_lead_session(self):
        self.prepare()
        origin = self.interrupted_team()
        _, request = self.continue_from(origin["attempt"])
        original = self.result(request)
        cases = (
            ("old-request", lambda value: value.update(request_digest=origin["request_digest"]), "result_binding_mismatch"),
            ("lead-adapter", lambda value: value.update(adapter="another-adapter"), "result_binding_mismatch"),
            ("lead-session", lambda value: value.update(run_id="/fixture/other-lead"), "invalid_researcher_parent"),
            ("child-parent", lambda value: value["researchers"][0].update(parent_run_id="/fixture/other-parent"),
             "invalid_researcher_parent"),
            ("child-id", lambda value: value["researchers"][0].update(id="renamed-child"), "continuation_result_mismatch"),
            ("child-adapter", lambda value: value["researchers"][0].update(adapter="another-adapter"), "continuation_result_mismatch"),
            ("child-run", lambda value: value["researchers"][0].update(run_id="/fixture/another-child"), "continuation_result_mismatch"),
            ("child-status", lambda value: value["researchers"][0].update(status="failed"), "continuation_result_mismatch"),
            ("child-identity", lambda value: value["researchers"][0]["identity"].update(evidence="different observation"),
             "continuation_result_mismatch"),
            ("child-launch", lambda value: value["researchers"][0]["launch"].update(source="different launch reference"),
             "continuation_result_mismatch"),
            ("child-report", lambda value: value["researchers"][0]["report"].update(source="different report"),
             "continuation_result_mismatch"),
            ("child-report-hash", lambda value: value["researchers"][0]["report"].update(source_sha256="0" * 64),
             "continuation_result_mismatch"),
            ("drop-child", lambda value: value["researchers"].pop(), "continuation_result_mismatch"),
            ("reorder-children", lambda value: value["researchers"].reverse(), "continuation_result_mismatch"),
            ("new-child", lambda value: self.researcher(value, "new-child"), "continuation_result_mismatch"),
        )
        for name, change, diagnostic in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                change(invalid)
                self.assert_record_refusal(invalid, diagnostic)
        reparented = copy.deepcopy(original)
        reparented["run_id"] = "/fixture/forked-lead"
        for child in reparented["researchers"]:
            child["parent_run_id"] = reparented["run_id"]
        self.assert_record_refusal(reparented, "continuation_result_mismatch")
        self.record(original)
        self.assertTrue(self.attempt(original["attempt"])["result"] == original,
                        "Valid retained researchers were not recorded unchanged")

    def test_continuation_preserves_known_lead_identity_and_can_gain_served_observation(self):
        for known in (False, True):
            with self.subTest(known=known):
                self.store = self.root / ("known" if known else "unknown")
                self.prepare()
                _, first = self.start()
                origin = self.result(first, "interrupted")
                if known:
                    origin["identity"]["model"] = "example-model"
                for row in origin["coverage"]:
                    row.update(state="unresolved", owners=[], evidence=[])
                self.record(origin)
                _, request = self.continue_from(origin["attempt"])
                preserved = self.result(request, "interrupted")
                for row in preserved["coverage"]:
                    row.update(state="unresolved", owners=[], evidence=[])
                cases = [("reviewer", lambda value: value["identity"].update(reviewer="another-reviewer")),
                         ("basis", lambda value: value["identity"].update(basis="operator_attestation")),
                         ("launch", lambda value: value.update(launch=None)),
                         ("session", lambda value: value.update(run_id="/fixture/another-session"))]
                if known:
                    cases += [("erased-model", lambda value: value["identity"].update(model=None)),
                              ("changed-model", lambda value: value["identity"].update(model="different-model"))]
                for name, change in cases:
                    with self.subTest(case=name):
                        invalid = copy.deepcopy(preserved)
                        change(invalid)
                        self.assert_record_refusal(invalid, "continuation_result_mismatch")
                completed = self.result(request)
                completed["identity"]["model"] = "example-model"
                self.record(completed)
                self.assertTrue(self.attempt(origin["attempt"])["result"] == origin,
                                "New observation rewrote the original lead identity")

    def test_new_contradictory_observation_remains_nonpassing_and_not_continuable(self):
        self.prepare()
        origin = self.interrupted_team()
        _, request = self.continue_from(origin["attempt"])
        contradictory = self.result(request, "interrupted")
        contradictory["identity"]["model"] = "different-served-model"
        for row in contradictory["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        invalid = copy.deepcopy(contradictory)
        invalid["verdict"] = "pass"
        self.assert_record_refusal(invalid, "incomplete_team_pass")
        summary = self.record(contradictory)
        self.assertFalse(summary["clean_review_ready"])
        self.assertIn({"slot": "first", "code": "team_incomplete"}, summary["blockers"])
        output, args = self.continuation_args(contradictory["attempt"])
        self.assert_start_refusal(args, output, "invalid_continuation")
        self.assertTrue(self.attempt(contradictory["attempt"])["result"] == contradictory,
                        "Contradictory native observation was hidden or rewritten")

    def test_unobserved_continuation_launch_can_close_honestly_but_cannot_continue(self):
        for verdict in ("failed", "cancelled", "interrupted"):
            with self.subTest(verdict=verdict):
                self.store = self.root / ("unobserved-" + verdict)
                self.prepare()
                origin = self.interrupted_team()
                previous = self.attempt(origin["attempt"])
                _, request = self.continue_from(origin["attempt"])
                result = self.result(request, verdict)
                result.update(run_id=None, launch=None, researchers=[],
                              identity={"basis": "unknown", "reviewer": None, "model": None, "evidence": ""})
                for row in result["coverage"]:
                    row.update(state="unresolved", owners=[], evidence=[])
                for name, change in (
                    ("observed-identity", lambda value: value.update(identity=copy.deepcopy(origin["identity"]))),
                    ("observed-launch", lambda value: value.update(launch=copy.deepcopy(origin["launch"]))),
                ):
                    with self.subTest(case=name):
                        invalid = copy.deepcopy(result)
                        change(invalid)
                        self.validate_result(invalid)  # Ordinary nonpass shape; continuation must refuse the claim.
                        self.assert_record_refusal(invalid, "continuation_result_mismatch")
                # These claims fail ordinary team validation before the redundant
                # continuation-specific unobserved-runtime restrictions.
                with self.subTest(case="declared-children"):
                    invalid = copy.deepcopy(result)
                    invalid["researchers"] = copy.deepcopy(origin["researchers"])
                    self.assert_record_refusal(invalid, "invalid_researcher_parent")
                with self.subTest(case="reviewed-row"):
                    invalid = copy.deepcopy(result)
                    invalid["coverage"][0].update(
                        state="reviewed", owners=["lead"],
                        evidence=[{"owner": "lead", **{key: invalid["provenance"][key]
                                                       for key in ("source", "source_sha256")}}])
                    self.assert_record_refusal(invalid, "unsubstantiated_coverage")
                summary = self.record(result)
                self.assertTrue(summary["blockers"] == [
                    {"slot": "first", "code": "required_" + verdict},
                    {"slot": "first", "code": "team_incomplete"},
                    {"slot": "second", "code": "required_not-started"}],
                    "Unobserved continuation incorrectly supplied readiness evidence")
                self.assertFalse(summary["clean_review_ready"])
                self.assertTrue(self.attempt(origin["attempt"]) == previous,
                                "Unobserved closure changed the observed origin")
                output, args = self.continuation_args(result["attempt"])
                self.assert_start_refusal(args, output, "invalid_continuation")
                _, retry = self.start(retry=result["attempt"])
                self.assertTrue(retry["continuation_history"] == [] and retry["request"]["continuation"] is None,
                                "Ordinary retry inherited an unobserved session claim")
                self.record(self.result(retry))

    def test_ordinary_retry_and_cross_team_reuse_remain_duplicate_identity(self):
        for mode in ("retry", "other-team", "other-candidate"):
            with self.subTest(mode=mode):
                self.store = self.root / ("unlinked-" + mode)
                self.prepare()
                origin = self.interrupted_team()
                old = copy.deepcopy(self.ledger()["candidates"][0])
                if mode == "other-candidate":
                    self.spec["questions"].append("Distinct successor scope.")
                    self.save_inputs()
                    summary = self.call("supersede", "--expect", self.candidate, "--reason", "New scope",
                        "--repo", self.repo, "--base", self.base, "--head", self.head,
                        "--inputs", self.inputs, "--sanitized")[1]
                    self.candidate = summary["candidate"]
                if mode == "retry":
                    _, request = self.start(retry=origin["attempt"])
                else:
                    _, request = self.start("second" if mode == "other-team" else "first")
                fresh = self.result(request)
                reused = copy.deepcopy(fresh)
                reused["run_id"] = origin["run_id"]
                reused["researchers"] = copy.deepcopy(origin["researchers"])
                self.validate_result(reused)  # Local shape is valid; history must reject reuse.
                self.assert_record_refusal(reused, "duplicate_identity")
                self.record(fresh)
                if mode == "other-candidate":
                    self.assertTrue(self.ledger()["candidates"][0] == old,
                                    "Unlinked successor reused or changed origin records")

    def test_continuation_chain_exports_exact_predecessors_and_rejects_history_tampering(self):
        self.spec["team_review"]["max_researchers"] = 2
        self.save_inputs()
        self.prepare()
        _, peer = self.start("second")
        self.record(self.result(peer))
        origin = self.interrupted_team()
        first = self.attempt(origin["attempt"])
        _, request = self.continue_from(origin["attempt"])
        partial = self.result(request, "interrupted")
        for row in partial["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.record(partial)
        second = self.attempt(partial["attempt"])
        output, args = self.continuation_args(origin["attempt"])
        self.assert_start_refusal(args, output, "invalid_continuation")
        _, final_request = self.continue_from(partial["attempt"])
        self.assertTrue(final_request["continuation_history"] == [first, second],
                        "Continuation chain export skipped, reordered or included unrelated attempts")
        summary = self.record(self.result(final_request))
        self.assertTrue(summary["clean_review_ready"], "Valid linked reuse did not complete the team")
        before, ledger = (self.store / "ledger.json").read_bytes(), self.ledger()
        cases = (
            ("combined-retry", lambda value: value["candidates"][0]["attempts"][2].update(retry=origin["attempt"]),
             "invalid_continuation"),
            ("wrong-result-digest", lambda value: value["candidates"][0]["attempts"][2]["request"]["continuation"].update(result_digest="0" * 64),
             "invalid_continuation"),
            ("peer-origin", lambda value: value["candidates"][0]["attempts"][2]["request"]["continuation"].update(attempt=peer["request"]["attempt"]),
             "invalid_continuation"),
            ("self-cycle", lambda value: value["candidates"][0]["attempts"][2]["request"]["continuation"].update(attempt=partial["attempt"]),
             "invalid_continuation"),
            ("skip-latest", lambda value: value["candidates"][0]["attempts"][3]["request"].update(continuation=copy.deepcopy(second["request"]["continuation"])),
             "invalid_continuation"),
            ("rewritten-original", lambda value: value["candidates"][0]["attempts"][1]["result"]["limitations"].append("altered old report"),
             "result_digest_mismatch"),
        )
        for name, change, diagnostic in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(ledger)
                change(invalid)
                ReviewTests.assert_ledger_shape_refusal(self, before, invalid, diagnostic)
        self.assertTrue(self.attempt(origin["attempt"]) == first
                        and self.attempt(partial["attempt"]) == second,
                        "History checks rewrote original continuation records")

    def test_continuation_coverage_still_requires_qualified_owners_and_lead_assessment(self):
        self.prepare()
        _, first = self.start()
        origin = self.result(first, "interrupted")
        good = self.researcher(origin, "completed")
        failed = self.researcher(origin, "failed")
        failed["status"] = "failed"
        for row in origin["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.record(origin)
        _, request = self.continue_from(origin["attempt"])
        original = self.result(request)
        self.delegate_file(original, good)
        cases = (
            ("missing", lambda value: value["coverage"].pop(), "missing_coverage"),
            ("unresolved", lambda value: value["coverage"][0].update(state="unresolved"), "incomplete_team_pass"),
            ("wrong-report", lambda value: value["coverage"][0]["evidence"][0].update(source_sha256="0" * 64),
             "coverage_report_mismatch"),
            ("failed-owner", lambda value: value["coverage"][0].update(owners=[failed["id"]],
                evidence=[{"owner": failed["id"], **failed["report"]}]), "unsubstantiated_coverage"),
            ("missing-lead", lambda value: value["coverage"][-1].update(owners=[good["id"]],
                evidence=[{"owner": good["id"], **good["report"]}]), "lead_assessment_required"),
        )
        for name, change, diagnostic in cases:
            with self.subTest(case=name):
                invalid = copy.deepcopy(original)
                change(invalid)
                self.assert_record_refusal(invalid, diagnostic)
        self.record(original)
        self.assertTrue(self.attempt(original["attempt"])["result"]["researchers"] == origin["researchers"],
                        "Failed but qualified nonowner disappeared during synthesis")

    def test_continuation_cannot_hide_an_unqualified_retained_researcher(self):
        self.prepare()
        _, first = self.start()
        origin = self.result(first, "interrupted")
        child = self.researcher(origin)
        child.update(status="failed", launch=None, report=None,
                     identity={"basis": "unknown", "reviewer": None, "model": None, "evidence": ""})
        for row in origin["coverage"]:
            row.update(state="unresolved", owners=[], evidence=[])
        self.record(origin)
        _, request = self.continue_from(origin["attempt"])
        result = self.result(request)
        self.assert_record_refusal(result, "incomplete_team_pass")
        hidden = copy.deepcopy(result)
        hidden["researchers"] = []
        self.assert_record_refusal(hidden, "continuation_result_mismatch")
        result["verdict"] = "interrupted"
        summary = self.record(result)
        self.assertIn({"slot": "first", "code": "team_incomplete"}, summary["blockers"])
        self.assertFalse(summary["clean_review_ready"])
        self.assertTrue(self.attempt(result["attempt"])["result"]["researchers"] == [child],
                        "Unqualified original researcher was concealed")

    def test_continuation_retains_findings_and_latest_rejection_after_risk_acceptance(self):
        for verdict in ("pass", "changes-required"):
            with self.subTest(verdict=verdict):
                self.store = self.root / ("findings-" + verdict)
                self.prepare()
                _, first = self.start()
                origin = self.result(first, "interrupted")
                child = self.researcher(origin)
                origin["findings"] = [self.finding()]
                for row in origin["coverage"]:
                    row.update(state="unresolved", owners=[], evidence=[])
                self.record(origin)
                previous = self.attempt(origin["attempt"])
                _, request = self.continue_from(origin["attempt"])
                result = self.result(request, verdict)
                proposal = {"researcher": child["id"], "finding": "original-proposal",
                            **{key: origin["findings"][0][key] for key in
                               ("kind", "severity", "confidence", "recommendation", "rationale")}}
                optional = self.finding()
                optional.update(kind="suggestion", severity=None, recommendation="optional", assessments=[proposal],
                                classification_reason="Lead retained the proposal and explained its optional scope.")
                result["findings"] = [optional]
                unexplained = copy.deepcopy(result)
                unexplained["findings"][0]["classification_reason"] = ""
                self.assert_record_refusal(unexplained, "missing_classification_reason")
                self.record(result)
                _, peer = self.start("second")
                summary = self.record(self.result(peer))
                expected = ([] if verdict == "pass" else
                            [{"slot": "first", "code": "unresolved_review_verdict"}])
                expected.append({"slot": None, "code": "recorded_findings"})
                self.assertEqual(summary["blockers"], expected)
                self.assertFalse(summary["clean_review_ready"])
                record = {"version": 3, "candidate": self.candidate, "id": "accepted-original",
                    "actor": "fixture-operator", "authority": "Explicit fixture risk authority.",
                    "decisions": [{"attempt": origin["attempt"], "finding": "issue", "decision": "accept-risk",
                                   "rationale": "Separately accepted; not erased from the original."}],
                    "provenance": {"importer": "fixture-operator", "source": "fixture-disposition",
                                   "source_sha256": REVIEW.sha(b"disposition")}}
                path = self.root / "v3-disposition.json"
                attempts = self.ledger()["candidates"][-1]["attempts"]
                finding_pairs = {(attempt["id"], finding["id"])
                                 for attempt in attempts if attempt["result"]
                                 for finding in attempt["result"]["findings"]}
                peer_attempt = peer["request"]["attempt"]
                self.assertTrue((origin["attempt"], "issue") in finding_pairs
                                and (result["attempt"], "issue") in finding_pairs
                                and (peer_attempt, "issue") not in finding_pairs
                                and any(attempt["id"] == peer_attempt for attempt in attempts),
                                "Wrong-attempt control must use a real finding and a real unrelated attempt")
                wrong_attempt = copy.deepcopy(record)
                wrong_attempt["id"] = "wrong-attempt"
                wrong_attempt["decisions"][0]["attempt"] = peer_attempt
                path.write_text(json.dumps(wrong_attempt))
                input_before = path.read_bytes()
                ledger_before = (self.store / "ledger.json").read_bytes()
                entries_before = {entry.name for entry in self.store.iterdir()}
                code, rejected = self.call(
                    "disposition", "--expect", self.candidate, "--disposition", path,
                    "--sanitized", "--authorize-accepted-risk", success=False)
                self.assertTrue(code == 1 and rejected == {"error": "unknown_disposition_finding"},
                                "A real finding under the wrong attempt must receive the exact membership refusal")
                self.assertTrue(path.read_bytes() == input_before
                                and (self.store / "ledger.json").read_bytes() == ledger_before
                                and self.ledger()["candidates"][-1]["attempts"] == attempts
                                and {entry.name for entry in self.store.iterdir()} == entries_before,
                                "Rejected wrong-attempt disposition must preserve input, ledger, attempts and store paths")
                ReviewTests.assert_lock_released(self)
                continuation_record = copy.deepcopy(record)
                continuation_record["id"] = "accepted-continuation"
                continuation_record["decisions"][0].update(
                    attempt=result["attempt"],
                    rationale="Accept only this continuation's optional finding.")
                path.write_text(json.dumps(continuation_record))
                summary = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                    "--sanitized", "--authorize-accepted-risk")[1]
                self.assertEqual(summary["blockers"], expected)
                self.assertEqual(summary["state"], "changes-required")
                self.assertFalse(summary["accepted_with_findings"],
                                 "Accepting a same-id continuation finding accepted the origin's finding")
                self.assertFalse(summary["clean_review_ready"])
                self.assertFalse(summary["publication_authority"])
                wrong_version = copy.deepcopy(record)
                wrong_version["version"] = 2
                path.write_text(json.dumps(wrong_version))
                before = (self.store / "ledger.json").read_bytes()
                rejected = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                     "--sanitized", "--authorize-accepted-risk", success=False)[1]
                self.assertTrue(rejected == {"error": "disposition_binding_mismatch"},
                                "V3 disposition accepted a different candidate version")
                self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                                "Rejected disposition changed the ledger")
                ReviewTests.assert_lock_released(self)
                path.write_text(json.dumps(record))
                summary = self.call("disposition", "--expect", self.candidate, "--disposition", path,
                                    "--sanitized", "--authorize-accepted-risk")[1]
                self.assertEqual(summary["blockers"], expected)
                self.assertEqual(summary["state"], "accepted-with-findings" if verdict == "pass" else "changes-required")
                self.assertEqual(summary["accepted_with_findings"], verdict == "pass")
                self.assertFalse(summary["clean_review_ready"])
                self.assertFalse(summary["publication_authority"])
                self.assertEqual(self.call("status", "--require-ready", success=False, failure_code=2)[0], 2)
                self.assertTrue(self.attempt(origin["attempt"]) == previous,
                                "Synthesis or risk acceptance rewrote original findings")
                self.assertTrue(self.attempt(result["attempt"])["result"]["findings"][0]["assessments"] == [proposal],
                                "Continued lead classification flattened the original proposal")

    def test_continuation_checkpoint_export_and_lost_response_preserve_history(self):
        self.prepare()
        origin = self.interrupted_team()
        previous = self.attempt(origin["attempt"])
        output, args = self.continuation_args(origin["attempt"])
        with patch.object(REVIEW.os, "replace", side_effect=OSError("owned checkpoint failure")):
            self.assert_start_refusal(args, output, "invalid_input_or_local_operation")
        self.assertEqual(list(self.store.glob(".checkpoint-*")), [])
        output, args = self.continuation_args(origin["attempt"])
        output.write_bytes(b"owned occupied export")
        self.assertEqual(self.call("start", *args, success=False)[1], {"error": "invalid_input_or_local_operation"})
        self.assertTrue(output.read_bytes() == b"owned occupied export", "Failed export overwrote its destination")
        pending = self.call("resume")[1]["slots"][0]
        self.assertEqual(pending["state"], "in-flight")
        self.assertEqual(pending["continuation_of"], origin["attempt"])
        recovery = self.root / "recovered-continuation.json"
        before = (self.store / "ledger.json").read_bytes()
        self.call("resume", "--attempt", pending["attempt"], "--output", recovery)
        self.assertTrue((self.store / "ledger.json").read_bytes() == before,
                        "Continuation recovery appended another attempt")
        request = json.loads(recovery.read_text())
        self.assertTrue(request["continuation_history"] == [previous], "Recovery lost exact continuation history")
        result = self.result(request)
        original_replace = REVIEW.os.replace

        def replaced_then_failed(source, target):
            original_replace(source, target)
            raise OSError(credential_shapes()[0][1])

        with patch.object(REVIEW.os, "replace", side_effect=replaced_then_failed):
            self.assertEqual(self.record(result, success=False), {"error": "invalid_input_or_local_operation"})
        self.assertEqual(self.call("resume")[1]["slots"][0]["state"], "pass")
        self.assert_record_refusal(result, "result_already_recorded")
        self.assertTrue(self.attempt(origin["attempt"]) == previous, "Recovery changed the interrupted origin")
        self.assertTrue(self.attempt(result["attempt"])["result"] == result, "Lost response erased committed synthesis")

    def test_v3_opt_in_preserves_legacy_histories_and_does_not_carry_approval(self):
        v3_inputs = copy.deepcopy(self.spec)
        for version in (1, 2):
            with self.subTest(version=version):
                self.store = self.root / ("legacy-" + str(version))
                self.spec = copy.deepcopy(v3_inputs)
                if version == 1:
                    self.spec.pop("version")
                    self.spec.pop("team_review")
                    for slot in self.spec["reviewers"]:
                        for key in ("model_selection", "identity_requirement", "researcher_model_overrides"):
                            slot.pop(key)
                else:
                    self.spec["version"] = 2
                self.save_inputs()
                self.prepare()
                for slot in ("first", "second"):
                    _, request = self.start(slot)
                    self.assertNotIn("continuation", request["request"])
                    self.assertNotIn("continuation_history", request)
                    result = ReviewTests.result(self, request) if version == 1 else self.result(request)
                    summary = self.record(result)
                self.assertEqual(summary["state"], "ready")
                self.assertTrue(all("continuation_of" not in slot for slot in summary["slots"]),
                                "Legacy status acquired v3 continuation semantics")
                old = copy.deepcopy(self.ledger()["candidates"][0])
                output, args = self.continuation_args(result["attempt"], "second")
                self.assert_start_refusal(args, output, "continuation_requires_v3")
                self.spec = copy.deepcopy(v3_inputs)
                self.save_inputs()
                summary = self.call("supersede", "--expect", self.candidate, "--reason", "Explicit v3 opt-in",
                    "--repo", self.repo, "--base", self.base, "--head", self.head,
                    "--inputs", self.inputs, "--sanitized")[1]
                self.candidate = summary["candidate"]
                self.assertEqual(summary["state"], "prepared")
                self.assertFalse(summary["clean_review_ready"])
                self.assertEqual(self.ledger()["version"], 3)
                self.assertTrue(self.ledger()["candidates"][0] == old, "V3 opt-in rewrote legacy records")
                legacy = self.call("status", "--candidate", old["digest"])[1]
                self.assertEqual(legacy["state"], "superseded")
                self.assertTrue(all("continuation_of" not in slot for slot in legacy["slots"]),
                                "Historical status was reinterpreted after v3 opt-in")
                _, fresh = self.start()
                self.assertTrue(fresh["continuation_history"] == [] and fresh["request"]["continuation"] is None,
                                "V3 opt-in inherited legacy approvals or runtime claims")
                self.record(self.result(fresh))


class FileOpeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-open-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_default_file_and_json_size_boundaries(self):
        self.assertEqual(REVIEW.MAX_FILE, 8 * 1024 * 1024)
        self.assertEqual(REVIEW.MAX_LEDGER, 32 * 1024 * 1024)
        path = self.root / "bounded.json"
        for limit, reader in ((REVIEW.MAX_FILE, REVIEW.read_bytes), (REVIEW.MAX_LEDGER, REVIEW.read_json)):
            with self.subTest(limit=limit):
                path.write_bytes(b"0" + b" " * (limit - 1))
                result = reader(path)
                if isinstance(result, bytes):
                    self.assertEqual(len(result), limit)
                else:
                    self.assertEqual(result, 0)
                with path.open("ab") as stream:
                    stream.write(b" ")
                with self.assertRaisesRegex(REVIEW.Stop, "^input_too_large$"):
                    reader(path)

    def test_growth_after_descriptor_check_uses_same_size_error(self):
        path = self.root / "growing"
        path.write_bytes(b"x")
        original = REVIEW.open_regular

        @contextlib.contextmanager
        def grow_after_check(path, limit, private):
            with original(path, limit, private) as stream:
                with path.open("ab") as append:
                    append.write(b"x")
                yield stream

        with patch.object(REVIEW, "open_regular", side_effect=grow_after_check):
            with self.assertRaisesRegex(REVIEW.Stop, "^input_too_large$"):
                REVIEW.read_bytes(path, 1)

    def test_serialized_record_size_boundary_preserves_prior_ledger(self):
        ledger = self.root / "ledger.json"
        overhead = len(REVIEW.canonical({"text": ""})) + 1
        value = {"text": "x" * (REVIEW.MAX_LEDGER - overhead)}
        REVIEW.write_new(ledger, value)
        self.assertEqual(ledger.stat().st_size, REVIEW.MAX_LEDGER)
        before = hashlib.sha256(ledger.read_bytes()).hexdigest()
        value["text"] += "x"
        rejected = self.root / "oversized-request.json"
        with self.assertRaisesRegex(REVIEW.Stop, "^ledger_too_large$"):
            REVIEW.write_new(rejected, value)
        self.assertFalse(os.path.lexists(rejected))
        with self.assertRaisesRegex(REVIEW.Stop, "^ledger_too_large$"):
            REVIEW.checkpoint(self.root, value)
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), before)
        self.assertEqual(list(self.root.glob(".checkpoint-*")), [])

    def test_guarded_open_errno_classes_are_static_and_other_errors_stay_generic(self):
        regular = self.root / "regular"
        regular.touch(mode=0o600)
        for operation in (lambda: REVIEW.read_bytes(regular), lambda: REVIEW.fsync_directory(self.root)):
            for number, expected in ((errno.ELOOP, "symlink_path"), (errno.ENOTDIR, "invalid_directory"),
                                     (errno.EACCES, "invalid_input_or_local_operation")):
                with self.subTest(errno=number):
                    output, errors = io.StringIO(), io.StringIO()
                    failure = OSError(number, credential_shapes()[0][1], str(regular))
                    with patch.object(REVIEW.os, "open", side_effect=failure), \
                         patch.object(REVIEW, "execute", side_effect=lambda _: operation()), \
                         contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = REVIEW.main(["status", "--store", str(self.root)])
                    assert_no_fixture_output(self, output.getvalue() + errors.getvalue())
                    self.assertEqual(code, 1)
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(json.loads(errors.getvalue()), {"error": expected})

    def test_nonregular_paths_are_rejected_before_open(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        regular = self.root / "regular"
        regular.touch(mode=0o600)
        with patch.object(REVIEW.os, "open", side_effect=AssertionError("must reject before open")):
            for path in (fifo, self.root):
                with self.subTest(path=path.name), self.assertRaisesRegex(REVIEW.Stop, "^invalid_file$"):
                    REVIEW.read_bytes(path)
            for path in (fifo, regular):
                with self.subTest(path=path.name), self.assertRaisesRegex(REVIEW.Stop, "^invalid_directory$"):
                    REVIEW.fsync_directory(path)

    def test_replacement_races_never_block_before_descriptor_checks(self):
        # Replace only a fixture-owned leaf immediately before the real open,
        # after path inspection. Each child has a strict timeout and no writer.
        # The real OS errno is an oracle independent of the controller's error
        # translation: O_DIRECTORY/O_NOFOLLOW outcomes vary across platforms.
        # The separate static errno-class test pins each exact translation.
        program = (
            "import errno, importlib.util, os, pathlib, sys\n"
            "spec=importlib.util.spec_from_file_location('review', sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
            "operation, replacement = sys.argv[3:]\n"
            "root=pathlib.Path(sys.argv[2]); target=root/'target'; backup=root/'original'\n"
            "if operation == 'directory': target.mkdir(mode=0o700)\n"
            "else: target.touch(mode=0o600)\n"
            "if operation == 'lock': target.rename(root/'lock'); target=root/'lock'\n"
            "original_open=os.open; reached=[]; failures=[]\n"
            "def unexpected_flock(*args): raise AssertionError('replacement must be refused before flock')\n"
            "if operation == 'lock': module.fcntl.flock=unexpected_flock\n"
            "def replace_then_open(path, flags, *args, **kwargs):\n"
            " if pathlib.Path(path) == target and not reached:\n"
            "  reached.append(flags); target.rename(backup)\n"
            "  if replacement == 'fifo': os.mkfifo(target, 0o600)\n"
            "  elif replacement == 'symlink': target.symlink_to(backup)\n"
            "  elif replacement == 'oversize': target.write_bytes(b'x'); target.chmod(0o600)\n"
            "  else: target.touch(mode=0o600); target.chmod(0o644)\n"
            " try: return original_open(path, flags, *args, **kwargs)\n"
            " except OSError as error: failures.append(error.errno); raise\n"
            "os.open=replace_then_open\n"
            "try:\n"
            " if operation == 'directory': module.fsync_directory(target)\n"
            " elif operation == 'lock':\n"
            "  with module.locked(root): raise AssertionError('accepted replaced lock')\n"
            " else: module.read_bytes(target, 0, private=True)\n"
            "except module.Stop as error:\n"
            " expected=({errno.ELOOP:'symlink_path', errno.ENOTDIR:'invalid_directory'}[failures[0]] if failures else\n"
            "  'unsafe_permissions' if replacement == 'permissions' else 'input_too_large' if replacement == 'oversize' else 'invalid_file')\n"
            " assert str(error) == expected\n"
            "else: raise AssertionError('accepted replacement')\n"
            "assert len(reached) == 1\n"
            "assert reached[0] & os.O_NONBLOCK and reached[0] & os.O_NOFOLLOW\n"
        )
        for operation in ("read", "lock", "directory"):
            replacements = ("fifo", "symlink") if operation == "directory" else ("fifo", "symlink", "oversize", "permissions")
            for replacement in replacements:
                with self.subTest(operation=operation, replacement=replacement):
                    fixture = self.root / (operation + "-" + replacement)
                    fixture.mkdir(mode=0o700)
                    result = subprocess.run([sys.executable, "-I", "-B", "-c", program,
                        str(SCRIPT), str(fixture), operation, replacement],
                        cwd=self.root, capture_output=True, timeout=2)
                    self.assertEqual(result.returncode, 0, "owned replacement-race child failed")


if __name__ == "__main__":
    unittest.main()
