"""Acceptance checks for read-derived discovery and explicit coverage limits.

All commands use owned synthetic files; no provider services or credentials.
"""

import copy
import unittest

import test_research_work_plan as fixture


class DiscoveryFollowupTests(unittest.TestCase):
    json_file = fixture.ResearchWorkPlanTests.json_file
    cli = fixture.ResearchWorkPlanTests.cli
    search_item = fixture.ResearchWorkPlanTests.search_item
    read_item = fixture.ResearchWorkPlanTests.read_item
    save_plan = fixture.ResearchWorkPlanTests.save_plan
    spec = fixture.ResearchWorkPlanTests.spec
    capture = fixture.ResearchWorkPlanTests.capture
    index = fixture.ResearchWorkPlanTests.index
    source = fixture.ResearchWorkPlanTests.source
    discover = fixture.ResearchWorkPlanTests.discover
    retrieve = fixture.ResearchWorkPlanTests.retrieve
    validate = fixture.ResearchWorkPlanTests.validate

    def setUp(self):
        fixture.ResearchWorkPlanTests.setUp(self)
        fixture.ResearchWorkPlanTests.inspected_bundle(self)
        self.contract["requirements"]["discovery_followups"] = True
        self.cli("amend", "--contract", self.json_file(self.contract))
        self.plan.update(wave_strategy="Read initial candidates, then search useful source language.",
                         checkpoints=[])
        self.ledger["sources"][0]["expansion_review"] = "Archive migration is a useful follow-up term."
        self.save_plan()

    def checkpoint(self, key="final", decision="synthesize", next_items=None):
        self.plan["checkpoints"].append({
            "id": key, "operations": sorted(p.name for p in (self.bundle / "operations").iterdir()),
            "inspected_reads": sorted({entry["read_id"] for source in self.ledger["sources"]
                                       for entry in source["inspections"]}),
            "questions": [question["id"] for question in self.contract["questions"]],
            "next_items": next_items or [], "decision": decision,
            "reason": "Reviewed the decision evidence and the unresolved measurement source.",
            "limits": "Fixture corpus only; the later measurement source remains inaccessible."})

    def lead(self):
        item = self.search_item("migration-followup", priority="normal",
                                origin={"source_id": "decision-note", "read_id": "read-01",
                                        "locator": "Paragraph 1"})
        item["request"]["query"] = "archive migration"
        self.plan["items"].append(item)
        lead = {"id": "migration-term", "kind": "vocabulary", "value": "archive migration",
                "questions": ["decision"], "locator": "Paragraph 1",
                "reason": "Find the prerequisite work under the term used in the source.",
                "status": "pending", "plan_item": item["id"]}
        self.ledger["sources"][0]["leads"].append(lead)
        return item, lead

    def execute_empty_search(self, item, key="followup-01"):
        self.save_plan()
        spec = self.spec(item, key)
        spec["command"][-1] = "print('{\"ids\":[]}')"
        self.capture(spec)
        self.index(key, [])
        item["status"] = "done"

    def test_read_then_search_and_explicit_final_bound_pass(self):
        item, lead = self.lead()
        self.checkpoint("seed-wave", "continue", [item["id"]])
        self.execute_empty_search(item)
        lead["status"] = "addressed"
        self.checkpoint(decision="bounded")
        self.save_plan()
        self.validate(phase="synthesis")
        view = (self.bundle / "plan.md").read_text()
        self.assertIn("archive migration", view)
        self.assertIn("seed-wave", view)
        self.assertIn("fixture corpus", view.lower())

    def test_pending_lead_blocks_even_when_work_is_bounded(self):
        item, _ = self.lead()
        item["status"] = "bounded"
        self.checkpoint()
        self.save_plan()
        result = self.validate(phase="synthesis", success=False)
        self.assertIn("unresolved discovery lead", str(result))

    def test_capped_search_remains_visible_in_final_coverage_review(self):
        item, lead = self.lead()
        item["request"]["cap"] = 1
        self.save_plan()
        spec = self.spec(item, "capped-followup")
        spec["command"][-1] = "print('{\"ids\":[\"decision-note\"]}')"
        self.capture(spec)
        self.index("capped-followup", ["decision-note"], "capped")
        self.ledger["sources"][0]["discovered_by"].append("capped-followup")
        item["status"] = "done"
        lead["status"] = "addressed"
        self.checkpoint(decision="bounded")
        self.save_plan()
        self.validate(phase="synthesis")
        view = (self.bundle / "plan.md").read_text()
        limits = view.split("### Search limits to review", 1)[1].split("### Pending discovery leads", 1)[0]
        self.assertIn("capped-followup", limits)
        self.assertIn("capped", limits)

    def test_unassigned_neighborhood_can_be_explicitly_bounded(self):
        self.ledger["sources"][0]["leads"].append({
            "id": "folder", "kind": "neighborhood", "value": "Original project folder",
            "questions": ["outcomes"], "locator": "Linked evaluation paragraph",
            "reason": "No folder identity exposed; later measurement coverage remains incomplete.",
            "status": "bounded"})
        self.checkpoint(decision="bounded")
        self.save_plan()
        self.validate(phase="synthesis")

    def test_addressed_lead_requires_executed_work(self):
        _, lead = self.lead()
        lead["status"] = "addressed"
        result = self.save_plan(success=False)
        self.assertIn("addressed lead needs executed", str(result))

    def test_addressed_read_requires_inspection_not_capture(self):
        item, lead = self.lead()
        item["kind"] = "read"
        item["request"] = {"source_id": "later-index", "format": "html", "extent": "Full fixture body"}
        self.save_plan()
        self.capture(self.spec(item, "later-read"))
        self.index("later-read", ["later-index"], "not-applicable")
        self.ledger["sources"][1]["discovered_by"].append("later-read")
        self.ledger["sources"][1]["reads"].append("later-read")
        item["status"] = "done"
        lead["status"] = "addressed"
        result = self.save_plan(success=False)
        self.assertIn("addressed read lead needs actual inspection", str(result))

    def test_analyzed_source_requires_expansion_assessment(self):
        del self.ledger["sources"][0]["expansion_review"]
        result = self.save_plan(success=False)
        self.assertIn("analyzed source needs expansion_review", str(result))

    def test_final_checkpoint_required_beyond_completed_worklist(self):
        result = self.validate(phase="synthesis", success=False)
        self.assertIn("synthesis needs final coverage checkpoint", str(result))

    def test_final_checkpoint_becomes_stale_after_new_search(self):
        self.checkpoint()
        self.save_plan()
        item, lead = self.lead()
        self.execute_empty_search(item)
        lead["status"] = "addressed"
        self.save_plan()
        result = self.validate(phase="synthesis", success=False)
        self.assertIn("checkpoint is stale", str(result))

    def test_checkpoint_cannot_claim_unperformed_inspection(self):
        self.checkpoint()
        self.plan["checkpoints"][-1]["inspected_reads"].append("search-01")
        self.save_plan(success=False)

    def test_checkpoints_cannot_be_rewritten_or_removed(self):
        self.checkpoint()
        self.save_plan()
        changed = copy.deepcopy(self.plan)
        changed["checkpoints"][0]["reason"] = "A different account."
        self.save_plan(plan=changed, success=False)
        self.plan["checkpoints"] = []
        self.save_plan(success=False)

    def test_observed_vocabulary_cannot_be_silently_replaced(self):
        _, lead = self.lead()
        self.save_plan()
        lead["value"] = "unrelated term"
        self.save_plan(success=False)

    def test_body_derived_search_requires_prior_snapshot_inspection(self):
        item, _ = self.lead()
        source = self.ledger["sources"][0]
        inspections = source["inspections"]
        source["inspections"] = []
        source["analysis"]["status"] = "pending"
        source["disposition"]["status"] = "blocked"
        self.ledger["claims"] = []
        self.save_plan()
        stale_spec = self.spec(item, "derived-search")
        self.capture(stale_spec, success=False)
        source["inspections"] = inspections
        self.save_plan()
        self.capture(stale_spec, success=False)
        self.capture(self.spec(item, "derived-search"))

    def test_final_checkpoint_must_cover_every_question(self):
        self.checkpoint()
        self.plan["checkpoints"][-1]["questions"] = ["decision"]
        self.save_plan()
        self.validate(phase="synthesis", success=False)

    def test_continuing_checkpoint_requires_concrete_next_work(self):
        self.checkpoint(decision="continue")
        self.save_plan(success=False)

    def test_followups_requirement_requires_research_plan(self):
        self.contract["requirements"]["research_plan"] = False
        self.cli("amend", "--contract", self.json_file(self.contract), success=False)


if __name__ == "__main__":
    unittest.main()
