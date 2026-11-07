"""Saved work intent applies to raw assignments and every execution boundary.

Only historical fixture construction disables the new matcher. Current gates
run unchanged; public CLI tests use fictional files and no provider/network.
"""

import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture
import test_research_graph_retained_reads as retained

WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
REAL_EVIDENCE = BUNDLE.sync_graph_evidence


class WorkIntentTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.ResearchGraphWorkTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def searches(self, changes=None):
        c = self.c
        c.items[0].update(status="dropped", reason="Use fictional search work")
        alpha = {"id": "alpha", "coverage": ["decision"], "kind": "search", "actor": "reader",
                 "provider": "archive", "priority": "normal", "status": "queued", "reason": "Discover fictional alpha",
                 "request": {"query": "alpha", "filters": {}, "cap": 2, "cursor": None, "route": "body"}}
        c.items.append(alpha)
        for field, value in (changes or {"query": "beta"}).items():
            item = copy.deepcopy(alpha)
            item["id"] = "other-" + field
            item["request"][field] = value
            c.items.append(item)
        c.save_plan()
        c.registration = WORK.register_plan(c.root, c.plan_hash)
        return c.row("work_items", c.binding("alpha")["work_id"])

    def packet(self, work, item):
        c = self.c
        with GRAPH.snapshot(c.root) as conn:
            return WORK._packet(c.root, conn, [WORK._insert("assignments", assignment_id="cross-" + item,
                work_id=work["work_id"], actor_id="reader", claimed_at=WORK._now(),
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                generation_at_claim=work["generation"], plan_item_id=item, plan_sha256=c.plan_hash),
                WORK._update("work_items", work, state="leased", transition_reason="Fictional raw assignment probe")])

    def historical_matcher(self):
        stack = ExitStack()
        # The fixture loader and runtime sibling loader may hold separate module
        # objects. Only reconstruct the former admission policy in this scope.
        for module in {WORK, INGEST._sibling("research_graph_work")}:
            stack.enter_context(patch.object(module, "_match_plan_work", return_value=None))
        return stack

    def historical_assignment(self, work):
        c = self.c
        with self.historical_matcher():
            GRAPH.ingest(c.root, self.packet(work, "other-query"), evidence_validator=WORK.evidence_validator)
            WORK.start_assignment(c.root, "cross-other-query", expected_revision=1)
        return "cross-other-query"

    def test_raw_cli_rejects_each_search_request_difference_without_spending(self):
        c = self.c
        changes = {"query": "beta", "filters": {"type": "note"}, "cursor": "page-two", "cap": 3, "route": "titles"}
        work = self.searches(changes)
        events = c.rows("graph_events")
        for field in changes:
            with self.subTest(field=field):
                self.assertNotEqual(work["work_id"], c.binding("other-" + field)["work_id"])
                c.error("scope_mismatch", WORK.claim, c.root, work["work_id"], "reader", c.plan_hash,
                        "other-" + field, expected_revision=1, enforce_fairness=False,
                        fairness_override_reason="Exercise fictional mismatched binding")
                path = c.directory / ("assignment-" + field + ".json")
                path.write_text(json.dumps(self.packet(work, "other-" + field)))
                result = subprocess.run([sys.executable, "-I", "-B", str(fixture.SCRIPTS / "research_bundle.py"),
                                         "graph", "reconcile", str(c.root), "--file", str(path)],
                                        cwd=c.directory, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "scope_mismatch")
        self.assertEqual(c.rows("assignments"), [])
        self.assertEqual(c.rows("graph_events"), events)
        self.assertEqual(BUDGET.status(c.root)["search_attempts"], 0)

    def test_historical_mismatch_cannot_admit_dispatch_record_or_index_receipt(self):
        c = self.c
        work = self.searches()
        assignment = self.historical_assignment(work)
        spec = c.spec(assignment, "beta-operation")
        with patch.object(BUNDLE, "sync_graph_work", c.automatic_work_hook):
            c.error("scope_mismatch", BUNDLE.start, c.root, spec, "tool-import")
        self.assertEqual(BUDGET.status(c.root)["search_attempts"], 0)
        # Reconstruct an old original admission/mirror, then exercise current
        # dispatch and receipt paths against those immutable originals.
        with self.historical_matcher():
            BUNDLE.start(c.root, spec, "tool-import")
            WORK.record_admission(c.root, "beta-operation")
        c.error("scope_mismatch", WORK.record_admission, c.root, "beta-operation")
        c.error("scope_mismatch", WORK.record_dispatch, c.root, "beta-operation")
        BUNDLE.import_output(c.root, "beta-operation", c.output, None, "ok")
        c.error("scope_mismatch", WORK.record_completion, c.root, "beta-operation")
        result = {"source_ids": [], "pagination": "complete", "next_cursor": None, "note": "Empty fictional result"}
        c.error("scope_mismatch", BUNDLE.index_result, c.root, "beta-operation", result)
        self.assertFalse((c.root / "operations/beta-operation/result.json").exists())
        self.assertEqual(c.row("operation_links", "beta-operation")["execution_state"], "admitted")
        self.assertEqual(c.row("work_items", work["work_id"])["state"], "running")
        self.assertEqual(BUDGET.status(c.root)["search_attempts"], 1)

    def test_historical_success_cannot_complete_mismatched_work(self):
        c = self.c
        work = self.searches()
        assignment = self.historical_assignment(work)
        with self.historical_matcher(), patch.object(BUNDLE, "sync_graph_work", c.automatic_work_hook), \
                patch.object(BUNDLE, "sync_graph_evidence", REAL_EVIDENCE):
            BUNDLE.start(c.root, c.spec(assignment, "beta-operation"), "tool-import")
            WORK.record_dispatch(c.root, "beta-operation")
            BUNDLE.import_output(c.root, "beta-operation", c.output, None, "ok")
            BUNDLE.index_result(c.root, "beta-operation", {"source_ids": [], "pagination": "complete", "next_cursor": None,
                                                         "note": "Empty historical fictional result"})
        events = c.rows("graph_events")
        c.error("scope_mismatch", WORK.complete_work, c.root, assignment, expected_revision=2, reason="Wrong work cannot complete")
        self.assertEqual(c.rows("graph_events"), events)
        self.assertEqual(c.row("work_items", work["work_id"])["state"], "running")

    def test_historical_wrong_query_can_only_retire_without_credit(self):
        c = self.c
        work = self.searches()
        assignment = self.historical_assignment(work)
        with self.historical_matcher():
            c.admit(assignment, "beta-operation")
        events = c.rows("graph_events")
        journal = (c.root / "research-budget.json").read_bytes()
        WORK.reconcile_operation(c.root, "beta-operation", expected_revision=2, worker_identity="reader", worker_stopped=True,
                                 cancel=True, reason="Checked the fictional original worker stopped")
        WORK.bound_work(c.root, assignment, expected_revision=2, state="cancelled", reason="Retire mismatched original intent")
        self.assertEqual(c.rows("graph_events")[:len(events)], events)
        self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)
        self.assertEqual(c.row("work_items", work["work_id"])["state"], "cancelled")
        self.assertEqual(c.rows("inspections"), [])
        self.assertEqual(c.row("operation_links", "beta-operation")["execution_state"], "cancelled")

    def test_atomic_completion_cannot_replace_the_executed_query(self):
        c = self.c
        self.searches()
        assignment = c.claim("alpha", enforce_fairness=False)
        with patch.object(BUNDLE, "sync_graph_work", c.automatic_work_hook), patch.object(BUNDLE, "sync_graph_evidence", REAL_EVIDENCE):
            BUNDLE.start(c.root, c.spec(assignment, "alpha-operation"), "tool-import")
            WORK.record_dispatch(c.root, "alpha-operation")
            BUNDLE.import_output(c.root, "alpha-operation", c.output, None, "ok")
            BUNDLE.index_result(c.root, "alpha-operation", {"source_ids": ["history-hit"], "pagination": "complete", "next_cursor": None,
                "sources": [{"source_id": "history-hit", "namespace": "fixture-doc", "external_id": "history", "version": version}
                            for version in ({"revision": "v1"}, {"revision": "v2"}, {}, {})],
                "note": "Four native fictional occurrences with distinct version observations"})
        self.assertEqual(len([r for r in c.rows("discoveries") if r["operation_id"] == "alpha-operation"]), 4)
        self.assertEqual(len(c.rows("artifact_versions")), 4)
        assigned = c.row("assignments", assignment)
        work = c.row("work_items", assigned["work_id"])
        request = dict(work["request_json"], query="unexecuted-query")
        with GRAPH.snapshot(c.root) as conn:
            packet = WORK._packet(c.root, conn, [WORK._update("assignments", assigned, state="completed", finished_at=WORK._now()),
                WORK._update("work_items", work, request_json=request, logical_key=GRAPH.logical_key(dict(work, request_json=request)),
                             state="completed", transition_reason="Attempted query substitution", terminal_reason="Attempted query substitution")])
        c.error("scope_mismatch", WORK._ingest, c.root, packet)
        self.assertEqual(c.row("work_items", work["work_id"]), work)
        WORK.complete_work(c.root, assignment, expected_revision=2, reason="Matching query completed")

    def test_completed_read_correction_is_exact_and_only_validates_historical_evidence(self):
        self.c.doCleanups()
        c = retained.RetainedReadTests()
        c.setUp()
        self.addCleanup(c.doCleanups)
        c.capture_original()
        assignment = c.plan_reread(purpose="Reassess retained source content")
        c.returned(assignment, identity="current-correction")
        assigned = c.row("assignments", assignment)
        work = c.row("work_items", assigned["work_id"])
        request = copy.deepcopy(work["request_json"])
        request.pop("freshness_basis")
        request["capture_ids"] = c.capture_ids
        request["extent"]["body"]["ranges"][0]["end"] = 1
        with GRAPH.snapshot(c.root) as conn:
            packet = WORK._packet(c.root, conn, [WORK._update("assignments", assigned, state="completed", finished_at=WORK._now()),
                WORK._update("work_items", work, request_json=request, logical_key=GRAPH.logical_key(dict(work, request_json=request)),
                             state="completed", transition_reason="Attempted scope substitution", terminal_reason="Attempted scope substitution")])
        c.error("scope_mismatch", WORK._ingest, c.root, packet)
        self.assertEqual(c.row("work_items", work["work_id"]), work)
        c.correct_and_complete(assignment)
        with GRAPH.snapshot(c.root) as conn:
            assigned = GRAPH._get(conn, "assignments", assignment)
            corrected = GRAPH._get(conn, "work_items", work["work_id"])
            c.error("scope_mismatch", WORK.validate_assignment_intent, c.root, conn, assigned)
            self.assertTrue(WORK.validate_assignment_intent(c.root, conn, assigned, allow_completed_correction=True))
            self.assertTrue(WORK._completion(c.root, conn, corrected))
            self.assertTrue(INGEST._returned(c.root, "current-correction", conn)["native"])
            changed = copy.deepcopy(corrected)
            changed["request_json"]["capture_ids"] = ["other-capture"]
            c.error("scope_mismatch", WORK.validate_assignment_intent, c.root, conn, assigned,
                    work=changed, allow_completed_correction=True)
            c.error("scope_mismatch", WORK._binding, c.root, conn, c.spec(assignment), False)
        self.assertEqual(BUDGET.status(c.root)["read_attempts"], 1)

    def test_old_routed_search_replays_and_completes_but_cannot_dispatch_again(self):
        c = self.c
        c.contract["requirements"]["search_routes"] = True
        BUNDLE.amend(c.root, c.contract)
        plan = BUNDLE.read(c.root / "plan.json")
        plan.pop("_record")
        plan["items"][0].update(status="dropped", reason="Exercise historical routed search")
        plan["items"].append({"id": "routed-search", "coverage": ["decision"], "kind": "search", "actor": "reader",
            "provider": "archive", "priority": "normal", "status": "queued", "reason": "Fictional routed search",
            "search_branch": "body-branch", "input_kind": "term",
            "request": {"query": "alpha", "filters": {}, "cap": 2, "cursor": None}})
        plan["search_routes"] = [{"id": "body-route", "provider": "archive", "family": "fixture-search",
            "artifact_types": ["document"], "locations": ["fixture"], "availability": "available", "checked_at": "2026-01-01",
            "basis": "Owned fixture", "limitations": "Fictional data", "invocation": {"tool": "fixture-search"},
            "modes": [{"id": "contains", "input_kinds": ["term"], "filter_fields": []}]}]
        plan["search_branches"] = [{"id": "body-branch", "coverage": ["decision"], "work_type": "review",
            "artifact_type": "document", "location": "fixture", "route": "body-route", "mode": "contains",
            "status": "selected", "reason": "Fictional search branch"}]

        def old_projection(function):
            def project(*args, **kwargs):
                work, parent = function(*args, **kwargs)
                if work["kind"] == "search":
                    work["request_json"]["provider_metadata"] = {"cap": work["request_json"]["provider_metadata"]["cap"]}
                    work["logical_key"] = GRAPH.logical_key(work)
                    work["work_id"] = WORK._identifier("work", work["logical_key"])
                return work, parent
            return project

        with ExitStack() as stack:
            for module in {WORK, INGEST._sibling("research_graph_work")}:
                stack.enter_context(patch.object(module, "_plan_work", side_effect=old_projection(module._plan_work)))
            with patch.object(BUNDLE, "graph_ledger_context", return_value=None):
                BUNDLE.save_plan(c.root, plan, c.ledger)
            c.plan_hash = BUNDLE.read(c.root / "bundle.json")["plan_sha256"]
            c.registration = WORK.register_plan(c.root, c.plan_hash)
            assignment = c.claim("routed-search", enforce_fairness=False)
            spec = dict(c.spec(assignment, "old-search"), search_branch="body-branch", input_kind="term", tool="fixture-search")
            BUNDLE.start(c.root, spec, "tool-import")
            WORK.record_admission(c.root, "old-search")
            WORK.record_dispatch(c.root, "old-search")
            BUNDLE.import_output(c.root, "old-search", c.output, None, "ok")
            WORK.record_completion(c.root, "old-search")
            BUNDLE.index_result(c.root, "old-search", {"source_ids": [], "pagination": "complete", "next_cursor": None,
                                                       "note": "Fictional old-recorder result"})
            INGEST.index_operation(c.root, "old-search")
        events = c.rows("graph_events")
        journal = (c.root / "research-budget.json").read_bytes()
        self.assertTrue(WORK.register_plan(c.root, c.plan_hash)["idempotent"])
        self.assertEqual(c.rows("graph_events"), events)
        c.error("scope_mismatch", WORK.prepare_operation, c.root, dict(spec, id="new-dispatch"))
        with GRAPH.snapshot(c.root) as conn:
            self.assertTrue(INGEST._operation(c.root, "old-search", conn)["native"])
            assigned = GRAPH._get(conn, "assignments", assignment)
            c.error("scope_mismatch", WORK.validate_assignment_intent, c.root, conn, assigned)
            self.assertTrue(WORK.validate_assignment_intent(c.root, conn, assigned, allow_legacy_search=True))
            wrong = dict(spec, tool="wrong-route")
            c.error("scope_mismatch", WORK._binding, c.root, conn, wrong, False)
        WORK.complete_work(c.root, assignment, expected_revision=2, reason="Original routed result completed")
        self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)

    def test_same_assignment_capture_correction_preserves_its_original_operation(self):
        c = self.c
        assignment = c.claim()
        c.admit(assignment)
        c.result()
        c.returned(assignment, identity="first-inspection")
        c.returned(assignment, identity="genuine-reread")
        c.capture_ids = [INGEST.capture_id("read-one", "html")]
        originals = {p: p.read_bytes() for directory in ("plans", "operations", "reader-returns")
                     for p in (c.root / directory).rglob("*") if p.is_file()}
        journal = (c.root / "research-budget.json").read_bytes()
        spec = BUNDLE.read(c.root / "operations/read-one/request.json")["spec"]
        retained.RetainedReadTests.correct_and_complete(c, assignment)
        with GRAPH.snapshot(c.root) as conn:
            assigned = GRAPH._get(conn, "assignments", assignment)
            work = GRAPH._get(conn, "work_items", assigned["work_id"])
            operation = INGEST._operation(c.root, "read-one", conn)
            self.assertTrue(operation["native"])
            self.assertEqual(operation["spec"]["graph_assignment_id"], assignment)
            captured = GRAPH._get(conn, "captures", c.capture_ids[0])
            self.assertTrue(INGEST._retained_capture(c.root, conn, captured)["native"])
            self.assertEqual(set(INGEST.validate_capture_bindings(c.root, conn, work)), set(c.capture_ids))
            self.assertTrue(WORK._completion(c.root, conn, work))
            self.assertEqual(WORK._binding(c.root, conn, spec, False)[0]["assignment_id"], assignment)
            c.error("scope_mismatch", WORK.validate_assignment_intent, c.root, conn, assigned)
            changed = copy.deepcopy(spec)
            changed["request"]["extent"] = "Different unadmitted provider request"
            c.error("scope_mismatch", WORK._binding, c.root, conn, changed, False)
            c.error("pending_dependency", WORK._binding, c.root, conn, dict(spec, id="unadmitted-operation"), False)
        c.error("lease_conflict", WORK.prepare_operation, c.root, dict(spec, id="new-operation"))
        c.error("lease_conflict", WORK.record_dispatch, c.root, "read-one")
        self.assertTrue(WORK.record_admission(c.root, "read-one")["idempotent"])
        self.assertTrue(WORK.record_completion(c.root, "read-one")["idempotent"])
        exports = INGEST._sibling("research_graph_exports")
        directory = Path(exports.export_snapshot(c.root, "same-assignment-correction")["manifest_path"]).parent
        context = exports.validate_ledger(c.root, BUNDLE.read(directory / "ledger.json"))
        completion = next(item for item in context["retained_plan_completions"] if item["assignment_id"] == assignment)
        self.assertEqual(completion["item"]["graph"]["capture_ids"], c.capture_ids)
        self.assertEqual(len(completion["inspection_ids"]), 2)
        self.assertEqual((c.root / "research-budget.json").read_bytes(), journal)
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
