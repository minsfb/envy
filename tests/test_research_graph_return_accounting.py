"""Frozen non-inspecting return accounting must never manufacture read credit."""

import copy
from pathlib import Path
import unittest

import test_research_graph_exports as fixture
import test_research_graph_withdrawal_projections as withdrawal_fixture

GRAPH, INGEST, EXPORTS, WORK, BUNDLE = fixture.GRAPH, fixture.INGEST, fixture.EXPORTS, fixture.WORK, fixture.BUNDLE


class NonInspectingReturnAccountingTests(unittest.TestCase):
    setUp = fixture.ResearchGraphExportsTests.setUp
    operation = fixture.ResearchGraphExportsTests.operation
    candidate = fixture.ResearchGraphExportsTests.candidate
    rejected = fixture.ResearchGraphExportsTests.rejected
    correction = fixture.ResearchGraphExportsTests.correction
    packet = fixture.ResearchGraphExportsTests.packet
    rows = fixture.ResearchGraphExportsTests.rows
    exported_context = fixture.ResearchGraphExportsTests.exported_context
    settle_scopes = fixture.ResearchGraphExportsTests.settle_scopes
    semantic_review = fixture.ResearchGraphExportsTests.semantic_review
    withdraw = withdrawal_fixture.WithdrawalProjectionTests.withdraw

    def start(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")

    def record(self, key, *, empty=False, two=False, ingest=True):
        draft = self.candidate(key)
        if empty:
            draft["graph"] = {}
        elif two:
            second = copy.deepcopy(draft["graph"]["inspections"][0])
            second["id"] = "second"
            draft["graph"]["inspections"].append(second)
        BUNDLE.budget.reader_return(self.root, draft)
        if ingest:
            INGEST.ingest_reader_return(self.root, key)
        return draft

    def stop_clocks(self):
        for actor in ("retriever", "reader"):
            BUNDLE.budget.activity(self.root, actor, "stop")

    def budget(self, ledger, context):
        return BUNDLE.budget.validate(self.root, ledger, "synthesis",
            return_corrections=context["return_corrections"],
            non_inspecting_returns=context["non_inspecting_returns"])

    def test_full_synthesis_keeps_withdrawn_and_zero_inspection_returns_separate(self):
        self.start()
        self.record("old-reading")
        self.inspection = self.rows("inspections")[0]["inspection_id"]
        self.withdraw()
        self.record("lead-only", empty=True)
        self.record("current-reading")
        self.settle_scopes()
        ledger, context = self.exported_context("settled")
        originals = {p.name: p.read_bytes() for p in (self.root / "reader-returns").iterdir()}
        budget = (self.root / "research-budget.json").read_bytes()
        result = BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))
        self.assertTrue(result["graph"]["stop_checks"]["settled"])
        self.assertEqual(result["research_budget"]["reader_returns"], 3)
        self.assertEqual(result["research_budget"]["withdrawn_reader_returns"], 1)
        self.assertEqual(result["research_budget"]["uninspected_reader_returns"], 1)
        self.assertEqual(result["research_budget"]["returned_leads_unreconciled"], 0)
        self.assertEqual([i["return_id"] for i in ledger["sources"][0]["inspections"]], ["current-reading"])
        self.assertEqual(ledger["claims"], [])
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "reader-returns").iterdir()}, originals)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)

    def test_non_inspecting_return_does_not_settle_source_work_or_hide_leads(self):
        self.start()
        self.record("lead-only", empty=True)
        self.stop_clocks()
        ledger, context = self.exported_context("no-inspection")
        self.assertFalse(context["stop_checks"]["settled"])
        self.assertEqual(ledger["sources"][0]["inspections"], [])
        self.assertEqual(self.budget(ledger, context)["uninspected_reader_returns"], 1)
        with self.assertRaisesRegex(ValueError, "unresolved work"):
            BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))
        changed = copy.deepcopy(ledger)
        changed["sources"][0]["leads"].pop()
        with self.assertRaisesRegex(ValueError, "returned leads missing"):
            self.budget(changed, context)

    def test_unaccepted_or_self_declared_empty_return_has_no_accounting_authority(self):
        self.start()
        self.record("pending", empty=True, ingest=False)
        self.stop_clocks()
        ledger, context = self.exported_context("pending")
        self.assertEqual(context["non_inspecting_returns"], {})
        with self.assertRaisesRegex(ValueError, "returns missing from ledger inspections"):
            self.budget(ledger, context)
        changed = copy.deepcopy(ledger)
        changed["_graph"]["non_inspecting_returns"] = {"pending": {"disposition": "no_inspections"}}
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.validate(self.root, changed, "research")

    def test_all_inspections_must_be_withdrawn_and_old_export_stays_historical(self):
        self.start()
        self.record("two-readings", two=True)
        self.stop_clocks()
        old, before = self.exported_context("before")
        self.inspection = self.rows("inspections")[0]["inspection_id"]
        self.withdraw()
        partial, context = self.exported_context("part-withdrawn")
        self.assertEqual(context["non_inspecting_returns"], {})
        self.assertEqual(len(partial["sources"][0]["inspections"]), 1)
        self.assertEqual(self.budget(partial, context)["withdrawn_reader_returns"], 0)
        remaining = next(r for r in self.rows("inspections") if r["inspection_id"] != self.inspection)
        record = dict(self.rows("inspection_withdrawals")[0])
        record.pop("created_batch_id")
        record.update(withdrawal_id="withdraw-two", inspection_id=remaining["inspection_id"])
        self.packet("withdraw-second", [INGEST._insert("inspection_withdrawals", **record)], kind="reconcile")
        current, context = self.exported_context("all-withdrawn")
        self.assertEqual(len(context["non_inspecting_returns"]["two-readings"]["inspection_ids"]), 2)
        self.assertEqual(self.budget(current, context)["withdrawn_reader_returns"], 1)
        self.assertEqual(EXPORTS.validate_ledger(self.root, old)["non_inspecting_returns"], {})
        self.assertEqual(before["non_inspecting_returns"], {})
        self.assertEqual(len(old["sources"][0]["inspections"]), 2)

    def test_withdrawn_return_still_requires_leads_and_unchanged_original_evidence(self):
        self.start()
        self.record("reading")
        self.inspection = self.rows("inspections")[0]["inspection_id"]
        self.withdraw()
        self.stop_clocks()
        ledger, context = self.exported_context("withdrawn")
        with self.assertRaisesRegex(ValueError, "returns missing from ledger inspections"):
            BUNDLE.budget.validate(self.root, ledger, "synthesis")
        changed = copy.deepcopy(ledger)
        changed["sources"][0]["leads"].pop()
        with self.assertRaisesRegex(ValueError, "returned leads missing"):
            self.budget(changed, context)
        basis = self.root / "reviews/reading-correction.txt"
        original = basis.read_bytes()
        basis.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "withdrawal evidence is missing or changed"):
            self.budget(ledger, context)
        basis.write_bytes(original)
        path = self.root / "reader-returns/reading.json"
        payload = BUNDLE.read(path)
        payload["return"]["coverage"] = "Changed after verification"
        BUNDLE.write(path, payload)
        with self.assertRaisesRegex(ValueError, "changed reader return"):
            self.budget(ledger, context)

    def test_correction_predecessor_can_resolve_to_a_withdrawn_terminal(self):
        original, digest, _ = self.rejected()
        draft = self.correction(original, digest)
        BUNDLE.budget.reader_return(self.root, draft)
        INGEST.ingest_reader_return(self.root, draft["id"])
        self.inspection = self.rows("inspections")[0]["inspection_id"]
        self.withdraw()
        self.stop_clocks()
        ledger, context = self.exported_context("corrected-then-withdrawn")
        result = self.budget(ledger, context)
        self.assertEqual(result["corrected_reader_returns"], 1)
        self.assertEqual(result["withdrawn_reader_returns"], 1)
        self.assertEqual(result["returned_leads_unreconciled"], 0)
        self.assertEqual(ledger["sources"][0]["inspections"], [])


if __name__ == "__main__":
    unittest.main()
