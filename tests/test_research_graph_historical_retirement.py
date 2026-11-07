"""Retire previously accepted invalid intent without granting current authority.

Only fixture construction restores a former scope policy. Recovery, replacement
reads, recorder evidence and replay run with all current validators enabled.
All outputs are owned fictional files; no provider or network is used.
"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture

WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET


class HistoricalRetirementTests(unittest.TestCase):
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
    question_binding_fixture = fixture.ResearchGraphWorkTests.question_binding_fixture

    def historical_question(self, admitted=False):
        self.question_binding_fixture()
        with patch.object(GRAPH, "plan_item_questions", return_value=["decision", "outcomes"]):
            work = self.binding("outcomes-only")["work_id"]
            assignment = WORK.claim(self.root, work, "reader", self.plan_hash, "decision-only", expected_revision=1)["assignment_id"]
            WORK.start_assignment(self.root, assignment, expected_revision=1)
            if admitted:
                self.admit(assignment)
        return assignment

    def historical_empty(self, admitted=False, inspected=False):
        extent = {"body": {"unit": "line", "ranges": []}}
        self.items[0]["graph"]["extent"] = extent
        with patch.object(GRAPH, "meaningful_read_extent", return_value=True):
            self.save_plan()
            self.registration = WORK.register_plan(self.root, self.plan_hash)
            assignment = self.claim()
            if admitted or inspected:
                self.admit(assignment)
            if inspected:
                self.result()
                self.returned(assignment, inspected_extent=extent)
        return assignment

    def retained(self):
        return {p.relative_to(self.root): p.read_bytes() for name in ("plans", "operations", "reader-returns")
                for p in (self.root / name).rglob("*") if p.is_file()}

    def assert_history(self, events, retained, budget):
        self.assertEqual(self.rows("graph_events")[:len(events)], events)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)
        for path, data in retained.items():
            self.assertEqual((self.root / path).read_bytes(), data)
        GRAPH.validate(self.root)
        GRAPH.recover(self.root, apply=True)
        self.assertEqual(self.rows("graph_events")[:len(events)], events)

    def test_wrong_question_release_allows_correct_assignment_and_actual_read(self):
        assignment = self.historical_question()
        original = self.row("assignments", assignment)
        events, retained = self.rows("graph_events"), self.retained()
        budget = (self.root / "research-budget.json").read_bytes()
        self.error("scope_mismatch", WORK.prepare_operation, self.root, self.spec(assignment))
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Original assignment used the wrong coverage item")
        self.assert_history(events, retained, budget)
        self.assertEqual(self.row("assignments", assignment)["plan_item_id"], original["plan_item_id"])
        self.assertEqual(self.row("assignments", assignment)["state"], "released")
        replacement = self.claim("outcomes-only", enforce_fairness=False)
        self.admit(replacement)
        self.result()
        self.returned(replacement)
        WORK.complete_work(self.root, replacement, expected_revision=2, reason="Correct question and actual retained inspection")
        self.assertEqual(self.row("work_items", original["work_id"])["state"], "completed")
        self.assertEqual({r["question_id"] for r in self.rows("sufficient_inspection")}, {"outcomes"})
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_wrong_question_reconciliation_cannot_resume_but_can_bound(self):
        assignment = self.historical_question()
        WORK.reconcile_assignment(self.root, assignment, expected_revision=2, reason="Inspect original scope mismatch")
        self.error("scope_mismatch", WORK.resume_assignment, self.root, assignment,
                   expected_revision=3, worker_identity="reader", reason="Invalid authority is not renewed")
        WORK.bound_work(self.root, assignment, expected_revision=3, state="cancelled", reason="Retired invalid original assignment")
        self.assertEqual(self.row("assignments", assignment)["state"], "released")
        self.assertEqual(self.rows("inspections"), [])
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 0)

    def test_empty_scope_release_closed_plan_and_corrected_read_preserve_originals(self):
        assignment = self.historical_empty(inspected=True)
        old_work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        events, retained = self.rows("graph_events"), self.retained()
        budget = (self.root / "research-budget.json").read_bytes()
        inspections = self.rows("inspections")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment,
                   expected_revision=2, reason="Vacuous scope supplies no completion")
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Retain empty historical scope without credit")
        self.assert_history(events, retained, budget)
        self.assertEqual(self.rows("inspections"), inspections)
        self.error("invalid_packet", self.claim)
        self.items[0].update(status="dropped", reason="Invalid historical scope retired; corrected scope follows")
        corrected = self.item("corrected", "reader")
        corrected["request"]["extent"] = "Corrected explicit inspection of body lines one through two"
        self.items.append(corrected)
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["reads"].append("read-one")
        # Use the ordinary pure preflight and publication hooks for continuation.
        with patch.object(BUNDLE, "sync_graph_plan", self.automatic_plan_hook):
            self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        self.assertEqual(self.row("work_items", old_work["work_id"])["state"], "cancelled")
        replacement = self.claim("corrected", enforce_fairness=False)
        self.admit(replacement, "corrected-read")
        self.result("corrected-read")
        self.returned(replacement, "corrected-read", "corrected-return")
        WORK.complete_work(self.root, replacement, expected_revision=2, reason="Valid scope and separate actual inspection")
        self.assertEqual(self.row("work_items", self.row("assignments", replacement)["work_id"])["state"], "completed")
        self.assertEqual(self.row("work_items", old_work["work_id"])["request_json"], old_work["request_json"])
        for path, data in retained.items():
            self.assertEqual((self.root / path).read_bytes(), data)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 2)
        GRAPH.recover(self.root, apply=True)

    def test_unresolved_originals_block_retirement_until_checked_cancellation(self):
        for kind in ("question", "empty"):
            with self.subTest(kind=kind):
                self.setUp()
                assignment = (self.historical_question if kind == "question" else self.historical_empty)(admitted=True)
                events, retained = self.rows("graph_events"), self.retained()
                budget = (self.root / "research-budget.json").read_bytes()
                self.error("pending_dependency", WORK.release_assignment, self.root, assignment,
                           expected_revision=2, reason="Unresolved work cannot be released")
                WORK.reconcile_operation(self.root, "read-one", expected_revision=2, worker_identity="reader",
                                         worker_stopped=True, cancel=True, reason="Checked original fixture worker stopped")
                WORK.bound_work(self.root, assignment, expected_revision=2, state="cancelled", reason="Invalid historical scope retired")
                self.assert_history(events, retained, budget)
                self.assertEqual(self.row("operation_links", "read-one")["execution_state"], "cancelled")
                self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
                self.assertEqual(self.rows("inspections"), [])

    def test_empty_scope_cannot_admit_or_gain_raw_assignment_authority(self):
        assignment = self.historical_empty()
        self.error("scope_mismatch", WORK.prepare_operation, self.root, self.spec(assignment))
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Invalid old scope released")
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [WORK._insert("assignments", assignment_id="new-invalid",
                work_id=self.row("assignments", assignment)["work_id"], actor_id="reader", claimed_at=WORK._now(),
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), generation_at_claim=0,
                plan_item_id="read-a", plan_sha256=self.plan_hash)])
        self.error("invalid_packet", GRAPH.ingest, self.root, packet, evidence_validator=WORK.evidence_validator)
        self.assertIsNone(self.row("assignments", "new-invalid"))
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 0)

    def test_raw_retirement_needs_paired_reason_and_cannot_smuggle_scope_or_credit(self):
        assignment = self.historical_question()
        original = self.row("assignments", assignment)
        work = self.row("work_items", original["work_id"])
        for changes in (None, {"state": "pending"}, {"state": "pending", "transition_reason": " "},
                        {"state": "completed", "transition_reason": "Attempted credit"},
                        {"state": "pending", "transition_reason": "Changed intent", "purpose": "Replacement purpose"}):
            with self.subTest(changes=changes), GRAPH.snapshot(self.root) as conn:
                mutations = [WORK._update("assignments", original, state="released", finished_at=WORK._now())]
                if changes is not None:
                    mutations.append(WORK._update("work_items", work, **changes))
                packet = WORK._packet(self.root, conn, mutations)
            self.error("scope_mismatch", GRAPH.ingest, self.root, packet, evidence_validator=WORK.evidence_validator)
            self.assertEqual(self.row("assignments", assignment), original)
            self.assertEqual(self.row("work_items", work["work_id"]), work)

    def test_new_closed_empty_scope_cannot_masquerade_as_historical_work(self):
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        plan["items"][0].update(status="dropped", reason="There is no accepted historical empty work")
        plan["items"][0]["graph"]["extent"] = {"body": {"unit": "line", "ranges": []}}
        self.error("scope_mismatch", WORK.validate_plan, self.root, plan, self.ledger)


if __name__ == "__main__":
    unittest.main()
