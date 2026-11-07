"""Owned Unix files + in-memory fake provider; no executable/network/provider.

One bounded fork checks real interprocess flock. Every other case is synchronous
and uses only owned temporary files. This module imports the candidate on load.
"""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import stat
import unittest
from unittest.mock import patch
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets/agent-assets/skills/publish-proposals/scripts/publication.py"
SPEC = importlib.util.spec_from_file_location("publication_core", SCRIPT)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)
CASES = json.loads((ROOT / "tests/fixtures/proposal-publication/cases.json").read_bytes())


class Crash(BaseException):
    pass


class FixturePath(os.PathLike):
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


class FakeAdapter:
    identity = {"name": "offline-fixture", "version": 1}

    def __init__(self, destination):
        self.destination = copy.deepcopy(destination)
        self.artifacts = []
        self.writes = 0
        self.reads = 0
        self.fail = None
        self.discovery_complete = True
        self.discovery_consistent = True
        self.duplicate = False

    def read_context(self):
        self.reads += 1
        return copy.deepcopy(self.destination)

    def find(self, request):
        self.reads += 1
        matches = [copy.deepcopy(item) for item in self.artifacts if item["marker"] == request["marker"]]
        if self.duplicate and matches:
            matches.append(copy.deepcopy(matches[0]))
        return {"complete": self.discovery_complete, "consistent": self.discovery_consistent, "matches": matches}

    def create(self, request):
        if self.fail == "before-write":
            raise Crash()
        self.writes += 1
        number = str(self.writes)
        destination = request["destination"]
        artifact = {name: copy.deepcopy(request[name]) for name in ("marker", "title", "body", "destination")}
        artifact.update(id=str(10000 + self.writes), number=number,
                        url="https://" + destination["host"] + "/" + destination["owner"] + "/" + destination["repository"] + "/issues/" + number)
        self.artifacts.append(copy.deepcopy(artifact))
        if self.fail == "after-write":
            raise Crash()
        if self.fail == "timeout-after-write":
            raise TimeoutError("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")
        if self.fail == "wrong-url":
            artifact["url"] = "https://other.example.invalid/issues/1"
        if self.fail == "wrong-target":
            artifact["destination"]["repository_id"] = "9009"
        if self.fail == "extra-field":
            artifact["unexpected"] = "SYNTHETIC_SECRET_MUST_NOT_ESCAPE"
        return artifact

    def read(self, artifact):
        self.reads += 1
        if self.fail == "readback":
            raise ValueError("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")
        matches = [item for item in self.artifacts if item["id"] == artifact["id"]]
        return copy.deepcopy(matches[0]) if len(matches) == 1 else None


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="proposal-core-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.store = self.root / "store"
        self.proposals = copy.deepcopy(CASES["proposals"])
        self.adapter = FakeAdapter(self.proposals["destination"])

    def prepare(self):
        result = CORE.prepare(self.store, self.proposals, "synthetic prepare request")
        self.expected = result["plan_digest"]
        return result

    def apply(self):
        return CORE.apply(self.store, self.expected, "synthetic exact filing approval", self.adapter)

    def reconcile(self):
        return CORE.reconcile(self.store, self.expected, "synthetic read-only reconciliation request", self.adapter)

    def receipt(self):
        return json.loads((self.store / "receipt.json").read_bytes())

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.store.iterdir() if p.is_file()}

    def expect_stop(self, code, function, *args):
        with self.assertRaisesRegex(CORE.Stop, "^" + code + "$"):
            function(*args)

    @contextlib.contextmanager
    def checkpoint_fault(self, checkpoint_number, point):
        """Inject once around actual owned syscalls, never replace checkpoint."""
        self.assertIn(checkpoint_number, (1, 2))
        self.assertIn(point, ("file-before", "file-after", "replace-before",
                              "replace-after", "directory-before", "directory-after"))
        original_sync, original_replace = os.fsync, os.replace
        progress = {"files": 0, "hits": 0, "trace": [], "staged": []}
        directory = self.store.stat()

        def observe(kind, when):
            progress["trace"].append((progress["files"], kind, when))
            if progress["files"] == checkpoint_number and point == kind + "-" + when:
                progress["hits"] += 1
                raise OSError("synthetic checkpoint boundary failure")

        def sync(descriptor):
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode):
                matches = [path for path in self.store.iterdir()
                           if path.name.startswith(".checkpoint-")
                           and (path.stat().st_dev, path.stat().st_ino) == (info.st_dev, info.st_ino)]
                self.assertEqual(len(matches), 1, "fsync did not target the owned stage")
                progress["files"] += 1
                progress["staged"].append(matches[0])
                kind = "file"
            else:
                self.assertTrue(stat.S_ISDIR(info.st_mode), "unexpected fsync descriptor type")
                self.assertEqual((info.st_dev, info.st_ino), (directory.st_dev, directory.st_ino))
                kind = "directory"
            observe(kind, "before")
            original_sync(descriptor)
            observe(kind, "after")

        def replace(source, destination):
            self.assertEqual(Path(source).parent, self.store)
            self.assertTrue(Path(source).name.startswith(".checkpoint-"))
            self.assertEqual(Path(destination), self.store / "receipt.json")
            observe("replace", "before")
            original_replace(source, destination)
            observe("replace", "after")

        with patch.object(CORE.os, "fsync", side_effect=sync), patch.object(CORE.os, "replace", side_effect=replace):
            yield progress

    def test_prepare_requires_request_and_never_calls_provider(self):
        self.expect_stop("invalid_text", CORE.prepare, self.store, self.proposals, "")
        self.assertFalse(self.store.exists())
        result = self.prepare()
        self.assertFalse(result["provider_available"])
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        self.assertEqual(set(self.snapshot()), CORE.FILES)

    def test_validation_is_store_free_and_workflow_neutral(self):
        CORE.proposal_check(self.proposals)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn("reflection_session", self.proposals)

    def test_owned_permissions_and_existing_store_not_reused(self):
        self.prepare()
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        for item in self.store.iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)
        before = self.snapshot()
        self.expect_stop("store_exists", CORE.prepare, self.store, self.proposals, "another request")
        self.assertEqual(self.snapshot(), before)

    def test_exact_approval_and_reference_required_before_any_provider_call(self):
        self.prepare()
        before = self.snapshot()
        self.expect_stop("approval_mismatch", CORE.apply, self.store, "0" * 64, "request", self.adapter)
        self.expect_stop("invalid_text", CORE.apply, self.store, self.expected, "", self.adapter)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_no_provider_selectable(self):
        self.prepare()
        before = self.snapshot()
        self.expect_stop("provider_unavailable", CORE.apply, self.store, self.expected, "request")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.adapter.writes, 0)

    def test_inline_creation_order_fixed_links_and_complete_receipt(self):
        self.prepare()
        result = self.apply()
        self.assertTrue(result["complete"])
        self.assertEqual(self.adapter.writes, 2)
        first, second = self.adapter.artifacts
        self.assertEqual(first["title"], "Synthetic foundational proposal")
        self.assertIn(first["url"], second["body"])
        self.assertNotIn("<issue-url:", second["body"])
        for artifact in self.adapter.artifacts:
            self.assertIn("## Detail", artifact["body"])
            self.assertIn(artifact["marker"], artifact["body"])
        self.assertEqual([e["event"] for e in self.receipt()["history"]], ["intent", "result", "verified"] * 2)
        self.apply()
        self.assertEqual(self.adapter.writes, 2)

    def test_plan_snapshots_input_and_has_explicit_typed_link_preview(self):
        plan = CORE.make_plan(self.proposals, "request")
        before = CORE.canonical(plan)
        self.proposals["proposals"][0]["detail"] = "Changed authoring input"
        self.assertEqual(CORE.canonical(plan), before)
        self.assertIn("<issue-url:create-", plan["operations"][1]["body"])
        CORE.plan_check(plan)

    def test_changed_plan_or_core_is_not_reapproved_by_a_digest_alone(self):
        self.prepare()
        plan = json.loads((self.store / "plan.json").read_bytes())
        plan["provider"]["version"] = 2
        del plan["digest"]
        plan["digest"] = CORE.digest(plan)
        (self.store / "plan.json").write_bytes(CORE.canonical(plan))
        self.expect_stop("provider_unavailable", CORE.status, self.store)

    def test_boolean_plan_versions_rejected_with_original_or_recomputed_digest(self):
        for location in ("plan", "provider"):
            for recompute in (False, True):
                with self.subTest(location=location, recompute=recompute):
                    self.store = self.root / (location + "-" + str(recompute))
                    self.adapter = FakeAdapter(self.proposals["destination"])
                    self.prepare()
                    plan = json.loads((self.store / "plan.json").read_bytes())
                    (plan if location == "plan" else plan["provider"])["version"] = True
                    if recompute:
                        plan["digest"] = CORE.digest({key: value for key, value in plan.items() if key != "digest"})
                    (self.store / "plan.json").write_bytes(CORE.canonical(plan))
                    before = self.snapshot()
                    code = "unsupported_version" if location == "plan" else "provider_unavailable"
                    self.expect_stop(code, CORE.status, self.store)
                    for call in (CORE.apply, CORE.reconcile):
                        self.expect_stop(code, call, self.store, plan["digest"], "synthetic authority", self.adapter)
                    self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
                    self.assertEqual(self.snapshot(), before)

    def test_stored_digest_and_canonical_operation_must_both_match(self):
        for recompute in (False, True):
            with self.subTest(recompute=recompute):
                self.store = self.root / ("digest-" + str(recompute))
                self.adapter = FakeAdapter(self.proposals["destination"])
                self.prepare()
                plan = json.loads((self.store / "plan.json").read_bytes())
                plan["operations"][0]["body"] += "Unexpected operation content.\n"
                if recompute:
                    plan["digest"] = CORE.digest({key: value for key, value in plan.items() if key != "digest"})
                (self.store / "plan.json").write_bytes(CORE.canonical(plan))
                before = self.snapshot()
                code = "plan_or_source_changed" if recompute else "plan_digest_mismatch"
                self.expect_stop(code, CORE.apply, self.store, plan["digest"], "synthetic authority", self.adapter)
                self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
                self.assertEqual(self.snapshot(), before)

    def test_separate_saved_proposal_version_is_type_exact(self):
        self.prepare()
        changed = copy.deepcopy(self.proposals)
        changed["version"] = True
        (self.store / "proposals.json").write_bytes(CORE.canonical(changed))
        before = self.snapshot()
        self.expect_stop("proposal_plan_mismatch", CORE.status, self.store)
        for call in (CORE.apply, CORE.reconcile):
            self.expect_stop("proposal_plan_mismatch", call, self.store, self.expected, "synthetic authority", self.adapter)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        self.assertEqual(self.snapshot(), before)

    def test_changed_source_identity_blocks_existing_store(self):
        self.prepare()
        with patch.object(CORE, "source_hash", return_value="f" * 64):
            self.expect_stop("plan_or_source_changed", CORE.status, self.store)

    def test_fixture_data_negative_mutations_before_store(self):
        for case in CASES["invalid_mutations"]:
            with self.subTest(case=case["name"]):
                value = copy.deepcopy(self.proposals)
                target = value
                for part in case["path"][:-1]:
                    target = target[part]
                target[case["path"][-1]] = case["value"]
                self.expect_stop(case["error"], CORE.prepare, self.store, value, "request")
                self.assertFalse(self.store.exists())

    def test_closed_audiences_and_no_implicit_declassification(self):
        self.proposals["destination"]["audience"] = {"kind": "internet", "realm": "internet", "id": "public"}
        self.expect_stop("audience_mismatch", CORE.proposal_check, self.proposals)
        for proposal in self.proposals["proposals"]:
            proposal["sensitivity"] = "public"
        CORE.proposal_check(self.proposals)

    def test_duplicate_and_cyclic_dependencies_rejected(self):
        self.proposals["proposals"][1]["dependencies"] = [self.proposals["proposals"][0]["id"]]
        self.expect_stop("dependency_cycle", CORE.proposal_check, self.proposals)
        self.proposals["proposals"][1]["dependencies"] = []
        self.proposals["proposals"][0]["dependencies"] *= 2
        self.expect_stop("duplicate_dependency", CORE.proposal_check, self.proposals)

    def test_proposal_count_detail_body_and_input_bounds(self):
        original = copy.deepcopy(self.proposals["proposals"][1])
        self.proposals["proposals"] = []
        for index in range(CORE.MAX_PROPOSALS):
            proposal = copy.deepcopy(original)
            proposal["id"] = format(index + 1, "032x")
            self.proposals["proposals"].append(proposal)
        CORE.proposal_check(self.proposals)
        self.proposals["proposals"].append(copy.deepcopy(original))
        self.expect_stop("invalid_list", CORE.proposal_check, self.proposals)
        value = copy.deepcopy(CASES["proposals"])
        value["proposals"][0]["detail"] = "x" * CORE.MAX_DETAIL
        CORE.proposal_check(value)
        value["proposals"][0]["detail"] += "x"
        self.expect_stop("invalid_text", CORE.proposal_check, value)
        self.assertEqual(CORE.parse(b"{}" + b" " * (CORE.MAX_INPUT - 2)), {})
        self.expect_stop("data_too_large", CORE.parse, b" " * (CORE.MAX_INPUT + 1))
        value["proposals"][0]["detail"] = "x" * CORE.MAX_DETAIL
        value["proposals"][0]["steps"] = ["x" * 2048] * 32
        self.expect_stop("invalid_text", CORE.make_plan, value, "request")

    def test_duplicate_keys_float_nonfinite_unicode_and_recursive_data(self):
        for data, code in ((b'{"a":1,"a":2}', "duplicate_key"), (b"1.0", "floating_point_unsupported"),
                           (b"NaN", "invalid_json_number"), (b'"\\ud800"', "invalid_text"),
                           ("{}".encode("utf-16"), "invalid_json")):
            with self.subTest(code=code):
                self.expect_stop(code, CORE.parse, data)
        cyclic = []
        cyclic.append(cyclic)
        self.expect_stop("cyclic_data", CORE.bounded, cyclic, CORE.MAX_INPUT)
        deep = []
        for _ in range(CORE.MAX_DEPTH + 1):
            deep = [deep]
        self.expect_stop("data_bound", CORE.bounded, deep, CORE.MAX_INPUT)
        self.expect_stop("data_bound", CORE.bounded, [None] * CORE.MAX_NODES, CORE.MAX_INPUT)
        self.expect_stop("invalid_json_type", CORE.bounded, object(), CORE.MAX_INPUT)

    def test_context_change_blocks_before_write(self):
        self.prepare()
        self.adapter.destination["account_id"] = "9009"
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.adapter.writes, 0)
        self.assertEqual(self.receipt()["operations"][0]["state"], "blocked")
        self.assertEqual(self.receipt()["history"][0]["event"], "blocked")

    def test_unavailable_provider_version_is_not_a_fallback(self):
        self.prepare()
        self.adapter.identity = {"name": "offline-fixture", "version": 2}
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.adapter.writes, 0)

    def test_boolean_fixture_identity_version_blocks_before_adapter_reads(self):
        self.prepare()
        self.adapter.identity = {"name": "offline-fixture", "version": True}
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        self.assertEqual(self.receipt()["operations"][0]["state"], "blocked")
        self.assertEqual([item["event"] for item in self.receipt()["history"]], ["blocked"])

    def test_existing_artifact_requires_explicit_future_adoption(self):
        self.prepare()
        plan = json.loads((self.store / "plan.json").read_bytes())
        request = CORE.request_for(plan, self.receipt(), plan["operations"][0])
        self.adapter.create(request)
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.adapter.writes, 1)

    def test_incomplete_initial_discovery_never_creates(self):
        self.prepare()
        self.adapter.discovery_complete = False
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.adapter.writes, 0)
        self.adapter.discovery_complete = True
        before, calls = self.snapshot(), (self.adapter.reads, self.adapter.writes)
        self.expect_stop("blocked_plan", self.apply)
        result = self.reconcile()
        self.assertFalse(result["complete"])
        self.assertEqual(result["counts"]["blocked"], 1)
        self.expect_stop("blocked_plan", self.apply)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), calls)

    def test_partial_preflight_block_is_terminal_and_fresh_store_cannot_adopt(self):
        self.prepare()
        original = self.adapter.find

        def incomplete_second(request):
            result = original(request)
            if self.adapter.writes == 1:
                result["complete"] = False
            return result

        with patch.object(self.adapter, "find", side_effect=incomplete_second):
            self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual([row["state"] for row in self.receipt()["operations"]], ["verified", "blocked"])
        first = copy.deepcopy(self.receipt()["operations"][0])
        before, calls = self.snapshot(), (self.adapter.reads, self.adapter.writes)
        self.expect_stop("blocked_plan", self.apply)
        self.assertFalse(self.reconcile()["complete"])
        self.expect_stop("blocked_plan", self.apply)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), calls)
        self.assertEqual(self.receipt()["operations"][0], first)

        # Negative fixture only: a fresh store is not a supported recovery action.
        original_store = self.store
        self.store = self.root / "unsupported-fresh-store"
        self.prepare()
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.receipt()["operations"][0]["state"], "blocked")
        self.assertEqual(self.adapter.writes, 1)
        self.store = original_store
        self.assertEqual(self.snapshot(), before)

    def test_crash_before_call_stays_uncertain_without_nonlaunch_proof(self):
        self.prepare()
        self.adapter.fail = "before-write"
        with self.assertRaises(Crash):
            self.apply()
        self.assertEqual(self.adapter.writes, 0)
        self.assertEqual(self.receipt()["operations"][0]["state"], "attempting")
        self.adapter.fail = None
        self.expect_stop("reconciliation_unresolved", self.reconcile)
        self.assertEqual(self.adapter.writes, 0)
        self.expect_stop("reconciliation_required", self.apply)

    def test_success_before_lost_response_reconciles_without_recreation(self):
        self.prepare()
        self.adapter.fail = "after-write"
        with self.assertRaises(Crash):
            self.apply()
        self.assertEqual(self.adapter.writes, 1)
        self.assertIsNone(self.receipt()["operations"][0]["artifact"])
        self.adapter.fail = None
        before, reads = self.snapshot(), self.adapter.reads
        self.expect_stop("reconciliation_required", self.apply)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (reads, 1))
        self.assertFalse(self.reconcile()["complete"])
        self.assertEqual(self.adapter.writes, 1)
        self.assertEqual(self.receipt()["history"][-1]["event"], "reconciled")
        self.assertEqual([row["state"] for row in self.receipt()["operations"]], ["verified", "planned"])
        first = copy.deepcopy(self.receipt()["operations"][0])
        self.assertTrue(self.apply()["complete"])
        self.assertEqual(self.adapter.writes, 2)
        self.assertEqual(self.receipt()["operations"][0], first)

    def test_timeout_after_write_diagnostics_are_sanitized(self):
        self.prepare()
        self.adapter.fail = "timeout-after-write"
        self.expect_stop("write_or_verification_uncertain", self.apply)
        self.assertEqual(self.adapter.writes, 1)
        self.assertNotIn(b"SYNTHETIC_SECRET", (self.store / "receipt.json").read_bytes())
        self.adapter.fail = None
        self.reconcile()
        self.assertEqual(self.adapter.writes, 1)

    def test_readback_failure_retains_result_and_mapping_together(self):
        self.prepare()
        self.adapter.fail = "readback"
        self.expect_stop("write_or_verification_uncertain", self.apply)
        receipt = self.receipt()
        self.assertEqual(receipt["operations"][0]["state"], "created-unverified")
        self.assertEqual(receipt["history"][-1]["artifact_id"], receipt["operations"][0]["artifact"]["id"])
        self.assertFalse(CORE.status(self.store)["complete"])
        self.adapter.fail = None
        self.reconcile()
        self.assertEqual(self.adapter.writes, 1)

    def test_later_failure_preserves_verified_first_artifact_and_partial_state(self):
        self.prepare()
        original = self.adapter.create

        def fail_second(request):
            if self.adapter.writes == 1:
                self.adapter.fail = "timeout-after-write"
            return original(request)

        with patch.object(self.adapter, "create", side_effect=fail_second):
            self.expect_stop("write_or_verification_uncertain", self.apply)
        receipt = self.receipt()
        first = copy.deepcopy(receipt["operations"][0])
        self.assertEqual(first["state"], "verified")
        self.assertEqual(receipt["operations"][1]["state"], "attempting")
        self.assertFalse(CORE.status(self.store)["complete"])
        self.adapter.fail = None
        self.assertTrue(self.reconcile()["complete"])
        self.assertEqual(self.receipt()["operations"][0], first)
        self.assertEqual(self.adapter.writes, 2)

    def test_result_checkpoint_failure_keeps_durable_intent(self):
        self.prepare()
        checkpoint = CORE.checkpoint

        def fail_result(store, receipt, plan):
            if receipt["history"][-1]["event"] == "result":
                raise OSError("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")
            checkpoint(store, receipt, plan)

        with patch.object(CORE, "checkpoint", side_effect=fail_result):
            self.expect_stop("write_or_verification_uncertain", self.apply)
        self.assertEqual(self.receipt()["history"][-1]["event"], "intent")
        self.assertEqual(self.adapter.writes, 1)
        self.reconcile()
        self.assertEqual(self.adapter.writes, 1)

    def test_intent_checkpoint_failure_never_calls_writer(self):
        self.prepare()
        before = self.snapshot()
        with patch.object(CORE, "checkpoint", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.adapter.writes, 0)
        self.assertEqual(self.snapshot(), before)

    def test_actual_checkpoint_failures_preserve_valid_old_or_new_receipt(self):
        points = ("file-before", "file-after", "replace-before", "replace-after",
                  "directory-before", "directory-after")
        for checkpoint_number in (1, 2):
            for point in points:
                with self.subTest(checkpoint=checkpoint_number, point=point):
                    self.store = self.root / ("checkpoint-" + str(checkpoint_number) + "-" + point)
                    self.adapter = FakeAdapter(self.proposals["destination"])
                    self.prepare()
                    before = self.snapshot()
                    with self.checkpoint_fault(checkpoint_number, point) as progress:
                        if checkpoint_number == 1:
                            with self.assertRaises(OSError):
                                self.apply()
                        else:
                            self.expect_stop("write_or_verification_uncertain", self.apply)
                    self.assertEqual(progress["hits"], 1, "intended fault was not reached")
                    self.assertEqual(progress["files"], checkpoint_number, "unexpected additional checkpoint")
                    kind, when = point.split("-")
                    self.assertEqual(progress["trace"][-1], (checkpoint_number, kind, when))
                    self.assertTrue(all(not path.exists() for path in progress["staged"]), "owned stage was retained after catchable failure")
                    after = self.snapshot()
                    self.assertEqual(set(after), CORE.FILES)
                    for name in CORE.FILES - {"receipt.json"}:
                        self.assertEqual(after[name], before[name])
                    for path in self.store.iterdir():
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertFalse(CORE.status(self.store)["complete"])
                    receipt = self.receipt()
                    replaced = point in {"replace-after", "directory-before", "directory-after"}
                    if checkpoint_number == 1:
                        expected_state = "attempting" if replaced else "planned"
                        expected_history = ["intent"] if replaced else []
                    else:
                        expected_state = "created-unverified" if replaced else "attempting"
                        expected_history = ["intent", "result"] if replaced else ["intent"]
                    self.assertEqual(receipt["operations"][0]["state"], expected_state)
                    self.assertEqual([item["event"] for item in receipt["history"]], expected_history)
                    self.assertEqual(receipt["operations"][0]["artifact"] is not None,
                                     checkpoint_number == 2 and replaced)
                    expected_writes = checkpoint_number - 1
                    self.assertEqual(self.adapter.writes, expected_writes)
                    if expected_state != "planned":
                        reads = self.adapter.reads
                        self.expect_stop("reconciliation_required", self.apply)
                        self.assertEqual(self.snapshot(), after, "blocked apply rewrote uncertain evidence")
                        self.assertEqual((self.adapter.reads, self.adapter.writes), (reads, expected_writes))
                    # No second create: only read-only reconciliation follows.
                    if checkpoint_number == 1 and replaced:
                        self.expect_stop("reconciliation_unresolved", self.reconcile)
                    else:
                        self.assertFalse(self.reconcile()["complete"])
                    self.assertEqual(self.adapter.writes, expected_writes)
                    self.assertEqual(self.receipt()["history"][:len(receipt["history"])], receipt["history"])
                    if expected_state == "planned":
                        self.assertEqual(self.snapshot(), after)

    def test_parent_directory_fsync_failure_never_accepts_store(self):
        parent = self.root.stat()
        for timing in ("before", "after"):
            with self.subTest(timing=timing):
                self.store = self.root / ("parent-fsync-" + timing)
                self.adapter = FakeAdapter(self.proposals["destination"])
                authority = "synthetic interrupted prepare request"
                self.expected = CORE.make_plan(self.proposals, authority)["digest"]
                original_sync = os.fsync
                hits = []

                def sync(descriptor):
                    info = os.fstat(descriptor)
                    matches = (stat.S_ISDIR(info.st_mode)
                               and (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino))
                    if matches and timing == "before":
                        hits.append("before")
                        raise OSError("synthetic parent fsync failure")
                    original_sync(descriptor)
                    if matches and timing == "after":
                        hits.append("after")
                        raise OSError("synthetic parent fsync failure")

                with patch.object(CORE.os, "fsync", side_effect=sync):
                    with self.assertRaises(OSError):
                        CORE.prepare(self.store, self.proposals, authority)
                self.assertEqual(hits, [timing], "intended parent fsync fault was not reached")
                info = self.store.lstat()
                self.assertTrue(stat.S_ISDIR(info.st_mode))
                self.assertEqual(info.st_uid, os.getuid())
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual(list(self.store.iterdir()), [], "failed preparation left an accepted store")
                for operation in (lambda: CORE.status(self.store), self.apply, self.reconcile):
                    with self.assertRaises(FileNotFoundError):
                        operation()
                    self.assertEqual(list(self.store.iterdir()), [])
                    self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
                    self.assertEqual(self.adapter.artifacts, [])
                self.expect_stop("store_exists", CORE.prepare, self.store, self.proposals, "another request")
                self.assertEqual(list(self.store.iterdir()), [], "incomplete initialization was repaired")

    def test_parent_directory_fsync_precedes_store_acceptance(self):
        parent = self.root.stat()
        original_sync = os.fsync
        events = []

        def sync(descriptor):
            info = os.fstat(descriptor)
            if (stat.S_ISDIR(info.st_mode)
                    and (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino)):
                self.assertEqual(events, [], "parent barrier was not the first fsync")
                self.assertEqual(list(self.store.iterdir()), [], "files existed before parent barrier")
                self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
                events.append("parent-before")
                original_sync(descriptor)
                events.append("parent-after")
                return
            self.assertEqual(events[:2], ["parent-before", "parent-after"])
            if stat.S_ISREG(info.st_mode):
                matches = [path.name for path in self.store.iterdir()
                           if (path.stat().st_dev, path.stat().st_ino) == (info.st_dev, info.st_ino)]
                self.assertEqual(len(matches), 1, "fsync did not target an owned store file")
                self.assertIn(matches[0], CORE.FILES)
                events.append("file-" + matches[0])
            else:
                self.assertTrue(stat.S_ISDIR(info.st_mode))
                store_info = self.store.stat()
                self.assertEqual((info.st_dev, info.st_ino), (store_info.st_dev, store_info.st_ino))
            original_sync(descriptor)

        with patch.object(CORE.os, "fsync", side_effect=sync):
            result = self.prepare()
        self.assertEqual(events, ["parent-before", "parent-after", "file-lock",
                                  "file-proposals.json", "file-plan.json", "file-receipt.json"])
        self.assertEqual(set(self.snapshot()), CORE.FILES)
        for path in self.store.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        CORE.plan_check(plan)
        self.assertEqual(result, {"plan_digest": plan["digest"], "operations": 2, "provider_available": False})
        status = CORE.status(self.store)
        self.assertFalse(status["complete"])
        self.assertEqual(status["counts"]["planned"], 2)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        self.assertEqual(self.adapter.artifacts, [])

    def test_actual_initialization_failure_preserves_incomplete_owned_store(self):
        for timing in ("before", "after"):
            with self.subTest(timing=timing):
                self.store = self.root / ("initialization-" + timing)
                original_sync = os.fsync
                hits = []

                def sync(descriptor):
                    info = os.fstat(descriptor)
                    target = self.store / "plan.json"
                    matches = (target.is_file() and stat.S_ISREG(info.st_mode)
                               and (target.stat().st_dev, target.stat().st_ino) == (info.st_dev, info.st_ino))
                    if matches and timing == "before":
                        hits.append("before")
                        raise OSError("synthetic incomplete initialization")
                    original_sync(descriptor)
                    if matches and timing == "after":
                        hits.append("after")
                        raise OSError("synthetic incomplete initialization")

                with patch.object(CORE.os, "fsync", side_effect=sync):
                    with self.assertRaises(OSError):
                        CORE.prepare(self.store, self.proposals, "synthetic interrupted prepare request")
                self.assertEqual(hits, [timing], "intended initialization fault was not reached")
                self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
                before = self.snapshot()
                self.assertEqual(set(before), CORE.FILES - {"receipt.json"})
                for path in self.store.iterdir():
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                CORE.proposal_check(CORE.read_json(self.store / "proposals.json", CORE.MAX_INPUT))
                CORE.plan_check(CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN))
                self.expect_stop("incomplete_or_extra_store_files", CORE.status, self.store)
                self.expect_stop("store_exists", CORE.prepare, self.store, self.proposals, "another prepare request")
                self.assertEqual(self.snapshot(), before, "incomplete initialization was repaired or overwritten")
                self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_duplicates_and_inconsistent_search_do_not_resolve_uncertainty(self):
        self.prepare()
        self.adapter.fail = "after-write"
        with self.assertRaises(Crash):
            self.apply()
        self.adapter.fail = None
        self.adapter.duplicate = True
        self.expect_stop("reconciliation_unresolved", self.reconcile)
        self.adapter.duplicate = False
        self.adapter.discovery_consistent = False
        self.expect_stop("reconciliation_unresolved", self.reconcile)
        self.assertEqual(self.adapter.writes, 1)
        self.assertEqual([e["event"] for e in self.receipt()["history"]], ["intent", "uncertain", "uncertain"])

    def test_wrong_returned_target_url_or_extra_field_is_never_verified(self):
        for failure in ("wrong-target", "wrong-url", "extra-field"):
            with self.subTest(failure=failure):
                self.store = self.root / failure
                self.adapter = FakeAdapter(self.proposals["destination"])
                self.prepare()
                self.adapter.fail = failure
                self.expect_stop("write_or_verification_uncertain", self.apply)
                self.assertFalse(CORE.status(self.store)["complete"])
                self.assertIsNone(self.receipt()["operations"][0]["artifact"])
                self.assertEqual([item["event"] for item in self.receipt()["history"]], ["intent"])
                self.assertEqual(self.adapter.writes, 1)
                self.assertNotIn(b"SYNTHETIC_SECRET", (self.store / "receipt.json").read_bytes())

    def test_remote_edit_is_not_overwritten_during_reconciliation(self):
        self.prepare()
        self.adapter.fail = "readback"
        self.expect_stop("write_or_verification_uncertain", self.apply)
        self.adapter.fail = None
        self.adapter.artifacts[0]["body"] += "\nUnrelated remote edit.\n"
        before = copy.deepcopy(self.adapter.artifacts)
        self.expect_stop("reconciliation_unresolved", self.reconcile)
        self.assertEqual(self.adapter.artifacts, before)
        self.assertEqual(self.adapter.writes, 1)

    def test_status_is_read_only_and_receipt_schema_is_closed(self):
        self.prepare()
        before = self.snapshot()
        CORE.status(self.store)
        self.assertEqual(self.snapshot(), before)
        receipt = self.receipt()
        receipt["invented_authority"] = True
        (self.store / "receipt.json").write_bytes(CORE.canonical(receipt))
        self.expect_stop("invalid_fields", CORE.status, self.store)

    def test_history_and_authority_limits_and_fabricated_state_rejected(self):
        self.prepare()
        plan = json.loads((self.store / "plan.json").read_bytes())
        receipt = self.receipt()
        receipt["operations"][0].update(state="attempting", attempt=1)
        self.expect_stop("history_state_mismatch", CORE.receipt_check, receipt, plan)
        receipt = self.receipt()
        receipt["authorities"] = [{"scope": "filing", "reference": "fixture", "plan_digest": self.expected}] * (CORE.MAX_AUTHORITIES + 1)
        self.expect_stop("invalid_list", CORE.receipt_check, receipt, plan)
        receipt = self.receipt()
        receipt["history"] = [None] * (CORE.MAX_HISTORY + 1)
        self.expect_stop("invalid_list", CORE.receipt_check, receipt, plan)

    def test_full_authority_list_blocks_apply_and_reconcile_without_calls(self):
        self.prepare()
        plan = json.loads((self.store / "plan.json").read_bytes())
        receipt = self.receipt()
        for index in range(CORE.MAX_AUTHORITIES):
            CORE.authorize(receipt, plan, self.expected, "fixture authority " + str(index), "filing")
        CORE.receipt_check(receipt, plan)
        (self.store / "receipt.json").write_bytes(CORE.canonical(receipt))
        before = self.snapshot()
        for call in (self.apply, self.reconcile):
            self.expect_stop("authority_limit", call)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_full_valid_history_blocks_apply_reconcile_and_event(self):
        self.prepare()
        plan = json.loads((self.store / "plan.json").read_bytes())
        receipt = self.receipt()
        CORE.authorize(receipt, plan, self.expected, "fixture filing", "filing")
        CORE.authorize(receipt, plan, self.expected, "fixture reconciliation", "reconciliation")
        row = receipt["operations"][0]
        request = CORE.request_for(plan, receipt, plan["operations"][0])
        artifact = self.adapter.create(request)
        row.update(state="attempting", attempt=1)
        CORE.event(receipt, row, "intent")
        row["state"] = "uncertain"
        for _ in range(CORE.MAX_HISTORY - 2):
            CORE.event(receipt, row, "uncertain")
        row.update(state="verified", artifact=artifact)
        CORE.event(receipt, row, "reconciled")
        self.assertEqual(len(receipt["history"]), CORE.MAX_HISTORY)
        CORE.receipt_check(receipt, plan)
        (self.store / "receipt.json").write_bytes(CORE.canonical(receipt))
        before, calls = self.snapshot(), (self.adapter.reads, self.adapter.writes)
        for call in (self.apply, self.reconcile):
            self.expect_stop("history_limit", call)
            self.assertEqual(self.snapshot(), before)
        memory = copy.deepcopy(receipt)
        self.expect_stop("history_limit", CORE.event, receipt, row, "reconciled")
        self.assertEqual(receipt, memory)
        self.assertEqual((self.adapter.reads, self.adapter.writes), calls)

    def test_receipt_capacity_reservation_blocks_before_adapter_calls(self):
        self.prepare()
        before = self.snapshot()
        # Lower only the reservation ceiling; the valid small store still fits.
        with patch.object(CORE, "MAX_RECEIPT", CORE.MAX_BODY):
            self.expect_stop("receipt_capacity", self.apply)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_dependency_request_requires_verified_mapping(self):
        plan = CORE.make_plan(self.proposals, "fixture preparation")
        receipt = CORE.initial_receipt(plan)
        before = CORE.canonical([plan, receipt])
        self.expect_stop("dependency_unverified", CORE.request_for, plan, receipt, plan["operations"][1])
        self.assertEqual(CORE.canonical([plan, receipt]), before)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_duplicate_returned_id_or_url_rejected_in_valid_history(self):
        self.prepare()
        self.apply()
        plan = json.loads((self.store / "plan.json").read_bytes())
        before, calls = self.snapshot(), (self.adapter.reads, self.adapter.writes)
        for duplicate in ("id", "url"):
            with self.subTest(duplicate=duplicate):
                receipt = self.receipt()
                first, second = receipt["operations"]
                if duplicate == "id":
                    second["artifact"]["id"] = first["artifact"]["id"]
                    for item in receipt["history"]:
                        if item["operation"] == second["id"] and item["artifact_id"] is not None:
                            item["artifact_id"] = first["artifact"]["id"]
                else:
                    for key in ("number", "url"):
                        second["artifact"][key] = first["artifact"][key]
                self.expect_stop("duplicate_artifact", CORE.receipt_check, receipt, plan)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), calls)

    def test_nonempty_lock_is_invalid_before_actual_lock_contention(self):
        self.prepare()
        lock = self.store / "lock"
        lock.write_bytes(b"synthetic nonempty lock")
        before = self.snapshot()
        self.expect_stop("data_too_large", CORE.status, self.store)
        for call in (self.apply, self.reconcile):
            self.expect_stop("data_too_large", call)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        lock.write_bytes(b"")  # Restore this test-owned fixture only.
        self.assertFalse(CORE.status(self.store)["complete"])

    def test_symlink_hardlink_fifo_permissions_and_extra_files_rejected(self):
        self.prepare()
        original = self.store / "receipt.json"
        preserved = self.root / "preserved.json"
        original.rename(preserved)
        original.symlink_to(preserved)
        self.expect_stop("symlink_path", CORE.status, self.store)
        original.unlink()
        os.link(preserved, original)
        self.expect_stop("unsafe_file", CORE.status, self.store)
        original.unlink()
        os.mkfifo(original, 0o600)
        self.expect_stop("invalid_file", CORE.status, self.store)
        original.unlink()
        preserved.rename(original)
        original.chmod(0o644)
        self.expect_stop("unsafe_file", CORE.status, self.store)
        original.chmod(0o600)
        (self.store / ".checkpoint-stale").write_text("Preserve unknown crash evidence")
        self.expect_stop("incomplete_or_extra_store_files", CORE.status, self.store)
        self.assertTrue((self.store / ".checkpoint-stale").exists())

    def test_parent_symlink_public_parent_and_source_tree_are_rejected(self):
        link = self.root / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        self.expect_stop("symlink_path", CORE.prepare, link / "store", self.proposals, "request")
        self.root.chmod(0o755)
        self.expect_stop("unsafe_directory", CORE.prepare, self.store, self.proposals, "request")
        self.root.chmod(0o700)
        (self.root / ".git").mkdir()
        self.expect_stop("store_inside_repository", CORE.prepare, self.store, self.proposals, "request")

    def test_symlink_parent_input_is_rejected_before_reading(self):
        target = self.root / "target"
        nested = target / "nested"
        nested.mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        normalized = self.root / "proposals.json"
        intended = target / "proposals.json"
        other = copy.deepcopy(self.proposals)
        other["batch_id"] = "f" * 32
        for path, value in ((normalized, self.proposals), (intended, other)):
            path.write_bytes(CORE.canonical(value))
            path.chmod(0o600)
        before = {path: path.read_bytes() for path in (normalized, intended)}
        spelling = str(alias / ".." / "proposals.json")
        for factory in (str, Path, FixturePath):
            with self.subTest(path_type=factory.__name__):
                with patch.object(CORE.os, "open", wraps=os.open) as opened:
                    self.expect_stop("parent_traversal", CORE.read_json, factory(spelling), CORE.MAX_INPUT)
                    opened.assert_not_called()
        output, error = io.StringIO(), io.StringIO()
        with patch.object(CORE.os, "open", wraps=os.open) as opened:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(CORE.main(["validate", "--input", spelling]), 1)
            opened.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(json.loads(error.getvalue()), {"error": "parent_traversal"})
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(self.store.exists())
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_symlink_parent_store_is_rejected_before_creation(self):
        target = self.root / "target"
        nested = target / "nested"
        nested.mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        spelling = str(alias / ".." / "store")
        for factory in (str, Path, FixturePath):
            with self.subTest(path_type=factory.__name__):
                with patch.object(CORE.os, "open", wraps=os.open) as opened:
                    self.expect_stop("parent_traversal", CORE.prepare, factory(spelling), self.proposals, "request")
                    opened.assert_not_called()
                self.assertFalse(self.store.exists())
                self.assertFalse((target / "store").exists())
                self.assertEqual({path.name for path in self.root.iterdir()}, {"target", "alias"})
                self.assertEqual({path.name for path in target.iterdir()}, {"nested"})
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_symlink_parent_existing_store_has_no_reads_mutations_or_adapter_calls(self):
        self.prepare()
        target = self.root / "target"
        nested = target / "nested"
        nested.mkdir(parents=True)
        target.chmod(0o700)
        alias = self.root / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        intended = target / "store"
        CORE.prepare(intended, self.proposals, "synthetic prepare request")
        before = {path: path.read_bytes() for store in (self.store, intended) for path in store.iterdir()}
        spelling = str(alias / ".." / "store")
        operations = ((CORE.status, ()),
                      (CORE.apply, (self.expected, "synthetic filing approval", self.adapter)),
                      (CORE.reconcile, (self.expected, "synthetic reconciliation request", self.adapter)))
        for factory in (str, Path, FixturePath):
            for function, arguments in operations:
                with self.subTest(path_type=factory.__name__, operation=function.__name__):
                    with patch.object(CORE.os, "open", wraps=os.open) as opened:
                        self.expect_stop("parent_traversal", function, factory(spelling), *arguments)
                        opened.assert_not_called()
                    self.assertEqual({path: path.read_bytes() for path in before}, before)
                    for store in (self.store, intended):
                        self.assertEqual({path.name for path in store.iterdir()}, CORE.FILES)
                    self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_direct_parent_components_are_rejected_before_normalization(self):
        plain = self.root / "plain"
        plain.mkdir()
        paths = (plain / "..", plain / ".." / "proposals.json",
                 plain / ".." / "plain" / ".." / "store")
        for path in paths:
            for factory in (str, Path, FixturePath):
                with self.subTest(path=str(path), path_type=factory.__name__):
                    with patch.object(CORE.os.path, "abspath", wraps=os.path.abspath) as normalize:
                        self.expect_stop("parent_traversal", CORE.checked_path, factory(str(path)))
                        normalize.assert_not_called()
        self.assertEqual({path.name for path in self.root.iterdir()}, {"plain"})
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_relative_text_and_pathlike_inputs_and_stores_remain_supported(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        selected = inputs / "..selected.json"
        selected.write_bytes(CORE.canonical(self.proposals))
        selected.chmod(0o600)
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            for index, factory in enumerate((str, Path, FixturePath)):
                with self.subTest(path_type=factory.__name__):
                    value = factory("./inputs/..selected.json")
                    self.assertEqual(CORE.checked_path(value), selected)
                    self.assertEqual(CORE.read_json(value, CORE.MAX_INPUT), self.proposals)
                    store = factory("./store-" + str(index))
                    result = CORE.prepare(store, self.proposals, "synthetic relative preparation")
                    status = CORE.status(store)
                    self.assertEqual(status["plan_digest"], result["plan_digest"])
                    self.assertFalse(status["complete"])
                    self.assertEqual(status["counts"]["planned"], len(self.proposals["proposals"]))
                    self.assertEqual({path.name for path in (self.root / ("store-" + str(index))).iterdir()}, CORE.FILES)
        finally:
            os.chdir(previous)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_wrong_owner_metadata_rejected_without_chown(self):
        self.prepare()
        wrong_uid = os.getuid() + 1
        before = self.snapshot()
        with patch.object(CORE.os, "getuid", return_value=wrong_uid):
            self.expect_stop("unsafe_directory", CORE.status, self.store)
            self.expect_stop("unsafe_file", CORE.read_json, self.store / "receipt.json", CORE.MAX_RECEIPT)
        self.assertEqual(self.snapshot(), before)

    def test_real_flock_blocks_owned_child_without_mutation(self):
        self.prepare()
        before = self.snapshot()
        with CORE.locked(self.store, writing=True):
            read_fd, write_fd = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                outcomes = []
                for call in (lambda: CORE.status(self.store), self.apply, self.reconcile):
                    outcome = b"wrong"
                    try:
                        call()
                    except CORE.Stop as error:
                        outcome = b"busy" if str(error) == "store_busy" else b"wrong"
                    except BaseException:
                        pass
                    outcomes.append(outcome)
                if (self.adapter.reads, self.adapter.writes) != (0, 0):
                    outcomes.append(b"unexpected-call")
                os.write(write_fd, b"|".join(outcomes))
                os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            reaped = False
            try:
                self.assertTrue(select.select([read_fd], [], [], 5)[0], "owned lock contender timed out")
                self.assertEqual(os.read(read_fd, 64), b"busy|busy|busy")
                pid, code = os.waitpid(child, 0)
                reaped = True
                self.assertEqual((pid, code), (child, 0))
            finally:
                os.close(read_fd)
                if not reaped:
                    os.kill(child, signal.SIGKILL)  # Only this unreaped owned child.
                    os.waitpid(child, 0)
        self.assertEqual(self.snapshot(), before)
        CORE.status(self.store)

    def test_environment_and_umask_are_not_changed(self):
        old = os.umask(0o027)
        try:
            before = dict(os.environ)
            self.prepare()
            self.apply()
            current = os.umask(0o027)
            self.assertEqual(current, 0o027)
            self.assertTrue(dict(os.environ) == before, "caller environment changed")
        finally:
            os.umask(old)

    def test_restrictive_umask_does_not_break_exact_owned_modes(self):
        old = os.umask(0o777)
        try:
            self.prepare()
            self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
            for item in self.store.iterdir():
                self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)
            self.apply()
            self.assertEqual(os.umask(0o777), 0o777)
        finally:
            os.umask(old)

    def test_cli_validation_has_no_store_and_errors_do_not_echo_input(self):
        path = self.root / "proposals.json"
        path.write_bytes(CORE.canonical(self.proposals))
        path.chmod(0o600)
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = CORE.main(["validate", "--input", str(path)])
        self.assertEqual(code, 0)
        self.assertFalse(self.store.exists())
        self.assertEqual(json.loads(output.getvalue())["proposals"], 2)
        path.write_text("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")
        with contextlib.redirect_stderr(error):
            self.assertEqual(CORE.main(["validate", "--input", str(path)]), 1)
        self.assertNotIn("SYNTHETIC_SECRET", error.getvalue())

    def test_cli_prepare_and_status_report_offline_state_without_rewriting(self):
        path = self.root / "proposals.json"
        path.write_bytes(CORE.canonical(self.proposals))
        path.chmod(0o600)
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = CORE.main(["prepare", "--input", str(path), "--store", str(self.store),
                              "--prepare-authority", "synthetic CLI preparation"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        plan = json.loads((self.store / "plan.json").read_bytes())
        self.assertEqual(result, {"plan_digest": plan["digest"], "operations": 2, "provider_available": False})
        self.assertEqual(plan["prepare_authority"], "synthetic CLI preparation")
        self.assertEqual(error.getvalue(), "")
        before = self.snapshot()
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(CORE.main(["status", "--store", str(self.store)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result, CORE.status(self.store))
        self.assertFalse(result["complete"])
        self.assertEqual(result["counts"]["planned"], 2)
        self.assertEqual(error.getvalue(), "")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_cli_store_failures_are_nonzero_sanitized_and_nonmutating(self):
        self.prepare()
        (self.store / "receipt.json").write_text("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")
        before = self.snapshot()
        for path, expected in ((self.store, "invalid_json"),
                               (self.root / "SYNTHETIC_SECRET_MISSING_STORE", "invalid_input_or_store")):
            with self.subTest(error=expected):
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = CORE.main(["status", "--store", str(path)])
                self.assertEqual(code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(json.loads(error.getvalue()), {"error": expected})
                self.assertNotIn("SYNTHETIC_SECRET", error.getvalue())
                self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))


class GithubFakeAdapter(FakeAdapter):
    """In-memory v2 protocol fixture, never a client compatibility assertion."""

    def __init__(self, destination):
        super().__init__(destination)
        fixture = json.loads((ROOT / "tests/fixtures/proposal-publication/github-cases.json").read_bytes())
        self.identity = {"name": "github-issues", "version": 1, "profile": "unavailable-graphql-v1",
                         "source_sha256": CORE.github_source_hash(), "client": fixture["client"],
                         "context": {"destination": copy.deepcopy(destination), "repository_node_id": "R_fixture",
                                     "visibility": "PRIVATE", "issues_enabled": True, "archived": False, "permission": "WRITE"},
                         "policy": copy.deepcopy(CORE.GITHUB_POLICY)}

    def read_context(self):
        self.reads += 1
        return copy.deepcopy(self.identity["context"])

    def find(self, request):
        found = super().find(request)
        return {"traversal_complete": found["complete"], "consistency": "not-guaranteed", "matches": found["matches"]}


class GithubPublicationTests(unittest.TestCase):
    # Reuse owned-fixture helpers, not TestCase inheritance (no duplicate P1 IDs).
    snapshot = PublicationTests.snapshot
    receipt = PublicationTests.receipt
    apply = PublicationTests.apply
    reconcile = PublicationTests.reconcile
    checkpoint_fault = PublicationTests.checkpoint_fault

    def setUp(self):
        PublicationTests.setUp(self)
        self.adapter = GithubFakeAdapter(self.proposals["destination"])

    def expect_stop(self, code, function, *args, **kwargs):
        with self.assertRaisesRegex(CORE.Stop, "^" + code + "$"):
            function(*args, **kwargs)

    def prepare(self):
        result = CORE.prepare(self.store, self.proposals, "synthetic v2 preparation", binding=self.adapter.identity)
        self.expected = result["plan_digest"]
        return result

    def test_live_entrypoints_refuse_before_paths_or_intent(self):
        for name, function in (("github-plan", CORE.github_plan), ("github-apply", CORE.github_apply),
                               ("github-reconcile", CORE.github_reconcile)):
            with self.subTest(command=name):
                with patch.object(CORE, "locked", side_effect=AssertionError("unexpected store access")), \
                     patch.object(CORE, "_github_provider", side_effect=CORE.Stop("github_compatibility_unavailable")), \
                     patch.object(CORE, "prepare", side_effect=AssertionError("unexpected preparation")), \
                     patch.object(CORE, "read_json", side_effect=AssertionError("unexpected input read")):
                    self.expect_stop("github_compatibility_unavailable", function, object(), enable=True)
                    error = io.StringIO()
                    with patch.dict(os.environ, {"ENVY_GITHUB_PROFILE": "enabled"}), contextlib.redirect_stderr(error):
                        self.assertEqual(CORE.main([name, "--store", "unread", "--enable-live"]), 1)
                    self.assertEqual(json.loads(error.getvalue()), {"error": "github_compatibility_unavailable"})
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_v2_exact_binding_and_policy_are_in_digest_receipt_and_status(self):
        self.prepare()
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        self.assertEqual(plan["version"], 2)
        self.assertEqual(plan["provider"], self.adapter.identity)
        self.assertEqual(self.receipt()["version"], 2)
        self.assertEqual(self.receipt()["policy"], CORE.GITHUB_POLICY)
        self.assertEqual(CORE.status(self.store)["policy"], CORE.GITHUB_POLICY)
        self.assertFalse(CORE.status(self.store)["provider_available"])
        before = self.snapshot()
        with patch.object(CORE, "github_source_hash", return_value="f" * 64):
            self.expect_stop("provider_source_changed", CORE.status, self.store)
        self.assertEqual(self.snapshot(), before)

    def test_v2_source_client_context_policy_changes_do_not_reuse_approval(self):
        self.prepare()
        original = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        for key in ("client", "context"):
            changed = copy.deepcopy(original)
            if key == "client":
                changed["provider"][key]["sha256"] = "b" * 64
            else:
                changed["provider"][key]["repository_node_id"] = "R_another"
            changed["digest"] = CORE.digest({k: v for k, v in changed.items() if k != "digest"})
            CORE.plan_check(changed)
            receipt = CORE.initial_receipt(changed)
            self.expect_stop("approval_mismatch", CORE.authorize, receipt, changed, self.expected, "fixture", "filing")
        for name, value in (("version", True), ("profile", "enabled-live"), ("extra", True)):
            changed = copy.deepcopy(self.adapter.identity); changed[name] = value
            with self.assertRaises(CORE.Stop):
                CORE.make_github_plan(self.proposals, "fixture", changed)
        changed = copy.deepcopy(self.adapter.identity); changed["policy"]["consistency"] = "guaranteed"
        self.expect_stop("unsupported_provider_policy", CORE.make_github_plan, self.proposals, "fixture", changed)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_v1_remains_version_one_without_v2_fields(self):
        plan = CORE.make_plan(self.proposals, "fixture v1")
        self.assertEqual(plan["version"], 1)
        self.assertEqual(plan["provider"], CORE.PROVIDER)
        self.assertEqual(CORE.initial_receipt(plan)["version"], 1)
        self.assertNotIn("policy", CORE.initial_receipt(plan))
        self.assertNotIn("policy", CORE.summary(plan, CORE.initial_receipt(plan)))

    def test_v2_preflight_pause_has_zero_attempt_then_explicit_continuation(self):
        self.prepare(); self.adapter.discovery_complete = False
        self.expect_stop("preflight_paused", self.apply)
        receipt = self.receipt()
        self.assertEqual([row["state"] for row in receipt["operations"]], ["planned", "planned"])
        self.assertEqual(receipt["operations"][0]["attempt"], 0)
        self.assertEqual(receipt["history"][-1]["event"], "preflight-paused")
        self.assertEqual(receipt["history"][-1]["reason"], "preflight_unavailable")
        self.assertEqual(self.adapter.writes, 0)
        self.adapter.discovery_complete = True
        self.assertTrue(self.apply()["complete"])
        self.assertEqual(self.adapter.writes, 2)
        self.assertEqual(self.receipt()["history"][0], receipt["history"][0])

    def test_v2_mismatched_adapter_identity_pauses_before_context_reads(self):
        self.prepare()
        self.adapter.identity["client"]["sha256"] = "c" * 64
        self.expect_stop("preflight_paused", self.apply)
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))
        self.assertEqual(self.receipt()["operations"][0]["attempt"], 0)

    def test_v2_pause_cannot_be_fabricated_after_intent_or_with_reason_text(self):
        self.prepare(); self.adapter.discovery_complete = False
        self.expect_stop("preflight_paused", self.apply)
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        receipt = self.receipt(); receipt["history"][0]["reason"] = "untrusted text"
        self.expect_stop("invalid_history_reason", CORE.receipt_check, receipt, plan)
        receipt = self.receipt(); receipt["operations"][0].update(state="attempting", attempt=1)
        self.expect_stop("history_state_mismatch", CORE.receipt_check, receipt, plan)

    def test_v2_lost_create_response_never_retries_and_positive_reconciliation_has_limits(self):
        self.prepare(); self.adapter.fail = "timeout-after-write"
        self.expect_stop("write_or_verification_uncertain", self.apply)
        before = self.snapshot()
        self.expect_stop("reconciliation_required", self.apply)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.adapter.writes, 1)
        self.adapter.fail = None
        result = self.reconcile()
        self.assertEqual(self.adapter.writes, 1)
        self.assertEqual(result["policy"]["reconciliation"], "required-artifact-not-authorship-or-global-uniqueness")
        self.assertFalse(result["complete"])
        self.assertTrue(self.apply()["complete"])
        self.assertEqual(self.adapter.writes, 2)

    def test_v2_empty_or_conflicting_discovery_never_reopens_attempt(self):
        for mode in ("empty", "edited", "duplicate", "partial"):
            with self.subTest(mode=mode):
                self.store = self.root / mode; self.adapter = GithubFakeAdapter(self.proposals["destination"])
                self.prepare(); self.adapter.fail = "timeout-after-write"
                self.expect_stop("write_or_verification_uncertain", self.apply)
                self.adapter.fail = None
                if mode == "empty": self.adapter.artifacts.clear()
                if mode == "edited": self.adapter.artifacts[0]["body"] += "Edited independently."
                if mode == "duplicate": self.adapter.duplicate = True
                if mode == "partial": self.adapter.discovery_complete = False
                self.expect_stop("reconciliation_unresolved", self.reconcile)
                self.expect_stop("reconciliation_required", self.apply)
                self.assertEqual(self.adapter.writes, 1)

    def test_v2_existing_marker_is_not_filtered_by_body_or_adopted(self):
        self.prepare()
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        self.adapter.create(CORE.request_for(plan, self.receipt(), plan["operations"][0]))
        self.adapter.artifacts[0]["body"] += "Unrelated edit."
        self.expect_stop("preflight_blocked", self.apply)
        self.assertEqual(self.adapter.writes, 1)
        self.assertEqual(self.receipt()["operations"][0]["attempt"], 1)
        self.assertEqual(self.receipt()["operations"][0]["state"], "blocked")

    def test_v2_marker_conflict_cannot_resume_after_empty_discovery(self):
        for mode in ("exact", "edited-title", "edited-body", "two-matches"):
            with self.subTest(mode=mode):
                self.store = self.root / mode
                self.adapter = GithubFakeAdapter(self.proposals["destination"])
                self.prepare()
                plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
                request = CORE.request_for(plan, self.receipt(), plan["operations"][0])
                self.adapter.create(request)
                if mode == "edited-title": self.adapter.artifacts[0]["title"] += " Edited."
                if mode == "edited-body": self.adapter.artifacts[0]["body"] += " Edited."
                if mode == "two-matches": self.adapter.create(request)
                writes = self.adapter.writes
                self.expect_stop("preflight_blocked", self.apply)
                receipt = self.receipt()
                self.assertEqual((receipt["operations"][0]["state"], receipt["operations"][0]["attempt"],
                                  receipt["operations"][0]["artifact"]), ("blocked", 1, None))
                self.assertEqual([event["event"] for event in receipt["history"]], ["blocked"])
                self.assertIsNone(receipt["history"][0]["reason"])
                self.assertEqual(self.adapter.writes, writes)
                self.adapter.artifacts.clear()
                before, reads = self.snapshot(), self.adapter.reads
                self.expect_stop("blocked_plan", self.apply)
                self.assertFalse(self.reconcile()["complete"])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual((self.adapter.reads, self.adapter.writes), (reads, writes))

    def test_v2_blocked_history_cannot_transition_to_preflight_paused(self):
        self.prepare()
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        self.adapter.create(CORE.request_for(plan, self.receipt(), plan["operations"][0]))
        self.expect_stop("preflight_blocked", self.apply)
        before = self.snapshot()
        for state, attempt in (("planned", 0), ("blocked", 1)):
            with self.subTest(state=state):
                receipt = self.receipt()
                row = receipt["operations"][0]
                row.update(state=state, attempt=attempt)
                receipt["history"].append({"sequence": 2, "operation": row["id"],
                                           "event": "preflight-paused", "artifact_id": None,
                                           "reason": "preflight_unavailable"})
                self.expect_stop("invalid_history_transition", CORE.receipt_check, receipt, plan)
        self.assertEqual(self.snapshot(), before)

    def test_v2_sole_discovery_requires_successful_exact_direct_read(self):
        for mode in ("failed-read", "changed-identity"):
            with self.subTest(mode=mode):
                self.store = self.root / mode
                self.adapter = GithubFakeAdapter(self.proposals["destination"])
                self.prepare(); self.adapter.fail = "timeout-after-write"
                self.expect_stop("write_or_verification_uncertain", self.apply)
                self.adapter.fail = None
                exact = copy.deepcopy(self.adapter.artifacts[0])
                changed = copy.deepcopy(exact); changed["id"] = "90001"
                options = ({"side_effect": ValueError("fixed synthetic read failure")}
                           if mode == "failed-read" else {"return_value": changed})
                with patch.object(self.adapter, "read", **options) as observed:
                    self.expect_stop("reconciliation_unresolved", self.reconcile)
                    observed.assert_called_once_with(exact)
                row = self.receipt()["operations"][0]
                self.assertEqual((row["state"], row["attempt"], row["artifact"]), ("uncertain", 1, None))
                self.assertNotIn("reconciled", [event["event"] for event in self.receipt()["history"]])
                self.assertEqual(self.adapter.writes, 1)
                before = self.snapshot()
                self.expect_stop("reconciliation_required", self.apply)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.adapter.writes, 1)

    def test_v2_receipt_version_policy_and_pause_schema_are_closed(self):
        self.prepare()
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        for key, value in (("version", 1), ("version", True), ("policy", {}), ("untrusted", True)):
            receipt = self.receipt(); receipt[key] = value
            with self.assertRaises(CORE.Stop): CORE.receipt_check(receipt, plan)

    def test_v2_body_audience_and_budget_refusals_precede_store_creation(self):
        binding = copy.deepcopy(self.adapter.identity)
        binding["context"]["visibility"] = "PUBLIC"
        self.expect_stop("audience_mismatch", CORE.prepare, self.store, self.proposals, "fixture", binding=binding)
        self.assertFalse(self.store.exists())
        proposals = copy.deepcopy(self.proposals); proposals["proposals"][0]["detail"] = "x" * CORE.GITHUB_BODY
        self.expect_stop("invalid_text", CORE.prepare, self.store, proposals, "fixture", binding=self.adapter.identity)
        self.assertFalse(self.store.exists())

    def test_v2_actual_intent_result_boundaries_retain_one_attempt(self):
        for checkpoint in (1, 2):
            for point in ("file-before", "file-after", "replace-before", "replace-after", "directory-before", "directory-after"):
                with self.subTest(checkpoint=checkpoint, point=point):
                    self.store = self.root / (str(checkpoint) + point)
                    self.adapter = GithubFakeAdapter(self.proposals["destination"]); self.prepare()
                    with self.checkpoint_fault(checkpoint, point) as progress:
                        with self.assertRaises((OSError, CORE.Stop)):
                            self.apply()
                    self.assertEqual(progress["hits"], 1)
                    self.assertEqual(self.adapter.writes, checkpoint - 1)
                    plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
                    CORE.receipt_check(self.receipt(), plan)
                    self.assertFalse(CORE.status(self.store)["complete"])
                    if self.receipt()["operations"][0]["state"] != "planned":
                        before = self.snapshot()
                        self.expect_stop("reconciliation_required", self.apply)
                        self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.adapter.writes, checkpoint - 1)


if __name__ == "__main__":
    unittest.main()
