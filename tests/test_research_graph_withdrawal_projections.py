"""Withdrawal must affect usable evidence without rewriting the historical graph."""

from datetime import datetime, timezone
from pathlib import Path
import unittest

import test_research_graph_exports as fixture

GRAPH, INGEST, EXPORTS, WORK, BUNDLE = fixture.GRAPH, fixture.INGEST, fixture.EXPORTS, fixture.WORK, fixture.BUNDLE
QUERIES = INGEST._sibling("research_graph_queries")


class WithdrawalProjectionTests(unittest.TestCase):
    setUp = fixture.ResearchGraphExportsTests.setUp
    operation = fixture.ResearchGraphExportsTests.operation
    returned = fixture.ResearchGraphExportsTests.returned
    candidate = fixture.ResearchGraphExportsTests.candidate
    packet = fixture.ResearchGraphExportsTests.packet
    rows = fixture.ResearchGraphExportsTests.rows
    target = fixture.ResearchGraphExportsTests.target
    target_rows = fixture.ResearchGraphExportsTests.target_rows
    selection = fixture.ResearchGraphExportsTests.selection

    def prepare(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        self.inspection = self.rows("inspections")[0]["inspection_id"]
        self.assertion = self.rows("assertions")[0]["assertion_id"]
        self.locator = self.rows("locators")[0]["locator_id"]
        self.packet("accept-reading", [INGEST._insert("assertion_decisions", decision_id="accept-finding",
            assertion_id=self.assertion, disposition="accepted", reason="Original reading was accepted",
            actor_id="coordinator", expected_revision=0)], kind="reconcile")
        self.packet("claim", [INGEST._insert("claims", claim_id="claim", text="The pilot paused.",
            basis="documented", status="accepted", reason="Original inspected passage"),
            INGEST._insert("claim_evidence", evidence_id="claim-passage", claim_id="claim", claim_revision=1,
                locator_id=self.locator, stance="support", rationale="Direct passage"),
            INGEST._insert("claim_questions", claim_id="claim", claim_revision=1, question_id="decision", relationship="answers")], kind="reconcile")

    def withdraw(self):
        basis = self.root / "reviews/reading-correction.txt"
        basis.parent.mkdir(exist_ok=True)
        basis.write_text("Reader admits that the original claimed extent was not displayed.\n")
        self.packet("withdraw-reading", [INGEST._insert("inspection_withdrawals", withdrawal_id="withdraw-one",
            inspection_id=self.inspection, actor_id="coordinator", reason="Full scope was overstated",
            evidence_path=str(basis.relative_to(self.root)), evidence_sha256=GRAPH._sha(basis.read_bytes()),
            withdrawn_at=datetime.now(timezone.utc).isoformat())], kind="reconcile")

    def exported(self, identity):
        directory = Path(EXPORTS.export_snapshot(self.root, identity)["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        return directory, ledger, EXPORTS.validate_ledger(self.root, ledger)

    def test_withdrawal_removes_credit_and_completion_but_preserves_frozen_history(self):
        self.prepare()
        before, old, _ = self.exported("before-withdrawal")
        self.assertEqual(len(old["sources"][0]["inspections"]), 1)
        self.assertEqual([c["id"] for c in old["claims"]], ["claim"])
        with GRAPH.snapshot(self.root) as connection:
            work = GRAPH._decoded(connection.execute("SELECT * FROM work_items WHERE work_id='work-inspect'").fetchone())
            work["request_json"]["freshness_basis"] = "captured_after_assignment"
            self.assertTrue(WORK._completion(self.root, connection, work))
        original_return = (self.root / "reader-returns/reader-one.json").read_bytes()
        budget = (self.root / "research-budget.json").read_bytes()
        self.withdraw()
        current, ledger, context = self.exported("after-withdrawal")
        self.assertEqual(ledger["sources"][0]["inspections"], [])
        self.assertEqual(ledger["sources"][0]["analysis"]["status"], "pending")
        self.assertEqual(ledger["sources"][0]["graph_withdrawn_inspection_ids"], [self.inspection])
        self.assertEqual(ledger["claims"], [])
        self.assertEqual(context["stop_checks"]["withdrawn_evidence_accepted_claim_ids"], ["claim"])
        self.assertEqual(context["stop_checks"]["withdrawn_evidence_accepted_assertion_ids"], [self.assertion])
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 0)
            with self.assertRaises(GRAPH.GraphError):
                WORK._completion(self.root, connection, work)
        self.assertEqual((self.root / "reader-returns/reader-one.json").read_bytes(), original_return)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)
        self.assertEqual(len(self.rows("inspections")), 1)
        EXPORTS.verify_export(before)
        EXPORTS.verify_export(current)
        self.assertEqual(BUNDLE.read(before / "ledger.json"), old)
        self.assertEqual(EXPORTS.validate_ledger(self.root, old)["claim_bindings"][0]["withdrawal_affected"], False)
        facts = QUERIES.query(self.root, "artifact", {"artifact_id": "source-a", "include": ["coverage"]}, 100)["items"]
        self.assertEqual(next(r for r in facts if r["record_type"] == "inspection")["withdrawn"], 1)
        self.assertEqual(next(r for r in facts if r["record_type"] == "inspection_withdrawal")["withdrawal_id"], "withdraw-one")
        self.assertTrue((current / "evidence/reviews/reading-correction.txt").is_file())

    def test_partial_reread_does_not_reinstate_full_coverage_or_unread_claim_locator(self):
        self.prepare()
        self.withdraw()
        draft = self.candidate("partial-reread")
        draft["graph"]["inspections"][0]["captures"][0]["extent"]["body"]["ranges"][0]["end"] = 2
        draft["graph"]["inspections"][0]["scopes"][0].update(coverage="partial", basis="Only lines 1 and 2 were read")
        draft["graph"]["locators"][0]["position"]["end"] = 2
        # The whole captured reference scan remains distinct from this partial read.
        INGEST.validate_reader_return(self.root, draft)
        BUNDLE.budget.reader_return(self.root, draft)
        INGEST.ingest_reader_return(self.root, draft["id"])
        _, ledger, context = self.exported("partial-reread")
        self.assertEqual(len(ledger["sources"][0]["inspections"]), 1)
        self.assertEqual(ledger["claims"], [])
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 0)
        self.assertEqual(context["stop_checks"]["withdrawn_evidence_accepted_claim_ids"], ["claim"])

    def test_reuse_keeps_withdrawal_and_its_basis_without_current_credit(self):
        self.prepare()
        self.withdraw()
        directory, _, _ = self.exported("withdrawn-reuse")
        target = self.target()
        EXPORTS.import_prior(target, directory, "prior-withdrawn", self.selection(
            record_ids={"inspection": [self.inspection], "assertion": [self.assertion]}))
        withdrawals = self.target_rows(target, "inspection_withdrawals")
        inspections = self.target_rows(target, "inspections")
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0]["inspection_id"], inspections[0]["inspection_id"])
        self.assertEqual(inspections[0]["provenance_basis"], "prior_run")
        self.assertTrue((target / withdrawals[0]["evidence_path"]).is_file())
        with GRAPH.snapshot(target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM effective_inspections").fetchone()[0], 0)
        GRAPH.validate(target)
        result = EXPORTS.export_snapshot(target, "reused-withdrawal")
        EXPORTS.verify_export(Path(result["manifest_path"]).parent)

    def test_full_reread_requires_new_claim_revision_and_keeps_original_withdrawn(self):
        self.prepare()
        self.withdraw()
        original = (self.root / "reader-returns/reader-one.json").read_bytes()
        draft = self.candidate("full-reread")
        INGEST.validate_reader_return(self.root, draft)
        BUNDLE.budget.reader_return(self.root, draft)
        INGEST.ingest_reader_return(self.root, draft["id"])
        _, before, _ = self.exported("reread-without-claim-review")
        self.assertEqual(before["claims"], [])
        claim = GRAPH._decoded(self.rows("claims")[0])
        self.packet("revalidate-claim", [WORK._update("claims", claim, status="accepted",
            reason="Revalidated the same conclusion against the new full inspection"),
            INGEST._insert("claim_evidence", evidence_id="revalidated-passage", claim_id="claim", claim_revision=2,
                locator_id=self.locator, stance="support", rationale="Passage rechecked in the new full inspection"),
            INGEST._insert("claim_questions", claim_id="claim", claim_revision=2, question_id="decision", relationship="answers")], kind="reconcile")
        _, after, context = self.exported("revalidated-claim")
        self.assertEqual([c["id"] for c in after["claims"]], ["claim"])
        self.assertEqual(after["claims"][0]["graph_revision"], 2)
        self.assertEqual(len(after["sources"][0]["inspections"]), 1)
        self.assertEqual(len(self.rows("inspections")), 2)
        self.assertEqual(len(self.rows("inspection_withdrawals")), 1)
        self.assertEqual(context["stop_checks"]["withdrawn_evidence_accepted_claim_ids"], [])
        self.assertEqual((self.root / "reader-returns/reader-one.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
