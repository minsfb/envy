"""Owned fictional contract/plan publication interruption and recovery checks."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "assets/agent-assets/skills/retrospective-research/scripts/research_bundle.py"
SPEC = importlib.util.spec_from_file_location("publication_bundle", HELPER)
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)


class CurrentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="research-current-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bundle"
        self.contract = {"version": 1, "purpose": "Fictional archive", "scope": "Owned records",
                         "requirements_source": "Local fixture", "requirements": {"candidate_table": False,
                         "brief_max_words": 80, "destination": "Local", "research_plan": True},
                         "providers": {"archive": {"read_formats": ["text"], "basis": "Owned fixture"}},
                         "questions": [{"id": "why", "text": "Why?", "central": False}]}
        self.plan = {"version": 1, "owner": "coordinator", "coverage": [{"id": "all", "questions": ["why"],
                     "terms": [], "providers": ["archive"], "people": [], "periods": [], "work_types": [], "reason": "Owned scope"}],
                     "items": [{"id": "search", "coverage": ["all"], "kind": "search", "actor": "reader",
                     "provider": "archive", "priority": "normal", "status": "queued", "reason": "Fictional search",
                     "request": {"query": "fiction", "filters": {}, "cap": 1, "cursor": None}}]}
        self.ledger = {"sources": []}
        BUNDLE.init(self.root, self.contract)

    def interrupt(self, name, call):
        actual = BUNDLE.write
        def fail(path, value):
            if path.name == name:
                raise OSError("Owned publication interruption")
            return actual(path, value)
        with patch.object(BUNDLE, "write", side_effect=fail):
            with self.assertRaises(OSError):
                call()

    def recover_cli(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(HELPER), "recover-current", str(self.root)],
                                env={"PATH": os.defpath}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "pass")
        return json.loads(result.stdout)

    def test_contract_metadata_failure_recovers_exact_snapshot_and_keeps_history(self):
        original = (self.root / "contract.json").read_bytes()
        changed = copy.deepcopy(self.contract)
        changed["requirements"]["brief_max_words"] = 70
        self.interrupt("bundle.json", lambda: BUNDLE.amend(self.root, changed))
        self.assertTrue((self.root / "current-publication.json").exists())
        result = self.recover_cli()
        self.assertEqual(BUNDLE.read(self.root / "contract.json"), changed)
        self.assertEqual(BUNDLE.digest(self.root / "contract.json"), BUNDLE.read(self.root / "bundle.json")["contract_sha256"])
        self.assertIn(original, [p.read_bytes() for p in (self.root / "contracts").iterdir()])
        self.assertFalse((self.root / "current-publication.json").exists())
        self.assertFalse(self.recover_cli()["result"]["pending"])
        BUNDLE.save_plan(self.root, self.plan, self.ledger)

    def test_plan_metadata_failure_can_resume_then_save_and_render(self):
        BUNDLE.save_plan(self.root, self.plan, self.ledger)
        old = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        previous = (self.root / "plans" / (old + ".json")).read_bytes()
        self.plan["items"][0]["reason"] = "Revised fictional search"
        self.interrupt("bundle.json", lambda: BUNDLE.save_plan(self.root, self.plan, self.ledger))
        result = self.recover_cli()
        recovered = result["result"]["sha256"]
        self.assertEqual(BUNDLE.read(self.root / "plan.json")["_record"]["parent_sha256"], old)
        self.assertEqual((self.root / "plans" / (old + ".json")).read_bytes(), previous)
        BUNDLE.render_plan(self.root)
        BUNDLE.save_plan(self.root, self.plan, self.ledger)
        self.assertEqual(BUNDLE.read(self.root / "plan.json")["_record"]["parent_sha256"], recovered)

    def test_first_plan_current_file_failure_retains_intent_for_retry(self):
        self.interrupt("plan.json", lambda: BUNDLE.save_plan(self.root, self.plan, self.ledger))
        self.assertFalse((self.root / "plan.json").exists())
        BUNDLE.save_plan(self.root, self.plan, self.ledger)
        BUNDLE.render_plan(self.root)
        self.assertFalse((self.root / "current-publication.json").exists())

    def test_recovery_refuses_unrelated_current_edit(self):
        changed = copy.deepcopy(self.contract)
        changed["requirements"]["brief_max_words"] = 70
        self.interrupt("bundle.json", lambda: BUNDLE.amend(self.root, changed))
        drift = dict(changed, scope="Unrelated edit to preserve")
        BUNDLE.write(self.root / "contract.json", drift)
        before = (self.root / "contract.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "outside pending publication"):
            BUNDLE.recover_current(self.root)
        self.assertEqual((self.root / "contract.json").read_bytes(), before)

    def test_graph_registration_failure_resumes_same_saved_plan(self):
        self.contract["requirements"]["research_budget"] = True
        BUNDLE.amend(self.root, self.contract)
        graph = BUNDLE.graph_module()
        graph.initialize(self.root, "coordinator", "publication-fixture")
        with patch.object(BUNDLE, "sync_graph_plan", side_effect=OSError("Owned graph registration interruption")):
            with self.assertRaises(OSError):
                BUNDLE.save_plan(self.root, self.plan, self.ledger)
        key = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        self.recover_cli()
        with graph.snapshot(self.root) as conn:
            self.assertEqual(conn.execute("SELECT plan_sha256 FROM plan_snapshots").fetchone()[0], key)
        self.assertEqual(BUNDLE.read(self.root / "bundle.json")["plan_sha256"], key)

    def interrupted_budget_enablement(self):
        changed = copy.deepcopy(self.contract)
        changed["requirements"].update(research_budget=True, research_plan=False)
        actual_mkdir = Path.mkdir
        def fail_returns_directory(path, *args, **kwargs):
            if path == self.root / "reader-returns":
                raise OSError("Owned interruption after the journal was written")
            return actual_mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", fail_returns_directory):
            with self.assertRaises(OSError):
                BUNDLE.amend(self.root, changed)
        self.assertTrue((self.root / "research-budget.json").is_file())
        self.assertFalse((self.root / "reader-returns").exists())
        self.assertTrue((self.root / "current-publication.json").is_file())
        return changed

    def test_budget_initialization_recovery_preserves_events_and_accepts_reader_return(self):
        self.interrupted_budget_enablement()
        BUNDLE.budget.activity(self.root, "reader", "start")
        journal = (self.root / "research-budget.json").read_bytes()
        self.recover_cli()
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)
        self.assertTrue((self.root / "reader-returns").is_dir())
        spec = {"id": "read-one", "actor": "reader", "provider": "archive", "kind": "read",
                "request": {"source_id": "fiction", "format": "text", "extent": "all"}, "timeout": 5,
                "tool": "fictional.read", "research": {"stage": "baseline", "branch": "fixture",
                "parent_operation": None, "root_basis": "Owned fictional root"}}
        BUNDLE.start(self.root, spec, "tool-import")
        text = Path(self.temp.name) / "fiction.txt"
        text.write_text("Owned fictional text.")
        BUNDLE.import_output(self.root, "read-one", text, None, "ok")
        BUNDLE.index_result(self.root, "read-one", {"source_ids": ["fiction"], "pagination": "not-applicable",
                            "next_cursor": None, "note": "Owned fictional extraction"})
        returned = {"id": "return-one", "read_id": "read-one", "actor": "reader", "inspection": "full",
                    "relevance": "relevant", "coverage": "Entire fictional text",
                    "expansion_review": "No further leads", "leads": []}
        BUNDLE.record_reader_return(self.root, returned)
        self.assertTrue((self.root / "reader-returns/return-one.json").is_file())
        events = BUNDLE.read(self.root / "research-budget.json")["events"]
        self.assertEqual(events[:1], json.loads(journal)["events"])
        self.assertEqual(sum(event["type"] == "reader-return" for event in events), 1)

    def test_budget_initialization_conflict_keeps_publication_pending(self):
        changed = self.interrupted_budget_enablement()
        journal = (self.root / "research-budget.json").read_bytes()
        conflict = self.root / "reader-returns"
        conflict.write_text("Unrelated file to preserve")
        with self.assertRaises(FileExistsError):
            BUNDLE.recover_current(self.root)
        self.assertTrue((self.root / "current-publication.json").is_file())
        self.assertEqual(conflict.read_text(), "Unrelated file to preserve")
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)
        conflict.rename(self.root / "retained-conflict.txt")
        BUNDLE.amend(self.root, changed)
        self.assertTrue(conflict.is_dir())
        self.assertFalse((self.root / "current-publication.json").exists())
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)


if __name__ == "__main__":
    unittest.main()
