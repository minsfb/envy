"""Protected follow-up tests use actual local graph/plan/budget/receipt helpers.

Fictional HTML and imported owned responses exercise the workflow without
provider calls, credentials, network, user documents or fabricated graph rows.
Automatic mirroring is disabled while tests invoke those same stages explicitly.
"""

import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture


WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
FOLLOWUPS = BUNDLE.graph_component("research_followups")
EXTENT = fixture.EXTENT


class ResearchFollowupTests(unittest.TestCase):
    source = fixture.ResearchGraphWorkTests.source
    rows = fixture.ResearchGraphWorkTests.rows
    row = fixture.ResearchGraphWorkTests.row
    binding = fixture.ResearchGraphWorkTests.binding
    claim = fixture.ResearchGraphWorkTests.claim
    admit = fixture.ResearchGraphWorkTests.admit
    result = fixture.ResearchGraphWorkTests.result

    def setUp(self):
        self.automatic_plan_hook = BUNDLE.sync_graph_plan
        for name in ("sync_graph_work", "sync_graph_evidence", "sync_graph_plan"):
            hook = patch.object(BUNDLE, name, return_value=None, create=True)
            hook.start()
            self.addCleanup(hook.stop)
        temporary = tempfile.TemporaryDirectory(prefix="envy-followup-protection-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "bundle"
        self.output = self.directory / "fictional.html"
        self.output.write_text('<p>Tern Navigation Guidelines: <a href="https://boards.example.invalid/prototype#frame">Prototype</a>.</p>\n'
                               '<p>Hybrid navigation: <a href="https://boards.example.invalid/prototype">Prototype again</a>.</p>\n')
        self.contract = {"version": 1, "purpose": "Trace fictional Tern input designs.", "scope": "Owned fictional records only.",
            "requirements_source": "Protected follow-up isolated fixture", "requirements": {"candidate_table": True,
            "brief_max_words": 100, "destination": "Local files", "research_budget": True, "research_plan": True,
            "protected_followups": True}, "providers": {"archive": {"read_formats": ["html", "text"],
            "basis": "Owned fictional imports", "reference_hosts": ["boards.example.invalid"]}},
            "questions": [{"id": "decision", "text": "How did the input design evolve?", "central": True}]}
        BUNDLE.init(self.root, self.contract)
        self.items = [self.item("read-a", "reader", "doc-a")]
        self.ledger = {"sources": [self.source("doc-a"), self.source("doc-b"), self.source("doc-c")]}
        self.ledger["sources"][1]["url"] = "https://boards.example.invalid/prototype"
        self.save_plan()
        GRAPH.initialize(self.root, "coordinator", "followup-fixture")
        self.registration = WORK.register_plan(self.root, self.plan_hash)

    def item(self, identity, actor="reader", source="doc-a", **annotations):
        annotations.setdefault("allocation", "baseline")
        annotations.setdefault("branch", "inputs")
        return fixture.ResearchGraphWorkTests.item(self, identity, actor, source, **annotations)

    def save_plan(self):
        sources = {source["id"]: source for source in self.ledger["sources"]}
        for path in (self.root / "operations").iterdir():
            if not (path / "result.json").exists():
                continue
            result = BUNDLE.read(path / "result.json")
            spec = BUNDLE.read(path / "request.json")["spec"]
            for identity in result["source_ids"]:
                if spec["id"] not in sources[identity]["discovered_by"]:
                    sources[identity]["discovered_by"].append(spec["id"])
            if spec["kind"] == "read" and BUNDLE.read(path / "receipt.json")["outcome"] == "ok":
                source = sources[spec["request"]["source_id"]]
                if spec["id"] not in source["reads"]:
                    source["reads"].append(spec["id"])
        fixture.ResearchGraphWorkTests.save_plan(self)

    def activity(self, allocation="baseline", actor="reader", purpose="research", branch="inputs"):
        totals = BUDGET.status(self.root)
        if actor in totals["active_actors"]:
            BUDGET.activity(self.root, actor, "stop")
        BUDGET.activity(self.root, actor, "start", allocation=allocation, purpose=purpose, branch=branch)

    def stop_activity(self):
        for actor in BUDGET.status(self.root)["active_actors"]:
            BUDGET.activity(self.root, actor, "stop")

    def spec(self, assignment, operation="read-one"):
        result = fixture.ResearchGraphWorkTests.spec(self, assignment, operation)
        item = next(i for i in self.items if i["id"] == result["plan_item"])
        result["research"].update(allocation=item["graph"]["allocation"],
                                  purpose=item["graph"].get("activity_purpose", "research"))
        return result

    def returned(self, assignment, operation="read-one", identity="reader-return", leads=True, references=True, reference_values=None):
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        returned = {"id": identity, "read_id": operation, "actor": self.row("assignments", assignment)["actor_id"],
            "graph_assignment_id": assignment, "inspection": "partial", "relevance": "relevant",
            "coverage": "Read two retained fictional HTML lines", "expansion_review": "Retain terminology and prototype links.",
            "leads": [{"id": "term", "kind": "vocabulary", "value": "Tern Navigation Guidelines", "locator": "body",
                       "reason": "Discover the organizing input guidance.", "questions": ["decision"]}] if leads else [],
            "graph": {"inspected_at": datetime.now(timezone.utc).isoformat(), "locators": [{"id": "body", "capture_id": "html",
                "kind": "line", "position": {"unit": "line", "start": 1, "end": 2}}],
                "inspections": [{"id": "body", "source_id": BUNDLE.read(self.root / "operations" / operation / "request.json")["spec"]["request"]["source_id"],
                    "extent": "Two fictional HTML lines", "method": "Fixture reader inspected retained bytes",
                    "limitations": "No real source relevance claim", "captures": [{"capture_id": "html", "extent": copy.deepcopy(EXTENT)}],
                    "scopes": [{"question_id": work["question_id"], "required_scope_key": work["required_scope_key"],
                        "format_policy_sha256": work["format_policy_sha256"], "coverage": "sufficient", "basis": "Both requested lines inspected"}]}],
                "scans": [{"id": "links", "capture_id": "html", "method": "Enumerated literal HTML anchors",
                    "extent": copy.deepcopy(EXTENT), "limitations": "Linked target has not been opened", "references": [
                        {"id": "prototype", "kind": "link", "value": "https://boards.example.invalid/prototype#frame", "label": "Prototype",
                         "locator_id": "body", "target_source_id": "doc-b"},
                        {"id": "prototype-repeat", "kind": "link", "value": "https://boards.example.invalid/prototype", "label": "Repeated prototype",
                         "locator_id": "body", "target_source_id": "doc-b"}]}] if references else [], "assertions": []}}
        if reference_values is not None:
            returned["graph"]["scans"][0]["references"] = reference_values
        BUDGET.reader_return(self.root, returned)
        INGEST.ingest_reader_return(self.root, identity)

    def seed(self, leads=True, references=True, reference_values=None):
        self.activity()
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        self.returned(assignment, leads=leads, references=references, reference_values=reference_values)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Read the full requested fictional seed extent")
        self.items[0]["status"] = "done"
        self.stop_activity()
        return assignment

    def test_internal_record_links_and_unresolved_title_mentions_enter_frontier(self):
        self.output.write_text('<p>See [[doc-b]], Tern system review, and #details.</p>\n<p>Details.</p>\n')
        self.seed(leads=False, reference_values=[
            {"id": "internal", "kind": "link", "value": "[[doc-b]]", "label": "Internal record", "locator_id": "body", "target_source_id": "doc-b"},
            {"id": "title", "kind": "mention", "value": "Tern system review", "label": "Unresolved review", "locator_id": "body"},
            {"id": "anchor", "kind": "link", "value": "#details", "label": "Local details", "locator_id": "body"}])
        items = [item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "reference"]
        self.assertEqual({item["label"] for item in items}, {"Internal record", "Unresolved review"})
        self.assertTrue(all(item["status"] == "unassigned" and item["generation"] == 1 for item in items))
        self.assertTrue(all(item["surface"] is None and item["urls"] == [] for item in items))

    def add(self, item):
        self.items.append(item)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)

    def search_item(self, identity="term-search", parent="read-one", generation=1, lead_id=None):
        return {"id": identity, "coverage": ["decision"], "kind": "search", "actor": "reader", "provider": "archive",
                "priority": "normal", "status": "queued", "reason": "Search terminology discovered in the inspected record",
                "request": {"query": identity, "filters": {}, "cap": 10, "cursor": None},
                "graph": {"allocation": "followup-" + str(generation), "branch": "inputs", "parent_operation": parent,
                          "stage": "priority" if generation == 1 else "deep", **({"lead_id": lead_id} if lead_id else {})}}

    def complete_search(self, item="term-search", operation="query-one", sources=("doc-b",),
                        pagination="complete", next_cursor=None):
        self.activity(next(i for i in self.items if i["id"] == item)["graph"]["allocation"])
        assignment = self.claim(item, enforce_fairness=False)
        self.admit(assignment, operation)
        BUNDLE.import_output(self.root, operation, self.output, None, "ok")
        WORK.record_completion(self.root, operation)
        BUNDLE.index_result(self.root, operation, {"source_ids": list(sources),
            "sources": [
                {"source_id": source, "title": "Fictional " + source, "type": "document"} for source in sources],
            "pagination": pagination, "next_cursor": next_cursor, "note": "Owned fixture result set"})
        INGEST.index_operation(self.root, operation)
        if pagination == "complete":
            WORK.complete_work(self.root, assignment, expected_revision=2, reason="All fictional search results indexed")
        else:
            WORK.bound_work(self.root, assignment, expected_revision=2,
                            reason="Continue the exact query in a separately planned page work item")
        next(i for i in self.items if i["id"] == item)["status"] = "done" if pagination == "complete" else "bounded"
        self.stop_activity()
        return assignment

    def complete_read(self, item, operation):
        selected = next(i for i in self.items if i["id"] == item)
        self.activity(selected["graph"]["allocation"], selected["actor"])
        assignment = self.claim(item, actor=selected["actor"], enforce_fairness=False)
        self.admit(assignment, operation)
        self.result(operation)
        self.returned(assignment, operation, identity=operation + "-return", leads=False, references=False)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Inspected the requested fictional child body")
        selected["status"] = "done"
        self.stop_activity()
        return assignment

    def decision(self, item, disposition="diminishing-value"):
        return {"key": item["key"], "basis_sha256": item["basis_sha256"], "disposition": disposition,
                "reason": "This specific fictional lead concerns a deferred comparison outside today's bounded question."}

    def checkpoint(self, key, **fields):
        return FOLLOWUPS.checkpoint(self.root, {"id": key, "actor": "coordinator",
            "reason": "Reviewed fictional record coverage and outstanding branches", **fields})

    def test_report_preserves_unread_vocabulary_and_groups_reference_surface(self):
        self.seed()
        report = FOLLOWUPS.status(self.root)
        self.assertEqual(len([item for item in report["items"] if item["kind"] in {"vocabulary", "reference"}]), 2)
        self.assertEqual({item["status"] for item in report["items"]}, {"unassigned"})
        term = next(item for item in report["items"] if item["kind"] == "vocabulary")
        self.assertEqual(term["label"], "Tern Navigation Guidelines")
        reference = next(item for item in report["items"] if item["kind"] == "reference")
        self.assertEqual(len(reference["reference_ids"]), 2)
        self.assertEqual(report["surfaces"][0]["providers"], ["archive"])
        self.assertEqual(report["surfaces"][0]["inspected_items"], 0)
        self.assertIn(reference["key"], report["surfaces"][0]["unassigned"])

    def test_metadata_only_known_host_requires_surface_followup(self):
        # doc-b is a supplied metadata seed on this known host. No source has
        # been read and there are no returned reference links or terms yet.
        report = FOLLOWUPS.status(self.root)
        surface_items = [item for item in report["items"] if item["surface"] == "boards.example.invalid"]
        self.assertEqual(len(surface_items), 1)
        self.assertEqual(surface_items[0]["status"], "unassigned")
        self.assertEqual(surface_items[0]["inspection_ids"], [])
        self.assertEqual(report["surfaces"][0]["inspected_items"], 0)
        self.add(self.item("surface-first-read", source="doc-b"))
        planned = FOLLOWUPS.status(self.root)
        self.assertEqual(next(item for item in planned["items"] if item["key"] == surface_items[0]["key"])["status"], "scheduled")
        self.complete_read("surface-first-read", "surface-first-op")
        inspected = FOLLOWUPS.status(self.root)
        self.assertTrue(next(item for item in inspected["items"] if item["key"] == surface_items[0]["key"])["inspection_ids"])

    def test_query_only_is_not_full_cycle_but_empty_search_is(self):
        self.seed(leads=False, references=False)
        self.add(self.search_item())
        self.complete_search()
        self.assertEqual(FOLLOWUPS.status(self.root)["cycles"]["followup-1"], [])
        self.add(self.search_item("empty-query"))
        self.complete_search("empty-query", "empty-result", sources=())
        cycles = FOLLOWUPS.status(self.root)["cycles"]["followup-1"]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["outcome"], "empty-search")

    def two_page_query(self, sources=("doc-c",)):
        self.seed(leads=False, references=False)
        self.inspect_seeded_surface()
        first = self.search_item()
        self.add(first)
        self.complete_search(operation="page-one", sources=sources, pagination="more", next_cursor="page-2")
        second = copy.deepcopy(first)
        second.update(id="term-page-two", status="queued")
        second["request"]["cursor"] = "page-2"
        second["graph"]["parent_operation"] = "page-one"
        self.add(second)
        return self.complete_search("term-page-two", "page-two", sources=())

    def test_empty_terminal_page_does_not_hide_unread_earlier_results(self):
        self.two_page_query()
        self.assertEqual(FOLLOWUPS.status(self.root)["cycles"]["followup-1"], [])
        with self.assertRaisesRegex(GRAPH.GraphError, "verified follow-up cycle"):
            self.checkpoint("release", release="followup-1")
        with self.assertRaisesRegex(GRAPH.GraphError, "meaningful cycles"):
            self.checkpoint("stop", stop="discretionary", no_opportunity={
                "followup-2": "The bounded fixture has no second-round leads."})

    def test_paginated_cycle_counts_selected_read_from_an_earlier_page(self):
        self.two_page_query()
        self.add(self.item("earlier-result", source="doc-c", allocation="followup-1", parent_operation="page-one"))
        self.complete_read("earlier-result", "earlier-result-op")
        cycle, = FOLLOWUPS.status(self.root)["cycles"]["followup-1"]
        self.assertEqual(cycle["outcome"], "searched-and-inspected")
        self.assertEqual(len(cycle["read_work_ids"]), 1)
        self.checkpoint("release", release="followup-1")

    def test_empty_query_can_span_multiple_pages(self):
        self.two_page_query(sources=())
        cycle, = FOLLOWUPS.status(self.root)["cycles"]["followup-1"]
        self.assertEqual(cycle["outcome"], "empty-search")
        self.checkpoint("release", release="followup-1")

    def bound_fixture_sources(self):
        mutations = [WORK._update("questions", q, current_status="bounded",
                                 reason="Isolated fixture exhausted its defined source set.") for q in self.rows("questions")]
        for artifact in self.rows("artifacts"):
            mutations.append(WORK._insert("source_outcomes", outcome_id="outcome-" + artifact["artifact_id"],
                artifact_id=artifact["artifact_id"], question_id="decision", required_scope_key="body",
                readability="unknown", disposition="deferred", actor_id="coordinator", observed_at=WORK._now(),
                reason="No further fixture source assessment is in scope."))
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, mutations)
        WORK._ingest(self.root, packet)
        self.save_plan()
        WORK.register_plan(self.root, self.plan_hash)

    def test_interrupted_preupgrade_release_rechecks_cycle_proof(self):
        assignment = self.two_page_query()
        terminal = self.row("work_items", self.row("assignments", assignment)["work_id"])
        old_cycles = {"followup-1": [{"entry_work_id": terminal["work_id"], "question": "decision",
            "branch": "inputs", "read_work_ids": [], "outcome": "empty-search"}], "followup-2": []}
        with patch.object(FOLLOWUPS, "_cycles", return_value=old_cycles), patch.object(
                BUDGET, "release", side_effect=RuntimeError("interrupted before release")):
            with self.assertRaisesRegex(RuntimeError, "interrupted before release"):
                self.checkpoint("old-release", release="followup-1")
        saved = self.root / "followup-checkpoints/old-release.json"
        original = saved.read_bytes()
        with self.assertRaisesRegex(GRAPH.GraphError, "checkpoint cycle proof changed"):
            self.checkpoint("old-release", release="followup-1")
        self.assertEqual(saved.read_bytes(), original)
        self.assertIsNone(BUDGET.status(self.root)["protected_followups"]["buckets"]["followup-1"]["released_at_checkpoint"])

    def test_synthesis_rechecks_preupgrade_pagination_cycle_proof(self):
        assignment = self.two_page_query()
        self.bound_fixture_sources()
        terminal = self.row("work_items", self.row("assignments", assignment)["work_id"])
        # Preserve an actual checkpoint generated with the pre-correction cycle
        # interpretation. Do not rewrite any immutable journal or saved proof.
        old_cycles = {"followup-1": [{"entry_work_id": terminal["work_id"], "question": "decision",
            "branch": "inputs", "read_work_ids": [], "outcome": "empty-search"}], "followup-2": []}
        exports = BUNDLE.graph_component("research_graph_exports")
        with patch.object(FOLLOWUPS, "_cycles", return_value=old_cycles):
            self.checkpoint("old-stop", stop="discretionary", no_opportunity={
                "followup-2": "The bounded fixture has no second-round leads."})
        directory = Path(exports.export_snapshot(self.root, "old-stop-proof")["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        context = exports.validate_ledger(self.root, ledger)
        review = self.root / "semantic-review.json"
        BUNDLE.write(review, {"reviewer": "fixture-reviewer", "note": "Synthetic integration attestation, not semantic approval.",
            "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]})
        with self.assertRaisesRegex(GRAPH.GraphError, "cycle proof changed"):
            BUNDLE.validate(self.root, ledger, "synthesis", review)

    def test_scope_amendment_invalidates_old_individual_exclusions(self):
        self.seed()
        report = FOLLOWUPS.status(self.root)
        self.checkpoint("first-triage", decisions=[self.decision(item) for item in report["items"]])
        self.assertTrue(all(item["reviewed_bound"] for item in FOLLOWUPS.status(self.root)["items"]))
        self.contract["scope"] = "Expanded to include the fictional prototype and organizing guidance."
        BUNDLE.amend(self.root, self.contract)
        self.assertTrue(all(item["reviewed_bound"] is None for item in FOLLOWUPS.status(self.root)["items"]))

    def test_stop_becomes_stale_after_new_research_activity_without_graph_changes(self):
        self.seed(leads=False, references=False)
        self.inspect_seeded_surface()
        self.checkpoint("complete-stop", stop="complete", no_opportunity={
            "followup-1": "The fully read fictional corpus contains no outgoing leads.",
            "followup-2": "There were no first-round opportunities to generate deeper leads."})
        FOLLOWUPS.validate_stop(self.root)
        before = FOLLOWUPS.status(self.root)["graph_sequence"]
        self.activity(purpose="coordination")
        self.stop_activity()
        self.assertEqual(FOLLOWUPS.status(self.root)["graph_sequence"], before)
        with self.assertRaisesRegex(GRAPH.GraphError, "stop review is stale"):
            FOLLOWUPS.validate_stop(self.root)

    def test_local_cap_cannot_justify_stopping_with_funded_followup_reserves(self):
        self.contract["research_budget"] = {"search_attempts": 20}
        BUNDLE.amend(self.root, self.contract)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        self.seed(leads=False, references=False)
        self.add(self.search_item())
        # Use real admitted baseline search work; the local baseline allowance
        # reaches 6 reserved + 4 shared while two follow-up reserves remain.
        for i in range(10):
            item = self.search_item("baseline-query-" + str(i))
            item["graph"] = {"allocation": "baseline", "branch": "inputs"}
            self.add(item)
            self.complete_search(item["id"], "baseline-op-" + str(i), sources=())
        report = FOLLOWUPS.status(self.root)
        self.assertFalse(FOLLOWUPS._cap_reached(report))
        self.assertEqual(report["budget"]["protected_followups"]["buckets"]["followup-1"]["guaranteed_remaining"]["search_attempts"], 5)
        with self.assertRaisesRegex(GRAPH.GraphError, "No research cap was reached"):
            self.checkpoint("false-cap", stop="cap")

    def test_unread_reference_is_not_an_access_denial(self):
        self.seed(leads=False)
        reference = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "reference")
        self.add(self.item("prototype-read", source="doc-b", allocation="followup-1", parent_operation="read-one",
                           reference_id=reference["reference_ids"][0]))
        assignment = self.claim("prototype-read")
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Scheduling preference only")
        with self.assertRaisesRegex(GRAPH.GraphError, "original failed read"):
            self.checkpoint("false-denial", decisions=[self.decision(reference, "access-blocked")])

    def test_actual_failed_read_and_matching_classification_support_denial(self):
        self.seed(leads=False)
        reference = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "reference")
        self.add(self.item("prototype-read", source="doc-b", allocation="followup-1", parent_operation="read-one",
                           reference_id=reference["reference_ids"][0]))
        self.activity("followup-1")
        assignment = self.claim("prototype-read")
        self.admit(assignment, "denied-read")
        failure = self.directory / "denial.txt"
        failure.write_text("Owned fixture provider response: access denied (403).")
        BUNDLE.import_output(self.root, "denied-read", failure, None, "error")
        WORK.record_completion(self.root, "denied-read")
        BUDGET.classify(self.root, "denied-read", "access-blocked", "The fictional provider returned explicit 403.")
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Original provider explicitly denied access", state="blocked")
        self.stop_activity()
        related = [item for item in FOLLOWUPS.status(self.root)["items"]
                   if self.binding("prototype-read")["work_id"] in item["work_ids"]]
        result = self.checkpoint("observed-denial", decisions=[self.decision(item, "access-blocked") for item in related])
        self.assertEqual(result["status"], "pass")
        reviewed = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["key"] == reference["key"])
        self.assertEqual(reviewed["reviewed_bound"]["disposition"], "access-blocked")
        self.checkpoint("terminal-bounded-stop", stop="discretionary", no_opportunity={
            "followup-1": "The only discovered child was attempted and access was denied.",
            "followup-2": "The unavailable child could not supply deeper leads."})
        FOLLOWUPS.validate_stop(self.root)

    def test_successful_unintelligible_capture_needs_scoped_locator_evidence(self):
        self.seed(leads=False)
        reference = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "reference")
        self.add(self.item("prototype-read", source="doc-b", allocation="followup-1", parent_operation="read-one",
                           reference_id=reference["reference_ids"][0]))
        self.activity("followup-1")
        assignment = self.claim("prototype-read")
        self.admit(assignment, "encoded-read")
        self.output.write_text('<p>Unintelligible encoded fixture payload: AZ09ZX12.</p>\n<p>No usable document body.</p>\n')
        self.result("encoded-read")
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        capture = next(row for row in self.rows("captures") if row["operation_id"] == "encoded-read")
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [
                WORK._insert("locators", locator_id="encoded-body", capture_id=capture["capture_id"], locator_kind="line",
                             locator_json={"unit": "line", "start": 1, "end": 2}),
                WORK._insert("source_outcomes", outcome_id="encoded-unreadable", artifact_id=work["artifact_id"],
                    question_id=work["question_id"], required_scope_key=work["required_scope_key"], readability="unreadable",
                    disposition="deferred", actor_id="coordinator", observed_at=WORK._now(),
                    operation_id="encoded-read", locator_id="encoded-body", reason="Fetched bytes contain no intelligible document content.")])
        WORK._ingest(self.root, packet)
        WORK.bound_work(self.root, assignment, expected_revision=2, reason="Retrieved but unreadable content", state="blocked")
        self.stop_activity()
        related = [item for item in FOLLOWUPS.status(self.root)["items"] if work["work_id"] in item["work_ids"]]
        with self.assertRaisesRegex(GRAPH.GraphError, "original failed read"):
            self.checkpoint("unreadable-is-not-denial", decisions=[self.decision(item, "access-blocked") for item in related])
        self.checkpoint("actual-unreadable", decisions=[self.decision(item, "unreadable") for item in related])
        current = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["key"] == reference["key"])
        self.assertEqual(current["reviewed_bound"]["disposition"], "unreadable")
        self.assertEqual(current["inspection_ids"], [])

    def test_deeper_admission_waits_for_explicit_triage_of_unscheduled_leads(self):
        self.seed()
        self.add(self.item("first-child", source="doc-c", allocation="followup-1", parent_operation="read-one"))
        self.complete_read("first-child", "first-child-op")
        self.add(self.item("second-child", source="doc-b", allocation="followup-2", parent_operation="first-child-op", stage="deep"))
        self.activity("followup-2")
        with self.assertRaisesRegex(GRAPH.GraphError, "earlier follow-up leads"):
            self.claim("second-child", enforce_fairness=False)
        terms = [item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "vocabulary"]
        self.assertEqual(len(terms), 1)
        self.checkpoint("explicit-term-triage", decisions=[self.decision(item) for item in terms])
        assignment = self.claim("second-child", enforce_fairness=False)
        self.admit(assignment, "deeper-read")
        self.assertEqual(BUDGET.status(self.root)["by_generation"], {"0": 1, "1": 1, "2": 1})
        self.result("deeper-read")
        self.returned(assignment, "deeper-read", identity="deeper-return", leads=False, references=False)
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Actual second-round child inspection")
        self.stop_activity()
        cycles = FOLLOWUPS.status(self.root)["cycles"]
        self.assertTrue(cycles["followup-1"] and cycles["followup-2"])
        self.checkpoint("bounded-complete-stop", stop="discretionary")
        FOLLOWUPS.validate_stop(self.root)

    def test_promoting_a_returned_term_creates_executable_followup(self):
        self.seed(references=False)
        lead = self.rows("leads")[0]
        self.add(self.search_item(lead_id=lead["lead_id"]))
        item = next(item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "vocabulary")
        self.assertEqual(item["status"], "scheduled")
        self.assertEqual(item["work_ids"], [self.binding("term-search")["work_id"]])
        self.complete_search()
        self.assertEqual(next(item for item in FOLLOWUPS.status(self.root)["items"] if item["kind"] == "vocabulary")["status"], "completed")
        self.assertEqual(FOLLOWUPS.status(self.root)["cycles"]["followup-1"], [])
        self.add(self.item("selected-result", source="doc-b", allocation="followup-1", parent_operation="query-one"))
        self.complete_read("selected-result", "selected-result-op")
        cycle = FOLLOWUPS.status(self.root)["cycles"]["followup-1"]
        self.assertEqual(cycle[0]["outcome"], "searched-and-inspected")
        self.assertEqual(cycle[0]["read_work_ids"], [self.binding("selected-result")["work_id"]])

    def test_baseline_and_first_followup_both_remain_eligible(self):
        self.seed(leads=False, references=False)
        self.add(self.item("more-baseline", actor="baseline-reader", source="doc-c"))
        self.add(self.search_item())
        choices = WORK.fair_frontier(self.root)["items"]
        self.assertEqual({work["generation"] for work in choices}, {0, 1})
        self.activity("baseline", "baseline-reader")
        self.claim("more-baseline", actor="baseline-reader", enforce_fairness=False)
        self.complete_search()
        self.assertEqual(self.row("work_items", self.binding("more-baseline")["work_id"])["state"], "running")
        self.assertEqual(self.row("work_items", self.binding("term-search")["work_id"])["state"], "completed")

    def prepare_releasable_round(self):
        self.seed(leads=False, references=False)
        self.inspect_seeded_surface()
        self.add(self.search_item())
        self.complete_search(sources=())

    def inspect_seeded_surface(self):
        self.add(self.item("baseline-surface-read", source="doc-b"))
        self.complete_read("baseline-surface-read", "baseline-surface-op")

    def defer_required_read(self):
        self.seed(leads=False, references=False)
        self.inspect_seeded_surface()
        self.add(self.item("deferred-read", source="doc-c"))
        assignment = self.claim("deferred-read", enforce_fairness=False)
        WORK.bound_work(self.root, assignment, expected_revision=2,
                        reason="Required fictional source deliberately left unread", state="deferred")
        self.items[-1]["status"] = "bounded"
        self.save_plan()
        WORK.register_plan(self.root, self.plan_hash)
        return {"followup-1": "No leads arose from the two inspected fixture records.",
                "followup-2": "No first-round opportunity produced a deeper lead."}

    def test_complete_stop_rejects_deferred_work_but_discretionary_preserves_it(self):
        reasons = self.defer_required_read()
        original = (self.root / "research-budget.json").read_bytes()
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            self.checkpoint("false-complete", stop="complete", no_opportunity=reasons)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), original)
        self.assertFalse((self.root / "followup-checkpoints/false-complete.json").exists())
        self.checkpoint("bounded-stop", stop="discretionary", no_opportunity=reasons)
        FOLLOWUPS.validate_stop(self.root)
        pending = self.row("work_items", self.binding("deferred-read")["work_id"])
        self.assertEqual(pending["state"], "deferred")

    def test_saved_false_complete_stop_is_rejected_without_rewriting_history(self):
        reasons = self.defer_required_read()
        # Emulate the old complete-only interpretation while retaining an actual
        # checkpoint. Current validation must reject it without rewriting it.
        with patch.object(FOLLOWUPS, "_require_complete", create=True):
            self.checkpoint("old-complete", stop="complete", no_opportunity=reasons)
        before = (self.root / "research-budget.json").read_bytes()
        saved = self.root / "followup-checkpoints/old-complete.json"
        original = saved.read_bytes()
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            FOLLOWUPS.validate_stop(self.root)
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            self.checkpoint("old-complete", stop="complete", no_opportunity=reasons)
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)

    def check_initial_plan_bound(self, state):
        self.seed(leads=False, references=False)
        self.add(self.item("child-one", source="doc-b", allocation="followup-1", parent_operation="read-one"))
        self.complete_read("child-one", "child-one-op")
        self.add(self.search_item("second-query", parent="child-one-op", generation=2))
        self.complete_search("second-query", "second-query-op", sources=())
        self.bound_fixture_sources()
        exports = BUNDLE.graph_component("research_graph_exports")
        directory = Path(exports.export_snapshot(self.root, "before-plan-bound")["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        plan = BUNDLE.plan_snapshot(self.root)[0]
        plan.pop("_record")
        skipped = self.item("initially-closed-read", source="doc-c")
        skipped.update(status=state, reason="Required fictional body deliberately remains unread")
        plan["items"].append(skipped)
        # Exercise the real generated-ledger and automatic registration boundary,
        # rather than the fixture's isolated hand-built plan projection.
        with patch.object(BUNDLE, "sync_graph_plan", self.automatic_plan_hook):
            BUNDLE.save_plan(self.root, plan, ledger)
        report = FOLLOWUPS.status(self.root)
        self.assertEqual(len(report["work"]), 3)
        self.assertTrue(all(w["verified_complete"] for w in report["work"]))
        budget_path = self.root / "research-budget.json"
        before = budget_path.read_bytes()
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            self.checkpoint("unprojected-complete", stop="complete")
        self.assertEqual(budget_path.read_bytes(), before)
        self.assertFalse((self.root / "followup-checkpoints/unprojected-complete.json").exists())

        with patch.object(FOLLOWUPS, "_require_complete"):
            self.checkpoint("old-unprojected-complete", stop="complete")
        saved = self.root / "followup-checkpoints/old-unprojected-complete.json"
        original, before = saved.read_bytes(), budget_path.read_bytes()
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            self.checkpoint("old-unprojected-complete", stop="complete")
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            FOLLOWUPS.validate_stop(self.root)
        directory = Path(exports.export_snapshot(self.root, "false-complete-proof")["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        with self.assertRaisesRegex(GRAPH.GraphError, "unverified planned work"):
            BUNDLE.validate(self.root, ledger, "synthesis")
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(budget_path.read_bytes(), before)

        self.checkpoint("honest-unprojected-bound", stop="discretionary")
        directory = Path(exports.export_snapshot(self.root, "bounded-proof")["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        context = exports.validate_ledger(self.root, ledger)
        review = self.root / "semantic-review.json"
        BUNDLE.write(review, {"reviewer": "fixture-reviewer", "note": "Command integration, not real research semantics.",
            "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]})
        self.assertEqual(BUNDLE.validate(self.root, ledger, "synthesis", review)["operations"], 3)
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(BUNDLE.plan_snapshot(self.root)[0]["items"][-1]["status"], state)

    def test_complete_stop_includes_read_first_saved_bounded(self):
        self.check_initial_plan_bound("bounded")

    def test_complete_stop_includes_read_first_saved_dropped(self):
        self.check_initial_plan_bound("dropped")

    def release_after_unrelated_overrun(self, metric):
        self.contract["research_budget"] = {"active_minutes": 100, "agent_minutes": 100, metric: 10}
        BUNDLE.amend(self.root, self.contract)
        self.save_plan()
        WORK.register_plan(self.root, self.plan_hash)
        now = [BUDGET.time.time()]
        with patch.object(BUDGET.time, "time", side_effect=lambda: now[0]):
            self.prepare_releasable_round()
            self.activity("baseline")
            now[0] += 300.001
            self.stop_activity()
            before = BUDGET.status(self.root)
            events = BUDGET.load(self.root / "research-budget.json")["events"]
            capacity = before["protected_followups"]
            self.assertLess(capacity["shared_pool"]["remaining"][metric], 0)
            self.assertGreater(capacity["buckets"]["followup-1"]["remaining"][metric], 0)
            self.checkpoint("release-after-overrun", release="followup-1")
            after = BUDGET.status(self.root)
            current = after["protected_followups"]
            self.assertEqual(BUDGET.load(self.root / "research-budget.json")["events"][:len(events)], events)
            for field in BUDGET.allocations.METRICS:
                self.assertEqual(before[field], after[field])
                self.assertEqual(capacity["shared_pool"]["spent"][field], current["shared_pool"]["spent"][field])
                self.assertEqual(capacity["buckets"]["followup-2"]["guaranteed_remaining"][field],
                                 current["buckets"]["followup-2"]["guaranteed_remaining"][field])
            self.assertAlmostEqual(current["shared_pool"]["remaining"][metric], 2.5 - .001 / 60)
            self.assertAlmostEqual(after["by_allocation"]["baseline"][metric], 5 + .001 / 60)
            # Released capacity is usable, while the same original global clock
            # still bounds the amount that all later work can consume.
            self.activity("extension", actor="extension-reader")
            now[0] += 60
            self.stop_activity()
            totals = BUDGET.status(self.root)
            seconds = BUDGET.allocations.remaining_seconds(
                "followup-2", totals["limits"], totals, totals["protected_followups"])
            self.assertAlmostEqual(seconds, (10 - totals[metric]) * 60)
            self.assertEqual(totals["global_overruns"], {})

    def test_public_release_after_unrelated_active_time_overrun(self):
        self.release_after_unrelated_overrun("active_minutes")

    def test_public_release_after_unrelated_agent_time_overrun(self):
        self.release_after_unrelated_overrun("agent_minutes")

    def test_selected_overspent_reserve_still_rejects_release(self):
        self.contract["research_budget"] = {"active_minutes": 10, "agent_minutes": 100}
        BUNDLE.amend(self.root, self.contract)
        self.save_plan()
        WORK.register_plan(self.root, self.plan_hash)
        now = [BUDGET.time.time()]
        with patch.object(BUDGET.time, "time", side_effect=lambda: now[0]):
            self.prepare_releasable_round()
            self.activity("followup-1")
            now[0] += 270.001
            self.stop_activity()
            original = (self.root / "research-budget.json").read_bytes()
            with self.assertRaisesRegex(GRAPH.GraphError, "overspent reserve"):
                self.checkpoint("overspent", release="followup-1")
            with self.assertRaisesRegex(ValueError, "overspent allocation reserve"):
                BUDGET.release(self.root, "followup-1", {"id": "fixture-proof", "sha256": "a" * 64})
            self.assertEqual((self.root / "research-budget.json").read_bytes(), original)
            self.assertFalse((self.root / "followup-checkpoints/overspent.json").exists())

    def test_active_release_failure_does_not_publish_checkpoint(self):
        self.prepare_releasable_round()
        self.activity("followup-1")
        before = (self.root / "research-budget.json").read_bytes()
        with self.assertRaisesRegex((GRAPH.GraphError, ValueError), "[Cc]lose.*activity"):
            self.checkpoint("active-release", release="followup-1")
        self.assertFalse((self.root / "followup-checkpoints/active-release.json").exists())
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)

    def test_exact_release_retry_is_idempotent(self):
        self.prepare_releasable_round()
        first = self.checkpoint("release-round", release="followup-1")
        before = (self.root / "research-budget.json").read_bytes()
        second = self.checkpoint("release-round", release="followup-1")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        events = BUDGET.load(self.root / "research-budget.json")["events"]
        self.assertEqual(sum(event["type"] == "allocation-release" for event in events), 1)
        self.assertEqual(sum(event["type"] == "followup-checkpoint" for event in events), 1)

    def test_interrupted_release_resumes_without_duplicate_checkpoint(self):
        self.prepare_releasable_round()
        with patch.object(BUDGET, "release", side_effect=OSError("Owned injected interruption before reserve release")):
            with self.assertRaises(OSError):
                self.checkpoint("interrupted-release", release="followup-1")
        self.assertTrue((self.root / "followup-checkpoints/interrupted-release.json").exists())
        self.checkpoint("interrupted-release", release="followup-1")
        events = BUDGET.load(self.root / "research-budget.json")["events"]
        self.assertEqual(sum(event["type"] == "allocation-release" for event in events), 1)
        self.assertEqual(sum(event["type"] == "followup-checkpoint" for event in events), 1)

    def test_cannot_enable_protection_after_untimed_legacy_execution(self):
        root = self.directory / "legacy-untimed"
        old = copy.deepcopy(self.contract)
        old["requirements"].pop("protected_followups")
        old["requirements"].update(research_budget=False, research_plan=False)
        BUNDLE.init(root, old)
        spec = {"id": "legacy-read", "actor": "reader", "provider": "archive", "kind": "read",
                "request": {"source_id": "legacy-source", "format": "html", "extent": "Two fictional lines"}}
        BUNDLE.start(root, spec, "tool-import")
        BUNDLE.import_output(root, "legacy-read", self.output, None, "ok")
        self.assertFalse((root / "research-budget.json").exists())
        original = (root / "contract.json").read_bytes()
        changed = copy.deepcopy(old)
        changed["requirements"].update(research_budget=True, research_plan=True, protected_followups=True)
        with self.assertRaises(ValueError):
            BUNDLE.amend(root, changed)
        self.assertEqual((root / "contract.json").read_bytes(), original)


    def test_retained_assessment_cannot_impersonate_coverage_review(self):
        self.activity()
        assignment = self.claim()
        self.admit(assignment)
        self.result()
        self.activity("extension", purpose="coverage-review")
        before = (self.root / "research-budget.json").read_bytes()
        with self.assertRaises(ValueError):
            BUDGET.assess(self.root, "read-one", "reader")
        with self.assertRaises(GRAPH.GraphError):
            BUDGET.assess(self.root, "read-one", "reader", assignment_validator=WORK.prepare_assessment)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), before)
        self.activity()
        BUDGET.assess(self.root, "read-one", "reader", assignment_validator=WORK.prepare_assessment)
        event = BUDGET.load(self.root / "research-budget.json")["events"][-1]
        self.assertEqual(event["graph_assignment_id"], assignment)
        self.assertEqual(event["allocation"], "baseline")

    def test_registered_coverage_reader_assesses_pinned_content_in_its_own_branch(self):
        self.seed(leads=False, references=False)
        WORK.register_actor(self.root, "coverage-reader", "coverage_reviewer", "coordinator")
        captures = [row["capture_id"] for row in self.rows("captures") if row["operation_id"] == "read-one"]
        self.add(self.item("coverage-read", actor="coverage-reader", capture_ids=captures,
                           allocation="extension", activity_purpose="coverage-review", branch="coverage"))
        assignment = self.claim("coverage-read", actor="coverage-reader", enforce_fairness=False)
        self.activity("extension", actor="coverage-reader", purpose="coverage-review", branch="coverage")
        BUDGET.assess(self.root, "read-one", "coverage-reader", assignment_validator=WORK.prepare_assessment)
        event = BUDGET.load(self.root / "research-budget.json")["events"][-1]
        self.assertEqual(event["graph_assignment_id"], assignment)
        self.assertEqual((event["allocation"], event["branch"]), ("extension", "coverage"))


    def test_synthesis_requires_stop_independent_of_optional_discovery_checks(self):
        self.seed(leads=False, references=False)
        exports = BUNDLE.graph_component("research_graph_exports")
        directory = Path(exports.export_snapshot(self.root, "before-stop")["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        self.assertNotIn("discovery_followups", self.contract["requirements"])
        with self.assertRaisesRegex(GRAPH.GraphError, "final follow-up stop checkpoint"):
            BUNDLE.validate(self.root, ledger, "synthesis")
        # Retain an actual bounded stop. Validation now proceeds to the existing
        # pending-source/plan gates instead of skipping or crashing in this gate.
        self.checkpoint("explicit-user-stop", stop="user")
        with self.assertRaises(ValueError) as later:
            BUNDLE.validate(self.root, ledger, "synthesis")
        self.assertNotIn("stop checkpoint", str(later.exception))

    def test_synthesis_accepts_real_cycles_with_both_discovery_settings(self):
        self.seed(leads=False, references=False)
        self.add(self.item("child-one", source="doc-b", allocation="followup-1", parent_operation="read-one"))
        self.complete_read("child-one", "child-one-op")
        self.add(self.search_item("second-query", parent="child-one-op", generation=2))
        self.complete_search("second-query", "second-query-op", sources=())
        self.bound_fixture_sources()
        exports = BUNDLE.graph_component("research_graph_exports")
        review = self.root / "semantic-review.json"
        for enabled in (False, True):
            with self.subTest(discovery_followups=enabled):
                if enabled:
                    self.contract["requirements"]["discovery_followups"] = True
                    BUNDLE.amend(self.root, self.contract)
                    plan = BUNDLE.plan_snapshot(self.root)[0]
                    plan.pop("_record", None)
                    plan["wave_strategy"] = "Baseline, linked inspection, then a second-generation term search."
                    plan["checkpoints"] = [{"id": "final-fixture-coverage",
                        "operations": ["read-one", "child-one-op", "second-query-op"],
                        "inspected_reads": ["read-one", "child-one-op"], "questions": ["decision"], "next_items": [],
                        "decision": "bounded", "reason": "Three fictional records define the isolated scope.",
                        "limits": "No live sources; this is a command integration fixture."}]
                    BUNDLE.save_plan(self.root, plan, ledger)
                    self.plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
                    WORK.register_plan(self.root, self.plan_hash)
                suffix = "discovery" if enabled else "plain"
                self.checkpoint("finished-" + suffix, stop="discretionary")
                directory = Path(exports.export_snapshot(self.root, "synthesis-" + suffix)["manifest_path"]).parent
                ledger = BUNDLE.read(directory / "ledger.json")
                context = exports.validate_ledger(self.root, ledger)
                self.assertTrue(context["stop_checks"]["settled"])
                BUNDLE.write(review, {"reviewer": "fixture-reviewer", "note": "Command integration, not real research semantics.",
                    "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]})
                result = BUNDLE.validate(self.root, ledger, "synthesis", review)
                self.assertEqual(result["operations"], 3)
                self.assertTrue(all(FOLLOWUPS.status(self.root)["cycles"].values()))
        self.activity()
        self.stop_activity()
        with self.assertRaisesRegex(GRAPH.GraphError, "stop review is stale"):
            BUNDLE.validate(self.root, ledger, "synthesis", review)

    def test_export_retains_stop_proof_without_invalidating_it(self):
        self.seed(leads=False, references=False)
        self.checkpoint("bounded-stop", stop="user")
        FOLLOWUPS.validate_stop(self.root)
        exports = BUNDLE.graph_component("research_graph_exports")
        exports.export_snapshot(self.root, "with-stop-proof")
        FOLLOWUPS.validate_stop(self.root)
        directory = self.root / "graph/exports/with-stop-proof"
        self.assertEqual(exports.verify_export(directory)["status"], "pass")
        self.assertEqual((directory / "evidence/followup-checkpoints/bounded-stop.json").read_bytes(),
                         (self.root / "followup-checkpoints/bounded-stop.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
