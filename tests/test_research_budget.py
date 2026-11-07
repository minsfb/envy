"""Owned-fixture admission, deadline, concurrency and return-reconciliation tests."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_retrospective_research as fixture
import test_research_work_plan as plan_fixture


MODULE = fixture.HELPER.with_name("research_budget.py")
SPEC = importlib.util.spec_from_file_location("budget_fixture", MODULE)
BUDGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUDGET)


class ResearchBudgetTests(unittest.TestCase):
    json_file = fixture.ResearchBundleTests.json_file
    text_file = fixture.ResearchBundleTests.text_file
    cli = fixture.ResearchBundleTests.cli
    capture = fixture.ResearchBundleTests.capture
    index = fixture.ResearchBundleTests.index
    validate = fixture.ResearchBundleTests.validate
    valid_bundle = fixture.ResearchBundleTests.valid_bundle
    publication_files = fixture.ResearchBundleTests.publication_files

    def setUp(self):
        fixture.ResearchBundleTests.setUp(self)
        self.contract["requirements"]["research_budget"] = True
        self.limits()
        self.cli("budget-activity", "--actor", "fixture-researcher", "--action", "start")

    def limits(self, **values):
        self.contract["research_budget"] = dict(self.contract.get("research_budget", {}), **values)
        self.cli("amend", "--contract", self.json_file(self.contract))

    def search_spec(self, key="search-1", **changes):
        spec = fixture.ResearchBundleTests.search_spec(self, key, **changes)
        spec["research"] = {"stage": "baseline", "branch": "archive", "parent_operation": None,
                            "root_basis": "Archive pilot is an explicit intake scope."}
        return spec

    def read_spec(self, key="read-1", **changes):
        return fixture.ResearchBundleTests.read_spec(self, key, **changes)

    def totals(self):
        return self.cli("budget-status")["result"]

    def derive(self, spec, parent, stage="priority"):
        spec["research"].update(stage=stage, parent_operation=parent)
        return spec

    def indexed(self, spec, ids, next_cursor=None):
        self.capture(spec)
        self.index(spec["id"], ids, cursor=next_cursor)
        return spec

    def failed(self, key, previous=None, classification="transient"):
        spec = self.search_spec(key, command=[sys.executable, "-c", "raise SystemExit(1)"])
        if previous:
            spec["research"]["retry_of"] = previous
        self.capture(spec, success=False)
        self.cli("budget-classify", "--id", key, "--classification", classification,
                 "--reason", "Fixture provider explicitly reported a temporary disconnect.")
        return spec

    def test_failed_attempts_are_spent_and_renamed_retries_cannot_evade_limit(self):
        self.limits(search_attempts=10, attempts_per_request=3)
        self.failed("a")
        spec = self.search_spec("renamed")
        denied = self.cli("capture", "--spec", self.json_file(spec), success=False)
        self.assertIn("explicit retry_of", denied["error"])
        self.failed("b", "a")
        self.failed("c", "b")
        spec["research"]["retry_of"] = "c"
        denied = self.cli("capture", "--spec", self.json_file(spec), success=False)
        self.assertIn("retry attempts exhausted", denied["error"])
        self.assertEqual(self.totals()["search_attempts"], 3)
        self.assertFalse((self.bundle / "operations" / "renamed").exists())

    def test_unknown_failure_needs_observed_transient_classification(self):
        self.failed("a", classification="unknown")
        spec = self.search_spec("b")
        spec["research"]["retry_of"] = "a"
        self.assertIn("observed transient", self.cli("capture", "--spec", self.json_file(spec), success=False)["error"])

    def test_terminal_source_does_not_retry_by_changing_format(self):
        spec = self.read_spec(command=[sys.executable, "-c", "raise SystemExit(1)"])
        self.capture(spec, success=False)
        self.cli("budget-classify", "--id", "read-1", "--classification", "access-blocked", "--reason", "Explicit 403.")
        alternate = self.read_spec("read-2", format="native-json")
        self.assertIn("terminal", self.cli("capture", "--spec", self.json_file(alternate), success=False)["error"])
        self.cli("budget-reopen", "--source-id", "record-a", "--reason", "Fixture access was restored.")
        self.capture(alternate)
        self.assertEqual(self.totals()["read_attempts"], 2)
        self.assertEqual(self.totals()["assessed_artifacts"], 1)

    def test_explicit_refresh_is_separate_from_automatic_retry(self):
        self.capture(self.search_spec("a"))
        spec = self.search_spec("b")
        spec["research"].update(refresh_of="a", refresh_basis="User requested a repeat to measure indexing variability.")
        self.capture(spec)
        self.assertEqual(self.totals()["search_attempts"], 2)

    def test_historical_reopen_does_not_reset_every_later_retry_epoch(self):
        command = [sys.executable, "-c", "raise SystemExit(1)"]
        self.capture(self.read_spec("blocked", command=command), success=False)
        self.cli("budget-classify", "--id", "blocked", "--classification", "access-blocked", "--reason", "Explicit denial.")
        self.cli("budget-reopen", "--source-id", "record-a", "--reason", "Provider access restored.")
        for key, previous in (("retry-1", None), ("retry-2", "retry-1"), ("retry-3", "retry-2")):
            spec = self.read_spec(key, command=command)
            if previous:
                spec["research"]["retry_of"] = previous
            self.capture(spec, success=False)
            self.cli("budget-classify", "--id", key, "--classification", "transient", "--reason", "Temporary network failure.")
        spec = self.read_spec("retry-4", command=command)
        spec["research"]["retry_of"] = "retry-3"
        self.assertIn("retry attempts exhausted", self.cli("capture", "--spec", self.json_file(spec), success=False)["error"])
        self.assertEqual(self.totals()["read_attempts"], 4)

    def test_concurrent_admission_cannot_oversubscribe_last_attempts(self):
        self.limits(search_attempts=2)
        processes = []
        for index in range(8):
            spec = self.search_spec("worker-%d" % index)
            spec["request"]["query"] += str(index)
            processes.append(subprocess.Popen([sys.executable, "-I", "-B", str(fixture.HELPER), "capture",
                                               str(self.bundle), "--spec", str(self.json_file(spec))],
                                              env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = [process.communicate(timeout=15) for process in processes]
        self.assertTrue(all(not error for _, error in results))
        self.assertEqual(sum(process.returncode == 0 for process in processes), 2)
        self.assertEqual(self.totals()["search_attempts"], 2)

    def test_concurrent_assessment_identity_limit_and_metadata_exception(self):
        self.limits(assessed_artifacts=1)
        metadata = self.read_spec("metadata")
        metadata["research"]["assess"] = False
        self.indexed(metadata, ["record-a"])
        self.assertEqual(self.totals()["assessed_artifacts"], 0)
        processes = []
        for index in range(6):
            spec = self.read_spec("assessor-%d" % index)
            spec["request"]["source_id"] = "candidate-%d" % index
            processes.append(subprocess.Popen([sys.executable, "-I", "-B", str(fixture.HELPER), "capture",
                                               str(self.bundle), "--spec", str(self.json_file(spec))],
                                              env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = [process.communicate(timeout=15) for process in processes]
        self.assertTrue(all(not error for _, error in results))
        self.assertEqual(sum(process.returncode == 0 for process in processes), 1)
        self.assertEqual(self.totals()["assessed_artifacts"], 1)
        self.assertEqual(self.totals()["read_attempts"], 2)

    def test_alternating_query_read_chain_cannot_reset_generation(self):
        self.limits(followup_generations=1)
        self.indexed(self.search_spec(), ["record-a"])
        self.indexed(self.derive(self.read_spec(), "search-1", "baseline"), ["record-a"])
        child = self.derive(self.search_spec("followup"), "read-1")
        child["request"]["query"] = "term discovered in source"
        self.indexed(child, ["record-b"])
        read = self.derive(self.read_spec("read-b"), "followup")
        read["request"]["source_id"] = "record-b"
        self.indexed(read, ["record-b"])
        next_query = self.derive(self.search_spec("grandchild"), "read-b", "deep")
        next_query["request"]["query"] = "new source term"
        self.assertIn("generation budget", self.cli("capture", "--spec", self.json_file(next_query), success=False)["error"])
        self.assertEqual(self.totals()["by_generation"], {"0": 2, "1": 2})

    def test_alternate_extent_and_observed_pagination_do_not_add_generation(self):
        self.indexed(self.search_spec(), ["record-a"], "next")
        page = self.derive(self.search_spec("page2"), "search-1", "baseline")
        page["request"]["cursor"] = "next"
        self.indexed(page, ["record-a"])
        self.indexed(self.derive(self.read_spec(), "page2", "baseline"), ["record-a"])
        alternate = self.derive(self.read_spec("alternate", format="native-json"), "read-1", "baseline")
        self.capture(alternate)
        self.assertEqual(self.totals()["by_generation"], {"0": 4})
        self.assertEqual(self.totals()["assessed_artifacts"], 1)

    def test_source_pagination_cannot_borrow_a_lower_generation_root(self):
        self.indexed(self.read_spec(), ["record-a"])
        self.indexed(self.search_spec(), ["record-a"], "next")
        page = self.derive(self.search_spec("wrong-root-page"), "search-1")
        page["request"]["cursor"] = "next"
        origin = {"source_id": "record-a", "read_id": "read-1", "locator": "paragraph 1"}
        before = (self.bundle / "research-budget.json").read_bytes()
        attempts = [e for e in json.loads(before)["events"] if e["type"] == "admit"]
        with self.assertRaisesRegex(ValueError, "cannot lower or change"):
            BUDGET.validate_source_origin(self.bundle, page, attempts, origin)
        self.assertEqual((self.bundle / "research-budget.json").read_bytes(), before)
        self.assertFalse((self.bundle / "operations/wrong-root-page").exists())

    def test_source_pagination_requires_every_original_receipt_and_index(self):
        self.indexed(self.read_spec(), ["record-a"])
        first = self.derive(self.search_spec("term-first"), "read-1")
        first["request"]["query"] = "source terminology"
        self.indexed(first, ["record-a"], "next")
        page = self.derive(self.search_spec("term-next"), "term-first")
        page["request"].update(query="source terminology", cursor="next")
        origin = {"source_id": "record-a", "read_id": "read-1", "locator": "paragraph 1"}
        before = (self.bundle / "research-budget.json").read_bytes()
        attempts = [e for e in json.loads(before)["events"] if e["type"] == "admit"]
        BUDGET.validate_source_origin(self.bundle, page, attempts, origin)
        for operation, filename in (("read-1", "stdout"), ("term-first", "stdout"), ("term-first", "receipt.json"), ("term-first", "result.json")):
            path = self.bundle / "operations" / operation / filename
            original = path.read_bytes()
            with self.subTest(operation=operation, filename=filename):
                try:
                    if filename == "stdout":
                        path.write_bytes(original + b"changed")
                    else:
                        changed = json.loads(original)
                        changed["outcome" if filename == "receipt.json" else "receipt_sha256"] = "wrong"
                        path.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, "unchanged successful receipt"):
                        BUDGET.validate_source_origin(self.bundle, page, attempts, origin)
                finally:
                    path.write_bytes(original)
        self.assertEqual((self.bundle / "research-budget.json").read_bytes(), before)

    def test_source_pagination_traversal_rejects_cycles_and_has_a_fixed_bound(self):
        origin = {"source_id": "record-a", "read_id": "source", "locator": "paragraph 1"}
        request = {"query": "source terminology", "filters": {}, "cap": 5, "cursor": "next"}
        spec = {"kind": "search", "provider": "archive", "request": request, "research": {"parent_operation": "page-0"}}
        for cycle in (True, False):
            visits = []
            def verified_node(root, key, attempts):
                # Isolate the traversal bound from the independently tested
                # immutable-file validator; no recorder state is manufactured.
                if key == "source":
                    return {"generation": 0}, {"spec": {"kind": "read", "request": {"source_id": "record-a"}}}, {}
                visits.append(key)
                parent = key if cycle else "page-" + str(len(visits))
                return {"generation": 1, "parent_operation": parent}, {"spec": spec}, {"next_cursor": "next"}
            with self.subTest(cycle=cycle), patch.object(BUDGET, "_indexed_admission", side_effect=verified_node):
                with self.assertRaisesRegex(ValueError, "cyclic or exceeds"):
                    BUDGET.validate_source_origin(self.bundle, spec, [None] * 1025, origin)
                self.assertLessEqual(len(visits), 1024)

    def test_metadata_term_query_retains_ancestry_without_forcing_source_read(self):
        self.indexed(self.search_spec(), ["record-a"])
        spec = self.derive(self.search_spec("metadata-term"), "search-1")
        spec["request"]["query"] = "organizing label in snippet"
        spec["research"].update(origin_source_id="record-a", origin_locator="result 1 snippet",
                                derivation_basis="The returned title contains a useful organizing label.")
        self.capture(spec)
        self.assertEqual(self.totals()["by_generation"], {"0": 1, "1": 1})

    def test_priority_milestone_does_not_block_unfinished_baseline_breadth(self):
        self.cli("budget-stage", "--stage", "priority", "--reason", "Priority reads interleave with baseline searching.")
        self.capture(self.search_spec())
        self.assertEqual(self.totals()["by_stage"]["baseline"], 1)

    def test_time_counts_union_and_sum_of_declared_actor_intervals(self):
        state = {"events": [{"type": "activity-start", "actor": "a", "at": 0},
                            {"type": "activity-start", "actor": "b", "at": 10},
                            {"type": "activity-stop", "actor": "a", "at": 20},
                            {"type": "activity-stop", "actor": "b", "at": 30}]}
        result = BUDGET.accounting(state, 500)
        self.assertEqual(result["active_minutes"], .5)
        self.assertAlmostEqual(result["agent_minutes"], 2 / 3)
        self.assertEqual(result["active_actors"], [])

    def test_admission_requires_actor_activity(self):
        self.cli("budget-activity", "--actor", "fixture-researcher", "--action", "stop")
        self.assertIn("start actor research", self.cli("capture", "--spec", self.json_file(self.search_spec()), success=False)["error"])

    def test_request_deadline_shortens_to_remaining_research_allowance(self):
        self.limits(active_minutes=.02)
        spec = self.search_spec(command=[sys.executable, "-c", "import time; time.sleep(10)"])
        operation = self.capture(spec, success=False)
        request = fixture.read_json(operation / "request.json")
        self.assertLess(request["spec"]["timeout"], 1.2)
        self.assertEqual(fixture.read_json(operation / "receipt.json")["outcome"], "timeout")
        next_spec = self.search_spec("later")
        next_spec["request"]["query"] = "later"
        self.assertIn("time budget exhausted", self.cli("capture", "--spec", self.json_file(next_spec), success=False)["error"])

    def test_tool_import_begin_returns_clamped_deadline_before_external_dispatch(self):
        self.limits(request_timeout_seconds=.05)
        result = self.cli("begin", "--spec", self.json_file(self.search_spec()))["result"]
        self.assertEqual(result["effective_timeout"], .05)
        operation = Path(result["operation"])
        self.assertEqual(operation, self.bundle / "operations" / "search-1")
        stored = fixture.read_json(operation / "request.json")["spec"]
        self.assertEqual(stored["timeout"], result["effective_timeout"])
        self.assertEqual(stored["research_admission"], result["research_admission"])
        self.assertFalse((operation / "receipt.json").exists())
        self.assertEqual(self.totals()["search_attempts"], 1)

    @unittest.skipUnless(os.name == "posix", "owned process-group cancellation is POSIX-specific")
    def test_timeout_kills_wrapper_grandchild_before_it_can_write(self):
        marker = self.work / "grandchild-survived"
        child_code = "import time; from pathlib import Path; time.sleep(.7); Path(%r).write_text('bad')" % str(marker)
        parent_code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child_code
        spec = self.search_spec(command=[sys.executable, "-c", parent_code])
        spec["timeout"] = .2
        self.capture(spec, success=False)
        time.sleep(.8)
        self.assertFalse(marker.exists())

    def returned(self, leads=True):
        return {"id": "worker-return", "read_id": "read-1", "actor": "fixture-researcher",
                "coverage": "Full fixture body", "inspection": "full", "relevance": "relevant",
                "expansion_review": "The source names a prerequisite workstream.",
                "leads": ([{"id": "term", "kind": "vocabulary", "value": "archive migration",
                            "locator": "paragraph 1", "questions": ["decision"],
                            "reason": "Find the prerequisite workstream."}] if leads else [])}

    def return_ledger(self, returned):
        source = self.ledger["sources"][0]
        source["inspections"] = [{"read_id": "read-1", "actor": "fixture-researcher",
                                  "coverage": "Full fixture body", "return_id": returned["id"]}]
        source["leads"] = []
        for original in returned["leads"]:
            source["leads"].append(dict(original, status="bounded",
                                        return_refs=[{"return_id": returned["id"], "lead_id": original["id"]}]))

    def test_final_delivery_can_validate_after_research_attempt_budget_is_exhausted(self):
        self.limits(search_attempts=1, read_attempts=1)
        self.valid_bundle()
        returned = self.returned(False)
        self.cli("reader-return", "--return", self.json_file(returned))
        self.return_ledger(returned)
        self.cli("budget-activity", "--actor", "fixture-researcher", "--action", "stop")
        result = self.validate(phase="synthesis")
        self.assertFalse(result["result"]["research_budget"]["delivery_limited"])
        self.assertEqual(result["result"]["research_budget"]["search_attempts"], 1)
        self.cli("publication", "--ledger", self.json_file(self.ledger),
                 "--manifest", self.json_file(self.publication_files()))

    def test_saved_plan_read_origin_cannot_be_relabelled_as_baseline_root(self):
        case = plan_fixture.ResearchWorkPlanTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.contract["requirements"]["research_budget"] = True
        case.cli("amend", "--contract", case.json_file(case.contract))
        for actor in ("fixture-researcher", "fixture-reader"):
            case.cli("budget-activity", "--actor", actor, "--action", "start")
        original_spec = case.spec

        def budget_spec(item, key, **kwargs):
            spec = original_spec(item, key, **kwargs)
            spec["research"] = {"stage": "baseline", "branch": "pilot-history",
                                "parent_operation": None if item["kind"] == "search" else "search-01",
                                "root_basis": "The pilot is explicitly in scope."}
            return spec

        case.spec = budget_spec
        source = case.retrieve()
        returned = self.returned(False)
        returned.update(read_id="read-01", actor="fixture-reader")
        case.cli("reader-return", "--return", case.json_file(returned))
        source["inspections"] = [{"read_id": "read-01", "actor": "fixture-reader",
                                  "coverage": "Full body", "return_id": returned["id"]}]
        source["analysis"] = {"status": "complete", "reason": "Inspected the body."}
        case.plan["items"][1]["status"] = "done"
        item = case.search_item("derived", origin={"source_id": "decision-note", "read_id": "read-01",
                                                   "locator": "paragraph 1"})
        item["request"]["query"] = "archive migration"
        case.plan["items"].append(item)
        case.save_plan()
        spec = case.spec(item, "derived-op")
        result = case.cli("capture", "--spec", case.json_file(spec), success=False)
        self.assertIn("ancestry differs", result["error"])
        spec["research"].update(parent_operation="read-01", stage="priority")
        case.capture(spec)
        self.assertEqual(case.cli("budget-status")["result"]["by_generation"], {"0": 2, "1": 1})

    def test_dropped_raw_return_lead_blocks_synthesis_even_if_claims_have_evidence(self):
        self.valid_bundle()
        returned = self.returned()
        self.cli("reader-return", "--return", self.json_file(returned))
        self.return_ledger(returned)
        self.cli("budget-activity", "--actor", "fixture-researcher", "--action", "stop")
        self.ledger["sources"][0]["leads"] = []
        self.assertIn("returned leads missing", self.validate(phase="synthesis", success=False)["error"])
        self.return_ledger(returned)
        self.validate(phase="synthesis")

    def test_empty_lead_return_cannot_disappear_from_read_inventory(self):
        self.valid_bundle()
        self.cli("reader-return", "--return", self.json_file(self.returned(False)))
        self.cli("budget-activity", "--actor", "fixture-researcher", "--action", "stop")
        self.assertIn("returns missing from ledger inspections", self.validate(phase="synthesis", success=False)["error"])

    def test_return_is_immutable_and_binds_actual_read_actor(self):
        self.valid_bundle()
        returned = self.returned(False)
        self.cli("reader-return", "--return", self.json_file(returned))
        self.assertIn("already recorded", self.cli("reader-return", "--return", self.json_file(returned), success=False)["error"])
        self.return_ledger(returned)
        self.ledger["sources"][0]["inspections"][0]["actor"] = "different-reader"
        self.assertIn("matching immutable reader return", self.validate(success=False)["error"])

    def test_metadata_only_read_cannot_be_promoted_without_assessment_admission(self):
        self.valid_bundle()
        spec = self.read_spec("metadata2")
        spec["request"]["extent"] = "Metadata discovery"
        spec["research"]["assess"] = False
        self.indexed(spec, ["record-a"])
        returned = self.returned(False)
        returned["read_id"] = "metadata2"
        self.assertIn("admit substantive assessment", self.cli("reader-return", "--return", self.json_file(returned), success=False)["error"])
        self.cli("budget-assess", "--read-id", "metadata2", "--actor", "fixture-researcher")
        self.cli("reader-return", "--return", self.json_file(returned))

    def test_changed_budget_journal_is_detected(self):
        self.capture(self.search_spec())
        path = self.bundle / "research-budget.json"
        state = fixture.read_json(path)
        state["events"][-1]["generation"] = 9
        path.write_text(json.dumps(state))
        self.assertIn("changed research budget journal", self.cli("budget-status", success=False)["error"])


if __name__ == "__main__":
    unittest.main()
