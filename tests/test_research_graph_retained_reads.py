"""Retained-capture assignments must require new reading, without a new fetch."""

import copy
from datetime import datetime, timezone
from pathlib import Path
import unittest

import test_research_graph_work as fixture


WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
EXPORTS = INGEST._sibling("research_graph_exports")


class RetainedReadTests(unittest.TestCase):
    setUp = fixture.ResearchGraphWorkTests.setUp
    source = fixture.ResearchGraphWorkTests.source
    item = fixture.ResearchGraphWorkTests.item
    save_plan = fixture.ResearchGraphWorkTests.save_plan
    rows = fixture.ResearchGraphWorkTests.rows
    row = fixture.ResearchGraphWorkTests.row
    binding = fixture.ResearchGraphWorkTests.binding
    claim = fixture.ResearchGraphWorkTests.claim
    spec = fixture.ResearchGraphWorkTests.spec
    admit = fixture.ResearchGraphWorkTests.admit
    result = fixture.ResearchGraphWorkTests.result
    returned = fixture.ResearchGraphWorkTests.returned
    error = fixture.ResearchGraphWorkTests.error

    def capture_original(self, formats=("html",)):
        original = self.claim()
        self.admit(original)
        self.result()
        self.returned(original)
        WORK.complete_work(self.root, original, expected_revision=2, reason="Original current reading completed")
        self.items[0].update(status="done", reason="Original source read")
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["reads"].append("read-one")
        self.capture_ids = [INGEST.capture_id("read-one", "html")]
        for fmt in formats[1:]:
            identity = "read-format-" + fmt
            item = self.item(identity, "reader", formats=[fmt])
            item["request"]["format"] = fmt
            self.items.append(item)
            self.save_plan()
            self.registration = WORK.register_plan(self.root, self.plan_hash)
            assigned = self.claim(identity, enforce_fairness=False)
            self.admit(assigned, operation=identity)
            self.result(operation=identity, formats=(fmt,))
            self.returned(assigned, operation=identity, identity="return-" + fmt, formats=(fmt,))
            self.complete(assigned)
            item.update(status="done", reason="Additional format captured and inspected")
            self.ledger["sources"][0]["discovered_by"].append(identity)
            self.ledger["sources"][0]["reads"].append(identity)
            self.capture_ids.append(INGEST.capture_id(identity, fmt))
        self.assertTrue(all(self.row("captures", identity)["version_id"] is None for identity in self.capture_ids))
        return original

    def plan_reread(self, **annotations):
        self.items.append(self.item("reread", "reader", **annotations))
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        return self.claim("reread", enforce_fairness=False)

    def complete(self, assignment):
        current = self.row("assignments", assignment)
        return WORK.complete_work(self.root, assignment, expected_revision=current["revision"], reason="Current assigned inspection of exact retained content")

    def correct_and_complete(self, assignment):
        assigned = self.row("assignments", assignment)
        work = self.row("work_items", assigned["work_id"])
        intent = copy.deepcopy(work["request_json"])
        intent.pop("freshness_basis")
        intent["capture_ids"] = self.capture_ids
        reason = "Complete actual reread with corrected retained-capture intent; original freshness requirement was mistaken"
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [
                WORK._update("assignments", assigned, state="completed", finished_at=datetime.now(timezone.utc).isoformat()),
                WORK._update("work_items", work, request_json=intent,
                             logical_key=GRAPH.logical_key(dict(work, request_json=intent)), state="completed",
                             transition_reason=reason, terminal_reason=reason)])
        return WORK._ingest(self.root, packet)

    def exported_context(self, identity):
        directory = Path(EXPORTS.export_snapshot(self.root, identity)["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        return ledger, EXPORTS.validate_ledger(self.root, ledger)

    def done_plan(self):
        plan = copy.deepcopy(BUNDLE.plan_snapshot(self.root)[0])
        item = next(i for i in plan["items"] if i["id"] == "reread")
        item["status"] = "done"
        item["reason"] = "Current assigned reading of retained evidence completed"
        item["graph"].pop("freshness_basis", None)
        item["graph"]["capture_ids"] = self.capture_ids
        return plan

    def test_completed_retained_plan_saves_and_renders_without_new_execution(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        self.returned(assignment, identity="current-plan-reread")
        self.complete(assignment)
        ledger, context = self.exported_context("retained-plan-complete")
        self.assertEqual(len(context["retained_plan_completions"]), 1)
        before = (self.root / "research-budget.json").read_bytes()
        BUNDLE.save_plan(self.root, self.done_plan(), ledger)
        saved = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        WORK.register_plan(self.root, saved)
        BUNDLE.render_plan(self.root)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        self.assertEqual(len(self.rows("operation_links")), 1)

    def test_atomic_policy_correction_has_truthful_done_plan(self):
        self.capture_original()
        assignment = self.plan_reread(purpose="Reassess retained source content")
        self.returned(assignment, identity="current-corrected-plan")
        self.correct_and_complete(assignment)
        ledger, _ = self.exported_context("corrected-plan-complete")
        BUNDLE.save_plan(self.root, self.done_plan(), ledger)
        WORK.register_plan(self.root, BUNDLE.read(self.root / "bundle.json")["plan_sha256"])

    def test_retained_done_rejects_pending_partial_or_changed_work(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        self.returned(assignment, identity="partial-plan-read", coverage="partial",
                      inspected_extent={"body": {"unit": "line", "ranges": [{"start": 1, "end": 1}]}})
        ledger, _ = self.exported_context("partial-plan")
        with self.assertRaisesRegex(ValueError, "done retained read lacks"):
            BUNDLE.save_plan(self.root, self.done_plan(), ledger)
        self.returned(assignment, identity="whole-plan-read")
        self.complete(assignment)
        ledger, context = self.exported_context("whole-plan")
        for field, value in (("capture_ids", ["unknown-capture"]), ("scope_key", "other-scope"),
                             ("extent", {"body": "all"}), ("questions", ["different-question"]),
                             ("formats", ["text"]), ("freshness_basis", "captured_after_assignment")):
            with self.subTest(field=field):
                plan = self.done_plan()
                plan["items"][-1]["graph"][field] = value
                with self.assertRaisesRegex(ValueError, "done retained read lacks"):
                    BUNDLE.checked_plan(plan, self.contract, ledger, BUNDLE.plan_operations(self.root), graph_context=context)
        plan = self.done_plan()
        plan["items"][-1]["actor"] = "different-reader"
        with self.assertRaisesRegex(ValueError, "done retained read lacks"):
            BUNDLE.checked_plan(plan, self.contract, ledger, BUNDLE.plan_operations(self.root), graph_context=context)

    def test_withdrawal_removes_current_plan_credit_preserves_old_render(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        self.returned(assignment, identity="withdrawn-plan-read")
        self.complete(assignment)
        ledger, _ = self.exported_context("plan-before-withdrawal")
        BUNDLE.save_plan(self.root, self.done_plan(), ledger)
        WORK.register_plan(self.root, BUNDLE.read(self.root / "bundle.json")["plan_sha256"])
        evidence = self.root / "reviews/retained-read-withdrawal.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text('{"basis":"Fictional reader retracts the declared inspection."}\n')
        inspected = next(i for i in self.rows("inspections") if i["return_id"] == "withdrawn-plan-read")
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [WORK._insert("inspection_withdrawals",
                withdrawal_id="withdraw-plan-read", inspection_id=inspected["inspection_id"], actor_id="coordinator",
                reason="Reader withdrew the full reading", evidence_path=str(evidence.relative_to(self.root)),
                evidence_sha256=GRAPH._sha(evidence.read_bytes()), withdrawn_at=datetime.now(timezone.utc).isoformat())])
        WORK._ingest(self.root, packet)
        current, context = self.exported_context("plan-after-withdrawal")
        self.assertEqual(context["retained_plan_completions"], [])
        with self.assertRaisesRegex(ValueError, "done retained read lacks"):
            BUNDLE.save_plan(self.root, self.done_plan(), current)
        BUNDLE.render_plan(self.root)

    def test_unversioned_capture_requires_new_inspection_but_no_provider_call(self):
        self.capture_original()
        original_files = {p.relative_to(self.root): p.read_bytes() for directory in ["operations", "reader-returns", "plans"]
                          for p in (self.root / directory).rglob("*") if p.is_file()}
        capture = self.row("captures", self.capture_ids[0])
        attempts = BUDGET.status(self.root)["read_attempts"]
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        self.error("pending_dependency", self.complete, assignment)
        self.returned(assignment, identity="actual-reread")
        self.complete(assignment)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], attempts)
        self.assertEqual(self.row("captures", self.capture_ids[0]), capture)
        self.assertEqual(len(self.rows("inspections")), 2)
        for relative, original in original_files.items():
            self.assertEqual((self.root / relative).read_bytes(), original, str(relative))

    def test_partial_inspection_does_not_complete_pinned_body(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        partial = {"body": {"unit": "line", "ranges": [{"start": 1, "end": 1}]}}
        self.returned(assignment, identity="partial-reread", coverage="partial", inspected_extent=partial)
        self.error("pending_dependency", self.complete, assignment)

    def test_atomic_correction_rolls_back_for_missing_or_partial_current_reading(self):
        self.capture_original()
        assignment = self.plan_reread(purpose="Reassess retained source content")
        assigned = self.row("assignments", assignment)
        work = self.row("work_items", assigned["work_id"])
        self.error("pending_dependency", self.correct_and_complete, assignment)
        self.assertEqual(self.row("assignments", assignment), assigned)
        self.assertEqual(self.row("work_items", work["work_id"]), work)
        partial = {"body": {"unit": "line", "ranges": [{"start": 1, "end": 1}]}}
        self.returned(assignment, identity="partial-current-reading", coverage="partial", inspected_extent=partial)
        self.error("pending_dependency", self.correct_and_complete, assignment)
        self.assertEqual(self.row("assignments", assignment), assigned)
        self.assertEqual(self.row("work_items", work["work_id"]), work)

    def test_retained_work_does_not_authorize_new_provider_admission(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        attempts = BUDGET.status(self.root)["read_attempts"]
        with self.assertRaises(GRAPH.GraphError):
            WORK.prepare_operation(self.root, self.spec(assignment, "unwanted-refetch"))
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], attempts)
        self.assertFalse((self.root / "operations/unwanted-refetch").exists())

    def test_same_source_different_capture_does_not_satisfy_pin(self):
        self.capture_original(formats=("html", "text"))
        assignment = self.plan_reread(capture_ids=[self.capture_ids[0]])
        count = len(self.rows("inspections"))
        with self.assertRaises(GRAPH.GraphError):
            self.returned(assignment, identity="wrong-capture", formats=(self.capture_ids[1],))
        self.assertEqual(len(self.rows("inspections")), count)

    def test_multiple_pins_are_sorted_and_every_requested_format_is_inspected(self):
        self.capture_original(formats=("html", "text"))
        assignment = self.plan_reread(capture_ids=list(reversed(self.capture_ids)), formats=["html", "text"])
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        self.assertEqual(work["request_json"]["capture_ids"], sorted(self.capture_ids))
        self.returned(assignment, identity="only-one-format", coverage="partial")
        self.error("pending_dependency", self.complete, assignment)
        self.returned(assignment, identity="both-formats", formats=self.capture_ids)
        self.complete(assignment)

    def test_direct_reconciliation_cannot_bind_unknown_capture(self):
        self.capture_original()
        assignment = self.plan_reread(purpose="Review retained content")
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        intent = copy.deepcopy(work["request_json"])
        intent.pop("freshness_basis")
        intent["capture_ids"] = ["missing-capture"]
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [WORK._update("work_items", work,
                request_json=intent, logical_key=GRAPH.logical_key(dict(work, request_json=intent)),
                transition_reason="Request a specific retained capture")])
        with self.assertRaises(GRAPH.GraphError):
            WORK._ingest(self.root, packet)
        self.assertEqual(self.row("work_items", work["work_id"]), work)

    def test_invalid_pins_and_conflicting_policy_are_rejected(self):
        self.capture_original()
        valid = self.capture_ids[0]
        cases = [
            {"capture_ids": []},
            {"capture_ids": valid},
            {"capture_ids": [valid, valid]},
            {"capture_ids": ["missing-capture"]},
            {"capture_ids": [valid], "version_id": "unknown-version"},
            {"capture_ids": [valid], "freshness_basis": "captured_after_assignment"},
            {"capture_ids": [valid], "formats": ["text"]},
        ]
        for annotations in cases:
            with self.subTest(annotations=annotations):
                plan = BUNDLE.read(self.root / "plans" / (self.plan_hash + ".json"))
                plan["items"] = [self.item("invalid-pinned-read", "reader", **annotations)]
                with self.assertRaises((GRAPH.GraphError, ValueError)):
                    WORK.validate_plan(self.root, plan, self.ledger)

    def test_capture_of_different_artifact_is_rejected(self):
        self.capture_original()
        plan = BUNDLE.read(self.root / "plans" / (self.plan_hash + ".json"))
        plan["items"] = [self.item("wrong-target", "reader", source="doc-b", capture_ids=self.capture_ids)]
        with self.assertRaises(GRAPH.GraphError):
            WORK.validate_plan(self.root, plan, self.ledger)

    def test_tampered_retained_bytes_cannot_be_planned_or_completed(self):
        self.capture_original()
        assignment = self.plan_reread(capture_ids=self.capture_ids)
        self.returned(assignment, identity="current-reread")
        (self.root / "operations/read-one/stdout").write_text("Changed fictional evidence.\n")
        with self.assertRaises(GRAPH.GraphError):
            self.complete(assignment)

    def test_explicit_current_intent_correction_retains_original_freshness_history(self):
        self.capture_original()
        # Deliberately reproduce the live operator's wrong default policy.
        assignment = self.plan_reread(purpose="Reassess retained source content")
        self.returned(assignment, identity="retained-read-under-wrong-policy")
        self.error("pending_dependency", self.complete, assignment)
        original_plan = (self.root / "plans" / (self.plan_hash + ".json")).read_bytes()
        original_return = (self.root / "reader-returns/retained-read-under-wrong-policy.json").read_bytes()
        original_budget = (self.root / "research-budget.json").read_bytes()
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        previous = copy.deepcopy(work)
        intent = copy.deepcopy(work["request_json"])
        intent.pop("freshness_basis")
        intent["capture_ids"] = self.capture_ids
        changed = dict(work, request_json=intent)
        key = GRAPH.logical_key(changed)
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [WORK._update("work_items", work,
                request_json=intent, logical_key=key,
                transition_reason="Explicit correction: the assigned task rereads this retained capture; original freshness requirement was mistaken")])
        # Active work intent is immutable. A correction may close it only as an
        # atomic coordinator completion with actual current inspection proof.
        with self.assertRaises(GRAPH.GraphError):
            WORK._ingest(self.root, packet)
        self.correct_and_complete(assignment)
        self.assertEqual((self.root / "plans" / (self.plan_hash + ".json")).read_bytes(), original_plan)
        self.assertEqual((self.root / "reader-returns/retained-read-under-wrong-policy.json").read_bytes(), original_return)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), original_budget)
        self.assertEqual(previous["request_json"]["freshness_basis"], "captured_after_assignment")
        self.assertNotIn("freshness_basis", self.row("work_items", work["work_id"])["request_json"])
        self.assertEqual(self.row("work_items", work["work_id"])["state"], "completed")


if __name__ == "__main__":
    unittest.main()
