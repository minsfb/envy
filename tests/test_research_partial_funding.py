"""Actual incremental costs and question-scoped partial resolution alternatives."""

import copy
import unittest
from unittest.mock import patch

import test_research_partial_closeout as fixture

BUNDLE, BUDGET, GRAPH, WORK, FOLLOWUPS = fixture.BUNDLE, fixture.BUDGET, fixture.GRAPH, fixture.WORK, fixture.FOLLOWUPS


class PartialFundingTests(unittest.TestCase):
    def setUp(self):
        self.run = fixture.ResearchPartialCloseoutTests()
        self.run.setUp()
        self.addCleanup(self.run.doCleanups)

    def configure(self, **limits):
        run = self.run
        run.contract["providers"]["archive"]["reference_hosts"] = []
        run.contract["research_budget"] = limits
        BUNDLE.amend(run.root, run.contract)
        run.save_plan()
        run.registration = WORK.register_plan(run.root, run.plan_hash)

    def work_target(self, item):
        identity = self.run.binding(item)["work_id"]
        return next(target for target in FOLLOWUPS.status(self.run.root)["unresolved"] if target.get("work_id") == identity)

    def false_metric_request(self, request, target, metric):
        request = copy.deepcopy(request)
        entry = next(entry for entry in request["unresolved"] if entry["key"] == target["key"])
        entry.pop("capacity_remaining", None)
        entry.pop("blocked_options", None)
        entry.update(disposition="allocation-deferred", allocation=target["allocation"], metric=metric)
        return request

    def check_reassessment(self, metric, *, retained):
        run = self.run
        self.configure(**{metric: 4})
        run.seed(leads=False, references=False)
        run.add(run.item("baseline-c", source="doc-c"))
        run.complete_read("baseline-c", "baseline-c-op")
        item = run.item("reassess-a", scope_key="second-scope")
        if retained:
            item["graph"]["capture_ids"] = [row["capture_id"] for row in run.rows("captures") if row["operation_id"] == "read-one"]
        else:
            item["request"]["format"] = "text"
        run.add(item)
        run.bound_fixture_sources()
        target = self.work_target("reassess-a")
        report = FOLLOWUPS.status(run.root)
        self.assertEqual(report["remaining_capacity"]["allocations"]["baseline"][metric], 0)
        self.assertEqual(report["remaining_capacity"]["global"][metric], 2)
        self.assertNotIn(metric, target["funding_options"][0]["required_metrics"])
        request = run.partial_request()
        with self.assertRaisesRegex(GRAPH.GraphError, "relevant metric"):
            FOLLOWUPS.checkpoint(run.root, self.false_metric_request(request, target, metric))
        FOLLOWUPS.checkpoint(run.root, request)
        before = BUDGET.status(run.root)
        assignment = run.claim("reassess-a", enforce_fairness=False)
        run.activity()
        if retained:
            BUDGET.assess(run.root, "read-one", "reader", assignment_validator=WORK.prepare_assessment)
        else:
            run.admit(assignment, "fresh-reassessment")
        self.assertEqual(BUDGET.status(run.root)[metric], before[metric])

    def test_retained_read_needs_no_new_read_attempt(self):
        self.check_reassessment("read_attempts", retained=True)

    def test_retained_assessed_source_needs_no_new_assessment_slot(self):
        self.check_reassessment("assessed_artifacts", retained=True)

    def test_fresh_read_of_assessed_source_still_needs_retrieval_only(self):
        self.check_reassessment("assessed_artifacts", retained=False)
        self.assertIn("read_attempts", self.work_target("reassess-a")["funding_options"][0]["required_metrics"])

    def test_retained_assessment_uses_original_capture_identity_across_alias(self):
        run = self.run
        self.configure(assessed_artifacts=4)
        run.seed(leads=False, references=False)
        with GRAPH.snapshot(run.root) as conn:
            original = WORK._artifact(conn, "doc-a")
            alias = WORK._artifact(conn, "doc-b")
            packet = WORK._packet(run.root, conn, [WORK._update("artifacts", run.row("artifacts", alias),
                canonical_artifact_id=original, canonical_basis="Both fictional IDs identify the same record")])
        WORK._ingest(run.root, packet)
        captures = [row["capture_id"] for row in run.rows("captures") if row["operation_id"] == "read-one"]
        run.add(run.item("alias-assessment", source="doc-b", capture_ids=captures))
        option, = self.work_target("alias-assessment")["funding_options"]
        self.assertEqual(option["assessment_source_id"], "doc-a")
        self.assertNotIn("doc-b", BUDGET.status(run.root)["assessed_artifact_ids"])
        self.assertNotIn("assessed_artifacts", option["required_metrics"])
        FOLLOWUPS.checkpoint(run.root, run.partial_request())
        run.claim("alias-assessment", enforce_fairness=False)
        run.activity()
        BUDGET.assess(run.root, "read-one", "reader", assignment_validator=WORK.prepare_assessment)
        self.assertEqual(BUDGET.status(run.root)["assessed_artifact_ids"], ["doc-a"])

    def test_retained_unassessed_capture_still_requires_an_assessment_slot(self):
        run = self.run
        self.configure(assessed_artifacts=4)
        for suffix in ("b", "c"):
            run.add(run.item("baseline-" + suffix, source="doc-" + suffix))
            run.complete_read("baseline-" + suffix, "baseline-" + suffix + "-op")
        run.activity()
        assignment = run.claim("read-a", enforce_fairness=False)
        original_spec = run.spec
        def capture_only(*args, **kwargs):
            spec = original_spec(*args, **kwargs)
            spec["research"]["assess"] = False
            return spec
        with patch.object(run, "spec", side_effect=capture_only):
            run.admit(assignment)
        run.result()
        WORK.bound_work(run.root, assignment, expected_revision=2, reason="Capture retained; inspection remains unperformed")
        run.items[0]["status"] = "bounded"
        run.stop_activity()
        captures = [row["capture_id"] for row in run.rows("captures") if row["operation_id"] == "read-one"]
        run.add(run.item("inspect-unassessed", capture_ids=captures))
        target = self.work_target("inspect-unassessed")
        option, = target["funding_options"]
        self.assertIn("assessed_artifacts", option["required_metrics"])
        self.assertNotIn("read_attempts", option["required_metrics"])
        request = run.partial_request()
        FOLLOWUPS.checkpoint(run.root, request)
        incorrect = copy.deepcopy(request)
        incorrect["id"] = "false-funded-assessment"
        entry = next(entry for entry in incorrect["unresolved"] if entry["key"] == target["key"])
        entry.pop("allocation")
        entry.pop("metric")
        entry.update(disposition="discretionary-unresolved", capacity_remaining=True)
        with self.assertRaisesRegex(GRAPH.GraphError, "usable capacity"):
            FOLLOWUPS.checkpoint(run.root, incorrect)
        run.claim("inspect-unassessed", enforce_fairness=False)
        run.activity()
        with self.assertRaisesRegex(ValueError, "assessed_artifacts"):
            BUDGET.assess(run.root, "read-one", "reader", assignment_validator=WORK.prepare_assessment)

    def surface(self):
        run = self.run
        run.contract["providers"]["archive"]["reference_hosts"] = ["boards.example.invalid"]
        BUNDLE.amend(run.root, run.contract)
        run.save_plan()
        run.registration = WORK.register_plan(run.root, run.plan_hash)
        return next(target for target in FOLLOWUPS.status(run.root)["unresolved"]
                    if target["kind"] == "source_surface" and target["question"] == "decision")

    def charge(self, allocation, minutes):
        self.run.activity(allocation)
        self.run.now[0] += minutes * 60
        self.run.stop_activity()

    def test_scheduled_surface_uses_funded_resolution_not_exhausted_discovery(self):
        run = self.run
        run.prepare_partial()
        target = self.surface()
        self.assertEqual((target["generation"], target["discovery_allocation"]), (0, "baseline"))
        option, = target["funding_options"]
        self.assertEqual(option["work_id"], run.binding("unread-result")["work_id"])
        self.assertEqual(option["allocation"], "followup-2")
        request = run.partial_request()
        bad = self.false_metric_request(request, target, "active_minutes")
        next(entry for entry in bad["unresolved"] if entry["key"] == target["key"])["allocation"] = "baseline"
        with self.assertRaisesRegex(GRAPH.GraphError, "actual funding"):
            FOLLOWUPS.checkpoint(run.root, bad)
        FOLLOWUPS.checkpoint(run.root, request)
        self.assertEqual(run.synthesis()[0]["status"], "partial")

    def test_scheduled_surface_uses_exhausted_resolution_not_funded_discovery(self):
        run = self.run
        run.prepare_partial(exhausted=False, release_baseline=False)
        self.charge("followup-2", 45)
        target = self.surface()
        capacities = FOLLOWUPS.status(run.root)["remaining_capacity"]
        self.assertGreater(capacities["allocations"]["baseline"]["active_minutes"], 0)
        self.assertEqual(capacities["allocations"]["followup-2"]["active_minutes"], 0)
        request = run.partial_request()
        entry = next(entry for entry in request["unresolved"] if entry["key"] == target["key"])
        self.assertEqual(entry["allocation"], "followup-2")
        FOLLOWUPS.checkpoint(run.root, request)
        self.assertEqual(run.synthesis()[0]["status"], "partial")

    def test_surface_with_a_funded_alternative_cannot_claim_all_options_exhausted(self):
        run = self.run
        run.prepare_partial()
        run.add(run.item("baseline-alternative", source="doc-b"))
        target = self.surface()
        self.assertEqual({option["allocation"] for option in target["funding_options"]}, {"baseline", "followup-2"})
        request = run.partial_request()
        bad = copy.deepcopy(request)
        entry = next(entry for entry in bad["unresolved"] if entry["key"] == target["key"])
        entry.pop("capacity_remaining")
        entry.update(disposition="allocation-deferred", blocked_options=[{
            "option_id": option["option_id"], "allocation": option["allocation"], "metric": "active_minutes"}
            for option in target["funding_options"]])
        with self.assertRaisesRegex(GRAPH.GraphError, "exhausted local allowance"):
            FOLLOWUPS.checkpoint(run.root, bad)
        FOLLOWUPS.checkpoint(run.root, request)
        self.assertEqual(run.synthesis()[0]["status"], "partial")

    def test_all_surface_options_need_current_distinct_exhaustion_proofs(self):
        run = self.run
        run.prepare_partial(exhausted=False, release_baseline=False)
        run.add(run.item("baseline-alternative", source="doc-b"))
        self.surface()
        self.charge("followup-2", 45)
        self.charge("baseline", 25)
        target = self.surface()
        request = run.partial_request()
        entry = next(entry for entry in request["unresolved"] if entry["key"] == target["key"])
        self.assertEqual(len(entry["blocked_options"]), 2)
        for mutation in ("missing", "duplicate", "stale"):
            bad = copy.deepcopy(request)
            proofs = next(entry for entry in bad["unresolved"] if entry["key"] == target["key"])["blocked_options"]
            if mutation == "missing":
                proofs.pop()
            elif mutation == "duplicate":
                proofs[1] = proofs[0]
            else:
                proofs[0]["option_id"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaisesRegex(GRAPH.GraphError, "resolving option"):
                FOLLOWUPS.checkpoint(run.root, bad)
        FOLLOWUPS.checkpoint(run.root, request)
        self.assertEqual(run.synthesis()[0]["status"], "partial")

    def test_other_question_work_cannot_supply_a_resolution_option(self):
        run = self.run
        run.prepare_partial(exhausted=False, release_baseline=False)
        run.contract["questions"].append({"id": "second", "text": "A separate fictional comparison", "central": False})
        for item in run.items:
            item["graph"]["questions"] = ["decision"]
        BUNDLE.amend(run.root, run.contract)
        run.save_plan()
        run.registration = WORK.register_plan(run.root, run.plan_hash)
        run.add(run.item("other-question", source="doc-b", questions=["second"]))
        self.charge("followup-2", 45)
        target = self.surface()
        option, = target["funding_options"]
        self.assertEqual(option["work_id"], run.binding("unread-result")["work_id"])
        self.assertNotEqual(option["work_id"], run.binding("other-question")["work_id"])
        request = run.partial_request()
        entry = next(entry for entry in request["unresolved"] if entry["key"] == target["key"])
        self.assertEqual(entry["disposition"], "allocation-deferred")
        FOLLOWUPS.checkpoint(run.root, request)

    def test_unplanned_count_costs_cannot_prove_exhaustion(self):
        run = self.run
        self.configure(read_attempts=4)
        run.seed(leads=False, references=False)
        run.add(run.item("baseline-c", source="doc-c"))
        run.complete_read("baseline-c", "baseline-c-op")
        target = self.surface()
        option, = target["funding_options"]
        self.assertIsNone(option["work_id"])
        self.assertNotIn("read_attempts", option["required_metrics"])
        self.assertIn("read_attempts", option["possible_metrics"])
        request = run.partial_request()
        with self.assertRaisesRegex(GRAPH.GraphError, "relevant metric"):
            FOLLOWUPS.checkpoint(run.root, self.false_metric_request(request, target, "read_attempts"))
        with self.assertRaisesRegex(GRAPH.GraphError, "usable capacity"):
            FOLLOWUPS.checkpoint(run.root, request)
        run.add(run.item("planned-surface-read", source="doc-b"))
        target = self.surface()
        self.assertIn("read_attempts", target["funding_options"][0]["required_metrics"])
        FOLLOWUPS.checkpoint(run.root, run.partial_request())

    def mixed_scope(self, *, bounded=False):
        run = self.run
        run.contract["questions"].append({"id": "second", "text": "What comparison remains?", "central": False})
        run.items[0]["graph"]["questions"] = ["decision"]
        self.configure()
        original = BUDGET.reader_return
        def two_questions(root, returned):
            for lead in returned["leads"]:
                lead["questions"] = ["decision", "second"]
            return original(root, returned)
        with patch.object(BUDGET, "reader_return", side_effect=two_questions):
            run.seed(leads=True, references=False)
        lead = run.rows("leads")[0]["lead_id"]
        item = run.search_item("first-question", lead_id=lead)
        item["graph"]["questions"] = ["decision"]
        run.add(item)
        run.complete_search("first-question", "question-op", sources=(),
                            pagination="more" if bounded else "complete", next_cursor="next" if bounded else None)
        run.bound_fixture_sources()
        if bounded:
            first = next(item for item in FOLLOWUPS.status(run.root)["items"] if item["question"] == "decision")
            run.checkpoint("first-question-bound", decisions=[{
                "key": first["key"], "basis_sha256": first["basis_sha256"], "disposition": "diminishing-value",
                "reason": "The first question's remaining comparison was explicitly bounded after its actual search."}])
        with GRAPH.snapshot(run.root) as conn:
            packet = WORK._packet(run.root, conn, [WORK._insert("source_outcomes", outcome_id="second-" + artifact["artifact_id"],
                artifact_id=artifact["artifact_id"], question_id="second", required_scope_key="body", readability="unknown",
                disposition="deferred", actor_id="coordinator", observed_at=WORK._now(),
                reason="The fictional second question retains its source-specific coverage limit.") for artifact in run.rows("artifacts")])
        WORK._ingest(run.root, packet)
        request = run.partial_request()
        missing = copy.deepcopy(request)
        missing["unresolved"] = [entry for entry in missing["unresolved"] if not entry["key"].endswith(":second")]
        with self.assertRaises(GRAPH.GraphError):
            FOLLOWUPS.checkpoint(run.root, missing)
        FOLLOWUPS.checkpoint(run.root, request)
        result, context, _ = run.synthesis()
        self.assertEqual(result["status"], "partial")
        scopes = next(obligation for obligation in context["obligation_bindings"] if obligation["lead_id"] == lead)["question_scopes"]
        self.assertEqual({scope["question_id"]: scope["status"] for scope in scopes},
                         {"decision": "bounded" if bounded else "resolved", "second": "unassigned"})
        bad = copy.deepcopy(context)
        next(obligation for obligation in bad["obligation_bindings"] if obligation["lead_id"] == lead)["question_scopes"][0]["status"] = "unknown"
        with self.assertRaisesRegex(GRAPH.GraphError, "unacknowledged"):
            FOLLOWUPS.partial_delivery(FOLLOWUPS.validate_stop(run.root), bad)

    def test_resolved_question_scope_does_not_require_an_unresolved_acknowledgment(self):
        self.mixed_scope()

    def test_independently_bounded_scope_does_not_require_an_unresolved_acknowledgment(self):
        self.mixed_scope(bounded=True)


if __name__ == "__main__":
    unittest.main()
