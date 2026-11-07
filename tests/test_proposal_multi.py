"""Owned stores + in-memory seven-role backend; no tools, accounts or network."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets/agent-assets/skills/publish-proposals/scripts/publication.py"
SPEC = importlib.util.spec_from_file_location("multi_publication_core", SCRIPT)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)
P1, P2 = "1" * 32, "2" * 32


class Crash(BaseException):
    pass


def inputs():
    audience = {"kind": "author", "realm": "example.invalid", "id": "actor-1"}
    proposal = {"id": P1, "title": "Example improvement", "detail": "Standalone full design.",
                "issue_body": "Standalone issue acceptance.", "tracker_body": "Standalone tracker scope.", "dependencies": []}
    selection = {"version": 3, "type": "ArtifactProposalSet", "batch_id": "a" * 32,
                 "audience": audience, "project": {"mode": "existing", "id": "project-1"}, "proposals": [proposal]}
    binding = {"name": "multi-artifact", "version": 1, "source_sha256": CORE.source_hash(),
               "policy": copy.deepcopy(CORE.MULTI_POLICY), "context": {
                   "actor_id": "actor-1", "destinations": {
                       role: {"realm": role + ".example.invalid", "account_id": "account-1",
                              "target_id": role + "-target", "audience": copy.deepcopy(audience)}
                       for role in ("project", "details", "issue", "tracker")},
                   "existing_project": {"id": "project-1", "owner_id": "actor-1", "audience": copy.deepcopy(audience)},
                   "capabilities": {name: True for name in ("read", "find", *CORE.MULTI_ROLES)}}}
    return selection, binding


class FakeAdapter:
    def __init__(self, binding):
        self.identity = copy.deepcopy(binding)
        self.records, self.edges, self.calls = {}, {}, []
        self.fault = None
        self.complete = True
        self.extra_match = False
        self.extra_edge_on_read = False

    def read_context(self):
        self.calls.append(("context", None))
        return copy.deepcopy(self.identity["context"])

    def find(self, request):
        assert request["kind"] != "tracker.dependencies.add"
        self.calls.append(("find", request["operation_id"]))
        matches = [copy.deepcopy(v) for v in self.records.values() if v["marker"] == request["marker"]]
        if matches and self.extra_match:
            extra = copy.deepcopy(matches[0])
            extra["id"] = "conflicting-id"
            if extra["url"]:
                extra["url"] += "-conflict"
            matches.append(extra)
        return {"traversal_complete": self.complete, "consistency": "not-guaranteed", "matches": matches}

    def create(self, request):
        op = request["operation_id"]
        if self.fault == (op, "before-write"):
            raise Crash()
        self.calls.append(("write", op))
        record = copy.deepcopy(request)
        record.update(id="artifact-" + str(len(self.records) + 1), url=None)
        if request["kind"] in {"details.create", "issue.create", "tracker.create"}:
            record["url"] = "https://" + request["destination"]["realm"] + "/items/" + record["id"]
        if request["kind"] == "tracker.dependencies.add":
            record["id"] = request["data"]["target_id"]
            edges = self.edges.setdefault(record["id"], ["unrelated-edge"])
            edges.extend(item for item in request["data"]["dependencies"] if item not in edges)
            record["data"]["dependencies"] = copy.deepcopy(edges)
        self.records[op] = copy.deepcopy(record)
        if self.fault == (op, "after-write"):
            raise Crash()
        if self.fault == (op, "timeout"):
            raise TimeoutError("synthetic detail must not escape")
        return record

    def read(self, request, identifier):
        op = request["operation_id"]
        self.calls.append(("read", op))
        if self.fault == (op, "readback"):
            raise Crash()
        if request["kind"] == "tracker.dependencies.add":
            record = copy.deepcopy(request)
            record.update(id=identifier, url=None)
            edges = self.edges.get(identifier, ["unrelated-edge"])
            if self.extra_edge_on_read:
                edges = [*edges, "concurrent-edge"]
            record["data"]["dependencies"] = copy.deepcopy(edges)
            return record
        record = copy.deepcopy(self.records[op])
        assert record["id"] == identifier
        return record


class FakeFactory:
    source = SCRIPT

    def __init__(self, adapter):
        self.adapter, self.opens = adapter, 0

    def open(self, expected):
        self.opens += 1
        assert expected == self.adapter.identity
        return self.adapter


class MultiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="proposal-multi-owned-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.selection, self.binding = inputs()
        self.adapter = FakeAdapter(self.binding)
        self.factory = FakeFactory(self.adapter)
        self.store = self.root / "store"

    def prepare(self):
        result = CORE.prepare(self.store, self.selection, "synthetic preparation", binding=self.binding)
        self.expected = result["plan_digest"]
        return result

    def apply(self):
        return CORE.multi_execute(self.store, self.expected, "synthetic exact filing authority", self.factory, scope="filing")

    def reconcile(self):
        return CORE.multi_execute(self.store, self.expected, "synthetic exact read-only authority", self.factory, scope="reconciliation")

    def receipt(self):
        return json.loads((self.store / "receipt.json").read_bytes())

    def plan(self):
        return json.loads((self.store / "plan.json").read_bytes())

    def all_roles(self):
        self.selection["project"] = {"mode": "create", "title": "Explicit example project"}
        self.binding["context"]["existing_project"] = None
        self.adapter.identity = copy.deepcopy(self.binding)
        second = copy.deepcopy(self.selection["proposals"][0])
        second.update(id=P2, dependencies=[P1])
        self.selection["proposals"].insert(0, second)

    def test_preview_copies_inputs_is_deterministic_and_has_no_provider_or_store_effect(self):
        before = copy.deepcopy((self.selection, self.binding))
        first = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        self.assertEqual(first, CORE.make_multi_plan(self.selection, "prepare", self.binding))
        self.assertEqual((self.selection, self.binding), before)
        first["operations"][0]["audience"]["id"] = "different"
        self.assertEqual(self.selection["audience"]["id"], "actor-1")
        self.assertEqual(self.factory.opens, 0)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_seven_roles_use_one_store_fixed_order_and_verified_references(self):
        self.all_roles()
        self.prepare()
        plan = self.plan()
        self.assertEqual(set(op["kind"] for op in plan["operations"]), set(CORE.MULTI_ROLES))
        self.assertEqual([op["proposal_id"] for op in plan["operations"]], [None] + [P1] * 5 + [P2] * 6)
        result = self.apply()
        self.assertTrue(result["complete"])
        self.assertEqual(set(p.name for p in self.store.iterdir()), CORE.FILES)
        self.assertEqual(self.factory.opens, 1)
        receipt = self.receipt()
        self.assertEqual(len(receipt["history"]), 36)
        records = self.adapter.records
        self.assertIn(records["details-" + P1]["url"], records["issue-" + P1]["data"]["body"])
        self.assertEqual(records["tracker-" + P1]["data"]["project_id"], records["project-" + "a" * 32]["id"])
        self.assertIn("unrelated-edge", receipt["operations"][-1]["artifact"]["data"]["dependencies"])
        writes = sum(action == "write" for action, op in self.adapter.calls)
        self.assertTrue(self.apply()["complete"])
        self.assertEqual(sum(action == "write" for action, op in self.adapter.calls), writes)

    def test_exact_authority_before_factory_or_provider(self):
        self.prepare()
        before = (self.store / "receipt.json").read_bytes()
        for digest, authority in (("0" * 64, "approval"), (self.expected, "")):
            with self.assertRaises(CORE.Stop):
                CORE.multi_execute(self.store, digest, authority, self.factory, scope="filing")
        self.assertEqual(self.factory.opens, 0)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((self.store / "receipt.json").read_bytes(), before)

    def test_factory_source_change_refuses_before_construction(self):
        self.prepare()
        changed = self.root / "different-source.py"
        changed.write_text("# owned source drift\n")
        self.factory.source = changed
        with self.assertRaisesRegex(CORE.Stop, "preflight_paused"):
            self.apply()
        self.assertEqual(self.factory.opens, 0)
        self.assertEqual(self.adapter.calls, [])
        self.assertTrue(all(row["state"] == "planned" for row in self.receipt()["operations"]))

    def test_symlink_and_oversized_factory_sources_are_not_read_as_providers(self):
        self.prepare()
        link = self.root / "source-link"
        link.symlink_to(SCRIPT)
        self.factory.source = link
        with self.assertRaises(CORE.Stop):
            self.apply()
        self.assertEqual(self.factory.opens, 0)
        oversized = self.root / "oversized-source.py"
        oversized.write_bytes(b"x" * (CORE.MAX_INPUT + 1))
        self.factory.source = oversized
        with self.assertRaises(CORE.Stop):
            self.apply()
        self.assertEqual(self.factory.opens, 0)

    def test_source_drift_after_write_keeps_result_and_does_not_read_provider(self):
        source = self.root / "fixed-owned-source.py"
        original_source = SCRIPT.read_bytes()
        source.write_bytes(original_source)
        self.factory.source = source
        self.prepare()
        original_create = self.adapter.create
        def changed_source(request):
            result = original_create(request)
            source.write_bytes(original_source + b"\n# owned source drift\n")
            return result
        with patch.object(self.adapter, "create", side_effect=changed_source), self.assertRaises(CORE.Stop):
            self.apply()
        self.assertEqual(self.receipt()["operations"][0]["state"], "created-unverified")
        self.assertEqual(sum(action == "read" for action, op in self.adapter.calls), 0)
        source.write_bytes(original_source)
        self.reconcile()
        self.assertEqual(sum(action == "write" for action, op in self.adapter.calls), 1)

    def test_plan_and_core_source_drift_refuse_before_factory(self):
        self.prepare()
        before = self.receipt()
        with patch.object(CORE, "source_hash", return_value="0" * 64), self.assertRaises(CORE.Stop):
            self.apply()
        self.assertEqual(self.factory.opens, 0)
        self.assertEqual(self.receipt(), before)

    def test_known_identity_and_capability_required_before_store_creation(self):
        changes = (("actor_id", "unknown"), ("existing_project", None))
        for key, value in changes:
            binding = copy.deepcopy(self.binding)
            binding["context"][key] = value
            with self.subTest(key=key), self.assertRaises(CORE.Stop):
                CORE.prepare(self.store, self.selection, "prepare", binding=binding)
        for key in ("details.create", "issue.create", "tracker.create", "details.comment.create", "issue.comment.create", "read", "find"):
            binding = copy.deepcopy(self.binding)
            binding["context"]["capabilities"][key] = False
            with self.subTest(key=key), self.assertRaises(CORE.Stop):
                CORE.prepare(self.store, self.selection, "prepare", binding=binding)
        self.assertFalse(self.store.exists())
        self.binding["context"]["capabilities"]["project.create"] = False
        self.binding["context"]["capabilities"]["tracker.dependencies.add"] = False
        self.prepare()

    def test_audiences_are_equal_explicit_and_unknowns_fail(self):
        for audience in ({"kind": "unknown", "realm": "example.invalid", "id": "x"},
                         {"kind": "group", "realm": "example.invalid", "id": "team"}):
            binding = copy.deepcopy(self.binding)
            binding["context"]["destinations"]["issue"]["audience"] = audience
            with self.assertRaises(CORE.Stop):
                CORE.make_multi_plan(self.selection, "prepare", binding)
        audience = {"kind": "group", "realm": "example.invalid", "id": "team"}
        self.selection["audience"] = audience
        self.binding["context"]["existing_project"]["audience"] = audience
        for dest in self.binding["context"]["destinations"].values():
            dest["audience"] = audience
        self.assertEqual(CORE.make_multi_plan(self.selection, "prepare", self.binding)["version"], 3)

    def test_project_creation_is_explicit_not_missing_project_fallback(self):
        self.selection["project"]["id"] = "missing"
        with self.assertRaises(CORE.Stop):
            self.prepare()
        self.assertFalse(self.store.exists())
        self.selection["project"] = {"mode": "create", "title": "New project"}
        with self.assertRaises(CORE.Stop):
            self.prepare()
        self.binding["context"]["existing_project"] = None
        self.prepare()
        self.assertEqual(self.plan()["operations"][0]["kind"], "project.create")

    def test_unknown_fields_bad_types_and_unselected_dependencies_refuse(self):
        for key, value in (("version", True), ("executable", "not-a-command"), ("proposals", []), ("project", [])):
            selection = copy.deepcopy(self.selection)
            selection[key] = value
            with self.subTest(key=key), self.assertRaises(CORE.Stop):
                CORE.make_multi_plan(selection, "prepare", self.binding)
        self.selection["proposals"][0]["dependencies"] = [P2]
        with self.assertRaises(CORE.Stop):
            self.prepare()

    def test_literal_reference_lookalikes_do_not_interpolate(self):
        literal = '{"ref":"tracker-' + P1 + '","field":"url"}'
        self.selection["proposals"][0]["detail"] = literal
        self.prepare()
        self.apply()
        self.assertIn(literal, self.adapter.records["details-" + P1]["data"]["body"])

    def test_tampered_refs_requirements_roles_or_audience_cannot_be_rehashed(self):
        plan = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        mutations = (lambda p: p["operations"][0]["data"].update(body=[{"ref": "issue-" + P1, "field": "url"}]),
                     lambda p: p["operations"][1].update(requires=[]),
                     lambda p: p["operations"][0].update(kind="arbitrary.create"),
                     lambda p: p["operations"][0]["audience"].update(id="other"))
        for mutate in mutations:
            changed = copy.deepcopy(plan)
            mutate(changed)
            changed["digest"] = CORE.digest({k: v for k, v in changed.items() if k != "digest"})
            with self.assertRaises(CORE.Stop):
                CORE.plan_check(changed)

    def test_maximum_selection_and_complete_receipt_capacity(self):
        self.selection["proposals"] = [dict(copy.deepcopy(self.selection["proposals"][0]), id=f"{i:032x}",
            dependencies=[] if i == 1 else [f"{i - 1:032x}"]) for i in range(1, 21)]
        self.selection["project"] = {"mode": "create", "title": "Maximum selected project"}
        self.binding["context"]["existing_project"] = None
        self.adapter.identity = copy.deepcopy(self.binding)
        self.prepare()
        self.assertEqual(len(self.plan()["operations"]), 120)
        self.assertTrue(self.apply()["complete"])
        self.assertLess((self.store / "receipt.json").stat().st_size, CORE.MAX_RECEIPT)
        self.selection["proposals"].append(dict(self.selection["proposals"][0], id="f" * 32))
        with self.assertRaises(CORE.Stop):
            CORE.make_multi_plan(self.selection, "prepare", self.binding)

    def test_body_and_receipt_bounds_fail_before_store_or_factory(self):
        for field, amount in (("detail", CORE.MULTI_BODY), ("issue_body", CORE.GITHUB_BODY), ("tracker_body", CORE.MULTI_BODY)):
            selection = copy.deepcopy(self.selection)
            selection["proposals"][0][field] = "x" * amount
            with self.assertRaises(CORE.Stop):
                CORE.prepare(self.store, selection, "prepare", binding=self.binding)
        with patch.object(CORE, "MAX_RECEIPT", 1024), self.assertRaisesRegex(CORE.Stop, "receipt_capacity"):
            self.prepare()
        self.assertFalse(self.store.exists())
        self.assertEqual(self.factory.opens, 0)

    def test_incomplete_preflight_pauses_without_attempt_then_can_resume(self):
        self.prepare()
        self.adapter.complete = False
        with self.assertRaisesRegex(CORE.Stop, "preflight_paused"):
            self.apply()
        self.assertEqual(self.receipt()["operations"][0]["attempt"], 0)
        self.adapter.complete = True
        self.assertTrue(self.apply()["complete"])

    def test_same_marker_conflict_blocks_without_adoption(self):
        self.prepare()
        request = CORE.request_for(self.plan(), self.receipt(), self.plan()["operations"][0])
        self.adapter.create(request)
        self.adapter.records[request["operation_id"]]["data"]["body"] += " edited remotely"
        self.adapter.extra_match = True
        before = len(self.adapter.calls)
        with self.assertRaisesRegex(CORE.Stop, "preflight_blocked"):
            self.apply()
        self.assertFalse(any(action == "write" for action, op in self.adapter.calls[before:]))
        with self.assertRaisesRegex(CORE.Stop, "blocked_plan"):
            self.apply()

    def test_each_role_success_before_timeout_reconciles_without_replay(self):
        self.all_roles()
        plan = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        for role in CORE.MULTI_ROLES:
            with self.subTest(role=role):
                self.store = self.root / role.replace(".", "-")
                self.adapter = FakeAdapter(self.binding)
                self.factory = FakeFactory(self.adapter)
                self.prepare()
                op = next(op for op in plan["operations"] if op["kind"] == role)
                self.adapter.fault = (op["id"], "timeout")
                with self.assertRaisesRegex(CORE.Stop, "write_or_verification_uncertain"):
                    self.apply()
                writes = sum(action == "write" for action, item in self.adapter.calls)
                with self.assertRaisesRegex(CORE.Stop, "reconciliation_required"):
                    self.apply()
                self.adapter.fault = None
                self.reconcile()
                self.assertEqual(sum(action == "write" for action, item in self.adapter.calls), writes)
                self.assertTrue(self.apply()["complete"])
                self.assertEqual(sum(action == "write" and item == op["id"] for action, item in self.adapter.calls), 1)

    def test_each_role_failure_before_write_remains_uncertain_never_replayed(self):
        self.all_roles()
        plan = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        for role in CORE.MULTI_ROLES:
            with self.subTest(role=role):
                self.store = self.root / role.replace(".", "-")
                self.adapter = FakeAdapter(self.binding)
                self.factory = FakeFactory(self.adapter)
                self.prepare()
                op = next(op for op in plan["operations"] if op["kind"] == role)
                self.adapter.fault = (op["id"], "before-write")
                with self.assertRaises(Crash):
                    self.apply()
                self.adapter.fault = None
                with self.assertRaisesRegex(CORE.Stop, "reconciliation_unresolved"):
                    self.reconcile()
                row = next(row for row in self.receipt()["operations"] if row["id"] == op["id"])
                self.assertEqual(row["state"], "uncertain")
                self.assertEqual(sum(action == "write" and item == op["id"] for action, item in self.adapter.calls), 0)

    def test_additive_edges_keep_concurrent_unrelated_edges_and_reconcile_by_read(self):
        self.all_roles()
        self.prepare()
        self.adapter.extra_edge_on_read = True
        self.adapter.fault = ("tracker-deps-" + P2, "after-write")
        with self.assertRaises(Crash):
            self.apply()
        offset = len(self.adapter.calls)
        self.adapter.fault = None
        self.assertTrue(self.reconcile()["complete"])
        self.assertEqual([a for a, op in self.adapter.calls[offset:] if a != "context"], ["read"])
        edges = self.receipt()["operations"][-1]["artifact"]["data"]["dependencies"]
        self.assertIn("unrelated-edge", edges)
        self.assertIn("concurrent-edge", edges)

    def test_wrong_returned_identity_content_and_extra_fields_leave_intent(self):
        self.prepare()
        original = self.adapter.create
        def wrong(request):
            result = original(request)
            result["extra"] = "synthetic detail"
            return result
        with patch.object(self.adapter, "create", side_effect=wrong), self.assertRaises(CORE.Stop) as caught:
            self.apply()
        self.assertEqual(str(caught.exception), "write_or_verification_uncertain")
        self.assertEqual(self.receipt()["operations"][0]["state"], "attempting")
        self.reconcile()
        self.assertEqual(self.receipt()["operations"][0]["state"], "verified")

    def test_changed_remote_body_is_not_adopted_during_reconciliation(self):
        self.prepare()
        self.adapter.fault = ("details-" + P1, "after-write")
        with self.assertRaises(Crash):
            self.apply()
        self.adapter.fault = None
        self.adapter.records["details-" + P1]["data"]["body"] += " edited"
        with self.assertRaisesRegex(CORE.Stop, "reconciliation_unresolved"):
            self.reconcile()

    def test_each_role_failed_direct_read_preserves_returned_mapping(self):
        self.all_roles()
        plan = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        for role in CORE.MULTI_ROLES:
            with self.subTest(role=role):
                self.store = self.root / role.replace(".", "-")
                self.adapter = FakeAdapter(self.binding)
                self.factory = FakeFactory(self.adapter)
                self.prepare()
                index = next(i for i, op in enumerate(plan["operations"]) if op["kind"] == role)
                op = plan["operations"][index]
                self.adapter.fault = (op["id"], "readback")
                with self.assertRaises(Crash):
                    self.apply()
                row = self.receipt()["operations"][index]
                self.assertEqual(row["state"], "created-unverified")
                identifier = row["artifact"]["id"]
                self.adapter.fault = None
                self.reconcile()
                self.assertEqual(self.receipt()["operations"][index]["artifact"]["id"], identifier)
                self.assertEqual(sum(a == "write" and key == op["id"] for a, key in self.adapter.calls), 1)

    def test_returned_url_parent_owner_and_destination_are_strict(self):
        self.all_roles()
        self.prepare()
        self.apply()
        plan, receipt = self.plan(), self.receipt()
        for operation, row in zip(plan["operations"], receipt["operations"]):
            request = CORE.request_for(plan, receipt, operation)
            mutations = [lambda a: a.update(id=True), lambda a: a.update(url="https://other.example.invalid/item"),
                         lambda a: a["destination"].update(target_id="another-target"),
                         lambda a: a["audience"].update(id="another-audience")]
            for key in ("owner_id", "parent_id", "project_id", "target_id"):
                if key in row["artifact"]["data"]:
                    mutations.append(lambda a, key=key: a["data"].update({key: "another-id"}))
            for mutate in mutations:
                changed = copy.deepcopy(row["artifact"])
                mutate(changed)
                with self.subTest(role=operation["kind"]), self.assertRaises(CORE.Stop):
                    CORE.artifact_check(changed, plan, request)

    def test_receipt_history_policy_and_attempt_cannot_be_reset(self):
        self.prepare()
        self.apply()
        plan, receipt = self.plan(), self.receipt()
        mutations = (lambda r: r.update(version=True), lambda r: r.update(policy={}),
                     lambda r: r.update(authorities=[]), lambda r: r.update(history=[]),
                     lambda r: r["operations"][0].update(attempt=0),
                     lambda r: r["history"][0].update(reason="untrusted-detail"))
        for mutate in mutations:
            changed = copy.deepcopy(receipt)
            mutate(changed)
            with self.assertRaises(CORE.Stop):
                CORE.receipt_check(changed, plan)

    def test_lost_result_requires_one_exact_match_and_direct_identity_read(self):
        for variant in ("duplicate", "incomplete", "wrong-read-id"):
            with self.subTest(variant=variant):
                self.store = self.root / variant
                self.adapter = FakeAdapter(self.binding)
                self.factory = FakeFactory(self.adapter)
                self.prepare()
                self.adapter.fault = ("details-" + P1, "after-write")
                with self.assertRaises(Crash):
                    self.apply()
                self.adapter.fault = None
                self.adapter.extra_match = variant == "duplicate"
                self.adapter.complete = variant != "incomplete"
                original_read = self.adapter.read
                def read(request, identifier):
                    actual = original_read(request, identifier)
                    if variant == "wrong-read-id":
                        actual["id"] = "other-id"
                        actual["url"] += "-other"
                    return actual
                with patch.object(self.adapter, "read", side_effect=read), self.assertRaisesRegex(CORE.Stop, "reconciliation_unresolved"):
                    self.reconcile()
                self.assertIsNone(self.receipt()["operations"][0]["artifact"])
                self.assertEqual(sum(a == "write" for a, op in self.adapter.calls), 1)

    def test_checkpoint_boundaries_for_every_role_preserve_last_durable_state(self):
        self.all_roles()
        plan = CORE.make_multi_plan(self.selection, "prepare", self.binding)
        original = CORE.checkpoint
        for role in CORE.MULTI_ROLES:
            index = next(i for i, op in enumerate(plan["operations"]) if op["kind"] == role)
            for checkpoint_number in (1, 2, 3):
                for after in (False, True):
                    with self.subTest(role=role, checkpoint=checkpoint_number, after=after):
                        self.store = self.root / (role.replace(".", "-") + f"-{checkpoint_number}-{after}")
                        self.adapter = FakeAdapter(self.binding)
                        self.factory = FakeFactory(self.adapter)
                        self.prepare()
                        count = 0
                        def fault(store, receipt, saved_plan):
                            nonlocal count
                            count += 1
                            hit = count == index * 3 + checkpoint_number
                            if hit and not after:
                                raise OSError("owned pre-checkpoint fault")
                            original(store, receipt, saved_plan)
                            if hit:
                                raise OSError("owned post-checkpoint fault")
                        with patch.object(CORE, "checkpoint", side_effect=fault), self.assertRaises((OSError, CORE.Stop)):
                            self.apply()
                        states = ("planned", "attempting", "created-unverified", "verified")
                        expected = states[checkpoint_number if after else checkpoint_number - 1]
                        self.assertEqual(self.receipt()["operations"][index]["state"], expected)
                        CORE.status(self.store)
                        writes_before = sum(a == "write" for a, op in self.adapter.calls)
                        if expected in {"created-unverified", "verified"}:
                            self.reconcile()
                            self.assertEqual(sum(a == "write" for a, op in self.adapter.calls), writes_before)
                        if expected == "attempting":
                            with self.assertRaisesRegex(CORE.Stop, "reconciliation_required"):
                                self.apply()

    def test_old_v1_and_v2_remain_distinct_and_no_new_cli_route(self):
        cases = json.loads((ROOT / "tests/fixtures/proposal-publication/cases.json").read_bytes())
        old = CORE.make_plan(cases["proposals"], "prepare")
        self.assertEqual(old["version"], 1)
        CORE.plan_check(old)
        with self.assertRaises(CORE.Stop):
            CORE.make_github_plan(self.selection, "prepare", self.binding)
        self.assertFalse(hasattr(CORE, "multi_provider"))


if __name__ == "__main__":
    unittest.main()
