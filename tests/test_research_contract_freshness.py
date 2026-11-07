"""Amended research questions cannot inherit completion from an older contract."""

import copy
import json
import os
import subprocess
import sys
import unittest

import test_research_graph_bundle as fixture


BUNDLE, EXPORTS, GRAPH, INGEST = fixture.BUNDLE, fixture.EXPORTS, fixture.GRAPH, fixture.INGEST


class ContractFreshnessTests(unittest.TestCase):
    setUp = fixture.GraphPublicationTests.setUp
    operation = fixture.GraphPublicationTests.operation
    returned = fixture.GraphPublicationTests.returned
    packet = fixture.GraphPublicationTests.packet
    rows = fixture.GraphPublicationTests.rows
    settle_scopes = fixture.GraphPublicationTests.settle_scopes
    exported_context = fixture.GraphPublicationTests.exported_context
    semantic_review = fixture.GraphPublicationTests.semantic_review

    def settled(self):
        self.operation(sources=[dict(source_id="doc-a", namespace="fixture-doc", external_id="A",
                                     title="Orchard review", url="https://example.invalid/a", type="document")])
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        self.settle_scopes()
        ledger, context = self.exported_context("original")
        review = self.semantic_review(context)
        self.assertTrue(BUNDLE.validate(self.root, ledger, "synthesis", review)["graph"]["stop_checks"]["settled"])
        return ledger, context, review

    def amend_question(self, *, central=False):
        changed = copy.deepcopy(self.contract)
        if central:
            changed["questions"][1]["central"] = True
        else:
            changed["questions"][0]["text"] = "Which measured gains followed the pilot restart?"
            changed["purpose"] = "Assess measured gains from the fictional restart."
        BUNDLE.amend(self.root, changed)
        self.contract_hash = BUNDLE.digest(self.root / "contract.json")
        return changed

    def test_changed_question_rejects_old_synthesis_through_public_cli(self):
        ledger, context, review = self.settled()
        self.amend_question()
        self.assertNotEqual(EXPORTS.validate_ledger(self.root, ledger)["contract_sha256"], self.contract_hash)
        with self.assertRaisesRegex(ValueError, "earlier contract"):
            BUNDLE.validate(self.root, ledger, "synthesis", review)
        command = [sys.executable, "-I", "-B", BUNDLE.__file__,
            "validate", str(self.root), "--ledger", str(self.root / "graph/exports/original/ledger.json"),
            "--phase", "synthesis", "--semantic-review", str(review)]
        result = subprocess.run(command, cwd=self.work, env={"PATH": os.defpath},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("earlier contract", json.loads(result.stdout)["error"])

    def test_centrality_amendment_also_requires_current_plan(self):
        ledger, _, review = self.settled()
        self.amend_question(central=True)
        with self.assertRaisesRegex(ValueError, "earlier contract"):
            BUNDLE.validate(self.root, ledger, "synthesis", review)

    def test_reexport_alone_does_not_refresh_the_active_plan(self):
        _, _, review = self.settled()
        self.amend_question()
        self.packet("retain-amended-contract", [INGEST._insert("contract_snapshots",
            contract_sha256=self.contract_hash, contract_revision=2,
            relative_path="contracts/" + self.contract_hash + ".json")], kind="reconcile")
        ledger, context = self.exported_context("amended-with-old-plan")
        self.assertEqual(context["contract_sha256"], self.contract_hash)
        with self.assertRaisesRegex(ValueError, "save an amended plan"):
            BUNDLE.validate(self.root, ledger, "synthesis", review)

    def test_old_ledger_can_seed_amended_plan_but_completion_needs_new_review(self):
        ledger, _, old_review = self.settled()
        self.amend_question()
        # This supported transition must remain possible before graph questions
        # have been synchronized to the newly saved contract.
        BUNDLE.save_plan(self.root, self.plan, ledger)
        self.assertTrue(EXPORTS.validate_ledger(self.root, ledger)["stale"])
        question = next(q for q in self.rows("questions") if q["question_id"] == "decision")
        self.assertEqual(question["current_status"], "reopened")
        current, _ = self.exported_context("reopened")
        with self.assertRaisesRegex(ValueError, "unresolved"):
            BUNDLE.validate(self.root, current, "synthesis", old_review)
        # The coordinator explicitly bounds the amended question for this
        # synthetic test, with the original retained follow-up still visible.
        self.packet("bound-amended-question", [dict(op="update", table="questions", id="decision",
            expected_revision=question["revision"], changes=dict(current_status="bounded",
            reason="The retained pilot record does not establish measured restart gains."))], kind="reconcile")
        work = INGEST._sibling("research_graph_work")
        self.packet("bound-amended-plan-work", [work._update("work_items", row, state="deferred",
            terminal_reason="No further sources are available within this fictional test.",
            transition_reason="Explicitly bound the amended plan's remaining research.")
            for row in self.rows("work_items") if row["state"] == "pending"],
            kind="reconcile", validator=work.evidence_validator)
        current, context = self.exported_context("amended-settled")
        self.assertTrue(context["stop_checks"]["settled"], context["stop_checks"])
        with self.assertRaisesRegex(ValueError, "different generated ledger"):
            BUNDLE.validate(self.root, current, "synthesis", old_review)
        review = self.semantic_review(context)
        self.assertTrue(BUNDLE.validate(self.root, current, "synthesis", review)["graph"]["stop_checks"]["settled"])

    def test_historical_plan_keeps_its_retained_question_wording(self):
        ledger, _, _ = self.settled()
        original_plan = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        original_contract = self.contract_hash
        changed = self.amend_question()
        BUNDLE.render_plan(self.root, ledger)
        rendered = (self.root / "plan.md").read_text()
        self.assertIn(original_plan, rendered)
        self.assertIn(original_contract, rendered)
        self.assertIn(self.contract["questions"][0]["text"], rendered)
        self.assertNotIn(changed["questions"][0]["text"], rendered)


if __name__ == "__main__":
    unittest.main()
