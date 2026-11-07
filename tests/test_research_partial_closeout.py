"""Partial delivery keeps fictional unread work and negative coverage intact."""

import copy
from html import escape
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_research_followups as fixture

WORK, GRAPH, BUNDLE, BUDGET, FOLLOWUPS = fixture.WORK, fixture.GRAPH, fixture.BUNDLE, fixture.BUDGET, fixture.FOLLOWUPS


class ResearchPartialCloseoutTests(unittest.TestCase):
    setUp = fixture.ResearchFollowupTests.setUp
    source = fixture.ResearchFollowupTests.source
    rows = fixture.ResearchFollowupTests.rows
    row = fixture.ResearchFollowupTests.row
    binding = fixture.ResearchFollowupTests.binding
    claim = fixture.ResearchFollowupTests.claim
    admit = fixture.ResearchFollowupTests.admit
    result = fixture.ResearchFollowupTests.result
    item = fixture.ResearchFollowupTests.item
    activity = fixture.ResearchFollowupTests.activity
    stop_activity = fixture.ResearchFollowupTests.stop_activity
    seed = fixture.ResearchFollowupTests.seed
    add = fixture.ResearchFollowupTests.add
    search_item = fixture.ResearchFollowupTests.search_item
    complete_search = fixture.ResearchFollowupTests.complete_search
    complete_read = fixture.ResearchFollowupTests.complete_read
    checkpoint = fixture.ResearchFollowupTests.checkpoint
    bound_fixture_sources = fixture.ResearchFollowupTests.bound_fixture_sources

    def spec(self, assignment, operation="read-one"):
        result = fixture.ResearchFollowupTests.spec(self, assignment, operation)
        if getattr(self, "routed", False) and result["kind"] == "search":
            result.update(search_branch=result["plan_item"], input_kind="term", tool="fixture-search")
        return result

    def save_plan(self):
        if not getattr(self, "routed", False):
            return fixture.ResearchFollowupTests.save_plan(self)
        original = BUNDLE.save_plan
        for item in self.items:
            if item["kind"] == "search":
                item.update(search_branch=item["id"], input_kind="term")

        def save(root, plan, ledger):
            plan["search_routes"] = [{"id": "archive", "provider": "archive", "family": "fixture",
                "artifact_types": ["document"], "locations": ["owned-corpus"], "availability": "available",
                "checked_at": "2026-01-01", "basis": "Owned synthetic tool imports", "limitations": "No live search recall",
                "invocation": {"tool": "fixture-search"}, "modes": [{"id": "terms", "input_kinds": ["term"], "filter_fields": []}]}]
            searches = [item for item in plan["items"] if item["kind"] == "search"]
            plan["search_branches"] = [{"id": item["id"], "coverage": ["decision"], "work_type": "review",
                "artifact_type": "document", "location": "owned-corpus", "route": "archive", "mode": "terms",
                "status": "omitted" if item["id"] == "no-search-yet" else "selected", "reason": "Declared fictional archive coverage"}
                for item in [{"id": "no-search-yet"}] + searches]
            return original(root, plan, ledger)
        with patch.object(BUNDLE, "save_plan", side_effect=save):
            return fixture.ResearchFollowupTests.save_plan(self)

    def returned(self, assignment, operation="read-one", **kwargs):
        if operation == "first-child-op":
            kwargs.update(leads=True, references=True)
        return fixture.ResearchFollowupTests.returned(self, assignment, operation, **kwargs)

    def prepare_partial(self, *, exhausted=True, release_baseline=True, routed=False):
        self.now = [10000.0]
        timer = patch.object(BUDGET.time, "time", side_effect=lambda: self.now[0])
        timer.start()
        self.addCleanup(timer.stop)
        self.routed = routed
        if routed:
            self.contract["requirements"]["search_routes"] = True
        self.contract["providers"]["archive"]["reference_hosts"] = []
        self.contract["research_budget"] = {"active_minutes": 100, "agent_minutes": 200}
        BUNDLE.amend(self.root, self.contract)
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        self.seed(leads=False, references=False)
        self.activity()
        self.now[0] += 5 * 60
        self.stop_activity()
        if release_baseline:
            self.checkpoint("baseline-done", release="baseline")
        self.add(self.item("first-child", source="doc-c", allocation="followup-1", parent_operation="read-one"))
        self.complete_read("first-child", "first-child-op")
        self.add(self.search_item("second-query", parent="first-child-op", generation=2))
        self.complete_search("second-query", "second-query-op", sources=("doc-b",))
        self.add(self.search_item("unexecuted-first-query"))
        self.add(self.item("unread-result", source="doc-b", allocation="followup-2", parent_operation="second-query-op"))
        self.items[-1]["priority"] = "high"
        self.bound_fixture_sources()
        if exhausted:
            self.activity("followup-1")
            self.now[0] += 70 * 60
            self.stop_activity()
            self.activity("followup-2")
            self.now[0] += 20 * 60
            self.stop_activity()

    def partial_request(self, identity="partial-delivery"):
        report = FOLLOWUPS.status(self.root)
        entries = []
        for target in report["unresolved"]:
            entry = {"key": target["key"], "basis_sha256": target["basis_sha256"], "priority": "high",
                "reason": "This fictional source may change the input decision account; its queued inspection remains unexecuted."}
            blocked = []
            for option in target["funding_options"]:
                remaining = report["remaining_capacity"]["allocations"][option["allocation"]]
                exhausted = [metric for metric in option["required_metrics"] if remaining[metric] <= 0]
                if exhausted:
                    blocked.append({"option_id": option["option_id"], "allocation": option["allocation"], "metric": exhausted[0]})
            if len(blocked) == len(target["funding_options"]):
                entry.update(disposition="allocation-deferred")
                if len(blocked) == 1:
                    entry.update({key: blocked[0][key] for key in ("allocation", "metric")})
                else:
                    entry["blocked_options"] = blocked
            else:
                entry.update(disposition="discretionary-unresolved", capacity_remaining=True)
            entries.append(entry)
        return {"id": identity, "actor": "coordinator", "reason": "Deliver a partial account with the plausible unread frontier and remaining capacity explicit.",
                "stop": "partial", "partial_basis_sha256": report["partial_basis_sha256"], "unresolved": entries}

    def synthesis(self, identity="partial-export"):
        exports = BUNDLE.graph_component("research_graph_exports")
        directory = Path(exports.export_snapshot(self.root, identity)["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        context = exports.validate_ledger(self.root, ledger)
        review = self.root / (identity + "-semantic.json")
        BUNDLE.write(review, {"reviewer": "fixture-reviewer", "note": "The uninspected fictional sources remain material coverage gaps.",
                             "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]})
        return BUNDLE.validate(self.root, ledger, "synthesis", review), context, directory

    def test_partial_delivery_preserves_local_exhaustion_and_funded_unread_work(self):
        self.prepare_partial()
        before = FOLLOWUPS.status(self.root)
        self.assertEqual(before["budget"]["active_minutes"], 95)
        self.assertEqual(before["budget"]["protected_followups"]["shared_pool"]["remaining"]["active_minutes"], 0)
        self.assertEqual(before["budget"]["protected_followups"]["buckets"]["followup-2"]["remaining"]["active_minutes"], 5)
        self.assertTrue(before["cycles"]["followup-1"])
        self.assertFalse(before["cycles"]["followup-2"])
        request = self.partial_request()
        FOLLOWUPS.checkpoint(self.root, request)
        FOLLOWUPS.validate_stop(self.root)
        result, context, directory = self.synthesis()
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["graph"]["partial_delivery"]["remaining_capacity"]["global"]["active_minutes"], 5)
        self.assertEqual(result["graph"]["partial_delivery"]["remaining_capacity"]["allocations"]["followup-2"]["active_minutes"], 5)
        self.assertFalse(context["stop_checks"]["settled"])
        self.assertFalse(result["graph"]["stop_checks"]["settled"])
        after = FOLLOWUPS.status(self.root)
        self.assertEqual(before["work"], after["work"])
        self.assertEqual(before["items"], after["items"])
        self.assertTrue(all(item["reviewed_bound"] is None for item in after["items"]))
        self.assertEqual((directory / "evidence/followup-checkpoints/partial-delivery.json").read_bytes(),
                         (self.root / "followup-checkpoints/partial-delivery.json").read_bytes())
        self.assertTrue(FOLLOWUPS.checkpoint(self.root, request)["idempotent"])
        GRAPH.validate(self.root)
        journal = (self.root / "research-budget.json").read_bytes()
        GRAPH.recover(self.root, apply=True, evidence_validator=BUNDLE.graph_evidence_validator)
        FOLLOWUPS.validate_stop(self.root)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)
        command = subprocess.run([sys.executable, "-I", "-B", str(Path(BUNDLE.__file__)), "validate", str(self.root),
            "--ledger", str(directory / "ledger.json"), "--phase", "synthesis", "--semantic-review",
            str(self.root / "partial-export-semantic.json")], capture_output=True, text=True, timeout=30)
        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        self.assertEqual(json.loads(command.stdout)["status"], "partial")
        self.assertFalse(json.loads(command.stdout)["result"]["coverage_complete"])
        ledger = BUNDLE.read(directory / "ledger.json")
        rows = ["<tr><td>" + escape(source["id"]) + "</td><td><a href=\"" + escape(source["url"], quote=True) + "\">Source</a></td>" +
                "".join("<td>" + escape(source[field]) + "</td>" for field in ("title", "coverage", "finding", "contributor_basis")) + "</tr>"
                for source in ledger["sources"]]
        artifacts = []
        for identity, other in (("brief", "companion"), ("companion", "brief")):
            content = '<p>Partial fictional account. <a href="https://example.invalid/' + other + '">Evidence</a></p>'
            if identity == "companion":
                content += "<table>" + "".join(rows) + "</table>"
            artifact = {"id": identity, "url": "https://example.invalid/" + identity}
            for side in ("expected", "actual"):
                path = self.root / (identity + "-" + side + ".html")
                path.write_text(content)
                artifact[side] = [str(path)]
            artifacts.append(artifact)
        view = BUNDLE.render_plan(self.root, ledger)
        published = BUNDLE.publication(self.root, ledger, {"artifacts": artifacts, "candidate_tables": [0],
            "plan_review": {"reviewer": "fixture-reviewer", "note": "The companion preserves the unread source rows.",
                            "companion_locator": "Candidate table", "plan_view_sha256": view["plan_view_sha256"]}},
            self.root / "partial-export-semantic.json")
        self.assertEqual(published["status"], "partial")
        self.assertFalse(published["coverage_complete"])

    def test_discretionary_partial_does_not_claim_exhaustion(self):
        self.prepare_partial(exhausted=False)
        request = self.partial_request()
        self.assertTrue(all(entry["disposition"] == "discretionary-unresolved" for entry in request["unresolved"]))
        FOLLOWUPS.checkpoint(self.root, request)
        result, _, _ = self.synthesis()
        self.assertEqual(result["status"], "partial")
        with self.assertRaisesRegex(GRAPH.GraphError, "No research cap was reached"):
            self.checkpoint("not-global-exhaustion", stop="cap")

    def test_partial_keeps_selected_unexecuted_search_route_visible(self):
        self.prepare_partial(exhausted=False, routed=True)
        FOLLOWUPS.checkpoint(self.root, self.partial_request())
        result, _, _ = self.synthesis()
        self.assertEqual(result["status"], "partial")
        self.assertIn("unexecuted-first-query", result["graph"]["partial_delivery"]["pending_plan_item_ids"])

    def test_partial_requires_complete_unique_current_item_decisions(self):
        self.prepare_partial()
        request = self.partial_request()
        for mutation, message in (("omit", "every unresolved"), ("duplicate", "unique current"), ("stale", "unique current")):
            bad = copy.deepcopy(request)
            if mutation == "omit":
                bad["unresolved"].pop()
            elif mutation == "duplicate":
                bad["unresolved"].append(bad["unresolved"][0])
            else:
                bad["unresolved"][0]["basis_sha256"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaisesRegex(GRAPH.GraphError, message):
                FOLLOWUPS.checkpoint(self.root, bad)
        self.assertFalse((self.root / "followup-checkpoints/partial-delivery.json").exists())

    def test_partial_rejects_false_funding_and_irrelevant_exhausted_metric(self):
        self.prepare_partial()
        request = self.partial_request()
        local = next(entry for entry in request["unresolved"] if entry["disposition"] == "allocation-deferred")
        for allocation, metric in (("followup-2", "active_minutes"), ("followup-1", "read_attempts")):
            bad = copy.deepcopy(request)
            changed = next(entry for entry in bad["unresolved"] if entry["key"] == local["key"])
            changed.update(allocation=allocation, metric=metric)
            with self.subTest(allocation=allocation, metric=metric), self.assertRaisesRegex(GRAPH.GraphError, "actual funding and relevant metric"):
                FOLLOWUPS.checkpoint(self.root, bad)
        bad = copy.deepcopy(request)
        funded = next(entry for entry in bad["unresolved"] if entry["disposition"] == "discretionary-unresolved")
        funded.pop("capacity_remaining")
        funded.update(disposition="allocation-deferred", allocation="followup-2", metric="active_minutes")
        with self.assertRaisesRegex(GRAPH.GraphError, "exhausted local allowance"):
            FOLLOWUPS.checkpoint(self.root, bad)

    def test_partial_is_not_completion_release_or_no_opportunity(self):
        self.prepare_partial()
        request = self.partial_request()
        for fields in ({"release": "followup-1"}, {"no_opportunity": {"followup-2": "Still has plausible unread results"}},
                       {"stop": "complete"}, {"stop": "discretionary"}):
            with self.subTest(fields=fields), self.assertRaises(GRAPH.GraphError):
                FOLLOWUPS.checkpoint(self.root, dict(request, **fields))
        FOLLOWUPS.checkpoint(self.root, request)
        for fields in ({"stop": "complete"}, {"stop": "discretionary"}, {"release": "followup-1"}):
            with self.subTest(fields=fields), self.assertRaises(GRAPH.GraphError):
                self.checkpoint("cannot-settle", **fields)

    def test_partial_does_not_remove_frontier_from_deeper_admission(self):
        self.prepare_partial(exhausted=False)
        item = self.search_item("third-query", parent="second-query-op", generation=3)
        item["graph"]["allocation"] = "extension"
        self.add(item)
        request = self.partial_request()
        FOLLOWUPS.checkpoint(self.root, request)
        with self.assertRaisesRegex(GRAPH.GraphError, "earlier follow-up leads"):
            self.claim("third-query", enforce_fairness=False)

    def test_partial_rejects_active_assignments_and_original_operations(self):
        self.prepare_partial(exhausted=False)
        assignment = self.claim("unexecuted-first-query", enforce_fairness=False)
        with self.assertRaisesRegex(GRAPH.GraphError, "settle active assignments"):
            FOLLOWUPS.checkpoint(self.root, self.partial_request())
        self.activity("followup-1")
        self.admit(assignment, "unfinished-query")
        self.stop_activity()
        with self.assertRaises(GRAPH.GraphError):
            FOLLOWUPS.checkpoint(self.root, self.partial_request())

    def test_partial_becomes_stale_after_research_or_contract_change(self):
        self.prepare_partial(exhausted=False)
        request = self.partial_request()
        FOLLOWUPS.checkpoint(self.root, request)
        self.activity("followup-2")
        self.stop_activity()
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.validate_stop(self.root)
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.checkpoint(self.root, request)
        FOLLOWUPS.checkpoint(self.root, self.partial_request("updated"))
        self.contract["scope"] += " Include an additional fictional period."
        BUNDLE.amend(self.root, self.contract)
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.validate_stop(self.root)

    def test_partial_becomes_stale_after_funding_release_without_research_change(self):
        self.prepare_partial(exhausted=False, release_baseline=False)
        request = self.partial_request()
        FOLLOWUPS.checkpoint(self.root, request)
        before = FOLLOWUPS.status(self.root)
        self.checkpoint("late-baseline-release", release="baseline")
        after = FOLLOWUPS.status(self.root)
        self.assertEqual(before["evidence_sha256"], after["evidence_sha256"])
        self.assertNotEqual(before["partial_basis_sha256"], after["partial_basis_sha256"])
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.validate_stop(self.root)
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.checkpoint(self.root, request)

    def test_partial_keeps_source_scope_and_question_gates(self):
        self.prepare_partial(exhausted=False)
        question = self.row("questions", "decision")
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [WORK._update("questions", question, current_status="open", reason="Unreviewed fictional question")])
        WORK._ingest(self.root, packet)
        FOLLOWUPS.checkpoint(self.root, self.partial_request())
        with self.assertRaisesRegex(GRAPH.GraphError, "settled source scopes, questions"):
            self.synthesis()

    def test_partial_does_not_excuse_unreviewed_source_scope(self):
        self.prepare_partial(exhausted=False)
        outcome = next(row for row in self.rows("source_outcomes") if row["artifact_id"] == self.row(
            "work_items", self.binding("unread-result")["work_id"])["artifact_id"])
        with GRAPH.snapshot(self.root) as conn:
            packet = WORK._packet(self.root, conn, [WORK._insert("source_outcomes", outcome_id="unreviewed-source",
                artifact_id=outcome["artifact_id"], question_id="decision", required_scope_key="body",
                readability="unknown", disposition=None, actor_id="coordinator", observed_at=WORK._now(),
                reason="Source-specific closeout still unreviewed", replaces_outcome_id=outcome["outcome_id"],
                changed_basis="Reopened the source disposition for coverage review")])
        WORK._ingest(self.root, packet)
        FOLLOWUPS.checkpoint(self.root, self.partial_request())
        with self.assertRaisesRegex(GRAPH.GraphError, "settled source scopes, questions"):
            self.synthesis()

    def test_partial_cannot_hide_changed_original_receipt_bytes(self):
        self.prepare_partial(exhausted=False)
        FOLLOWUPS.checkpoint(self.root, self.partial_request())
        (self.root / "operations/first-child-op/stdout").write_text("Changed synthetic original")
        with self.assertRaises((GRAPH.GraphError, ValueError)):
            FOLLOWUPS.validate_stop(self.root)

    def test_partial_plan_change_requires_a_new_decision_set(self):
        self.prepare_partial(exhausted=False)
        request = self.partial_request()
        FOLLOWUPS.checkpoint(self.root, request)
        self.items[-1]["priority"] = "normal"
        self.save_plan()
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.validate_stop(self.root)
        with self.assertRaisesRegex(GRAPH.GraphError, "stale"):
            FOLLOWUPS.checkpoint(self.root, request)

    def test_partial_discovery_review_preserves_unexecuted_next_items(self):
        self.prepare_partial(exhausted=False)
        exports = BUNDLE.graph_component("research_graph_exports")
        directory = Path(exports.export_snapshot(self.root, "before-discovery-plan")["manifest_path"]).parent
        self.contract["requirements"]["discovery_followups"] = True
        BUNDLE.amend(self.root, self.contract)
        plan = BUNDLE.plan_snapshot(self.root)[0]
        plan.pop("_record")
        plan["wave_strategy"] = "Inspect the fictional seed and child, search the new term, then preserve the unread result."
        plan["checkpoints"] = [{"id": "partial-coverage", "operations": ["read-one", "first-child-op", "second-query-op"],
            "inspected_reads": ["read-one", "first-child-op"], "questions": ["decision"],
            "next_items": ["unexecuted-first-query", "unread-result"], "decision": "bounded",
            "reason": "The newly discovered result is still unread and may alter the account.",
            "limits": "Partial fictional investigation; both next items remain pending."}]
        BUNDLE.save_plan(self.root, plan, BUNDLE.read(directory / "ledger.json"))
        self.plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        self.registration = WORK.register_plan(self.root, self.plan_hash)
        FOLLOWUPS.checkpoint(self.root, self.partial_request())
        result, _, _ = self.synthesis()
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["graph"]["stop_checks"]["nonterminal_obligation_ids"])


if __name__ == "__main__":
    unittest.main()
