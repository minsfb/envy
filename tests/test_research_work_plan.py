#!/usr/bin/env python3
"""CLI acceptance tests for durable, evidence-bound research work plans.

Every source, actor and provider is fictional. Child commands only read or write
files below an owned temporary directory, with no inherited service credentials.
These tests check recordkeeping; they cannot establish semantic truth or recall.
"""

import copy
import hashlib
from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
HELPER = (ROOT / "assets/agent-assets/skills/retrospective-research/scripts/"
          "research_bundle.py")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(path):
    return {str(item.relative_to(path)): item.read_bytes()
            for item in path.rglob("*") if item.is_file()}


class ResearchWorkPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="envy-research-plan-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.bundle = self.work / "bundle"
        self.serial = 0
        self.contract = {
            "version": 1,
            "purpose": "Explain the fictional Orchard pilot's pause.",
            "scope": "Supplied records of the Orchard pilot and its former Grove name.",
            "requirements_source": "Synthetic request for a living plan and evidence brief.",
            "requirements": {"candidate_table": True, "brief_max_words": 100,
                             "destination": "Owned local HTML documents.",
                             "research_plan": True},
            "providers": {
                "archive": {"read_formats": ["html"], "basis": "Supplied faithful HTML fixture."},
                "register": {"read_formats": ["text"], "basis": "Supplied plain-text register."},
            },
            "questions": [{"id": "decision", "text": "Why was the pilot paused?", "central": True},
                          {"id": "outcomes", "text": "Were later outcomes measured?", "central": False}],
        }
        self.cli("init", "--contract", self.json_file(self.contract))
        self.ledger = {"sources": []}
        self.plan = {
            "version": 1,
            "owner": "fixture-coordinator",
            "coverage": [{"id": "pilot-history", "questions": ["decision", "outcomes"],
                          "terms": ["Orchard", "Grove"], "providers": ["archive", "register"],
                          "people": ["fictional-reviewer"], "periods": ["2022-2024"],
                          "work_types": ["decision", "evaluation"],
                          "reason": "Trace both names and distinguish decisions from measured outcomes."}],
            "items": [self.search_item()],
        }

    def json_file(self, value):
        self.serial += 1
        path = self.work / ("input-%03d.json" % self.serial)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def cli(self, action, *args, root=None, success=True):
        process = subprocess.run(
            [sys.executable, "-I", "-B", str(HELPER), action,
             str(root or self.bundle), *map(str, args)],
            cwd=self.work, env={"PATH": os.defpath},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.stderr, "", process.stderr)
        try:
            record = json.loads(process.stdout)
        except ValueError:
            self.fail("CLI did not return a JSON diagnostic: " + repr(process.stdout))
        self.assertEqual(process.returncode, 0 if success else 1, record)
        self.assertEqual(record["status"], "pass" if success else "fail", record)
        return record

    def search_item(self, key="find-pilot", **changes):
        item = {"id": key, "coverage": ["pilot-history"], "kind": "search",
                "actor": "fixture-researcher", "provider": "archive", "priority": "high",
                "status": "queued", "reason": "Locate the original decision and possible later measurements.",
                "request": {"query": '"Orchard" OR "Grove"', "filters": {"before": "2025-01-01"},
                            "cap": 2, "cursor": None}}
        item.update(changes)
        return item

    def read_item(self, key="inspect-decision", **changes):
        item = {"id": key, "coverage": ["pilot-history"], "kind": "read",
                "actor": "fixture-reader", "provider": "archive", "priority": "high",
                "status": "queued", "reason": "Inspect the primary decision and its remaining unknowns.",
                "request": {"source_id": "decision-note", "format": "html",
                            "extent": "Full supplied body and decision table; linked appendix unavailable."},
                "origin": {"source_id": "decision-note", "locator": "Search result title and source link"}}
        item.update(changes)
        return item

    def save_plan(self, plan=None, ledger=None, success=True, root=None):
        result = self.cli("plan", "--plan", self.json_file(self.plan if plan is None else plan),
                          "--ledger", self.json_file(self.ledger if ledger is None else ledger),
                          root=root, success=success)
        if success and root is None:
            self.plan_hash = result["result"]["plan_sha256"]
        return result

    def spec(self, item, key, command=None, plan_hash=None):
        return {"id": key, "actor": item["actor"], "provider": item["provider"],
                "kind": item["kind"], "request": copy.deepcopy(item["request"]),
                "plan_item": item["id"], "plan_sha256": plan_hash or self.plan_hash,
                "command": command or [sys.executable, "-I", "-B", "-c",
                                       "print('{\"ids\":[\"decision-note\",\"later-index\"]}')"],
                "cwd": str(self.work), "timeout": 5}

    def capture(self, spec, success=True, root=None):
        self.cli("capture", "--spec", self.json_file(spec), root=root, success=success)
        return (root or self.bundle) / "operations" / spec["id"]

    def index(self, key, source_ids, pagination="complete", root=None):
        self.cli("index", "--id", key, "--result", self.json_file({
            "source_ids": source_ids, "pagination": pagination, "next_cursor": None,
            "note": "Synthetic IDs checked against retained output; coverage is limited to supplied records.",
        }), root=root)

    def source(self, key, title):
        return {"id": key, "title": title, "url": "https://example.invalid/" + key,
                "coverage": "Search metadata only; body not yet inspected.",
                "finding": "Candidate requiring inspection.",
                "contributor_basis": "No contribution established from search metadata.",
                "discovered_by": ["search-01"], "reads": [], "inspections": [],
                "analysis": {"status": "pending", "reason": "Body not inspected."},
                "disposition": {"status": "pending", "reason": "Candidate retained for assessment."},
                "leads": []}

    def discover(self, pagination="complete"):
        self.save_plan()
        self.capture(self.spec(self.plan["items"][0], "search-01"))
        self.index("search-01", ["decision-note", "later-index"], pagination)
        self.ledger["sources"] = [self.source("decision-note", "Orchard pause decision"),
                                   self.source("later-index", "Orchard later-outcome index")]
        self.plan["items"][0]["status"] = "done"
        self.plan["items"].append(self.read_item())
        self.save_plan()

    def retrieve(self):
        self.discover()
        document = self.work / "decision.html"
        document.write_text("<h1>Orchard pause</h1><p>The pilot paused pending archive migration.</p>"
                            "<p>A linked evaluation may contain later outcomes.</p>", encoding="utf-8")
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())", str(document)]
        self.capture(self.spec(self.plan["items"][1], "read-01", command=command))
        self.index("read-01", ["decision-note"], "not-applicable")
        source = self.ledger["sources"][0]
        source["discovered_by"].append("read-01")
        source["reads"].append("read-01")
        return source

    def inspected_bundle(self):
        source = self.retrieve()
        source["coverage"] = "Full supplied HTML body inspected; linked appendix remains unavailable."
        source["finding"] = "The pilot paused pending archive migration; later outcomes remain unknown."
        source["inspections"] = [{"read_id": "read-01", "actor": "fixture-reader",
                                  "coverage": source["coverage"]}]
        source["analysis"] = {"status": "complete", "reason": "Decision scope and outcome gap checked."}
        source["disposition"] = {"status": "used", "reason": "Primary evidence for the pause decision."}
        self.plan["items"][1]["status"] = "done"
        self.ledger["claims"] = [{"id": "pause", "text": "The pilot paused pending archive migration.",
                                  "basis": "Contemporaneous decision record.", "reads": ["read-01"],
                                  "locator": "operations/read-01/stdout, paragraph 1"}]
        self.ledger["questions"] = [
            {"id": "decision", "status": "answered", "claims": ["pause"],
             "reason": "The inspected primary decision states the reason."},
            {"id": "outcomes", "status": "bounded", "claims": [],
             "reason": "The later index is retained but its underlying measurements are unavailable."},
        ]
        self.ledger["semantic_review"] = {"reviewer": "fixture-editor",
                                          "note": "Checked pause versus cancellation and target versus outcome."}
        self.ledger["sources"][1]["disposition"] = {
            "status": "blocked", "reason": "Index retained; underlying measurements are unavailable."}
        self.save_plan()

    def validate(self, phase="research", ledger=None, success=True, root=None):
        return self.cli("validate", "--ledger", self.json_file(self.ledger if ledger is None else ledger),
                        "--phase", phase, root=root, success=success)

    def publication_files(self):
        view = self.cli("plan-view", "--ledger", self.json_file(self.ledger))["result"]
        brief = ('<html><body><h1>Orchard decision</h1>'
                 '<p>The pilot paused pending archive migration; later outcomes remain unknown.</p>'
                 '<p><a href="https://example.invalid/companion">Evidence companion</a></p></body></html>')
        companion = ('<html><body><h1>Orchard evidence</h1>'
                     '<p><a href="https://example.invalid/brief">Decision brief</a></p>'
                     '<table><tr><th>ID</th><th>Source</th><th>Coverage</th><th>Finding</th><th>Basis</th></tr>')
        for source in self.ledger["sources"]:
            companion += ('<tr><td>{id}</td><td><a href="{url}">{title}</a></td>'
                          '<td>{coverage}</td><td>{finding}</td><td>{contributor_basis}</td></tr>').format(
                              **{key: escape(source[key]) for key in
                                 ("id", "url", "title", "coverage", "finding", "contributor_basis")})
        companion += ('</table><h2>Research plan</h2><pre>' +
                      escape((self.bundle / "plan.md").read_text(encoding="utf-8")) + '</pre></body></html>')
        artifacts = []
        for key, body in (("brief", brief), ("companion", companion)):
            expected, actual = self.work / (key + "-expected.html"), self.work / (key + "-actual.html")
            expected.write_text(body, encoding="utf-8")
            actual.write_text(body, encoding="utf-8")
            artifacts.append({"id": key, "url": "https://example.invalid/" + key,
                              "expected": [str(expected)], "actual": [str(actual)]})
        return {"artifacts": artifacts, "candidate_tables": [0],
                "plan_review": {"reviewer": "fixture-editor", "note": "Plan and source states are present in companion.",
                                "companion_locator": "Research plan section after candidate table",
                                "plan_view_sha256": view["plan_view_sha256"]}}

    def test_initial_plan_precedes_discovery_and_has_a_recoverable_empty_inventory(self):
        self.save_plan()
        saved = read_json(self.bundle / "plan.json")
        checkpoint = self.bundle / "plans" / (self.plan_hash + ".ledger.json")
        self.assertEqual(read_json(checkpoint), {"sources": []})
        self.assertEqual(saved["_record"]["ledger_sha256"], digest(checkpoint))
        self.assertIsNone(saved["_record"]["parent_sha256"])
        self.assertEqual(list((self.bundle / "operations").iterdir()), [])
        before = snapshot(self.bundle / "plans")
        self.cli("plan-view")
        self.assertEqual(snapshot(self.bundle / "plans"), before)
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("fixture-coordinator", "find-pilot", "Orchard", "Grove", "queued"):
            self.assertIn(text, rendered)

    def test_saved_plan_has_readable_durable_inventory_and_immutable_revisions(self):
        self.discover()
        previous_hash = self.plan_hash
        previous_path = self.bundle / "plans" / (previous_hash + ".json")
        previous_bytes = previous_path.read_bytes()
        self.assertEqual(digest(previous_path), previous_hash)
        operations_before = snapshot(self.bundle / "operations")
        self.plan["items"][1]["priority"] = "normal"
        self.plan["items"][1]["reason"] += " Reprioritized after the decision record was located."
        self.save_plan()
        self.assertNotEqual(self.plan_hash, previous_hash)
        self.assertEqual(previous_path.read_bytes(), previous_bytes)
        self.assertEqual(snapshot(self.bundle / "operations"), operations_before)
        saved = read_json(self.bundle / "plan.json")
        self.assertEqual(saved["owner"], "fixture-coordinator")
        self.assertEqual(saved["items"][1]["priority"], "normal")
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("find-pilot", "inspect-decision", "Orchard", "Grove", "decision-note", "later-index"):
            self.assertIn(text, rendered)

    def test_capture_is_bound_to_plan_saved_before_execution(self):
        self.save_plan()
        before_hash = self.plan_hash
        operation = self.capture(self.spec(self.plan["items"][0], "search-01"))
        request = read_json(operation / "request.json")
        self.assertEqual(request["spec"]["plan_sha256"], before_hash)
        self.assertEqual(request["spec"]["plan_item"], "find-pilot")
        self.assertTrue((self.bundle / "plans" / (before_hash + ".json")).is_file())
        self.index("search-01", ["decision-note", "later-index"])
        self.ledger["sources"] = [self.source("decision-note", "Decision"), self.source("later-index", "Index")]
        self.plan["items"][0]["status"] = "done"
        self.save_plan()
        self.assertEqual(read_json(operation / "request.json"), request)
        self.validate()

    def test_missing_unknown_or_mismatched_binding_never_executes(self):
        self.save_plan()
        marker = self.work / "must-not-exist"
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')", str(marker)]
        changes = [{"plan_item": None}, {"plan_item": "unknown"}, {"plan_sha256": None},
                   {"plan_sha256": "0" * 64}, {"actor": "unassigned-actor"}, {"provider": "register"},
                   {"kind": "read", "request": self.read_item()["request"]}]
        for field, value in (("query", "different query"), ("filters", {}), ("cap", 3), ("cursor", "page-2")):
            request = copy.deepcopy(self.plan["items"][0]["request"])
            request[field] = value
            changes.append({"request": request})
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                spec = self.spec(self.plan["items"][0], "rejected-%d" % number, command=command)
                spec.update(change)
                before = snapshot(self.bundle)
                self.capture(spec, success=False)
                self.assertFalse(marker.exists())
                self.assertEqual(snapshot(self.bundle), before)

    def test_prior_active_assignment_remains_usable_until_current_item_is_closed(self):
        self.save_plan()
        assigned_hash = self.plan_hash
        self.plan["items"][0]["status"] = "active"
        self.plan["items"][0]["reason"] = "Researcher is running the assigned request."
        self.save_plan()
        self.assertNotEqual(self.plan_hash, assigned_hash)
        self.capture(self.spec(self.plan["items"][0], "search-01", plan_hash=assigned_hash))
        self.index("search-01", ["decision-note", "later-index"])
        self.ledger["sources"] = [self.source("decision-note", "Decision"), self.source("later-index", "Index")]
        marker = self.work / "closed-work-must-not-run"
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')", str(marker)]
        for status in ("done", "bounded", "dropped"):
            with self.subTest(status=status):
                self.plan["items"][0]["status"] = status
                self.plan["items"][0]["reason"] = "Current assignment explicitly closed: " + status
                self.save_plan()
                before = snapshot(self.bundle)
                self.capture(self.spec(self.plan["items"][0], "closed-" + status,
                                       command=command, plan_hash=assigned_hash), success=False)
                self.assertFalse(marker.exists())
                self.assertEqual(snapshot(self.bundle), before)
        self.plan["items"][0]["status"] = "active"
        self.plan["items"][0]["reason"] = "Assignment explicitly reopened to repeat the same query."
        self.save_plan()
        self.capture(self.spec(self.plan["items"][0], "reopened", plan_hash=assigned_hash))

    def test_changed_snapshot_or_checkpoint_rejects_capture_before_command_execution(self):
        self.save_plan()
        marker = self.work / "tampered-plan-must-not-run"
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')", str(marker)]
        paths = [Path("plan.json"), Path("plans") / (self.plan_hash + ".json"),
                 Path("plans") / (self.plan_hash + ".ledger.json")]
        for number, path in enumerate(paths):
            with self.subTest(path=path):
                root = self.work / ("tampered-%d" % number)
                shutil.copytree(self.bundle, root)
                with (root / path).open("ab") as stream:
                    stream.write(b"\n")
                before = snapshot(root)
                self.capture(self.spec(self.plan["items"][0], "rejected", command=command),
                             root=root, success=False)
                self.assertFalse(marker.exists())
                self.assertEqual(snapshot(root), before)

    def test_required_plan_cannot_be_attached_retroactively(self):
        item = self.plan["items"][0]
        spec = {"id": "unplanned", "actor": item["actor"], "provider": item["provider"],
                "kind": "search", "request": item["request"],
                "command": [sys.executable, "-I", "-B", "-c", "print('unplanned')"],
                "cwd": str(self.work)}
        before = snapshot(self.bundle)
        self.capture(spec, success=False)
        self.assertEqual(snapshot(self.bundle), before)

    def test_every_returned_candidate_is_required_even_when_not_used(self):
        self.discover()
        self.validate()
        incomplete = copy.deepcopy(self.ledger)
        incomplete["sources"].pop()
        self.validate(ledger=incomplete, success=False)
        self.save_plan(ledger=incomplete, success=False)

    def test_retrieval_does_not_establish_inspection_analysis_or_claim_support(self):
        source = self.retrieve()
        self.validate()
        self.assertEqual(source["inspections"], [])
        self.assertEqual(source["analysis"]["status"], "pending")
        invalid = copy.deepcopy(self.ledger)
        invalid["sources"][0]["analysis"] = {"status": "complete", "reason": "Claimed complete from retrieval alone."}
        self.validate(ledger=invalid, success=False)
        invalid = copy.deepcopy(self.ledger)
        invalid["claims"] = [{"id": "unsupported", "text": "The pilot was paused.", "basis": "Retrieved text.",
                              "reads": ["read-01"], "locator": "operations/read-01/stdout"}]
        self.validate(ledger=invalid, success=False)
        self.cli("plan-view", "--ledger", self.json_file(self.ledger))
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("read-01", "unread", "pending"):
            self.assertIn(text, rendered)

    def test_read_work_can_be_done_while_source_assessment_is_still_pending(self):
        self.retrieve()
        self.plan["items"][1]["status"] = "done"
        self.save_plan()
        self.validate()
        self.assertEqual(self.ledger["sources"][0]["analysis"]["status"], "pending")
        self.validate(phase="synthesis", success=False)
        for status in ("used", "context"):
            with self.subTest(status=status):
                invalid = copy.deepcopy(self.ledger)
                invalid["sources"][0]["disposition"] = {"status": status, "reason": "Proposed source use."}
                self.validate(ledger=invalid, success=False)

    def test_inspection_without_completed_analysis_cannot_support_a_claim(self):
        source = self.retrieve()
        source["inspections"] = [{"read_id": "read-01", "actor": "fixture-reader",
                                  "coverage": "Full supplied source body."}]
        self.ledger["claims"] = [{"id": "premature", "text": "The pilot was paused.",
                                  "basis": "Source inspected but not analyzed.",
                                  "reads": ["read-01"], "locator": "operations/read-01/stdout"}]
        for status in ("pending", "excluded"):
            with self.subTest(status=status):
                source["analysis"] = {"status": status, "reason": "No completed interpretation."}
                self.validate(success=False)
        source["analysis"] = {"status": "complete", "reason": "Interpreted the pause record and its scope."}
        self.validate()

    def test_inspection_requires_an_actual_successful_matching_read(self):
        self.retrieve()
        for read_id in ("missing-read", "search-01"):
            with self.subTest(read_id=read_id):
                invalid = copy.deepcopy(self.ledger)
                invalid["sources"][0]["inspections"] = [
                    {"read_id": read_id, "actor": "fixture-reader", "coverage": "Full supplied body."}]
                self.validate(ledger=invalid, success=False)
        invalid = copy.deepcopy(self.ledger)
        invalid["sources"][1]["inspections"] = [
            {"read_id": "read-01", "actor": "fixture-reader", "coverage": "Full supplied body."}]
        self.validate(ledger=invalid, success=False)

    def test_done_work_requires_successful_indexed_receipts(self):
        self.save_plan()
        invalid = copy.deepcopy(self.plan)
        invalid["items"][0]["status"] = "done"
        before = snapshot(self.bundle)
        self.save_plan(plan=invalid, success=False)
        self.assertEqual(snapshot(self.bundle), before)
        self.capture(self.spec(self.plan["items"][0], "failed-search", command=[
            sys.executable, "-I", "-B", "-c", "import sys; print('partial'); sys.exit(3)"],), success=False)
        self.index("failed-search", [], "unknown")
        self.save_plan(plan=invalid, success=False)
        self.capture(self.spec(self.plan["items"][0], "unindexed-search"))
        self.save_plan(plan=invalid, success=False)

    def test_completed_other_work_cannot_close_an_unexecuted_assignment(self):
        self.discover()
        invalid = copy.deepcopy(self.plan)
        invalid["items"].append(self.search_item("unexecuted", status="done"))
        before = snapshot(self.bundle)
        self.save_plan(plan=invalid, success=False)
        self.assertEqual(snapshot(self.bundle), before)

    def test_followup_origin_requires_known_source_locator_and_matching_read(self):
        self.inspected_bundle()
        origins = [{"source_id": "unknown", "locator": "Paragraph 2"},
                   {"source_id": "decision-note", "locator": ""},
                   {"source_id": "decision-note", "read_id": "missing", "locator": "Paragraph 2"},
                   {"source_id": "decision-note", "read_id": "search-01", "locator": "Paragraph 2"},
                   {"source_id": "later-index", "read_id": "read-01", "locator": "Paragraph 2"}]
        for origin in origins:
            with self.subTest(origin=origin):
                invalid = copy.deepcopy(self.plan)
                invalid["items"].append(self.search_item("follow-evaluation", origin=origin))
                before = snapshot(self.bundle)
                self.save_plan(plan=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)
        origin = {"source_id": "decision-note", "read_id": "read-01", "locator": "Paragraph 2"}
        self.plan["items"].append(self.search_item("follow-evaluation", origin=origin))
        self.ledger["sources"][0]["leads"] = [{
            "id": "evaluation-reference", "locator": "Paragraph 2", "reason": "Decision links possible later outcomes.",
            "target_source_id": "later-index", "plan_item": "follow-evaluation"}]
        self.save_plan()
        self.validate()
        self.validate(phase="synthesis", success=False)
        self.plan["items"][-1]["status"] = "bounded"
        self.plan["items"][-1]["reason"] = "The supplied fixture has no evaluation body; retain the unknown outcome."
        self.save_plan()
        self.validate(phase="synthesis")
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("follow-evaluation", "evaluation-reference", "Paragraph 2", "read-01", "bounded"):
            self.assertIn(text, rendered)

    def test_followup_origin_rejects_failed_or_unindexed_read_receipts(self):
        self.discover()
        failed = self.spec(self.plan["items"][1], "failed-read", command=[
            sys.executable, "-I", "-B", "-c", "import sys; print('partial body'); sys.exit(4)"])
        self.capture(failed, success=False)
        self.index("failed-read", ["decision-note"], "not-applicable")
        self.ledger["sources"][0]["discovered_by"].append("failed-read")
        self.capture(self.spec(self.plan["items"][1], "unindexed-read", command=[
            sys.executable, "-I", "-B", "-c", "print('<p>Supplied decision body.</p>')"]))
        for read_id in ("failed-read", "unindexed-read"):
            with self.subTest(read_id=read_id):
                invalid = copy.deepcopy(self.plan)
                invalid["items"].append(self.search_item("follow-evaluation", origin={
                    "source_id": "decision-note", "read_id": read_id, "locator": "Paragraph 2"}))
                before = snapshot(self.bundle)
                self.save_plan(plan=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_plan_can_change_while_a_recorded_tool_capture_is_in_progress(self):
        self.save_plan()
        spec = self.spec(self.plan["items"][0], "tool-search")
        del spec["command"]
        self.cli("begin", "--spec", self.json_file(spec))
        request = (self.bundle / "operations/tool-search/request.json").read_bytes()
        self.plan["items"][0]["status"] = "active"
        self.plan["items"][0]["reason"] = "Provider call is still in progress."
        self.save_plan()
        self.assertEqual((self.bundle / "operations/tool-search/request.json").read_bytes(), request)
        self.assertIn("in progress", (self.bundle / "plan.md").read_text(encoding="utf-8"))
        output = self.work / "tool-output.json"
        output.write_text('{"ids": []}', encoding="utf-8")
        self.cli("finish", "--id", "tool-search", "--output", output, "--outcome", "ok")
        self.index("tool-search", [])
        self.plan["items"][0]["status"] = "done"
        self.save_plan()
        self.validate()

    def test_leads_cannot_reference_unknown_destinations_or_disagree_with_work_origin(self):
        self.inspected_bundle()
        self.plan["items"].append(self.search_item("follow-evaluation", origin={
            "source_id": "decision-note", "read_id": "read-01", "locator": "Paragraph 2"}))
        lead = {"id": "evaluation-reference", "locator": "Paragraph 2", "reason": "Follow a linked evaluation."}
        changes = [{}, {"target_source_id": "missing"}, {"plan_item": "missing"},
                   {"plan_item": "follow-evaluation", "locator": "A different paragraph"}]
        for change in changes:
            with self.subTest(change=change):
                invalid = copy.deepcopy(self.ledger)
                invalid["sources"][0]["leads"] = [dict(lead, **change)]
                before = snapshot(self.bundle)
                self.save_plan(ledger=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_discovered_leads_and_sources_remain_in_later_plan_revisions(self):
        self.inspected_bundle()
        self.ledger["sources"][0]["leads"] = [{
            "id": "evaluation-reference", "locator": "Paragraph 2", "reason": "Keep the linked candidate.",
            "target_source_id": "later-index"}]
        seed = self.source("seeded-unread", "Unresolved supplied reference")
        seed["discovered_by"] = ["seed:synthetic-intake"]
        self.ledger["sources"].append(seed)
        self.save_plan()
        variants = []
        without_lead = copy.deepcopy(self.ledger)
        without_lead["sources"][0]["leads"] = []
        variants.append(without_lead)
        reused_lead = copy.deepcopy(self.ledger)
        reused_lead["sources"][0]["leads"][0]["target_source_id"] = "seeded-unread"
        variants.append(reused_lead)
        without_seed = copy.deepcopy(self.ledger)
        without_seed["sources"].pop()
        variants.append(without_seed)
        for number, invalid in enumerate(variants):
            with self.subTest(number=number):
                before = snapshot(self.bundle)
                self.save_plan(ledger=invalid, success=False)
                self.validate(ledger=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_capped_execution_can_be_done_but_coverage_limit_stays_visible(self):
        self.discover(pagination="capped")
        self.assertEqual(read_json(self.bundle / "plan.json")["items"][0]["status"], "done")
        self.cli("plan-view")
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("search-01", "capped", '"cap": 2', "decision-note", "later-index"):
            self.assertIn(text, rendered)
        self.assertRegex(rendered.lower(), r"(limited|limits?|not exhaustive|not establish.*exhaustive)")

    def test_unresolved_high_priority_work_blocks_synthesis_but_remains_visible(self):
        self.inspected_bundle()
        self.plan["items"].append(self.search_item("follow-later-outcomes"))
        self.plan["items"][-1]["request"]["query"] = '"Orchard" "evaluation"'
        self.save_plan()
        self.validate()
        self.validate(phase="synthesis", success=False)
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        self.assertIn("follow-later-outcomes", rendered)
        self.assertIn("queued", rendered)
        self.plan["items"][-1]["status"] = "active"
        self.save_plan()
        self.validate(phase="synthesis", success=False)
        self.plan["items"][-1]["priority"] = "low"
        self.plan["items"][-1]["reason"] = "Optional wider search does not resolve a central question."
        self.save_plan()
        self.validate(phase="synthesis")

    def test_work_definitions_and_ids_cannot_be_rewritten_or_removed(self):
        self.discover()
        for field, value in (("actor", "different-researcher"), ("provider", "register"),
                             ("request", {"query": "replacement", "filters": {}, "cap": None, "cursor": None})):
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.plan)
                invalid["items"][0][field] = value
                before = snapshot(self.bundle)
                self.save_plan(plan=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)
        invalid = copy.deepcopy(self.plan)
        invalid["items"].pop(0)
        self.save_plan(plan=invalid, success=False)
        invalid = copy.deepcopy(self.plan)
        invalid["coverage"][0]["id"] = "renamed-history"
        for item in invalid["items"]:
            item["coverage"] = ["renamed-history"]
        self.save_plan(plan=invalid, success=False)

    def test_queued_or_dropped_work_ids_cannot_be_reused_for_a_different_request(self):
        self.save_plan()
        for status in ("queued", "dropped"):
            with self.subTest(status=status):
                self.plan["items"][0]["status"] = status
                self.plan["items"][0]["reason"] = "Retain the original request even without a receipt."
                self.save_plan()
                invalid = copy.deepcopy(self.plan)
                invalid["items"][0]["request"]["query"] = "replacement query"
                before = snapshot(self.bundle)
                self.save_plan(plan=invalid, success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_malformed_plan_inputs_fail_cleanly_without_changing_bundle(self):
        self.save_plan()
        variants = [[], None, {"version": 1, "owner": "coordinator", "coverage": [], "items": []}]
        for field, value in (("coverage", [None]), ("coverage", {}), ("items", [None]), ("items", {})):
            invalid = copy.deepcopy(self.plan)
            invalid[field] = value
            variants.append(invalid)
        for field, value in (("filters", []), ("cap", True), ("cap", -1), ("query", [])):
            invalid = copy.deepcopy(self.plan)
            invalid["items"][0]["request"][field] = value
            variants.append(invalid)
        for number, invalid in enumerate(variants):
            with self.subTest(number=number):
                before = snapshot(self.bundle)
                self.cli("plan", "--plan", self.json_file(invalid), "--ledger", self.json_file(self.ledger), success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_malformed_ledgers_fail_cleanly_before_plan_or_view_writes(self):
        self.retrieve()
        variants = [None, [], {"sources": None}, {"sources": [None]}, {"sources": [[]]}]
        for field, value in (("inspections", {}), ("inspections", [None]),
                             ("analysis", []), ("disposition", None), ("leads", [None])):
            invalid = copy.deepcopy(self.ledger)
            invalid["sources"][0][field] = value
            variants.append(invalid)
        for claims in ([None], [[]], {}):
            invalid = copy.deepcopy(self.ledger)
            invalid["claims"] = claims
            variants.append(invalid)
        for number, invalid in enumerate(variants):
            with self.subTest(number=number):
                before = snapshot(self.bundle)
                path = self.json_file(invalid)
                self.cli("plan", "--plan", self.json_file(self.plan), "--ledger", path, success=False)
                self.cli("plan-view", "--ledger", path, success=False)
                self.assertEqual(snapshot(self.bundle), before)

    def test_malformed_capture_bindings_fail_without_running_command(self):
        self.save_plan()
        marker = self.work / "malformed-spec-must-not-run"
        command = [sys.executable, "-I", "-B", "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')", str(marker)]
        changes = [{"plan_item": []}, {"plan_sha256": {}}, {"actor": []},
                   {"provider": []}, {"request": []}, {"kind": {}}]
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                spec = self.spec(self.plan["items"][0], "malformed-%d" % number, command=command)
                spec.update(change)
                before = snapshot(self.bundle)
                self.capture(spec, success=False)
                self.assertFalse(marker.exists())
                self.assertEqual(snapshot(self.bundle), before)

    def test_legacy_contracts_need_no_plan_or_source_work_fields(self):
        for flag in (None, False):
            with self.subTest(flag=flag):
                contract = copy.deepcopy(self.contract)
                if flag is None:
                    del contract["requirements"]["research_plan"]
                else:
                    contract["requirements"]["research_plan"] = flag
                root = self.work / ("legacy-" + str(flag))
                self.cli("init", "--contract", self.json_file(contract), root=root)
                spec = self.spec(self.plan["items"][0], "search-01", plan_hash="unused")
                del spec["plan_item"], spec["plan_sha256"]
                self.capture(spec, root=root)
                self.index("search-01", ["decision-note", "later-index"], root=root)
                ledger = {"sources": [self.source("decision-note", "Decision"), self.source("later-index", "Index")]}
                for source in ledger["sources"]:
                    for field in ("inspections", "analysis", "disposition", "leads"):
                        del source[field]
                result = self.validate(ledger=ledger, root=root)["result"]
                self.assertEqual((result["operations"], result["sources"]), (1, 2))
                self.assertFalse((root / "plan.json").exists())
                self.assertFalse((root / "plans").exists())

    def test_enabling_plans_later_preserves_preexisting_operation_receipts(self):
        legacy = self.work / "legacy-amended"
        contract = copy.deepcopy(self.contract)
        del contract["requirements"]["research_plan"]
        self.cli("init", "--contract", self.json_file(contract), root=legacy)
        spec = self.spec(self.plan["items"][0], "search-01", plan_hash="unused")
        del spec["plan_item"], spec["plan_sha256"]
        self.capture(spec, root=legacy)
        self.index("search-01", ["decision-note", "later-index"], root=legacy)
        self.ledger["sources"] = [self.source("decision-note", "Decision"), self.source("later-index", "Index")]
        before = snapshot(legacy / "operations")
        self.cli("amend", "--contract", self.json_file(self.contract), root=legacy)
        self.save_plan(root=legacy)
        self.validate(root=legacy)
        self.assertEqual(snapshot(legacy / "operations"), before)

    def test_tightened_read_policy_retains_closed_work_and_requires_new_supported_request(self):
        self.discover()
        old_hash = self.plan_hash
        old_spec = self.spec(self.plan["items"][1], "obsolete-read", plan_hash=old_hash)
        self.contract["providers"]["archive"] = {
            "read_formats": ["native-json"], "basis": "Updated fixture policy requires complete native document structure."}
        self.cli("amend", "--contract", self.json_file(self.contract))
        self.plan["items"][1]["status"] = "bounded"
        self.plan["items"][1]["reason"] = "HTML is no longer accepted; use the replacement native read."
        replacement = self.read_item("inspect-native-decision")
        replacement["request"]["format"] = "native-json"
        self.plan["items"].append(replacement)
        self.save_plan()
        before = snapshot(self.bundle)
        self.capture(old_spec, success=False)
        self.assertEqual(snapshot(self.bundle), before)
        self.capture(self.spec(replacement, "native-read", command=[
            sys.executable, "-I", "-B", "-c", "print('{\"body\":\"Pilot paused pending archive migration.\"}')"]))
        self.index("native-read", ["decision-note"], "not-applicable")
        source = self.ledger["sources"][0]
        source["discovered_by"].append("native-read")
        source["reads"].append("native-read")
        self.plan["items"][-1]["status"] = "done"
        self.save_plan()
        self.validate()
        saved = read_json(self.bundle / "plan.json")
        self.assertEqual(saved["items"][1]["request"]["format"], "html")
        self.assertEqual(saved["items"][1]["status"], "bounded")

    def test_successful_local_investigation_preserves_unread_source_and_inspected_claim(self):
        self.inspected_bundle()
        result = self.validate(phase="synthesis")["result"]
        self.assertEqual(result["operations"], 2)
        self.assertEqual(result["sources"], 2)
        self.assertEqual(result["claims"], 1)
        before = snapshot(self.bundle)
        self.validate(phase="synthesis")
        self.assertEqual(snapshot(self.bundle), before)
        self.cli("plan-view", "--ledger", self.json_file(self.ledger))
        rendered = (self.bundle / "plan.md").read_text(encoding="utf-8")
        for text in ("decision-note", "later-index", "fixture-reader", "pending", "complete"):
            self.assertIn(text, rendered)

    def test_local_artifact_check_requires_review_of_the_current_plan_view(self):
        self.inspected_bundle()
        manifest = self.publication_files()
        invalid = copy.deepcopy(manifest)
        del invalid["plan_review"]
        self.cli("publication", "--ledger", self.json_file(self.ledger),
                 "--manifest", self.json_file(invalid), success=False)
        for field in ("reviewer", "note", "companion_locator", "plan_view_sha256"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(manifest)
                del invalid["plan_review"][field]
                self.cli("publication", "--ledger", self.json_file(self.ledger),
                         "--manifest", self.json_file(invalid), success=False)
        result = self.cli("publication", "--ledger", self.json_file(self.ledger),
                          "--manifest", self.json_file(manifest))["result"]
        self.assertEqual(result["plan_view_sha256"], manifest["plan_review"]["plan_view_sha256"])
        self.assertEqual(set(result["artifacts"]), {"brief", "companion"})
        self.plan["items"][0]["reason"] += " Scope narrowed to supplied archive records."
        self.save_plan()
        self.cli("publication", "--ledger", self.json_file(self.ledger),
                 "--manifest", self.json_file(manifest), success=False)
        refreshed = self.publication_files()
        self.assertNotEqual(refreshed["plan_review"]["plan_view_sha256"], manifest["plan_review"]["plan_view_sha256"])
        self.cli("publication", "--ledger", self.json_file(self.ledger),
                 "--manifest", self.json_file(refreshed))


if __name__ == "__main__":
    unittest.main()
