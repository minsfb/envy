"""Account for real cancelled admissions without inventing provider receipts."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture


WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
EXPORTS = INGEST._sibling("research_graph_exports")


class CancelledAdmissionTests(unittest.TestCase):
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
    cancelled_request = fixture.ResearchGraphWorkTests.cancelled_request

    def export(self, key):
        result = EXPORTS.export_snapshot(self.root, key)
        directory = Path(result["manifest_path"]).parent
        return directory, BUNDLE.read(directory / "ledger.json")

    def cancelled(self):
        assignment = self.claim()
        self.cancelled_request(assignment)
        return assignment

    def test_public_validation_accounts_for_cancellation_without_executed_credit(self):
        self.cancelled()
        original = (self.root / "operations/read-one/request.json").read_bytes()
        budget = (self.root / "research-budget.json").read_bytes()
        directory, ledger = self.export("cancelled")
        status = BUNDLE.read(directory / "ingestion.json")
        self.assertTrue(status["complete"])
        self.assertEqual(status["items"][0]["state"], "cancelled")
        result = subprocess.run([sys.executable, "-I", "-B", str(fixture.SCRIPTS / "research_bundle.py"),
            "validate", str(self.root), "--ledger", str(directory / "ledger.json"), "--phase", "research"],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checked = json.loads(result.stdout)["result"]
        self.assertEqual(checked["operations"], 1)
        self.assertEqual(checked["executed_operations"], 0)
        self.assertEqual(checked["cancelled_operations"], ["read-one"])
        self.assertEqual(checked["tool_imports"], 0)
        self.assertFalse(checked["graph"]["stop_checks"]["settled"])
        self.assertTrue(all(not source["reads"] and not source["inspections"] for source in ledger["sources"]))
        self.assertTrue(all(not q["followups"] for q in ledger["questions"]))
        self.assertEqual(ledger["claims"], [])
        self.assertEqual((self.root / "operations/read-one/request.json").read_bytes(), original)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)
        self.assertEqual({p.name for p in (self.root / "operations/read-one").iterdir()}, {"request.json"})
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_checkpoint_accounts_for_cancelled_attempt_but_cannot_mark_item_done(self):
        self.cancelled()
        _, ledger = self.export("checkpoint")
        context = BUNDLE.graph_ledger_context(self.root, ledger)
        operations = BUNDLE.plan_operations(self.root)
        plan = dict(wave_strategy="Finish breadth, then bound unexecuted work.", checkpoints=[dict(id="stop",
            operations=["read-one"], inspected_reads=[], questions=["decision"], next_items=[],
            decision="bounded", reason="No captured result; cancellation is retained.", limits="No reading credit.")])
        BUNDLE.check_discovery_followups(plan, self.contract, {}, {}, operations, set(), "synthesis", context)
        with self.assertRaisesRegex(ValueError, "unfinished operations"):
            BUNDLE.check_discovery_followups(plan, self.contract, {}, {}, operations, set(), "synthesis", None)
        saved = BUNDLE.read(self.root / "plan.json")
        saved["items"][0]["status"] = "done"
        with self.assertRaisesRegex(ValueError, "lacks successful indexed execution"):
            BUNDLE.checked_plan(saved, self.contract, ledger, operations, graph_context=context)
        saved["items"][0]["status"] = "queued"
        BUNDLE.save_plan(self.root, saved, ledger)
        rendered = (self.root / "plan.md").read_text()
        self.assertIn("cancelled; no captured result", rendered)
        self.assertIn("no executed research credit", rendered)

    def test_unresolved_or_unproven_cancellation_remains_pending(self):
        assignment = self.claim()
        self.admit(assignment, dispatch=False)
        directory, ledger = self.export("unresolved")
        self.assertFalse(BUNDLE.read(directory / "ingestion.json")["complete"])
        with self.assertRaisesRegex(ValueError, "receipt and index"):
            BUNDLE.validate(self.root, ledger, "research")
        WORK.reconcile_operation(self.root, "read-one", expected_revision=1, worker_identity="reader",
                                 worker_stopped=True, reason="Original worker checked stopped.", cancel=True)
        with patch.object(INGEST._sibling("research_graph_work"), "_historical_worker_stopped", return_value=False):
            self.assertEqual(INGEST.ingestion_status(self.root)["items"][0]["state"], "pending")
        tables = BUNDLE.read(self.export("stopped")[0] / "graph.json")["tables"]
        tables["graph_events"] = [event for event in tables["graph_events"]
            if not event["payload_json"].get("after", {}).get("transition_reason", "").startswith("Worker reconciliation: ")]
        proof = EXPORTS._operation_proofs(tables, lambda name: EXPORTS._read(self.root, name))["read-one"]
        self.assertFalse(proof["worker_termination_confirmed"])
        self.assertFalse(EXPORTS._terminal_followup(proof))

    def test_new_cancellation_cannot_rewrite_older_pending_inventory(self):
        self.cancelled()
        original_status = INGEST.ingestion_status
        with patch.object(INGEST, "ingestion_status", side_effect=lambda root, **kw:
                          original_status(root, **dict(kw, status_version=1))):
            old, old_ledger = self.export("old-format")
        old_bytes = (old / "ingestion.json").read_bytes()
        self.assertNotIn("status_version", json.loads(old_bytes))
        self.assertEqual(json.loads(old_bytes)["items"][0]["state"], "pending")
        current, ledger = self.export("new-format")
        EXPORTS.verify_export(old)
        EXPORTS.verify_export(current)
        self.assertEqual((old / "ingestion.json").read_bytes(), old_bytes)
        self.assertEqual(EXPORTS.validate_ledger(self.root, old_ledger)["cancelled_operations"], {})
        with self.assertRaisesRegex(ValueError, "receipt and index"):
            BUNDLE.validate(self.root, old_ledger, "research")
        self.assertEqual(BUNDLE.validate(self.root, ledger, "research")["cancelled_operations"], ["read-one"])

    def test_changed_request_or_additional_retained_output_cannot_use_frozen_cancellation(self):
        self.cancelled()
        directory, ledger = self.export("checked")
        request = self.root / "operations/read-one/request.json"
        original = request.read_bytes()
        request.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "request changed"):
            BUNDLE.validate(self.root, ledger, "research")
        request.write_bytes(original)
        output = request.parent / "stdout"
        output.write_text("An unexplained retained result cannot disappear.")
        self.assertFalse(INGEST.ingestion_status(self.root)["complete"])
        with self.assertRaisesRegex(ValueError, "other retained evidence"):
            BUNDLE.validate(self.root, ledger, "research")
        EXPORTS.verify_export(directory)
        self.assertTrue(output.exists())

    def test_late_receipt_needs_reconciliation_and_does_not_change_frozen_export(self):
        self.cancelled()
        directory, ledger = self.export("before-late-receipt")
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        self.assertFalse(INGEST.ingestion_status(self.root)["complete"])
        with self.assertRaisesRegex(ValueError, "other retained evidence"):
            BUNDLE.validate(self.root, ledger, "research")
        EXPORTS.verify_export(directory)
        self.assertTrue(BUNDLE.read(directory / "ingestion.json")["complete"])


if __name__ == "__main__":
    unittest.main()
