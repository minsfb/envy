"""G4 lifecycle tests against real local plan, budget, graph and return recorders.

Operation outputs use owned fictional files and the recorder's supported
tool-import path; one owned Python child verifies subprocess timeout kill/wait.
No provider or network occurs. Automatic wrapper hooks are disabled in manual
seam tests. One explicit historical regression restores the old outcome mapping
while recording real originals; all other validators and recorders run unchanged.
"""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "assets/agent-assets/skills/retrospective-research/scripts"
SPEC = importlib.util.spec_from_file_location("research_graph_work", SCRIPTS / "research_graph_work.py")
WORK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORK)
GRAPH, INGEST, BUNDLE, BUDGET = WORK.graph, WORK.ingestion, WORK.bundle, WORK.budget
EXTENT = {"body": {"unit": "line", "ranges": [{"start": 1, "end": 2}]}}


class ResearchGraphWorkTests(unittest.TestCase):
    def setUp(self):
        self.automatic_work_hook = getattr(BUNDLE, "sync_graph_work", None)
        self.automatic_plan_hook = getattr(BUNDLE, "sync_graph_plan", None)
        for name in ("sync_graph_work", "sync_graph_evidence", "sync_graph_plan"):
            wrapper = patch.object(BUNDLE, name, return_value=None, create=True)
            wrapper.start()
            self.addCleanup(wrapper.stop)
        temporary = tempfile.TemporaryDirectory(prefix="envy-graph-work-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "bundle"
        self.output = self.directory / "fictional.html"
        self.output.write_text("<p>Fictional Tern queue review.</p>\n<p>Input mode approved; export pending.</p>\n")
        self.contract = {"version": 1, "purpose": "Explain a fictional scoped approval.", "scope": "Owned local sample records.",
                         "requirements_source": "G4 isolated fixture", "requirements": {"candidate_table": True, "brief_max_words": 100,
                         "destination": "Local files", "research_budget": True, "research_plan": True},
                         "providers": {"archive": {"read_formats": ["html", "text"], "basis": "Local fictional import"}},
                         "questions": [{"id": "decision", "text": "What was approved?", "central": True}]}
        BUNDLE.init(self.root, self.contract)
        self.items = [self.item("read-a", "reader", "doc-a")]
        self.ledger = {"sources": [self.source("doc-a"), self.source("doc-b")]}
        self.save_plan()
        GRAPH.initialize(self.root, "coordinator", "work-fixture")
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        BUDGET.activity(self.root, "reader", "start")

    def source(self, identity):
        return {"id": identity, "title": "Fictional " + identity, "url": "https://example.invalid/" + identity,
                "coverage": "Unread", "finding": "Pending", "contributor_basis": "Unknown", "discovered_by": ["seed:fixture"],
                "reads": [], "inspections": [], "analysis": {"status": "pending", "reason": "Unread"},
                "disposition": {"status": "pending", "reason": "Unread"}, "leads": []}

    def item(self, identity, actor, source="doc-a", **annotations):
        return {"id": identity, "coverage": ["decision"], "kind": "read", "actor": actor, "provider": "archive", "priority": "normal",
                "status": "queued", "reason": "Inspect fictional source", "request": {"source_id": source, "format": "html", "extent": "Body lines one through two"},
                "graph": {"extent": copy.deepcopy(EXTENT), "scope_key": "body", "format_policy_sha256": "a" * 64, **annotations}}

    def save_plan(self):
        plan = {"version": 1, "owner": "coordinator", "coverage": [{"id": "decision", "questions": [q["id"] for q in self.contract["questions"]], "terms": ["Tern"], "providers": ["archive"],
                "people": [], "periods": [], "work_types": ["review"], "reason": "Scoped approval question"}], "items": self.items}
        # These G4 cases isolate plan/work lifecycle using hand-built inventories.
        # G5's separate bundle tests exercise the real generated-ledger boundary.
        with patch.object(BUNDLE, "graph_ledger_context", return_value=None):
            BUNDLE.save_plan(self.root, plan, self.ledger)
        self.plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]

    def rows(self, table):
        with GRAPH.snapshot(self.root) as connection:
            return [GRAPH._decoded(row) for row in connection.execute("SELECT * FROM " + table)]

    def row(self, table, identity):
        with GRAPH.snapshot(self.root) as connection:
            return GRAPH._get(connection, table, identity)

    def binding(self, item="read-a"):
        return next(b for b in self.registration["bindings"] if b["plan_item_id"] == item)

    def claim(self, item="read-a", actor="reader", start=True, enforce_fairness=True):
        binding = self.binding(item)
        work = self.row("work_items", binding["work_id"])
        result = WORK.claim(self.root, work["work_id"], actor, self.plan_hash, item, expected_revision=work["revision"], enforce_fairness=enforce_fairness,
                            fairness_override_reason="Coordinator explicitly selects the changed-scope fixture for this check" if not enforce_fairness else None)
        assignment = result["assignment_id"]
        if start:
            WORK.start_assignment(self.root, assignment, expected_revision=1)
        return assignment

    def spec(self, assignment, operation="read-one"):
        assigned = self.row("assignments", assignment)
        work = self.row("work_items", assigned["work_id"])
        plan = BUNDLE.read(self.root / "plans" / (assigned["plan_sha256"] + ".json"))
        item = next(i for i in plan["items"] if i["id"] == assigned["plan_item_id"])
        return dict(id=operation, actor=assigned["actor_id"], provider=item["provider"], kind=item["kind"], request=copy.deepcopy(item["request"]),
                    plan_sha256=assigned["plan_sha256"], plan_item=assigned["plan_item_id"], graph_assignment_id=assignment,
                    research={"stage": work["stage"], "branch": work["branch"], "parent_operation": item.get("graph", {}).get("parent_operation"), "root_basis": "Supplied fictional root"})

    def admit(self, assignment, operation="read-one", mirror=True, dispatch=True):
        spec = self.spec(assignment, operation)
        WORK.prepare_operation(self.root, spec)
        BUNDLE.start(self.root, spec, "tool-import")
        if mirror:
            WORK.record_admission(self.root, spec)
        if dispatch:
            WORK.record_dispatch(self.root, operation)
        return spec

    def result(self, operation="read-one", formats=("html",), pagination="complete", version=None, stable_identity=False, capture_extent=None):
        BUNDLE.import_output(self.root, operation, self.output, None, "ok")
        WORK.record_completion(self.root, operation)
        request = BUNDLE.read(self.root / "operations" / operation / "request.json")["spec"]
        source = request["request"].get("source_id", "doc-a")
        observation = {"source_id": source, "title": "Fictional " + source, "type": "document"}
        if stable_identity:
            observation.update(namespace="fixture-doc", external_id="stable-A")
        if version:
            observation["version"] = {"revision": version}
        result = {"source_ids": [source], "sources": [observation], "pagination": pagination, "next_cursor": None,
                  "note": "All fictional fixture candidates retained", "captures": [{"id": fmt, "source_id": source, "format": fmt,
                  "relative_path": "operations/" + operation + "/stdout", "extent": copy.deepcopy(EXTENT if capture_extent is None else capture_extent), "extraction_status": "complete",
                  "limitations": "No provider access; local fictional bytes"} for fmt in formats]}
        BUNDLE.index_result(self.root, operation, result)
        INGEST.index_operation(self.root, operation)

    def returned(self, assignment, operation="read-one", identity="reader-return", formats=("html",), coverage="sufficient", inspected_extent=None):
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        returned = {"id": identity, "read_id": operation, "actor": self.row("assignments", assignment)["actor_id"], "graph_assignment_id": assignment,
                    "inspection": "partial", "relevance": "relevant", "coverage": "Inspected the two retained fictional body lines",
                    "expansion_review": "No additional links in these fixture lines", "leads": [],
                    "graph": {"inspected_at": datetime.now(timezone.utc).isoformat(), "locators": [], "scans": [], "assertions": [],
                    "inspections": [{"id": "body", "source_id": BUNDLE.read(self.root / "operations" / operation / "request.json")["spec"]["request"].get("source_id", "doc-a"), "extent": "Two retained body lines", "method": "Actual fixture inspection attestation",
                    "limitations": "Meaning remains a reader attestation", "captures": [{"capture_id": fmt, "extent": copy.deepcopy(EXTENT if inspected_extent is None else inspected_extent)} for fmt in formats],
                    "scopes": [{"question_id": work["question_id"], "required_scope_key": work["required_scope_key"], "format_policy_sha256": work["format_policy_sha256"],
                                "coverage": coverage, "basis": "Requested formats and two-line extent inspected"}]}]}}
        BUDGET.reader_return(self.root, returned)
        INGEST.ingest_reader_return(self.root, identity)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(GRAPH.GraphError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def question_binding_fixture(self, narrowed=False):
        self.root = self.directory / ("question-narrowed" if narrowed else "question-coverage")
        self.contract["questions"].append({"id": "outcomes", "text": "What happened?", "central": True})
        BUNDLE.init(self.root, self.contract)
        GRAPH.initialize(self.root, "coordinator", "question-binding")
        self.items = [self.item("decision-only", "reader"), self.item("outcomes-only", "reader")]
        coverage = []
        for item, question in zip(self.items, ("decision", "outcomes")):
            item["coverage"] = [question]
            if narrowed:
                item["graph"]["questions"] = [question]
            coverage.append({"id": question, "questions": ["decision", "outcomes"] if narrowed else [question],
                             "terms": ["Fictional"], "providers": ["archive"], "people": [], "periods": [],
                             "work_types": ["review"], "reason": "Owned question scope"})
        self.items[0]["priority"], self.items[1]["priority"] = "low", "high"
        with patch.object(BUNDLE, "sync_graph_plan", self.automatic_plan_hook):
            BUNDLE.save_plan(self.root, {"version": 1, "owner": "coordinator", "coverage": coverage, "items": self.items}, self.ledger)
        self.plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        BUDGET.activity(self.root, "reader", "start")

    def test_claim_question_is_limited_by_saved_coverage_and_narrowing(self):
        for narrowed in (False, True):
            with self.subTest(narrowed=narrowed):
                self.contract["questions"] = self.contract["questions"][:1]
                self.question_binding_fixture(narrowed)
                work = self.binding("outcomes-only")["work_id"]
                self.error("scope_mismatch", WORK.claim, self.root, work, "reader", self.plan_hash,
                           "decision-only", expected_revision=1)
                self.assertEqual(self.rows("assignments"), [])
                assignment = WORK.claim(self.root, work, "reader", self.plan_hash, "outcomes-only", expected_revision=1)["assignment_id"]
                WORK.start_assignment(self.root, assignment, expected_revision=1)
                self.admit(assignment)
                self.result()
                self.returned(assignment)
                WORK.complete_work(self.root, assignment, expected_revision=2, reason="Actual matching question read")
                self.assertEqual(self.row("work_items", work)["state"], "completed")
                self.assertEqual([r["question_id"] for r in self.rows("sufficient_inspection")], ["outcomes"])

    def test_raw_native_assignment_cannot_exceed_saved_question(self):
        self.question_binding_fixture()
        work = self.binding("outcomes-only")["work_id"]
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [WORK._insert("assignments", assignment_id="wrong-question",
                work_id=work, actor_id="reader", claimed_at=WORK._now(),
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                generation_at_claim=0, plan_item_id="decision-only", plan_sha256=self.plan_hash)])
        self.error("scope_mismatch", GRAPH.ingest, self.root, packet)
        self.assertEqual(self.rows("assignments"), [])

    def test_historical_mismatched_question_cannot_admit_or_ingest(self):
        self.question_binding_fixture()
        work = self.binding("outcomes-only")["work_id"]
        # Preserve an assignment accepted by the former validator. Current
        # operational gates must reject it without rewriting accepted history.
        with patch.object(GRAPH, "plan_item_questions", return_value=["decision", "outcomes"]):
            assignment = WORK.claim(self.root, work, "reader", self.plan_hash, "decision-only", expected_revision=1)["assignment_id"]
            WORK.start_assignment(self.root, assignment, expected_revision=1)
        self.error("scope_mismatch", WORK.prepare_operation, self.root, self.spec(assignment))
        with GRAPH.snapshot(self.root) as conn:
            self.error("scope_mismatch", INGEST._assignment, self.root, conn, assignment, "reader")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 0)
        self.assertEqual(self.row("assignments", assignment)["plan_item_id"], "decision-only")

    def test_interrupted_admission_restores_intent_without_refund_or_dispatch(self):
        assignment = self.claim()
        operation = self.spec(assignment, "orphan")
        original_write = BUNDLE.write
        def fail_request(path, value):
            if path.name == "request.json":
                raise OSError("Owned request persistence failure")
            return original_write(path, value)
        with patch.object(BUNDLE, "write", side_effect=fail_request):
            with self.assertRaises(OSError):
                BUNDLE.start(self.root, operation, "tool-import")
        journal = (self.root / "research-budget.json").read_bytes()
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        planned = BUNDLE.plan_operations(self.root)
        self.assertEqual(planned["orphan"][1:], (None, None))
        self.assertEqual(planned["orphan"][0]["graph_assignment_id"], assignment)
        self.assertFalse((self.root / "operations/orphan/request.json").exists())
        self.error("pending_dependency", WORK.recover_admission, self.root, "orphan", worker_identity="reader", worker_stopped=False, reason="Uncertain")
        self.error("permission_denied", WORK.recover_admission, self.root, "orphan", worker_identity="someone-else", worker_stopped=True, reason="Checked")
        recovered = WORK.recover_admission(self.root, "orphan", worker_identity="reader", worker_stopped=True, reason="Original fixture worker stopped before dispatch")
        self.assertFalse(recovered["dispatch_authorized"])
        self.assertEqual(self.row("operation_links", "orphan")["execution_state"], "cancelled")
        self.assertEqual({p.name for p in (self.root / "operations/orphan").iterdir()}, {"request.json"})
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)
        again = WORK.recover_admission(self.root, "orphan", worker_identity="reader", worker_stopped=True, reason="Same retained check")
        self.assertTrue(again["idempotent"])
        self.error("pending_dependency", WORK.record_dispatch, self.root, "orphan")
        WORK.recover_retry(self.root, "orphan", expected_revision=2, reason="Observed local persistence failure")
        retry = self.spec(assignment, "retry")
        retry["research"]["retry_of"] = "orphan"
        WORK.prepare_operation(self.root, retry)
        BUNDLE.start(self.root, retry, "tool-import")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 2)
        events = BUNDLE.read(self.root / "research-budget.json")["events"]
        self.assertFalse(next(e for e in events if e.get("operation") == "retry")["fresh_epoch"])

    def test_admission_directory_failure_and_interrupted_recovery(self):
        assignment = self.claim()
        operation = self.spec(assignment, "no-directory")
        actual_mkdir = Path.mkdir
        def fail_directory(path, *args, **kwargs):
            if path == self.root / "operations/no-directory":
                raise OSError("Owned operation directory failure")
            return actual_mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", fail_directory):
            with self.assertRaises(OSError):
                BUNDLE.start(self.root, operation, "tool-import")
        self.assertIn("no-directory", BUNDLE.plan_operations(self.root))
        original_write = BUNDLE.write
        def fail_restored_request(path, value):
            if path.name == "request.json":
                raise OSError("Owned interrupted recovery")
            return original_write(path, value)
        with patch.object(BUNDLE, "write", side_effect=fail_restored_request):
            with self.assertRaises(OSError):
                WORK.recover_admission(self.root, "no-directory", worker_identity="reader", worker_stopped=True, reason="Checked original worker")
        proof = self.root / "admission-recoveries/no-directory.json"
        before = proof.read_bytes()
        WORK.recover_admission(self.root, "no-directory", worker_identity="reader", worker_stopped=True, reason="Resume same recovery")
        self.assertEqual(proof.read_bytes(), before)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual(WORK.recovery_status(self.root)["items"], [])

    def test_admission_recovery_refuses_late_output_and_existing_request(self):
        assignment = self.claim()
        self.admit(assignment, dispatch=False)
        self.error("pending_dependency", WORK.recover_admission, self.root, "read-one", worker_identity="reader", worker_stopped=True, reason="Ordinary reconciliation needed")
        (self.root / "operations/read-one/stdout").write_text("Late original output")
        self.error("pending_dependency", WORK.recover_admission, self.root, "read-one", worker_identity="reader", worker_stopped=True, reason="Do not erase late output")
        self.assertFalse((self.root / "admission-recoveries/read-one.json").exists())

    def source_term_search(self):
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        self.returned(assignment)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Original source inspected before term discovery")
        self.items[0].update(status="done", reason="Original source inspection retained")
        self.ledger["sources"][0]["reads"].append("read-one")
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["inspections"].append({"read_id": "read-one", "actor": "reader", "coverage": "Actual fixture body inspected", "return_id": "reader-return"})
        first = {"id": "term-first", "coverage": ["decision"], "kind": "search", "actor": "searcher", "provider": "archive", "priority": "normal",
                 "status": "queued", "reason": "Follow the source's fictional terminology", "request": {"query": "fixture term", "filters": {"route": "body"}, "cap": 2, "cursor": None},
                 "origin": {"source_id": "doc-a", "read_id": "read-one", "locator": "line 1"},
                 "graph": {"branch": "archive", "stage": "priority", "parent_operation": "read-one"}}
        self.items.append(first)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        BUDGET.activity(self.root, "searcher", "start")
        assignment = self.claim("term-first", "searcher")
        self.finish_search(assignment, "term-first-op", next_cursor="page-2")
        first.update(status="bounded", reason="Original first page retained; continuation is separate work")
        return first

    def finish_search(self, assignment, operation, next_cursor=None):
        self.admit(assignment, operation)
        BUNDLE.import_output(self.root, operation, self.output, None, "ok")
        WORK.record_completion(self.root, operation)
        BUNDLE.index_result(self.root, operation, {"source_ids": ["doc-b"], "pagination": "more" if next_cursor else "complete",
                                                   "next_cursor": next_cursor, "note": "Fictional indexed result with explicit continuation"})
        INGEST.index_operation(self.root, operation)
        self.ledger["sources"][1]["discovered_by"].append(operation)
        if next_cursor:
            WORK.bound_work(self.root, assignment, expected_revision=2, reason="Additional indexed page remains separate work")
        else:
            WORK.complete_work(self.root, assignment, expected_revision=2, reason="Complete actual page indexed")

    def page_item(self, first, identity, parent, cursor):
        page = copy.deepcopy(first)
        page.update(id=identity, status="queued", reason="Continue the same source-derived result stream")
        page["request"]["cursor"] = cursor
        page["graph"]["parent_operation"] = parent
        return page

    def test_source_read_origin_and_immediate_pagination_parent_remain_distinct(self):
        first = self.source_term_search()
        parent, cursor = "term-first-op", "page-2"
        for number in (2, 3):
            item = self.page_item(first, "term-page-" + str(number), parent, cursor)
            self.items.append(item)
            self.save_plan()
            self.registration = WORK.register_plan(self.root, self.plan_hash)
            assignment = self.claim(item["id"], "searcher")
            operation = item["id"] + "-op"
            wrong = self.spec(assignment, "wrong-immediate-parent")
            wrong["research"]["parent_operation"] = "read-one"
            self.error("scope_mismatch", WORK.prepare_operation, self.root, wrong)
            with self.assertRaisesRegex(ValueError, "planned immediate parent"):
                BUDGET.admit(self.root, wrong, lambda: self.fail("A different immediate parent was admitted"))
            self.finish_search(assignment, operation, "page-3" if number == 2 else None)
            item.update(status="bounded" if number == 2 else "done", reason="Actual page retained without changing its source origin")
            stored = BUNDLE.read(self.root / "operations" / operation / "request.json")["spec"]
            self.assertEqual(stored["research"]["parent_operation"], parent)
            saved = BUNDLE.read(self.root / "plans" / (stored["plan_sha256"] + ".json"))
            self.assertEqual(next(i for i in saved["items"] if i["id"] == item["id"])["origin"], first["origin"])
            self.assertEqual(self.row("work_items", self.row("assignments", assignment)["work_id"])["generation"], 1)
            parent, cursor = operation, "page-3"
        totals = BUDGET.status(self.root)
        self.assertEqual((totals["search_attempts"], totals["read_attempts"], totals["assessed_artifacts"]), (3, 1, 1))
        self.assertEqual(totals["by_generation"], {"0": 1, "1": 3})

    def test_pagination_preflight_rejects_wrong_cursor_route_source_or_branch(self):
        first = self.source_term_search()
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        for field in ("cursor", "route", "source", "branch", "cap"):
            candidate = copy.deepcopy(plan)
            page = self.page_item(first, "invalid-page", "term-first-op", "page-2")
            if field == "cursor":
                page["request"]["cursor"] = "unobserved"
            elif field == "route":
                page["request"]["filters"]["route"] = "titles"
            elif field == "source":
                page["origin"]["source_id"] = "doc-b"
            elif field == "branch":
                page["search_branch"] = "different-route"
            else:
                page["request"]["cap"] = 3
            candidate["items"].append(page)
            before = (self.root / "research-budget.json").read_bytes()
            with self.subTest(field=field):
                self.error("scope_mismatch", WORK.validate_plan, self.root, candidate, self.ledger)
                self.assertEqual((self.root / "research-budget.json").read_bytes(), before)

    def test_closed_pagination_intent_survives_a_later_alternate_parent_admission(self):
        first = self.source_term_search()
        old = self.page_item(first, "old-page", "term-first-op", "page-2")
        self.items.append(old)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        old_hash = self.plan_hash
        old_bytes = (self.root / "plans" / (old_hash + ".json")).read_bytes()
        old.update(status="bounded", reason="Historical page was never admitted; retain its original immediate parent")
        corrected = self.page_item(first, "corrected-page", "read-one", "page-2")
        self.items.append(corrected)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assigned = self.claim("corrected-page", "searcher")
        self.finish_search(assigned, "corrected-page-op")
        corrected.update(status="done", reason="Original alternate source-parent operation completed")
        original_operation = self.row("operation_links", "corrected-page-op")
        budget_before = (self.root / "research-budget.json").read_bytes()
        # Both the exact old queued snapshot and a new plan retaining the old
        # bounded item are projections; neither can dispatch an old request.
        self.assertTrue(WORK.register_plan(self.root, old_hash)["idempotent"])
        self.save_plan()
        WORK.register_plan(self.root, self.plan_hash)
        self.assertEqual((self.root / "plans" / (old_hash + ".json")).read_bytes(), old_bytes)
        self.assertEqual(self.row("operation_links", "corrected-page-op"), original_operation)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget_before)

        active = BUNDLE.read(self.root / "plan.json")
        active.pop("_record")
        active["items"].append(self.page_item(first, "renamed-reset", "term-first-op", "page-2"))
        self.error("scope_mismatch", WORK.validate_plan, self.root, active, self.ledger)
        retry = copy.deepcopy(BUNDLE.read(self.root / "operations/corrected-page-op/request.json")["spec"])
        retry.update(id="reset-retry", plan_sha256=old_hash, plan_item="old-page")
        retry["research"].update(parent_operation="term-first-op", refresh_of="corrected-page-op", refresh_basis="Explicit repeat still cannot change ancestry")
        with self.assertRaisesRegex(ValueError, "retry cannot reset ancestry"):
            BUDGET.admit(self.root, retry, lambda: self.fail("Rejected ancestry reached the admission callback"))
        self.error("scope_mismatch", WORK._generation, self.root, retry)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget_before)

    def test_documented_pending_seed_bootstraps_with_documented_contract_and_plan(self):
        references = SCRIPTS.parent / "references"
        def examples(name):
            return [json.loads(body) for body in re.findall(r"```json\n(.*?)\n\s*```", (references / name).read_text(), re.S)]
        contract = examples("bundle-procedure.md")[0]
        plan = examples("research-plan.md")[0]
        mapping = examples("search-routes.md")[0]
        seed = next(body for body in examples("graph-workflow.md") if "sources" in body)
        plan.update(mapping)
        # The route example asks callers to map or bound the remaining work
        # types. These explicit omissions complete that documented base plan.
        for work_type in plan["coverage"][0]["work_types"]:
            if work_type != "review":
                branch = copy.deepcopy(plan["search_branches"][0])
                branch.update(id="bounded-" + work_type, work_type=work_type, status="omitted",
                              reason="Documentation bootstrap fixture only; no provider work is authorized")
                plan["search_branches"].append(branch)
        plan["items"][0].update(search_branch="review-documents-native", input_kind="term")
        plan["items"].append(next(body for body in examples("research-plan.md") if body.get("id") == "read-review"))
        plan.update(wave_strategy="Register the supplied seed and bounded route before any real discovery or reading", checkpoints=[])
        root = self.directory / "documented-bootstrap"
        inputs = {}
        for name, value in (("contract", contract), ("plan", plan), ("seed", seed)):
            inputs[name] = self.directory / ("documented-" + name + ".json")
            inputs[name].write_text(json.dumps(value))
        for args in (["init", str(root), "--contract", str(inputs["contract"])],
                     ["graph", "init", str(root), "--run-id", "documented-seed"],
                     ["plan", str(root), "--plan", str(inputs["plan"]), "--ledger", str(inputs["seed"])]):
            result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "research_bundle.py"), *args],
                                    cwd=self.directory, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with GRAPH.snapshot(root) as conn:
            self.assertEqual(conn.execute("SELECT supplied_seed_id FROM discoveries").fetchone()[0], "seed:intake-1")
            self.assertEqual(conn.execute("SELECT count(*) FROM plan_snapshots").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM work_items WHERE state='pending'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM captures").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM inspections").fetchone()[0], 0)
        self.assertEqual(BUDGET.status(root)["search_attempts"], 0)
        self.assertEqual(BUDGET.status(root)["read_attempts"], 0)

    def test_real_saved_plan_materializes_work_without_a_second_inventory(self):
        self.assertTrue(WORK.enabled(self.root))
        self.assertFalse(WORK.enabled(self.directory / "legacy"))
        self.assertEqual(len(self.rows("work_items")), 1)
        work = self.rows("work_items")[0]
        self.assertEqual(work["request_json"]["freshness_basis"], "captured_after_assignment")
        self.assertEqual(self.rows("plan_snapshots")[0]["plan_sha256"], self.plan_hash)
        self.assertTrue(WORK.register_plan(self.root, self.plan_hash)["idempotent"])

    def test_competing_claims_are_atomic_and_do_not_duplicate_leases(self):
        binding = self.binding()
        def compete():
            try:
                return WORK.claim(self.root, binding["work_id"], "reader", self.plan_hash, "read-a", expected_revision=1)["status"]
            except GRAPH.GraphError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: compete(), range(2)))
        self.assertEqual(sorted(outcomes), ["pass", "revision_conflict"])
        self.assertEqual(len(self.rows("assignments")), 1)
        self.assertEqual(self.rows("work_items")[0]["state"], "leased")

    def test_plan_amendment_syncs_questions_and_preserves_all_seed_and_work_origins(self):
        original_plan = self.plan_hash
        self.items[0]["graph"] = {"extent": copy.deepcopy(EXTENT)}
        duplicate = copy.deepcopy(self.items[0])
        duplicate.update(id="another-route", actor="other-reader", reason="The same ordinary read from another planning route")
        self.items.append(duplicate)
        self.ledger["sources"][0]["discovered_by"].extend(["seed:second-supplied-route", "seed:second-supplied-route"])
        amended = copy.deepcopy(self.contract)
        amended["questions"][0]["text"] = "Which approval was actually scoped to input?"
        amended["questions"].append({"id": "export", "text": "Was export separately approved?", "central": True})
        BUNDLE.amend(self.root, amended)
        self.contract = amended
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        for question in ("decision", "export"):
            bindings = [b for b in self.registration["bindings"] if b["question_id"] == question]
            self.assertEqual(len({b["work_id"] for b in bindings}), 1)
            origins = [o for o in self.rows("work_origins") if o["work_id"] == bindings[0]["work_id"]]
            self.assertEqual(len(origins), 2)
        seeds = [d for d in self.rows("discoveries") if d["supplied_seed_id"] == "seed:second-supplied-route"]
        self.assertEqual(len(seeds), 2)
        current = self.row("questions", "decision")
        self.assertEqual(current["current_status"], "reopened")
        self.assertEqual(current["text"], amended["questions"][0]["text"])
        WORK.register_plan(self.root, original_plan)
        self.assertEqual(self.row("questions", "decision"), current)
        self.assertEqual(self.row("questions", "export")["contract_revision"], current["contract_revision"])

    def test_equivalent_plan_does_not_silently_reopen_bounded_work(self):
        assignment = self.claim()
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Explicit initial scope bound")
        self.items.append(self.item("same-intent", "reader"))
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        self.assertEqual(self.binding("same-intent")["work_id"], self.binding()["work_id"])
        self.assertTrue(self.binding("same-intent")["reopen_required"])
        self.assertEqual(WORK.fair_frontier(self.root)["items"], [])
        work = self.row("work_items", self.binding()["work_id"])
        WORK.reopen_work(self.root, work["work_id"], expected_revision=work["revision"], reason="Coordinator expanded the previously bounded question")
        self.claim("same-intent")

    def test_plan_preflight_uses_registration_projection_without_persisting(self):
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        plan["items"].append(self.item("new-read", "new-reader", "doc-c", extent={"body": "all"}))
        ledger = copy.deepcopy(self.ledger)
        ledger["sources"].append(self.source("doc-c"))
        tracked = [self.root / "bundle.json", self.root / "plan.json", self.root / "research-budget.json"] + list((self.root / "plans").iterdir())
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in tracked}
        with GRAPH.snapshot(self.root) as conn:
            sequence = GRAPH._head(conn)
        for field in ("seed", "extent", "actor", "annotation", "policy", "stage", "scope", "complete"):
            candidate, sources = copy.deepcopy(plan), copy.deepcopy(ledger)
            if field == "seed":
                sources["sources"][-1]["discovered_by"] = ["seed:supplied seeds.json doc-c"]
            elif field == "extent":
                candidate["items"][-1]["graph"].pop("extent")
            elif field == "actor":
                candidate["items"][-1]["actor"] = "reader with spaces"
            elif field == "annotation":
                candidate["items"][-1]["graph"]["unsupported"] = True
            elif field == "policy":
                candidate["items"][-1]["graph"]["format_policy_sha256"] = "invalid"
            elif field == "stage":
                candidate["items"][-1]["graph"]["stage"] = "unrecognized"
            elif field == "scope":
                candidate["items"][-1]["graph"]["scope_key"] = ""
            else:
                candidate["items"][-1]["graph"]["extent"] = {"body": {**EXTENT["body"], "complete": True,
                    "attestation": "This is a capture declaration", "limitations": "It does not belong in a request"}}
            with self.subTest(field=field):
                self.error("invalid_packet", WORK.validate_plan, self.root, candidate, sources)
        checked = WORK.validate_plan(self.root, plan, ledger)
        self.assertFalse(checked["plan_saved"])
        self.assertFalse(checked["dispatch_authorized"])
        self.assertEqual({str(p.relative_to(self.root)): p.read_bytes() for p in tracked}, before)
        with GRAPH.snapshot(self.root) as conn:
            self.assertEqual(GRAPH._head(conn), sequence)
        self.assertEqual(len(list((self.root / "plans").iterdir())), len(before) - 3)
        with patch.object(BUNDLE, "graph_ledger_context", return_value=None):
            BUNDLE.save_plan(self.root, plan, ledger)
        registered = WORK.register_plan(self.root, BUNDLE.read(self.root / "bundle.json")["plan_sha256"])
        self.assertEqual({(b["plan_item_id"], b["question_id"]): b["work_id"] for b in checked["bindings"]},
                         {(b["plan_item_id"], b["question_id"]): b["work_id"] for b in registered["bindings"]})

    def completed_root_for_review(self):
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        self.returned(assignment)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Original root inspected")
        self.items[0].update(status="done", reason="Completed original root")
        self.ledger["sources"][0]["reads"].append("read-one")
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["inspections"].append({"read_id": "read-one", "actor": "reader",
            "coverage": "Actual fixture body inspected", "return_id": "reader-return"})

    def test_refresh_root_stage_is_rejected_before_plan_or_budget_mutation(self):
        self.completed_root_for_review()
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        plan["items"] = self.items + [self.item("review", "reader", stage="priority", purpose="Independent review")]
        before = {p: p.read_bytes() for p in (self.root / "bundle.json", self.root / "plan.json", self.root / "research-budget.json")}
        self.error("scope_mismatch", WORK.validate_plan, self.root, plan, self.ledger)
        spec = {"kind": "read", "provider": "archive", "request": self.items[0]["request"],
                "research": {"parent_operation": None, "stage": "priority", "root_basis": "Repeat the current primary source"}}
        self.error("scope_mismatch", WORK._generation, self.root, spec)
        plan["items"][-1]["graph"]["stage"] = "baseline"
        self.assertFalse(WORK.validate_plan(self.root, plan, self.ledger)["dispatch_authorized"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_historical_refresh_root_can_close_but_cannot_be_reactivated(self):
        self.completed_root_for_review()
        review = self.item("review", "reader", stage="priority", purpose="Independent review")
        self.items.append(review)
        original_generation = WORK._generation
        def old_refresh_shortcut(root, spec, **kwargs):
            if spec["research"].get("parent_operation") is None and spec["research"]["stage"] == "priority":
                return 0
            return original_generation(root, spec, **kwargs)
        # Only reproduce the old projection shortcut. Plans, work origins,
        # admissions and all remaining validations are real recorder records.
        with patch.object(WORK, "_generation", side_effect=old_refresh_shortcut), \
             patch.object(BUNDLE.graph_component("research_graph_work"), "_generation", side_effect=old_refresh_shortcut):
            self.save_plan()
            self.registration = WORK.register_plan(self.root, self.plan_hash)
        original_hash = self.plan_hash
        original_work = self.row("work_items", self.binding("review")["work_id"])
        original_files = {p: p.read_bytes() for p in (self.root / "plans" / (original_hash + ".json"), self.root / "research-budget.json")}
        self.assertTrue(WORK.register_plan(self.root, original_hash)["idempotent"])
        self.error("scope_mismatch", self.claim, "review", "reader")
        review.update(status="dropped", reason="Unadmitted review intent retained; corrected replacement has baseline stage")
        replacement = self.item("review-corrected", "reader", stage="baseline", purpose="Corrected independent review")
        self.items.append(replacement)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        closed = self.row("work_items", original_work["work_id"])
        self.assertEqual(closed["state"], "cancelled")
        self.assertEqual((closed["stage"], closed["generation"]), ("priority", 0))
        assignment = self.claim("review-corrected", "reader")
        spec = self.spec(assignment, "review-corrected-op")
        spec["research"].update(refresh_of="read-one", refresh_basis="Independent current source review")
        WORK.prepare_operation(self.root, spec)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), original_files[self.root / "research-budget.json"])
        # A real corrected admission succeeds, without provider dispatch.
        BUNDLE.start(self.root, spec, "tool-import")
        WORK.record_admission(self.root, spec)
        self.assertEqual((self.root / "plans" / (original_hash + ".json")).read_bytes(), original_files[self.root / "plans" / (original_hash + ".json")])
        budget_before = json.loads(original_files[self.root / "research-budget.json"])
        budget_after = BUNDLE.read(self.root / "research-budget.json")
        self.assertEqual(budget_after["events"][:-1], budget_before["events"])
        self.assertEqual(budget_after["events"][-1]["operation"], "review-corrected-op")

    def test_unregistered_closed_priority_root_cannot_create_historical_permission(self):
        self.completed_root_for_review()
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        fake = self.item("never-recorded", "reader", stage="priority", purpose="Unrecorded review intent")
        fake.update(status="dropped", reason="No prior graph binding exists")
        plan["items"] = self.items + [fake]
        self.error("scope_mismatch", WORK.validate_plan, self.root, plan, self.ledger)

    def test_explicit_followup_stage_matches_recorder_generation_limits(self):
        self.source_term_search()
        derived = self.item("second-document", "reader", "doc-b", parent_operation="read-one", stage="baseline")
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        plan["items"] = self.items + [derived]
        self.error("scope_mismatch", WORK.validate_plan, self.root, plan, self.ledger)
        derived["graph"].pop("stage")
        checked = WORK.validate_plan(self.root, plan, self.ledger)
        self.assertFalse(checked["dispatch_authorized"])

    def test_graph_first_save_plan_rejects_empty_required_components(self):
        extents = [{"body": {}}, {"body": {"unit": "line", "ranges": []}},
                   {"comments": {"ids": []}}, {"body": {"unit": "line"}},
                   {**EXTENT, "comments": {"ids": []}},
                   {"body": {"attestation": "Opaque new scope", "limitations": "No bounds"}}]
        for number, extent in enumerate(extents):
            with self.subTest(extent=extent):
                root = self.directory / ("empty-plan-" + str(number))
                BUNDLE.init(root, self.contract)
                GRAPH.initialize(root, "coordinator", "empty-plan-" + str(number))
                plan = BUNDLE.read(self.root / "plan.json")
                plan.pop("_record")
                plan["items"][0]["graph"]["extent"] = extent
                before = GRAPH.validate(root)["graph_sequence"]
                # Restore the real automatic graph-first save/preflight seam.
                with patch.object(BUNDLE, "sync_graph_plan", self.automatic_plan_hook):
                    self.error("invalid_packet", BUNDLE.save_plan, root, plan, self.ledger)
                self.assertEqual(GRAPH.validate(root)["graph_sequence"], before)
                self.assertFalse((root / "plan.json").exists())

    def test_direct_read_packet_rejects_empty_required_extent(self):
        work = self.row("work_items", self.binding()["work_id"])
        for extent in ({"body": {}}, {"body": {"unit": "line", "ranges": []}}, {"comments": {"ids": []}}):
            with self.subTest(extent=extent):
                request = dict(work["request_json"], extent=extent)
                with GRAPH.snapshot(self.root) as conn:
                    packet = WORK._packet(self.root, conn, [WORK._update("work_items", work, request_json=request)])
                self.error("invalid_packet", GRAPH.ingest, self.root, packet, evidence_validator=WORK.evidence_validator)
                self.assertEqual(self.row("work_items", work["work_id"]), work)

    def test_historical_empty_scope_replays_but_cannot_complete_current_work(self):
        extent = {"body": {"unit": "line", "ranges": []}}
        self.items[0]["graph"]["extent"] = extent
        # Model only the old vacuous-scope policy; all native originals and
        # ingestion/completion validators otherwise run unchanged.
        with patch.object(GRAPH, "meaningful_read_extent", return_value=True):
            self.save_plan()
            self.registration = WORK.register_plan(self.root, self.plan_hash)
            assignment = self.claim()
            self.admit(assignment)
            self.result()
            self.returned(assignment, inspected_extent=extent)
        original = (self.root / "reader-returns/reader-return.json").read_bytes()
        inspections = self.rows("inspections")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment,
                   expected_revision=2, reason="An empty declared scope cannot complete reading")
        GRAPH.validate(self.root)
        GRAPH.recover(self.root, apply=True)
        self.assertEqual(self.rows("inspections"), inspections)
        self.assertEqual((self.root / "reader-returns/reader-return.json").read_bytes(), original)
        self.assertEqual(self.row("assignments", assignment)["state"], "running")

    def test_graph_bound_opaque_plan_replays_without_reinterpreting_its_scope(self):
        root = self.directory / "legacy-plan"
        BUNDLE.init(root, self.contract)
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        plan["items"][0].pop("graph")
        BUNDLE.save_plan(root, plan, self.ledger)
        key = BUNDLE.read(root / "bundle.json")["plan_sha256"]
        GRAPH.initialize(root, "coordinator", "old-plan-run")
        original_project = WORK._plan_work
        def old_policy(*args, **kwargs):
            kwargs["allow_opaque"] = True
            return original_project(*args, **kwargs)
        # Reproduce only the previous projection policy; original saved files,
        # registration validation and accepted graph events are real.
        with patch.object(WORK, "_plan_work", side_effect=old_policy):
            first = WORK.register_plan(root, key)
        with GRAPH.snapshot(root) as conn:
            original = GRAPH._get(conn, "work_items", first["bindings"][0]["work_id"])
        self.assertTrue(WORK.register_plan(root, key)["idempotent"])
        self.error("invalid_packet", WORK.validate_plan, root, plan, self.ledger)
        with GRAPH.snapshot(root) as conn:
            self.assertEqual(GRAPH._get(conn, "work_items", original["work_id"]), original)
        WORK.claim(root, original["work_id"], "reader", key, "read-a", expected_revision=1)
        self.assertIn("attestation", original["request_json"]["extent"]["body"])
        self.assertFalse(GRAPH.satisfies_scope(EXTENT, EXTENT, original["request_json"]["extent"]))

    def test_canonical_alias_dedup_preserves_history_and_native_index_return_bindings(self):
        original = self.row("work_items", self.binding()["work_id"])
        prior_assignment = self.claim()
        self.admit(prior_assignment)
        self.result(stable_identity=True)
        stable = next(row for row in self.rows("artifact_keys") if row["namespace"] == "fixture-doc")
        WORK.release_assignment(self.root, prior_assignment, expected_revision=2, reason="Capture retained; source identity being reconciled")
        with GRAPH.snapshot(self.root) as connection:
            target = WORK._artifact(connection, "doc-b")
            packet = WORK._packet(self.root, connection, [WORK._update("artifacts", self.row("artifacts", original["artifact_id"]),
                canonical_artifact_id=target, canonical_basis="The two supplied IDs identify the same fictional record")])
        GRAPH.ingest(self.root, packet)
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["reads"].append("read-one")
        self.ledger["sources"].append(self.source("doc-c"))
        self.items.extend([self.item("alias-route", "reader", "doc-b"), self.item("other-source", "other-reader", "doc-c")])
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        self.assertEqual(self.binding("alias-route")["work_id"], original["work_id"])
        self.assertEqual(len(self.rows("work_items")), 2)
        current = self.row("work_items", original["work_id"])
        self.assertEqual((current["logical_key"], current["artifact_id"], current["generation"]),
                         (original["logical_key"], original["artifact_id"], original["generation"]))
        self.error("scope_mismatch", WORK.claim, self.root, current["work_id"], "other-reader", self.plan_hash, "other-source",
                   expected_revision=current["revision"], enforce_fairness=False, fairness_override_reason="Check mismatched canonical source rejection")
        assignment = self.claim("alias-route", enforce_fairness=False)
        # A reader assigned through doc-b can inspect the real capture from doc-a.
        self.returned(assignment, identity="prior-alias-return", coverage="partial")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Prior alias capture still predates current assignment")
        self.admit(assignment, operation="read-alias")
        self.result(operation="read-alias", stable_identity=True)
        self.assertEqual(next(row for row in self.rows("artifact_keys") if row["namespace"] == "fixture-doc"), stable)
        alias = next(row for row in self.rows("artifact_keys") if row["external_id"] == "doc-b" and row["namespace"].startswith("bundle-source:"))
        self.assertNotEqual(alias["artifact_id"], stable["artifact_id"])
        self.assertEqual(next(row for row in self.rows("captures") if row["operation_id"] == "read-alias")["artifact_id"], alias["artifact_id"])
        self.returned(assignment, operation="read-alias", identity="fresh-alias-return")
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Actual fresh capture through the reconciled alias inspected")
        self.assertEqual(self.row("work_items", original["work_id"])["logical_key"], original["logical_key"])
        self.assertEqual(self.row("assignments", prior_assignment)["generation_at_claim"], original["generation"])
        other = self.claim("other-source", "other-reader")
        BUDGET.activity(self.root, "other-reader", "start")
        self.admit(other, operation="read-other")
        self.result(operation="read-other")
        self.error("scope_mismatch", self.returned, assignment, operation="read-other", identity="wrong-source-return")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 3)
        self.assertEqual(len(self.rows("reader_returns")), 2)

    def test_invalid_plan_graph_annotation_cannot_partially_register_new_seeds(self):
        artifacts = self.rows("artifacts")
        events = self.rows("graph_events")
        tracked = [self.root / "bundle.json", self.root / "plan.json", self.root / "research-budget.json"] + list((self.root / "plans").iterdir())
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in tracked}
        self.ledger["sources"].append(self.source("new-seed"))
        self.items.append(self.item("invalid-graph", "reader", "new-seed", invented_scope=True))
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.save_plan()
        self.assertEqual(caught.exception.code, "invalid_packet")
        self.assertEqual(caught.exception.details["field"], "items[invalid-graph].graph.invented_scope")
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes() for path in tracked}, before)
        self.assertEqual(len(list((self.root / "plans").iterdir())), len(before) - 3)
        self.assertEqual(self.rows("artifacts"), artifacts)
        self.assertEqual(self.rows("graph_events"), events)

    def test_plan_preflight_rejects_missing_followup_foreign_keys(self):
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        plan = BUNDLE.read(self.root / "plan.json")
        plan.pop("_record")
        for field in ("lead_id", "reference_id"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(plan)
                candidate["items"].append(self.item("child", "child-reader", "doc-b", parent_operation="read-one", **{field: "absent-origin"}))
                before = self.rows("graph_events")
                with self.assertRaises(GRAPH.GraphError) as caught:
                    WORK.validate_plan(self.root, candidate, self.ledger)
                self.assertEqual(caught.exception.code, "unknown_identity")
                self.assertEqual(caught.exception.details["field"], "items[child].graph." + field)
                self.assertEqual(self.rows("graph_events"), before)

    def test_complete_body_capture_satisfies_explicit_whole_body_request(self):
        self.items[0]["graph"]["extent"] = {"body": "all"}
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim(enforce_fairness=False)
        self.admit(assignment)
        captured = {"body": {**EXTENT["body"], "complete": True,
            "attestation": "The retained fixture contains the complete two-line body",
            "limitations": "This says nothing about comments, visuals or source revisions"}}
        self.result(capture_extent=captured)
        self.returned(assignment, inspected_extent={"body": {"unit": "line", "ranges": [{"start": 1, "end": 1}]}}, coverage="partial")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Only one body line inspected")
        self.returned(assignment, identity="full-body-return", inspected_extent=EXTENT)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Actual complete captured body bounds inspected")
        self.assertEqual(self.row("work_items", self.row("assignments", assignment)["work_id"])["state"], "completed")
        self.assertEqual(self.row("captures", INGEST.capture_id("read-one", "html"))["capture_extent_json"], captured)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_accepted_old_all_scope_history_replays_without_new_scope_admission(self):
        self.items[0]["graph"]["extent"] = {"body": "all"}
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim(enforce_fairness=False)
        self.admit(assignment)
        self.result(capture_extent={"body": "all"})
        def old_policy(captured, inspected, requested):
            return GRAPH.extent_contains(captured, inspected) and GRAPH.extent_contains(inspected, requested)
        # Model the previously accepted scope policy only. The recorder bytes,
        # native validations, accepted packets and lifecycle transitions are real.
        with patch.object(GRAPH, "satisfies_scope", side_effect=old_policy):
            self.returned(assignment, inspected_extent={"body": "all"})
            WORK.complete_work(self.root, assignment, expected_revision=2, reason="Historical scope policy accepted this declaration")
        self.assertFalse(GRAPH.satisfies_scope({"body": "all"}, {"body": "all"}, {"body": "all"}))
        before = self.rows("graph_events")
        last = self.rows("ingest_batches")[-1]
        replay = GRAPH.ingest(self.root, self.root / last["packet_path"])
        self.assertTrue(replay["idempotent"])
        GRAPH.checkpoint(self.root, "historical-all")
        GRAPH.recover(self.root, apply=True)
        self.assertEqual(GRAPH.validate(self.root)["status"], "pass")
        self.assertEqual(self.rows("graph_events"), before)

    def test_admission_crash_missing_mirror_stays_spent_and_blocks_replacement(self):
        assignment = self.claim()
        spec = self.admit(assignment, mirror=False, dispatch=False)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual(self.rows("operation_links"), [])
        self.error("pending_dependency", WORK.release_assignment, self.root, assignment, expected_revision=2, reason="Lease disappeared")
        self.error("pending_dependency", WORK.prepare_operation, self.root, self.spec(assignment, "replacement"))
        WORK.record_admission(self.root, spec)
        self.assertEqual(self.rows("operation_links")[0]["execution_state"], "admitted")
        self.error("pending_dependency", WORK.prepare_operation, self.root, spec)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def cancelled_request(self, assignment, operation="read-one", previous=None):
        spec = self.spec(assignment, operation)
        if previous:
            spec["research"]["retry_of"] = previous
        WORK.prepare_operation(self.root, spec)
        BUNDLE.start(self.root, spec, "tool-import")
        WORK.record_admission(self.root, spec)
        WORK.reconcile_operation(self.root, operation, expected_revision=1, worker_identity=spec["actor"],
                                 worker_stopped=True, reason="Original reader stopped after local synchronization failed.", cancel=True)
        return spec

    def test_cancelled_admission_public_recovery_preserves_originals_and_allows_one_retry(self):
        assignment = self.claim()
        self.cancelled_request(assignment)
        original = (self.root / "operations/read-one/request.json").read_bytes()
        old_events = BUNDLE.read(self.root / "research-budget.json")["events"]
        graph_events = self.rows("graph_events")
        request = self.directory / "recover.json"
        request.write_text(json.dumps({"action": "recover-retry", "operation_id": "read-one", "expected_revision": 2,
                                       "reason": "Observed local synchronization failure before the owned provider command started."}))
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "research_bundle.py"), "graph", "work",
                                 str(self.root), "--file", str(request)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertFalse(receipt["dispatch_authorized"])
        self.assertFalse(receipt["fresh_epoch"])
        events = BUNDLE.read(self.root / "research-budget.json")["events"]
        self.assertEqual(events[:-1], old_events)
        self.assertEqual(events[-1]["cancelled_admission"]["request_sha256"], BUNDLE.digest(self.root / "operations/read-one/request.json"))
        self.assertEqual(self.rows("graph_events"), graph_events)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual((self.root / "operations/read-one/request.json").read_bytes(), original)
        self.assertEqual([p.name for p in (self.root / "operations/read-one").iterdir()], ["request.json"])
        repeated = WORK.recover_retry(self.root, "read-one", expected_revision=2, reason="Same checked local failure.")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(BUNDLE.read(self.root / "research-budget.json")["events"], events)
        retry = self.spec(assignment, "read-retry")
        retry["research"]["retry_of"] = "read-one"
        with patch.object(BUNDLE, "sync_graph_work", self.automatic_work_hook):
            BUNDLE.start(self.root, retry, "tool-import")
        self.assertEqual(self.row("operation_links", "read-retry")["execution_state"], "admitted")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 2)
        self.assertFalse(BUNDLE.read(self.root / "research-budget.json")["events"][-1]["fresh_epoch"])

    def test_cancelled_retry_cannot_reset_attempt_limit_or_request_identity(self):
        assignment = self.claim()
        previous = None
        for number in range(1, 4):
            operation = "cancelled-" + str(number)
            self.cancelled_request(assignment, operation, previous)
            WORK.recover_retry(self.root, operation, expected_revision=2, reason="Repeated local interruption; worker checked stopped.")
            previous = operation
        retry = self.spec(assignment, "fourth")
        retry["research"]["retry_of"] = previous
        before = (self.root / "research-budget.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "retry attempts exhausted"):
            BUNDLE.start(self.root, retry, "tool-import")
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 3)
        self.assertFalse((self.root / "operations/fourth").exists())
        retry["request"]["extent"] = "A different request cannot borrow recovery"
        with self.assertRaisesRegex(ValueError, "retry_of does not match"):
            BUDGET.admit(self.root, retry, lambda: self.fail("unexpected admission"))

    def test_cancelled_retry_rejects_unresolved_stale_and_non_request_only_operations(self):
        assignment = self.claim()
        self.admit(assignment, dispatch=False)
        before = (self.root / "research-budget.json").read_bytes()
        self.error("pending_dependency", WORK.recover_retry, self.root, "read-one", expected_revision=1, reason="Still active.")
        self.error("pending_dependency", WORK.reconcile_operation, self.root, "read-one", expected_revision=1,
                   worker_identity="reader", worker_stopped=False, reason="No stop observed.", cancel=True)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        WORK.reconcile_operation(self.root, "read-one", expected_revision=1, worker_identity="reader",
                                 worker_stopped=True, reason="Reader confirmed stopped.", cancel=True)
        self.error("revision_conflict", WORK.recover_retry, self.root, "read-one", expected_revision=1, reason="Stale revision.")
        for name in ("stdout", "stderr", "receipt.json"):
            path = self.root / "operations/read-one" / name
            path.write_text("Retained bytes must not disappear")
            try:
                self.error("pending_dependency", WORK.recover_retry, self.root, "read-one", expected_revision=2, reason="Has other retained evidence.")
                self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
            finally:
                path.unlink()

    def test_cancelled_retry_checks_original_request_and_requires_retained_stop_proof(self):
        assignment = self.claim()
        self.cancelled_request(assignment)
        before = (self.root / "research-budget.json").read_bytes()
        with patch.object(WORK, "_historical_worker_stopped", return_value=False):
            self.error("pending_dependency", WORK.recover_retry, self.root, "read-one", expected_revision=2, reason="No retained proof.")
        path = self.root / "operations/read-one/request.json"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        self.error("integrity_error", WORK.recover_retry, self.root, "read-one", expected_revision=2, reason="Changed original.")
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        path.write_bytes(original)
        WORK.recover_retry(self.root, "read-one", expected_revision=2, reason="Original restored; checked worker stopped.")
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "recovered admission request changed"):
            state = BUNDLE.read(self.root / "research-budget.json")
            BUDGET.classification(self.root, state, next(e for e in state["events"] if e["type"] == "admit"))

    def test_late_real_receipt_takes_precedence_over_cancelled_recovery(self):
        assignment = self.claim()
        self.cancelled_request(assignment)
        WORK.recover_retry(self.root, "read-one", expected_revision=2, reason="Original worker stopped; result was not available.")
        # A real late imported output is retained through the normal recorder.
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        state = BUNDLE.read(self.root / "research-budget.json")
        admission = next(e for e in state["events"] if e["type"] == "admit")
        self.assertEqual(BUDGET.classification(self.root, state, admission), "ok")
        retry = self.spec(assignment, "unneeded-retry")
        retry["research"]["retry_of"] = "read-one"
        with self.assertRaisesRegex(ValueError, "observed transient"):
            BUNDLE.start(self.root, retry, "tool-import")

    def test_cancelled_derived_search_retry_keeps_generation_and_parent(self):
        first = self.source_term_search()
        page = self.page_item(first, "term-page-2", "term-first-op", "page-2")
        self.items.append(page)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim(page["id"], "searcher")
        self.cancelled_request(assignment, "cancelled-page")
        WORK.recover_retry(self.root, "cancelled-page", expected_revision=2, reason="Local worker synchronization failed.")
        retry = self.spec(assignment, "retry-page")
        retry["research"]["retry_of"] = "cancelled-page"
        wrong = copy.deepcopy(retry)
        wrong["research"].update(stage="baseline", parent_operation=None)
        self.error("scope_mismatch", WORK.prepare_operation, self.root, wrong)
        with patch.object(BUNDLE, "sync_graph_work", self.automatic_work_hook):
            BUNDLE.start(self.root, retry, "tool-import")
        admission = BUNDLE.read(self.root / "research-budget.json")["events"][-1]
        self.assertEqual((admission["generation"], admission["parent_operation"], admission["fresh_epoch"]),
                         (1, "term-first-op", False))
        self.assertEqual(BUDGET.status(self.root)["by_generation"], {"0": 1, "1": 3})

    def test_raw_reconcile_cannot_release_an_unmirrored_original_admission(self):
        assignment = self.claim()
        self.admit(assignment, mirror=False, dispatch=False)
        assigned = self.row("assignments", assignment)
        work = self.row("work_items", assigned["work_id"])
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [WORK._update("assignments", assigned, state="released", finished_at=WORK._now()),
                WORK._update("work_items", work, state="pending", transition_reason="Raw transition must retain original admission")])
        self.error("pending_dependency", GRAPH.ingest, self.root, packet)
        self.error("pending_dependency", GRAPH.ingest, self.root, packet, evidence_validator=WORK.evidence_validator)
        self.error("pending_dependency", GRAPH.recover, self.root, packet["batch_id"], True, evidence_validator=WORK.evidence_validator)
        path = self.directory / "raw-release.json"
        path.write_bytes((self.root / "graph/updates" / (packet["batch_id"] + ".json")).read_bytes())
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "research_bundle.py"), "graph", "reconcile", str(self.root), "--file", str(path)],
                                cwd=self.directory, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "pending_dependency")
        self.assertEqual(self.row("assignments", assignment), assigned)
        self.assertEqual(self.row("work_items", work["work_id"]), work)
        self.assertEqual(WORK.recovery_status(self.root)["items"][0]["unresolved_operation_ids"], ["read-one"])
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_late_admission_of_terminal_assignment_stays_visible_and_blocks_claim(self):
        assignment = self.claim()
        spec = self.spec(assignment)
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="No recorded operation yet")
        # Model the old recorder/mirror race with real originals, while the
        # fixture's normal automatic wrappers remain disabled.
        BUNDLE.start(self.root, spec, "tool-import")
        status = WORK.recovery_status(self.root)["items"]
        self.assertEqual((status[0]["assignment_id"], status[0]["state"], status[0]["unresolved_operation_ids"]),
                         (assignment, "released", ["read-one"]))
        self.error("pending_dependency", self.claim)
        WORK.record_admission(self.root, spec)
        WORK.reconcile_operation(self.root, "read-one", expected_revision=1, worker_identity="reader", worker_stopped=True,
                                 cancel=True, reason="Original worker explicitly checked before replacement")
        self.assertEqual(WORK.recovery_status(self.root)["items"], [])
        self.claim()
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_imported_receipt_has_no_invented_dispatch_and_read_needs_inspection(self):
        assignment = self.claim()
        self.admit(assignment, dispatch=False)
        self.result()
        operations = [e for e in self.rows("graph_events") if e["entity_kind"] == "operation_links"]
        states = [e["payload_json"]["after"]["execution_state"] for e in operations]
        self.assertEqual(states, ["admitted", "unknown", "succeeded"])
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Subprocess succeeded")
        self.returned(assignment)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Actual requested scope inspected")
        self.assertEqual(self.rows("work_items")[0]["state"], "completed")
        self.assertTrue(WORK.register_plan(self.root, self.plan_hash)["idempotent"])

    def withdrawn_completed_read(self, policy):
        narrow = {"body": {"unit": "line", "ranges": [{"start": 1, "end": 1}]}}
        retrieval = self.claim()
        self.admit(retrieval)
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        WORK.record_completion(self.root, "read-one")
        BUNDLE.index_result(self.root, "read-one", {"source_ids": ["doc-a"], "pagination": "complete", "next_cursor": None,
            "sources": [{"source_id": "doc-a", "version": {"revision": "fictional-v1"}}],
            "note": "Two named native captures of the same fictional bytes", "captures": [
                {"id": name, "source_id": "doc-a", "format": "html", "relative_path": "operations/read-one/stdout",
                 "extent": copy.deepcopy(EXTENT), "extraction_status": "complete", "limitations": "Owned fixture"}
                for name in ("required", "other")]})
        INGEST.index_operation(self.root, "read-one")
        WORK.bound_work(self.root, retrieval, expected_revision=2, reason="Retrieval retained; separate inspection follows")
        self.items[0].update(status="bounded", reason="Retrieval retained; separate inspection follows")
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"][0]["reads"].append("read-one")
        binding = ({"capture_ids": [INGEST.capture_id("read-one", "required")]} if policy == "pins"
                   else {"version_id": self.rows("artifact_versions")[0]["version_id"]})
        self.items.append(self.item("inspect-required", "reader", **binding))
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim("inspect-required", enforce_fairness=False)
        self.returned(assignment, identity="read-required", formats=("required",))
        original_assignment = INGEST._assignment
        def historical_scope(*args, **kwargs):
            assigned, work, item = original_assignment(*args, **kwargs)
            if assigned["assignment_id"] != assignment:
                return assigned, work, item
            work = copy.deepcopy(work)
            request = json.loads(work["request_json"])
            if policy == "pins":
                request.pop("capture_ids")
            elif policy == "extent":
                request["extent"] = narrow
            work["request_json"] = json.dumps(request)
            return assigned, work, item
        # Reconstruct an already accepted overclaim under the former loose
        # inspection policy. The saved plan/work remain exact; only this second
        # return sees the historical scope. Current completion, withdrawal and
        # export validation below run unchanged against both retained returns.
        if policy == "matching":
            self.returned(assignment, identity="read-other", formats=("other",))
        else:
            with patch.object(INGEST, "_assignment", side_effect=historical_scope):
                self.returned(assignment, identity="read-other", formats=("other",),
                              inspected_extent=narrow if policy == "extent" else EXTENT)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Required scope inspected")
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        with GRAPH.snapshot(self.root) as conn:
            self.assertTrue(WORK._completion(self.root, conn, work))
        original_return = (self.root / "reader-returns/read-required.json").read_bytes()
        original_budget = (self.root / "research-budget.json").read_bytes()
        evidence = self.root / "withdrawal.txt"
        evidence.write_text("The first reading was overstated; retain the other independent reading.\n")
        mutations = [WORK._insert("inspection_withdrawals", withdrawal_id="withdraw-required",
            inspection_id=INGEST._id("inspection", "read-required", "body"), actor_id="coordinator",
            reason="Correct the first reading", evidence_path="withdrawal.txt",
            evidence_sha256=GRAPH._sha(evidence.read_bytes()), withdrawn_at=GRAPH._now())]
        for question in self.rows("questions"):
            mutations.append(WORK._update("questions", question, current_status="bounded", reason="Fixture research boundary"))
        for artifact in self.rows("artifacts"):
            mutations.append(WORK._insert("source_outcomes", outcome_id="bound-" + artifact["artifact_id"],
                artifact_id=artifact["artifact_id"], question_id="decision", required_scope_key="body", readability="readable",
                disposition="deferred", actor_id="coordinator", observed_at=GRAPH._now(), reason="Explicit source expansion bound"))
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, mutations)
        WORK._ingest(self.root, packet)
        with GRAPH.snapshot(self.root) as conn:
            if policy == "matching":
                self.assertTrue(WORK._completion(self.root, conn, work))
            else:
                self.error("pending_dependency", WORK._completion, self.root, conn, work)
        exports = INGEST._sibling("research_graph_exports")
        directory = Path(exports.export_snapshot(self.root, "withdrawn-required")["manifest_path"]).parent
        context = exports.validate_ledger(self.root, BUNDLE.read(directory / "ledger.json"))
        self.assertEqual(context["stop_checks"]["withdrawn_inspection_completed_work_ids"],
                         [] if policy == "matching" else [work["work_id"]])
        self.assertEqual(context["stop_checks"]["settled"], policy == "matching")
        self.assertFalse(any(value for key, value in context["stop_checks"].items()
                             if key not in {"settled", "withdrawn_inspection_completed_work_ids"}))
        self.assertEqual(self.row("work_items", work["work_id"]), work)
        self.assertEqual(len(self.rows("inspections")), 2)
        self.assertEqual((self.root / "reader-returns/read-required.json").read_bytes(), original_return)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), original_budget)

    def test_withdrawn_pin_cannot_be_replaced_by_an_unpinned_inspection_at_stop(self):
        self.withdrawn_completed_read("pins")

    def test_withdrawn_full_extent_cannot_be_replaced_by_a_shorter_read_at_stop(self):
        self.withdrawn_completed_read("extent")

    def test_matching_effective_read_still_satisfies_completed_work_after_withdrawal(self):
        self.withdrawn_completed_read("matching")

    def test_expired_lease_remains_active_until_original_worker_reconciles(self):
        assignment = self.claim()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with patch.object(WORK, "_now", return_value=future):
            self.error("lease_conflict", WORK.prepare_operation, self.root, self.spec(assignment))
            self.assertTrue(WORK.recovery_status(self.root)["items"][0]["lease_expired"])
            self.error("lease_conflict", WORK.renew_lease, self.root, assignment, expected_revision=2)
        WORK.reconcile_assignment(self.root, assignment, expected_revision=2, reason="Original worker lease expired; inspecting that worker")
        self.assertEqual(self.row("assignments", assignment)["state"], "reconciling")
        self.assertEqual(WORK.fair_frontier(self.root)["items"], [])
        self.error("permission_denied", WORK.resume_assignment, self.root, assignment, expected_revision=3, worker_identity="different-worker", reason="Replacement worker")
        WORK.resume_assignment(self.root, assignment, expected_revision=3, worker_identity="reader", reason="Original reader confirmed alive")
        self.assertEqual(self.row("assignments", assignment)["state"], "running")

    def test_unknown_operation_needs_explicit_original_worker_evidence_and_no_refund(self):
        assignment = self.claim()
        self.admit(assignment)
        self.error("pending_dependency", WORK.reconcile_operation, self.root, "read-one", expected_revision=2,
                   worker_identity="reader", worker_stopped=False, cancel=True, reason="Lease expired only")
        WORK.reconcile_operation(self.root, "read-one", expected_revision=2, worker_identity="reader", worker_stopped=False,
                                 reason="Original worker state unknown")
        self.assertEqual(self.rows("operation_links")[0]["execution_state"], "unknown")
        self.error("pending_dependency", WORK.release_assignment, self.root, assignment, expected_revision=2, reason="Unknown call")
        WORK.reconcile_operation(self.root, "read-one", expected_revision=3, worker_identity="reader", worker_stopped=True, cancel=True,
                                 reason="Original worker checked and stopped before tool dispatch")
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Original worker reconciled; admission stays spent")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual(self.rows("operation_links")[0]["execution_state"], "cancelled")
        again = self.claim()
        repeated = self.spec(again, "new-attempt")
        WORK.prepare_operation(self.root, repeated)
        with self.assertRaises(ValueError):
            BUNDLE.start(self.root, repeated, "tool-import")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def interrupted_import(self, outcome, dispatch):
        assignment = self.claim()
        self.admit(assignment, dispatch=dispatch)
        BUNDLE.import_output(self.root, "read-one", self.output, None, outcome)
        original = (self.root / "operations/read-one/receipt.json").read_bytes()
        BUNDLE.index_result(self.root, "read-one", {"source_ids": [], "pagination": "complete", "next_cursor": None,
                           "note": "Interrupted tool observation; no source body or worker termination attested"})
        INGEST.index_operation(self.root, "read-one")
        WORK.record_completion(self.root, "read-one")
        operation = self.row("operation_links", "read-one")
        self.assertEqual(operation["execution_state"], "unknown")
        self.assertEqual(WORK.recovery_status(self.root)["items"][0]["unresolved_operation_ids"], ["read-one"])
        self.error("pending_dependency", WORK.release_assignment, self.root, assignment, expected_revision=2, reason="Receipt exists only")
        with GRAPH.snapshot(self.root) as conn:
            forged = WORK._packet(self.root, conn, [WORK._update("operation_links", operation, execution_state="failed")])
        self.error("scope_mismatch", GRAPH.ingest, self.root, forged, evidence_validator=INGEST.evidence_validator)
        self.error("scope_mismatch", GRAPH.ingest, self.root, forged, evidence_validator=WORK.evidence_validator)
        self.error("pending_dependency", WORK.reconcile_operation, self.root, "read-one", expected_revision=operation["revision"],
                   worker_identity="reader", worker_stopped=False, cancel=True, reason="Receipt does not prove the original remote worker stopped")
        WORK.reconcile_operation(self.root, "read-one", expected_revision=operation["revision"], worker_identity="reader", worker_stopped=True,
                                 cancel=True, reason="Original remote worker explicitly checked and stopped")
        self.assertTrue(WORK.record_completion(self.root, "read-one")["idempotent"])
        INGEST.index_operation(self.root, "read-one")
        self.assertEqual(self.row("operation_links", "read-one")["execution_state"], "cancelled")
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Explicit original worker reconciliation retained")
        self.claim()
        self.assertEqual((self.root / "operations/read-one/receipt.json").read_bytes(), original)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual(self.rows("captures"), [])

    def test_imported_interruption_keeps_dispatched_worker_unresolved(self):
        self.interrupted_import("interrupted", dispatch=True)

    def test_imported_timeout_without_dispatch_observation_keeps_worker_unresolved(self):
        self.interrupted_import("timeout", dispatch=False)

    def test_owned_subprocess_timeout_is_terminal_after_recorder_kills_and_waits(self):
        assignment = self.claim()
        spec = self.spec(assignment)
        spec.update(command=[sys.executable, "-I", "-B", "-c", "import time; time.sleep(10)"], cwd=str(self.directory), timeout=1)
        self.assertEqual(BUNDLE.capture(self.root, spec), 1)
        receipt = BUNDLE.read(self.root / "operations/read-one/receipt.json")
        self.assertEqual((receipt["outcome"], receipt["provenance"]), ("timeout", "captured-subprocess"))
        WORK.record_completion(self.root, "read-one")
        self.assertEqual(self.row("operation_links", "read-one")["execution_state"], "failed")
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Owned child process group was killed and waited by the recorder")
        self.assertEqual(WORK.recovery_status(self.root)["items"], [])
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_historical_failed_import_requires_exact_later_worker_stop_statement(self):
        assignment = self.claim()
        self.admit(assignment)
        BUNDLE.import_output(self.root, "read-one", self.output, None, "interrupted")
        # Reproduce only the old classification policy to create accepted
        # historical state; original admission, receipt and validators are real.
        with patch.object(INGEST, "receipt_execution_state", return_value="failed"):
            WORK.record_completion(self.root, "read-one")
        original = self.row("operation_links", "read-one")
        receipt = (self.root / "operations/read-one/receipt.json").read_bytes()
        self.assertEqual(original["execution_state"], "failed")
        self.assertEqual(WORK.recovery_status(self.root)["items"][0]["unresolved_operation_ids"], ["read-one"])
        self.error("pending_dependency", WORK.release_assignment, self.root, assignment, expected_revision=2, reason="Old enum alone")
        self.error("pending_dependency", WORK.record_completion, self.root, "read-one")
        BUNDLE.index_result(self.root, "read-one", {"source_ids": [], "pagination": "complete", "next_cursor": None, "note": "Retain the old interruption without rewriting its accepted state"})
        INGEST.index_operation(self.root, "read-one")
        self.assertEqual(self.row("operation_links", "read-one"), original)
        with GRAPH.snapshot(self.root) as conn:
            work = self.row("work_items", self.row("assignments", assignment)["work_id"])
            weak = {"worker_stopped": True, "operation_id": "read-one", "reason": "No bound receipt or original worker identity"}
            packet = WORK._packet(self.root, conn, [WORK._update("work_items", work, transition_reason="Worker reconciliation: " + json.dumps(weak))])
        GRAPH.ingest(self.root, packet)
        self.assertEqual(WORK.recovery_status(self.root)["items"][0]["unresolved_operation_ids"], ["read-one"])
        WORK.reconcile_operation(self.root, "read-one", expected_revision=original["revision"], worker_identity="reader", worker_stopped=True,
                                 cancel=True, reason="Original interrupted worker explicitly checked and stopped after the retained receipt")
        self.assertEqual(self.row("operation_links", "read-one"), original)
        self.assertTrue(WORK.record_completion(self.root, "read-one")["worker_reconciled"])
        WORK.release_assignment(self.root, assignment, expected_revision=2, reason="Original worker reconciled; historical failed state retained")
        self.claim()
        self.assertEqual((self.root / "operations/read-one/receipt.json").read_bytes(), receipt)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_multiple_formats_and_actual_capture_freshness_are_required(self):
        self.items[0]["graph"]["formats"] = ["html", "text"]
        text_item = self.item("read-text", "text-reader")
        text_item["request"]["format"] = "text"
        self.items.append(text_item)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim(enforce_fairness=False)
        self.admit(assignment)
        self.result()
        text_assignment = self.claim("read-text", "text-reader", enforce_fairness=False)
        BUDGET.activity(self.root, "text-reader", "start")
        self.admit(text_assignment, operation="read-two")
        self.result(operation="read-two", formats=("text",))
        captures = ("html", INGEST.capture_id("read-two", "text"))
        self.returned(assignment, formats=("html",), coverage="partial")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Only HTML inspected")
        self.returned(assignment, identity="complete-return", formats=captures)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Both actual formats inspected")
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        WORK.reopen_work(self.root, work["work_id"], expected_revision=work["revision"], reason="New freshness requirement for another read")
        again = self.claim(enforce_fairness=False)
        # A new actual inspection of old bytes is still not a fresh capture.
        self.returned(again, identity="stale-capture-return", formats=captures)
        self.error("pending_dependency", WORK.complete_work, self.root, again, expected_revision=2, reason="Historical bytes re-inspected")

    def test_requested_revision_cannot_be_satisfied_by_other_captured_revision(self):
        self.items[0]["graph"]["version_id"] = "requested-revision"
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignment = self.claim(enforce_fairness=False)
        self.admit(assignment)
        self.result(version="different-revision")
        self.returned(assignment, coverage="partial")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Wrong source revision")

    def test_search_requires_indexed_complete_pagination_and_caps_stay_bounded(self):
        self.items[0].update(status="dropped", reason="Replace with search-first fixture")
        search = {"id": "search-a", "coverage": ["decision"], "kind": "search", "actor": "searcher", "provider": "archive", "priority": "normal",
                  "status": "queued", "reason": "Find the fictional vocabulary", "request": {"query": "Tern", "filters": {}, "cap": 2, "cursor": None}}
        self.items.append(search)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        BUDGET.activity(self.root, "searcher", "start")
        assignment = self.claim("search-a", "searcher")
        self.admit(assignment, operation="search-one")
        BUNDLE.import_output(self.root, "search-one", self.output, None, "ok")
        WORK.record_completion(self.root, "search-one")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Only command success")
        BUNDLE.index_result(self.root, "search-one", {"source_ids": ["doc-a"], "pagination": "capped", "next_cursor": "page-2", "note": "Explicit provider cap left additional candidates"})
        INGEST.index_operation(self.root, "search-one")
        self.error("pending_dependency", WORK.complete_work, self.root, assignment, expected_revision=2, reason="Search capped")
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Provider cap preserved; breadth remains incomplete")
        self.assertEqual(self.row("work_items", self.row("assignments", assignment)["work_id"])["state"], "deferred")

    def test_complete_search_retains_every_discovery_before_finishing(self):
        self.items[0].update(status="dropped", reason="Use the indexed search fixture")
        self.items.append({"id": "search-a", "coverage": ["decision"], "kind": "search", "actor": "searcher", "provider": "archive", "priority": "normal",
                           "status": "queued", "reason": "Discover fictional candidates", "request": {"query": "Tern", "filters": {}, "cap": 2, "cursor": None}})
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        BUDGET.activity(self.root, "searcher", "start")
        assignment = self.claim("search-a", "searcher")
        self.admit(assignment, operation="search-one")
        BUNDLE.import_output(self.root, "search-one", self.output, None, "ok")
        WORK.record_completion(self.root, "search-one")
        BUNDLE.index_result(self.root, "search-one", {"source_ids": ["doc-a", "doc-b"], "sources": [{"source_id": "doc-a"}, {"source_id": "doc-a"}],
                                                   "pagination": "complete", "next_cursor": None, "note": "Both source IDs and repeated route occurrences retained"})
        INGEST.index_operation(self.root, "search-one")
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Every indexed occurrence retained and pagination complete")
        self.assertEqual(len([d for d in self.rows("discoveries") if d["operation_id"] == "search-one"]), 3)

    def test_breadth_wave_waits_for_active_baseline_and_override_needs_reason(self):
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Initial source captured for follow-up fixture")
        self.ledger["sources"][0]["discovered_by"].append("read-one")
        self.ledger["sources"].append(self.source("doc-c"))
        self.items[0].update(status="bounded", reason="Original source explicitly bounded")
        self.items.extend([self.item("baseline-b", "baseline-reader", "doc-b"),
                           self.item("follow-c", "follow-reader", "doc-c", parent_operation="read-one")])
        self.items[-1]["priority"] = "high"
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        baseline, follow = self.binding("baseline-b"), self.binding("follow-c")
        self.assertEqual([w["work_id"] for w in WORK.fair_frontier(self.root)["items"]], [baseline["work_id"]])
        self.error("pending_dependency", WORK.claim, self.root, follow["work_id"], "follow-reader", self.plan_hash, "follow-c", expected_revision=1)
        self.error("permission_denied", WORK.claim, self.root, follow["work_id"], "follow-reader", self.plan_hash, "follow-c", expected_revision=1, enforce_fairness=False)
        active = self.claim("baseline-b", "baseline-reader")
        self.assertEqual(WORK.fair_frontier(self.root)["items"], [])
        WORK.bound_work(self.root, active, expected_revision=2, reason="Breadth source explicitly bounded")
        self.assertEqual(WORK.fair_frontier(self.root)["items"][0]["work_id"], follow["work_id"])
        derived = self.claim("follow-c", "follow-reader")
        self.assertEqual(self.row("assignments", derived)["generation_at_claim"], 1)

    def test_less_served_role_precedes_numeric_priority_without_erasing_disagreement(self):
        assignment = self.claim()
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="First content source explicitly bounded")
        self.items[0].update(status="bounded", reason="Keep the first branch decision")
        self.ledger["sources"].append(self.source("doc-c"))
        self.items.extend([self.item("content-b", "reader-b", "doc-b"), self.item("critical-c", "reader-c", "doc-c")])
        self.items[-2]["priority"] = "high"
        self.items[-1]["priority"] = "low"
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        with GRAPH.snapshot(self.root) as connection:
            mutations = [WORK._insert("assertions", assertion_id="role-" + sid, kind="role", subject_artifact_id=WORK._artifact(connection, sid),
                                     predicate="has_role", value_json=role, question_id="decision", basis="metadata", assessed_at=WORK._now())
                         for sid, role in (("doc-a", "content"), ("doc-b", "content"), ("doc-c", "critical"))]
            mutations.append(WORK._insert("assertion_decisions", decision_id="challenge-critical", assertion_id="role-doc-c", disposition="challenged",
                                          reason="Its role remains disputed", actor_id="coordinator", expected_revision=0))
            packet = WORK._packet(self.root, connection, mutations)
        GRAPH.ingest(self.root, packet)
        frontier = WORK.fair_frontier(self.root)["items"]
        self.assertEqual(frontier[0]["work_id"], self.binding("critical-c")["work_id"])
        self.assertEqual(frontier[0]["roles"], ["critical"])
        self.assertEqual(self.rows("assertion_decisions")[0]["disposition"], "challenged")

    def test_frontier_balances_branches_then_questions(self):
        assignment = self.claim()
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Record one served archive/decision slot")
        amended = copy.deepcopy(self.contract)
        amended["questions"].append({"id": "export", "text": "What is the export approval?", "central": True})
        BUNDLE.amend(self.root, amended)
        self.contract = amended
        self.items[0].update(status="bounded", reason="Original archive question explicitly bounded")
        self.items[0]["graph"]["questions"] = ["decision"]
        self.ledger["sources"].extend([self.source("doc-c"), self.source("doc-d")])
        self.items.extend([self.item("served-branch", "reader-b", "doc-b", questions=["export"]),
                           self.item("other-question", "reader-c", "doc-c", branch="other", questions=["decision"]),
                           self.item("other-export", "reader-d", "doc-d", branch="other", questions=["export"])])
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        frontier = WORK.fair_frontier(self.root)["items"]
        # Other branch has no claim; export has no claim within the tied branch.
        self.assertEqual(frontier[0]["work_id"], self.binding("other-export")["work_id"])
        self.assertEqual({w["branch"] for w in frontier[:2]}, {"archive", "other"})
        for invalid in (True, 0, -1, "1"):
            self.error("invalid_packet", WORK.claim, self.root, frontier[0]["work_id"], "reader-d", self.plan_hash, "other-export", expected_revision=invalid)

    def test_source_access_bound_is_not_irrelevance_and_needs_changed_basis(self):
        assignment = self.claim()
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [WORK._insert("source_outcomes", outcome_id="access", artifact_id=work["artifact_id"],
                question_id="decision", required_scope_key="body", readability="access_blocked", disposition="deferred", reason="Original route denied access",
                actor_id="coordinator", observed_at=WORK._now())])
        GRAPH.ingest(self.root, packet)
        self.error("pending_dependency", WORK.prepare_operation, self.root, self.spec(assignment))
        WORK.bound_work(self.root, assignment, expected_revision=2, state="blocked", reason="Access unavailable, relevance unassessed")
        current = self.row("work_items", work["work_id"])
        self.error("pending_dependency", WORK.reopen_work, self.root, work["work_id"], expected_revision=current["revision"], reason="Try again without new evidence")
        with GRAPH.snapshot(self.root) as connection:
            packet = WORK._packet(self.root, connection, [WORK._insert("source_outcomes", outcome_id="access-reconciled", artifact_id=work["artifact_id"],
                question_id="decision", required_scope_key="body", readability="unknown", disposition="deferred", reason="Capability changed; retrieval not yet tried",
                replaces_outcome_id="access", changed_basis="Original access capability restored", actor_id="coordinator", observed_at=WORK._now())])
        GRAPH.ingest(self.root, packet)
        WORK.reopen_work(self.root, work["work_id"], expected_revision=current["revision"], reason="Explicit changed access basis")
        self.assertEqual(self.row("work_items", work["work_id"])["state"], "pending")
        self.assertEqual(self.rows("assertions"), [])

    def test_nested_scope_and_original_shared_cap_across_children(self):
        WORK.register_actor(self.root, "lead", "research_lead", "coordinator", branch_scope="archive", stage_scope="baseline")
        self.items[0].update(status="dropped", reason="Lead delegates fixture work")
        self.items.append(self.item("lead-item", "lead", purpose="Coordinate the delegated branch"))
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        parent = self.claim("lead-item", "lead")
        self.error("permission_denied", WORK.register_actor, self.root, "escape", "reader", "lead", branch_scope="other", stage_scope="baseline",
                   by_actor_id="lead", parent_assignment_id=parent)
        for actor in ("child-one", "child-two"):
            WORK.register_actor(self.root, actor, "reader", "lead", branch_scope="archive/part", stage_scope="baseline", by_actor_id="lead", parent_assignment_id=parent)
            self.items.append(self.item(actor, actor, "doc-a" if actor == "child-one" else "doc-b", branch="archive/part", purpose="Inspect delegated branch evidence"))
            BUDGET.activity(self.root, actor, "start")
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        amended = copy.deepcopy(self.contract)
        amended["research_budget"] = {"read_attempts": 1}
        BUNDLE.amend(self.root, amended)
        self.contract = amended
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        assignments = []
        ordered = [w["work_id"] for w in WORK.fair_frontier(self.root)["items"]]
        actors = sorted(("child-one", "child-two"), key=lambda actor: ordered.index(self.binding(actor)["work_id"]))
        for actor in actors:
            work = self.row("work_items", self.binding(actor)["work_id"])
            claimed = WORK.claim(self.root, work["work_id"], actor, self.plan_hash, actor, expected_revision=1, by_actor_id="lead", parent_assignment_id=parent)
            WORK.start_assignment(self.root, claimed["assignment_id"], expected_revision=1)
            assignments.append(claimed["assignment_id"])
        self.admit(assignments[0])
        spec = self.spec(assignments[1], "child-second")
        WORK.prepare_operation(self.root, spec)
        with self.assertRaises(ValueError):
            BUNDLE.start(self.root, spec, "tool-import")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertFalse((self.root / "operations" / "child-second").exists())
        WORK.release_assignment(self.root, assignments[1], expected_revision=2, reason="Original shared cap stops new research")
        self.assertTrue(WORK.fair_frontier(self.root)["items"])

    def test_parent_automatic_recorder_hooks_preserve_the_same_boundaries(self):
        if self.automatic_work_hook is None:
            self.skipTest("Parent automatic work wrapper not installed yet")
        assignment = self.claim()
        with patch.object(BUNDLE, "sync_graph_work", self.automatic_work_hook):
            BUNDLE.start(self.root, self.spec(assignment), "tool-import")
            self.assertEqual(self.rows("operation_links")[0]["execution_state"], "admitted")
            BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
            self.assertEqual(self.rows("operation_links")[0]["execution_state"], "succeeded")
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_parent_automatic_save_plan_registers_original_hash_once(self):
        if self.automatic_plan_hook is None:
            self.skipTest("Parent automatic plan wrapper not installed yet")
        self.items.append(self.item("automatically-bound", "other-reader", "doc-b"))
        with patch.object(BUNDLE, "sync_graph_plan", self.automatic_plan_hook):
            self.save_plan()
        self.assertEqual(len(self.rows("work_items")), 2)
        self.assertTrue(any(p["plan_sha256"] == self.plan_hash for p in self.rows("plan_snapshots")))
        self.assertTrue(WORK.register_plan(self.root, self.plan_hash)["idempotent"])


if __name__ == "__main__":
    unittest.main()
