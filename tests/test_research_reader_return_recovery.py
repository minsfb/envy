"""Recover interrupted return publication from its unchanged original event."""

import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture

WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
REAL_EVIDENCE = BUNDLE.sync_graph_evidence


class ReaderReturnRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.ResearchGraphWorkTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def test_exact_public_retry_repairs_missing_file_without_new_event_or_time(self):
        for explicit_time in (False, True):
            with self.subTest(explicit_time=explicit_time):
                if explicit_time:
                    self.c.doCleanups()
                    self.setUp()
                c = self.c
                assignment = c.claim()
                c.admit(assignment)
                c.result()
                work = c.row("work_items", c.row("assignments", assignment)["work_id"])
                returned = {"id": "crash-return", "read_id": "read-one", "actor": "reader", "graph_assignment_id": assignment,
                    "inspection": "partial", "relevance": "relevant", "coverage": "Two fictional lines inspected",
                    "expansion_review": "No additional leads in the fictional text", "leads": [],
                    "graph": {"locators": [], "scans": [], "assertions": [], "inspections": [{"id": "body", "source_id": "doc-a",
                        "extent": "Two lines", "method": "Fictional fixture attestation", "limitations": "Owned fictional data",
                        "captures": [{"capture_id": "html", "extent": copy.deepcopy(fixture.EXTENT)}],
                        "scopes": [{"question_id": "decision", "required_scope_key": work["required_scope_key"],
                                    "format_policy_sha256": work["format_policy_sha256"], "coverage": "sufficient", "basis": "Two lines"}]}]}}
                if explicit_time:
                    returned["graph"]["inspected_at"] = datetime.now(timezone.utc).isoformat()
                destination = c.root / "reader-returns/crash-return.json"
                original_write = BUDGET.write
                def fail_destination(path, value):
                    if path == destination:
                        raise OSError("Owned injected publication interruption after preflight")
                    return original_write(path, value)
                with patch.object(BUDGET, "write", side_effect=fail_destination):
                    with self.assertRaises(OSError):
                        BUNDLE.record_reader_return(c.root, returned)
                journal = (c.root / "research-budget.json").read_bytes()
                events = BUNDLE.read(c.root / "research-budget.json")["events"]
                committed = [e for e in events if e["type"] == "reader-return"]
                self.assertEqual(len(committed), 1)
                self.assertFalse(destination.exists())
                for field in ("coverage", "time"):
                    changed = copy.deepcopy(returned)
                    if field == "coverage":
                        changed[field] = "Different interpretation"
                    else:
                        changed["graph"]["inspected_at"] = datetime.now(timezone.utc).isoformat()
                    with self.assertRaises(GRAPH.GraphError):
                        BUNDLE.record_reader_return(c.root, changed)
                    self.assertFalse(destination.exists())
                    self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)
                with patch.object(BUNDLE, "sync_graph_evidence", REAL_EVIDENCE):
                    result = BUNDLE.record_reader_return(c.root, returned)
                self.assertEqual(result["state"], "complete")
                self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)
                self.assertEqual(BUNDLE.read(destination)["event"], committed[0]["id"])
                self.assertEqual(BUNDLE.read(destination)["return"], returned)
                inspection = c.rows("inspections")[0]
                expected = returned["graph"].get("inspected_at") or datetime.fromtimestamp(committed[0]["at"], timezone.utc).isoformat()
                self.assertEqual(GRAPH._timestamp(inspection["inspected_at"]), GRAPH._timestamp(expected))
                WORK.complete_work(c.root, assignment, expected_revision=2, reason="Recovered original inspection")
                self.assertEqual(c.row("work_items", work["work_id"])["state"], "completed")
                self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)


if __name__ == "__main__":
    unittest.main()
