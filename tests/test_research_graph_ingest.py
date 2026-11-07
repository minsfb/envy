"""Native-recorder ingestion tests. All files/data are owned fictional fixtures.

No provider, network, process execution, or user state is involved. Operation
requests, receipt files, budget admissions, saved plans and returns are produced
through the actual existing recorder functions, never fixture_only substitutes.
"""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "assets/agent-assets/skills/retrospective-research/scripts"
FIXTURES = ROOT / "tests/fixtures/retrospective-research/graph-ingest"
SPEC = importlib.util.spec_from_file_location("research_graph_ingest", SCRIPTS / "research_graph_ingest.py")
INGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGEST)
GRAPH = INGEST.graph
BUNDLE = INGEST._sibling("research_bundle")
BUDGET = BUNDLE.budget


class ResearchGraphIngestTests(unittest.TestCase):
    def setUp(self):
        hook = patch.object(BUNDLE, "sync_graph_evidence", return_value=None, create=True)
        hook.start()
        self.sync_hook = hook
        self.addCleanup(hook.stop)
        work_hook = patch.object(BUNDLE, "sync_graph_work", return_value=None, create=True)
        work_hook.start()
        self.addCleanup(work_hook.stop)
        temporary = tempfile.TemporaryDirectory(prefix="envy-graph-ingest-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "bundle"
        self.contract = {
            "version": 1, "purpose": "Explain the fictional Orchard pause.", "scope": "Owned sample records.",
            "requirements_source": "Fictional graph ingestion acceptance fixture.",
            "requirements": {"candidate_table": True, "brief_max_words": 100, "destination": "Local files",
                             "research_budget": True, "research_plan": True},
            "providers": {"archive": {"read_formats": ["html"], "basis": "Owned HTML fixture."}},
            "questions": [{"id": "decision", "text": "Why pause?", "central": True}, {"id": "outcomes", "text": "Later outcomes?", "central": False}]}
        BUNDLE.init(self.root, self.contract)
        for actor in ("retriever", "reader"):
            BUDGET.activity(self.root, actor, "start")
        self.request = {"source_id": "doc-a", "format": "html", "extent": "Retained HTML body lines 1 through 4"}
        self.items = [{"id": key, "coverage": ["history"], "kind": "read", "actor": actor,
                       "provider": "archive", "priority": "normal", "status": "queued",
                       "reason": "Inspect the supplied source", "request": self.request,
                       "graph": {"extent": {"body": {"unit": "line", "ranges": [{"start": 1, "end": 4}]}},
                                 "scope_key": "body", "format_policy_sha256": "a" * 64, "purpose": key}}
                      for key, actor in (("retrieve", "retriever"), ("inspect", "reader"))]
        self.plan = {"version": 1, "owner": "coordinator", "coverage": [{"id": "history", "questions": ["decision", "outcomes"],
            "terms": ["Orchard"], "providers": ["archive"], "people": [], "periods": [], "work_types": ["review"], "reason": "Trace the supplied pause."}], "items": self.items}
        ledger = {"sources": [{"id": "doc-a", "title": "Orchard review", "url": "https://example.invalid/a",
            "coverage": "Unread", "finding": "Pending", "contributor_basis": "Unknown", "discovered_by": ["seed:fixture"],
            "reads": [], "inspections": [], "analysis": {"status": "pending", "reason": "Unread"},
            "disposition": {"status": "pending", "reason": "Unread"}, "leads": []}]}
        BUNDLE.save_plan(self.root, self.plan, ledger)
        self.plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        self.contract_hash = BUNDLE.digest(self.root / "contract.json")
        GRAPH.initialize(self.root, "coordinator", "fixture-run")
        now = datetime.now(timezone.utc)
        rows = [INGEST._insert("plan_snapshots", plan_sha256=self.plan_hash, contract_sha256=self.contract_hash, relative_path="plans/" + self.plan_hash + ".json"),
                INGEST._insert("artifacts", artifact_id="source-a", title="Orchard review", artifact_type="document"),
                INGEST._insert("artifact_keys", namespace="bundle-source:fixture-run", external_id="doc-a", artifact_id="source-a", basis="Supplied seed local alias"),
                INGEST._insert("artifact_keys", namespace="fixture-doc", external_id="A", artifact_id="source-a", basis="Supplied provider identity"),
                INGEST._insert("discoveries", discovery_id="seed-a", artifact_id="source-a", supplied_seed_id="fixture", observed_title="Orchard review")]
        for item in self.items:
            key, actor = item["id"], item["actor"]
            work = dict(work_id="work-" + key, kind="read", artifact_id="source-a", question_id="decision",
                        request_json={"source_id": "doc-a", "formats": ["html"], "freshness_basis": "captured_after_assignment",
                                      "extent": {"body": {"unit": "line", "ranges": [{"start": 1, "end": 4}]}}},
                        required_scope_key="body", format_policy_sha256="a" * 64, purpose=key, stage="baseline", branch="archive",
                        generation=0, transition_reason="Supplied root source")
            work["logical_key"] = GRAPH.logical_key(work)
            rows += [INGEST._insert("actors", actor_id=actor, parent_actor_id="coordinator", role="reader"),
                     INGEST._insert("plan_items", plan_sha256=self.plan_hash, plan_item_id=key),
                     INGEST._insert("work_items", **work),
                     INGEST._insert("work_origins", origin_id="origin-" + key, work_id="work-" + key, root_basis="Supplied seed", originating_generation=0),
                     INGEST._insert("assignments", assignment_id="assignment-" + key, work_id="work-" + key, actor_id=actor,
                                    claimed_at=now.isoformat(), lease_expires_at=(now + timedelta(hours=1)).isoformat(),
                                    generation_at_claim=0, plan_item_id=key, plan_sha256=self.plan_hash)]
        self.packet("setup", rows, kind="reconcile", validator=BUNDLE.graph_evidence_validator)

    def packet(self, key, mutations, kind="observe", validator=None):
        return GRAPH.ingest(self.root, dict(schema_version=1, batch_id=key, actor_id="coordinator", assignment_id=None,
                                           packet_kind=kind, contract_sha256=self.contract_hash,
                                           submitted_at=datetime.now(timezone.utc).isoformat(), mutations=mutations), evidence_validator=validator)

    def operation(self, key="read-one", sources=None, native=True, captured=True, query=False, mutate_result=None,
                  preflight=False, index=True):
        spec = dict(id=key, actor="retriever", provider="archive", kind="read", request=copy.deepcopy(self.request),
                    plan_sha256=self.plan_hash, plan_item="retrieve", graph_assignment_id="assignment-retrieve",
                    research={"stage": "baseline", "branch": "archive", "parent_operation": None, "root_basis": "Supplied root source"})
        if not native or query:
            # Exercise the recorder's supported legacy mode through a real
            # retained contract amendment, without weakening its validators.
            old = BUNDLE.contract_at(self.root)
            if old["requirements"]["research_plan"]:
                old["requirements"]["research_plan"] = False
                BUNDLE.amend(self.root, old)
                self.contract_hash = BUNDLE.digest(self.root / "contract.json")
                self.packet("legacy-contract", [INGEST._insert("contract_snapshots", contract_sha256=self.contract_hash,
                    contract_revision=2, relative_path="contracts/" + self.contract_hash + ".json")], kind="reconcile")
            spec.pop("graph_assignment_id", None)
            spec.pop("plan_item", None)
            spec.pop("plan_sha256", None)
            if query:
                spec.update(kind="search", request={"query": key, "filters": {}, "cap": 100000, "cursor": None})
            BUNDLE.start(self.root, spec, "tool-import")
        else:
            BUNDLE.start(self.root, spec, "tool-import")
        BUNDLE.import_output(self.root, key, FIXTURES / "source.html", None, "ok")
        sources = sources if sources is not None else [{"source_id": "doc-a", "namespace": "fixture-doc", "external_id": "A", "title": "Orchard review", "type": "document"}]
        result = dict(source_ids=list(dict.fromkeys(s["source_id"] for s in sources)), sources=sources,
                      pagination="complete", next_cursor=None, note="Every supplied fixture occurrence is retained.")
        if captured and not query:
            result["captures"] = [{"id": "stdout", "source_id": "doc-a", "format": "html", "relative_path": "operations/" + key + "/stdout",
                                   "extent": {"body": {"unit": "line", "ranges": [{"start": 1, "end": 4}]}},
                                   "extraction_status": "complete", "limitations": "Comments, revisions and visuals unavailable."}]
        if mutate_result:
            mutate_result(result)
        if not index:
            return spec, result
        if preflight:
            BUNDLE.index_result(self.root, key, result)
        else:
            # Historical recorder fixture: retain the coarse envelope before
            # graph validation, including originals from before index preflight.
            BUNDLE.write(self.root / "operations" / key / "result.json", BUNDLE.prepare_index_result(self.root, key, result))
            BUNDLE.sync_graph_evidence(self.root, operation_id=key)
        return spec

    def returned(self, change=None, native=True):
        returned = json.loads((FIXTURES / "reader-return.json").read_text())
        returned["graph"]["inspected_at"] = datetime.now(timezone.utc).isoformat()
        if not native:
            returned.pop("graph")
            returned.pop("graph_assignment_id")
        if change:
            change(returned)
        BUDGET.reader_return(self.root, returned)
        return returned

    def rows(self, table):
        with GRAPH.snapshot(self.root) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM " + table)]

    def candidate(self, key="reader-one", invalid=False):
        returned = json.loads((FIXTURES / "reader-return.json").read_text())
        returned["id"] = key
        returned["graph"].pop("inspected_at")
        if invalid:
            returned["graph"]["inspections"][0]["captures"][0]["extent"]["body"]["ranges"][0]["end"] = 3
            returned["graph"]["locators"][0]["position"]["end"] = 3
        return returned

    def rejected(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        returned = self.candidate(invalid=True)
        BUDGET.reader_return(self.root, returned)
        with self.assertRaises(GRAPH.GraphError) as caught:
            INGEST.ingest_reader_return(self.root, returned["id"])
        self.assertEqual(caught.exception.code, "scope_mismatch")
        original_hash = BUNDLE.digest(self.root / "reader-returns/reader-one.json")
        result = INGEST.reject_reader_return(self.root, "reader-one", expected_original_sha256=original_hash,
                                            reason="Numeric inspected bounds do not meet the requested scope")
        self.assertEqual(result["state"], "rejected")
        return returned, original_hash, result["rejection"]

    def correction(self, original, digest, key="reader-corrected"):
        candidate = copy.deepcopy(original)
        candidate["id"] = key
        candidate["corrects"] = {"return_id": original["id"], "original_sha256": digest, "reason": "Preserve inspection and frontier with partial scope"}
        candidate["graph"]["inspections"][0]["scopes"][0].update(coverage="partial", basis="Only the explicitly inspected numeric extent is supported")
        return candidate

    def test_preflight_uses_real_rules_without_mutating_original_bundle(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        (self.root / "graph/exports/unrelated.txt").write_text("Not a preflight dependency")
        (self.root / "graph/checkpoints/unrelated.txt").write_text("Not a preflight dependency")
        with GRAPH.snapshot(self.root) as connection:
            before_db = list(connection.iterdump())
            with INGEST._validation_clone(self.root, connection) as clone:
                self.assertFalse(list((clone / "graph/exports").iterdir()))
                self.assertFalse(list((clone / "graph/checkpoints").iterdir()))
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                  if p.is_file() and not p.name.startswith("research.sqlite")}
        self.assertTrue(INGEST.validate_reader_return(self.root, self.candidate())["valid"])
        with self.assertRaises(GRAPH.GraphError) as caught:
            INGEST.validate_reader_return(self.root, self.candidate(invalid=True))
        self.assertEqual(caught.exception.code, "scope_mismatch")
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(before_db, list(connection.iterdump()))
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                                if p.is_file() and not p.name.startswith("research.sqlite")})
        self.assertFalse((self.root / "reader-returns/reader-one.json").exists())

    def test_many_locators_do_not_repeat_capture_identity_queries(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")

        def repeated_locations(returned):
            locator = returned["graph"]["locators"][0]
            returned["graph"]["locators"].extend(dict(copy.deepcopy(locator), id="location-" + str(i))
                                                 for i in range(100))

        self.returned(repeated_locations)
        with GRAPH.snapshot(self.root) as connection:
            data = INGEST._returned(self.root, "reader-one", connection)
            small = copy.deepcopy(data)
            small["returned"]["graph"]["locators"] = small["returned"]["graph"]["locators"][:1]
            with patch.object(INGEST, "_canonical", wraps=INGEST._canonical) as canonical:
                INGEST._return_groups(self.root, connection, small)
                baseline_calls = canonical.call_count
            with patch.object(INGEST, "_canonical", wraps=INGEST._canonical) as canonical:
                groups = INGEST._return_groups(self.root, connection, data)
                self.assertEqual(canonical.call_count, baseline_calls)
            locators = [m for group in groups for m in group if m["table"] == "locators"]
            self.assertGreaterEqual(len(locators), 101)
            # This memo belongs to one declaration expansion only. A later
            # expansion must recheck the current snapshot's capture identity.
            with patch.object(INGEST, "_canonical", wraps=INGEST._canonical) as canonical:
                self.assertEqual(groups, INGEST._return_groups(self.root, connection, data))
                self.assertEqual(canonical.call_count, baseline_calls)

    def test_rejected_return_unblocks_unrelated_results_but_cannot_replay(self):
        original, digest, rejected = self.rejected()
        immutable = {p: p.read_bytes() for p in (self.root / "graph/normalizations").rglob("*") if p.is_file()}
        raw = (self.root / "reader-returns/reader-one.json").read_bytes()
        status = INGEST.ingestion_status(self.root)
        self.assertFalse(status["complete"])
        item = next(x for x in status["items"] if x["kind"] == "return")
        self.assertEqual(item["state"], "rejected")
        self.assertEqual(item["unaccepted_inventory"]["leads"], original["leads"])
        self.assertIn(rejected["relative_path"], status["immutable_receipts"])
        for attempt in (lambda: GRAPH.ingest(self.root, self.root / rejected["packets"][0]["path"], evidence_validator=INGEST.evidence_validator),
                        lambda: GRAPH.recover(self.root, rejected["packets"][0]["batch_id"], apply=True, evidence_validator=INGEST.evidence_validator)):
            with self.assertRaises(GRAPH.GraphError) as caught:
                attempt()
            self.assertIn(caught.exception.code, {"scope_mismatch", "integrity_error"})
        self.operation("independent", query=True, sources=[{"source_id": "child", "namespace": "fixture-doc", "external_id": "child", "title": "Unread child"}])
        INGEST.index_operation(self.root, "independent")
        self.operation("empty", query=True, sources=[])
        INGEST.index_operation(self.root, "empty")
        self.assertTrue(any(r["external_id"] == "child" for r in self.rows("artifact_keys")))
        self.assertEqual(self.rows("inspections"), [])
        self.assertEqual(self.rows("reference_occurrences"), [])
        self.assertEqual(raw, (self.root / "reader-returns/reader-one.json").read_bytes())
        self.assertEqual(immutable, {p: p.read_bytes() for p in immutable})

    def test_accepted_correction_preserves_inventory_time_budget_and_proposed_assertions(self):
        original, digest, rejected = self.rejected()
        before = BUDGET.load(self.root / "research-budget.json")
        event = next(e for e in before["events"] if e["type"] == "reader-return")
        candidate = self.correction(original, digest)
        INGEST.validate_reader_return(self.root, candidate)
        BUDGET.reader_return(self.root, candidate)
        INGEST.ingest_reader_return(self.root, candidate["id"])
        status = INGEST.ingestion_status(self.root)
        self.assertTrue(status["complete"])
        self.assertEqual(next(x for x in status["items"] if x["original_id"] == original["id"])["state"], "corrected")
        self.assertEqual(len(self.rows("reference_occurrences")), 3)
        self.assertEqual(len(self.rows("lead_origins")), 4)
        self.assertEqual(self.rows("sufficient_inspection"), [])
        self.assertEqual(self.rows("assertion_decisions"), [])
        expected_time = GRAPH._timestamp(datetime.fromtimestamp(event["at"], timezone.utc).isoformat())
        self.assertEqual(self.rows("inspections")[0]["inspected_at"], expected_time)
        after = BUDGET.load(self.root / "research-budget.json")
        for field in ("search_attempts", "read_attempts", "assessed_artifacts"):
            self.assertEqual(BUDGET.accounting(before)[field], BUDGET.accounting(after)[field])
        ledger = {"sources": [{"id": "doc-a", "inspections": [{"read_id": "read-one", "return_id": candidate["id"], "actor": "reader"}],
            "leads": [dict(lead, return_refs=[{"return_id": candidate["id"], "lead_id": lead["id"]}]) for lead in candidate["leads"]]}]}
        checked = BUDGET.check_returns(self.root, after, ledger, "synthesis", return_corrections=status["return_corrections"])
        self.assertEqual(checked["returned_leads_unreconciled"], 0)
        self.assertEqual(checked["corrected_reader_returns"], 1)
        forged = copy.deepcopy(status["return_corrections"])
        forged[original["id"]]["correction_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            BUDGET.check_returns(self.root, after, ledger, "synthesis", return_corrections=forged)
        with self.assertRaises(ValueError):
            BUDGET.check_returns(self.root, after, ledger, "synthesis")

    def test_correction_rejects_lost_frontier_new_inspection_and_sibling_acceptance(self):
        original, digest, _ = self.rejected()
        candidate = self.correction(original, digest)
        mutations = [lambda r: r["leads"].pop(), lambda r: r["leads"][0]["questions"].pop(),
                     lambda r: r["graph"]["scans"][0]["references"].pop(),
                     lambda r: r["graph"]["inspections"].append(dict(r["graph"]["inspections"][0], id="new-inspection")),
                     lambda r: r["graph"].update(inspected_at=datetime.now(timezone.utc).isoformat()),
                     lambda r: r.update(actor="retriever"), lambda r: r["corrects"].update(original_sha256="0" * 64)]
        for mutate in mutations:
            bad = copy.deepcopy(candidate)
            mutate(bad)
            with self.assertRaises((ValueError, GRAPH.GraphError)):
                INGEST.validate_reader_return(self.root, bad)
        sibling = self.correction(original, digest, "reader-sibling")
        INGEST.validate_reader_return(self.root, candidate)
        INGEST.validate_reader_return(self.root, sibling)
        BUDGET.reader_return(self.root, candidate)
        INGEST.ingest_reader_return(self.root, candidate["id"])
        with self.assertRaises(GRAPH.GraphError) as caught:
            INGEST.validate_reader_return(self.root, sibling)
        self.assertEqual(caught.exception.code, "scope_mismatch")
        self.assertEqual(len(self.rows("inspections")), 1)

    def test_correction_chain_and_concurrent_siblings_keep_one_inspection(self):
        original, digest, _ = self.rejected()
        middle = copy.deepcopy(original)
        middle.update(id="reader-middle", corrects={"return_id": original["id"], "original_sha256": digest, "reason": "Explicit failed scope correction attempt"})
        BUDGET.reader_return(self.root, middle)
        with self.assertRaises(GRAPH.GraphError):
            INGEST.ingest_reader_return(self.root, middle["id"])
        middle_hash = BUNDLE.digest(self.root / "reader-returns/reader-middle.json")
        INGEST.reject_reader_return(self.root, middle["id"], expected_original_sha256=middle_hash, reason="Scope remains unsupported")
        candidates = [self.correction(middle, middle_hash, key) for key in ("reader-final-a", "reader-final-b")]
        def record(candidate):
            try:
                with GRAPH.writer(self.root):
                    INGEST.validate_reader_return(self.root, candidate)
                    BUDGET.reader_return(self.root, candidate)
                    INGEST.ingest_reader_return(self.root, candidate["id"])
                return candidate["id"]
            except GRAPH.GraphError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            winners = [key for key in workers.map(record, candidates) if key]
        self.assertEqual(len(winners), 1)
        status = INGEST.ingestion_status(self.root)
        self.assertTrue(status["complete"])
        mapping = status["return_corrections"]
        self.assertEqual(mapping[original["id"]]["chain"], [original["id"], middle["id"], winners[0]])
        self.assertEqual(mapping[middle["id"]]["chain"], [middle["id"], winners[0]])
        self.assertEqual(len(self.rows("inspections")), 1)
        self.assertEqual(len(list((self.root / "reader-returns").glob("*.json"))), 3)

    def test_rejection_crash_is_idempotent_and_tamper_does_not_hide_pending(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        original = self.candidate(invalid=True)
        BUDGET.reader_return(self.root, original)
        with self.assertRaises(GRAPH.GraphError):
            INGEST.ingest_reader_return(self.root, original["id"])
        digest = BUNDLE.digest(self.root / "reader-returns/reader-one.json")
        actual_save = INGEST._save
        def crash_after_save(path, data):
            actual_save(path, data)
            if path.name == "rejection.json":
                raise RuntimeError("owned crash after durable rejection")
        with patch.object(INGEST, "_save", side_effect=crash_after_save):
            with self.assertRaises(RuntimeError):
                INGEST.reject_reader_return(self.root, original["id"], expected_original_sha256=digest, reason="Unsupported scope")
        result = INGEST.reject_reader_return(self.root, original["id"], expected_original_sha256=digest, reason="Unsupported scope")
        self.assertTrue(result["idempotent"])
        path = self.root / result["rejection"]["relative_path"]
        payload = BUNDLE.read(path)
        payload["reason"] = "Changed rejection without its record hash"
        BUNDLE.write(path, payload)
        status = INGEST.ingestion_status(self.root)
        self.assertFalse(status["complete"])
        item = next(x for x in status["items"] if x["kind"] == "return")
        self.assertEqual(item["error"]["code"], "integrity_error")
        self.assertEqual(self.rows("reader_returns"), [])

    def test_rejection_refuses_valid_or_accepted_returns_and_bad_original_hash(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        returned = self.returned()
        actual_ingest = GRAPH.ingest
        def stop_return(root, packet, **kwargs):
            if "/return-" in str(packet):
                raise RuntimeError("owned fault before acceptance")
            return actual_ingest(root, packet, **kwargs)
        with patch.object(GRAPH, "ingest", side_effect=stop_return):
            with self.assertRaises(RuntimeError):
                INGEST.ingest_reader_return(self.root, returned["id"])
        digest = BUNDLE.digest(self.root / "reader-returns/reader-one.json")
        for expected, code in (("0" * 64, "integrity_error"), (digest, "invalid_packet")):
            with self.assertRaises(GRAPH.GraphError) as caught:
                INGEST.reject_reader_return(self.root, returned["id"], expected_original_sha256=expected, reason="Attempted invalid rejection")
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.rows("reader_returns"), [])
        INGEST.ingest_reader_return(self.root, returned["id"])
        with self.assertRaises(GRAPH.GraphError):
            INGEST.reject_reader_return(self.root, returned["id"], expected_original_sha256=digest, reason="Accepted original cannot retire")

    def test_conflicting_unmerged_provider_key_and_local_alias_are_rejected(self):
        self.packet("other-provider-identity", [INGEST._insert("artifacts", artifact_id="source-b", title="Different document", artifact_type="document"),
            INGEST._insert("artifact_keys", namespace="fixture-doc", external_id="B", artifact_id="source-b", basis="Independent fictional provider ID"),
            INGEST._insert("discoveries", discovery_id="seed-b", artifact_id="source-b", supplied_seed_id="fixture-b", observed_title="Different document")])
        self.operation(sources=[{"source_id": "doc-a", "namespace": "fixture-doc", "external_id": "B", "title": "Conflicting alias", "type": "document"}])
        with self.assertRaises(GRAPH.GraphError) as caught:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(caught.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("captures"), [])
        self.assertEqual(next(row for row in self.rows("artifact_keys") if row["external_id"] == "doc-a")["artifact_id"], "source-a")

    def test_capture_is_not_inspection_and_return_uses_actual_reader(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.assertEqual(self.rows("inspections"), [])
        self.assertEqual(self.rows("operation_links")[0]["verification_state"], "verified")
        returned = self.returned()
        original = (self.root / "reader-returns/reader-one.json").read_bytes()
        result = INGEST.ingest_reader_return(self.root, returned["id"])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(self.rows("reader_returns")[0]["actor_id"], "reader")
        self.assertEqual(self.rows("reader_returns")[0]["assignment_id"], "assignment-inspect")
        self.assertEqual(self.rows("operation_links")[0]["assignment_id"], "assignment-retrieve")
        self.assertEqual(len(self.rows("reference_occurrences")), 3)
        self.assertEqual(len(self.rows("lead_origins")), 4)
        self.assertEqual(len(self.rows("followup_obligations")), 6)
        self.assertTrue(all(row["state"] == "pending" for row in self.rows("followup_obligations")))
        self.assertEqual(self.rows("assertion_decisions"), [])
        self.assertEqual(self.rows("reconciled_assertions")[0]["disposition"], "proposed")
        self.assertTrue(INGEST.ingest_reader_return(self.root, returned["id"])["idempotent"])
        self.assertEqual(original, (self.root / "reader-returns/reader-one.json").read_bytes())

    def test_bundle_entry_points_automatically_index_and_ingest_returns(self):
        self.sync_hook.stop()
        self.operation(preflight=True)
        self.assertEqual(len(self.rows("captures")), 1)
        self.assertEqual(self.rows("inspections"), [])
        returned = json.loads((FIXTURES / "reader-return.json").read_text())
        returned["graph"]["inspected_at"] = datetime.now(timezone.utc).isoformat()
        BUNDLE.record_reader_return(self.root, returned)
        self.assertEqual(len(self.rows("inspections")), 1)
        self.assertEqual(len(self.rows("followup_obligations")), 6)
        self.assertTrue(INGEST.ingestion_status(self.root)["complete"])

    def test_index_preflight_rejects_bad_versions_without_retaining_or_spending(self):
        _, draft = self.operation(index=False)
        original_files = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                          if p.is_file() and not p.name.startswith("research.sqlite")}
        with GRAPH.snapshot(self.root) as connection:
            original_rows = list(connection.iterdump())
        for version in ("scalar-revision", {"modified_at": "2020-04-07 17:19:52 PDT"}):
            invalid = copy.deepcopy(draft)
            invalid["sources"][0]["version"] = version
            for attempt in (INGEST.validate_index_result, BUNDLE.index_result):
                with self.subTest(version=version, attempt=attempt.__name__), self.assertRaises(GRAPH.GraphError) as caught:
                    attempt(self.root, "read-one", invalid)
                self.assertEqual(caught.exception.code, "invalid_packet")
        self.assertEqual(original_files, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                                         if p.is_file() and not p.name.startswith("research.sqlite")})
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(original_rows, list(connection.iterdump()))
        self.assertTrue(INGEST.validate_index_result(self.root, "read-one", draft)["valid"])
        self.assertFalse((self.root / "operations/read-one/result.json").exists())
        self.sync_hook.stop()
        BUNDLE.index_result(self.root, "read-one", draft)
        self.assertTrue(INGEST.ingestion_status(self.root)["complete"])

    def test_index_preflight_keeps_receipt_identical_viewer_copies(self):
        viewer = self.root / "viewer.html"
        viewer.write_bytes((FIXTURES / "source.html").read_bytes())
        _, draft = self.operation(index=False, mutate_result=lambda result: result["captures"][0].update(relative_path="viewer.html"))
        self.assertTrue(INGEST.validate_index_result(self.root, "read-one", draft)["valid"])
        viewer.write_text("Derived text cannot become captured evidence")
        with self.assertRaises(GRAPH.GraphError) as caught:
            BUNDLE.index_result(self.root, "read-one", draft)
        self.assertEqual(caught.exception.code, "integrity_error")
        self.assertFalse((self.root / "operations/read-one/result.json").exists())

    def malformed_index(self, version=None, key="read-one"):
        version = version if version is not None else {"revision": "retained-revision", "modified_at": "2020-04-07 17:19:52 PDT"}
        self.operation(key, mutate_result=lambda result: result["sources"][0].update(version=version))
        return BUNDLE.digest(self.root / "operations" / key / "result.json")

    def omit_version(self, digest, key="read-one", fields=None, reason="Retain raw optional metadata; omit its unsupported normalization"):
        return INGEST.omit_index_version(self.root, key, expected_original_sha256=digest, reason=reason,
            omissions=[{"source_index": 0, "source_id": "doc-a", "fields": fields or ["modified_at"]}])

    def test_version_omission_keeps_originals_candidates_capture_and_lifecycle(self):
        def observations(result):
            result["sources"][0]["version"] = {"revision": "retained-revision", "modified_at": "2020-04-07 17:19:52 PDT"}
            result["sources"].append(dict(result["sources"][0], version={"revision": "retained-revision"}))
            result["source_ids"].append("excluded-stub")
        self.operation(mutate_result=observations)
        work = INGEST._sibling("research_graph_work")
        work.record_completion(self.root, "read-one")
        original = self.root / "operations/read-one/result.json"
        before = {p: p.read_bytes() for p in original.parent.iterdir() if p.is_file()}
        budget_before = (self.root / "research-budget.json").read_bytes()
        with self.assertRaises(GRAPH.GraphError):
            INGEST.index_operation(self.root, "read-one")
        result = self.omit_version(BUNDLE.digest(original))
        self.assertEqual(result["state"], "complete")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(budget_before, (self.root / "research-budget.json").read_bytes())
        self.assertEqual(len([row for row in self.rows("discoveries") if row["operation_id"] == "read-one"]), 3)
        self.assertEqual(len(self.rows("captures")), 1)
        self.assertEqual(len(self.rows("artifact_versions")), 1)
        self.assertEqual(self.rows("artifact_versions")[0]["provider_revision"], "retained-revision")
        self.assertIsNone(self.rows("artifact_versions")[0]["modified_at"])
        with GRAPH.snapshot(self.root) as connection:
            effective = INGEST._operation(self.root, "read-one", connection)
        expected = BUNDLE.read(original)
        del expected["sources"][0]["version"]["modified_at"]
        self.assertEqual(effective["result"], expected)
        status = INGEST.ingestion_status(self.root)
        self.assertTrue(status["complete"])
        self.assertIn(result["omission_path"], status["immutable_receipts"])
        self.assertIn(result["omission_path"], BUNDLE.read(self.root / INGEST._normalization_path("operation", "read-one"))["dependencies"])
        self.assertTrue(self.omit_version(BUNDLE.digest(original))["idempotent"])
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.omit_version(BUNDLE.digest(original), reason="Conflicting replacement intent")
        self.assertEqual(caught.exception.code, "batch_conflict")

    def test_scalar_version_omission_and_tampered_proof_fail_closed(self):
        digest = self.malformed_index("scalar-revision")
        result = self.omit_version(digest, fields=["version"])
        self.assertEqual(self.rows("artifact_versions"), [])
        self.assertEqual(BUNDLE.read(self.root / "operations/read-one/result.json")["sources"][0]["version"], "scalar-revision")
        proof = self.root / result["omission_path"]
        original_proof = proof.read_bytes()
        record = BUNDLE.read(proof)
        record["reason"] = "Changed without the original proof hash"
        BUNDLE.write(proof, record)
        self.assertFalse(INGEST.ingestion_status(self.root)["complete"])
        proof.write_bytes(original_proof)
        proof.unlink()
        with self.assertRaises(GRAPH.GraphError):
            INGEST.index_operation(self.root, "read-one")

    def test_version_omission_refuses_accepted_content_and_other_changes(self):
        digest = self.malformed_index()
        invalid = [
            {"source_index": 0, "source_id": "other", "fields": ["modified_at"]},
            {"source_index": 99, "source_id": "doc-a", "fields": ["modified_at"]},
            {"source_index": 0, "source_id": "doc-a", "fields": ["revision"]},
            {"source_index": 0, "source_id": "doc-a", "fields": ["title"]},
        ]
        for omission in invalid:
            with self.subTest(omission=omission), self.assertRaises(GRAPH.GraphError):
                INGEST.omit_index_version(self.root, "read-one", expected_original_sha256=digest, omissions=[omission], reason="Invalid scope")
        with self.assertRaises(GRAPH.GraphError):
            self.omit_version("0" * 64)
        self.assertFalse((self.root / INGEST._omission_path("read-one")).exists())
        INGEST._sibling("research_graph_work").record_completion(self.root, "read-one")
        self.packet("accepted-occurrence", [INGEST._insert("discoveries", discovery_id="already-indexed", artifact_id="source-a",
                                                        operation_id="read-one", observed_title="Accepted earlier")])
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.omit_version(digest)
        self.assertEqual(caught.exception.code, "invalid_packet")

    def test_version_omission_crash_after_sidecar_is_resumable_without_new_spending(self):
        digest = self.malformed_index()
        budget_before = (self.root / "research-budget.json").read_bytes()
        save = INGEST._save
        def crash(path, data):
            save(path, data)
            if path == self.root.resolve() / INGEST._omission_path("read-one"):
                raise RuntimeError("Owned crash after durable omission receipt")
        with patch.object(INGEST, "_save", side_effect=crash), self.assertRaises(RuntimeError):
            self.omit_version(digest)
        status = INGEST.ingestion_status(self.root)
        self.assertFalse(status["complete"])
        self.assertEqual(status["items"][0]["state"], "unindexed")
        self.assertIn(INGEST._omission_path("read-one"), status["immutable_receipts"])
        self.assertEqual(self.omit_version(digest)["state"], "complete")
        self.assertEqual(budget_before, (self.root / "research-budget.json").read_bytes())

    def test_version_omission_refuses_preexisting_plan_files(self):
        digest = self.malformed_index()
        directory = (self.root / INGEST._normalization_path("operation", "read-one")).parent
        directory.mkdir(parents=True)
        (directory / "packet-00000.json").write_text("Owned interrupted normalization fixture")
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.omit_version(digest)
        self.assertEqual(caught.exception.code, "invalid_packet")
        self.assertFalse((self.root / INGEST._omission_path("read-one")).exists())

    def test_version_omission_crash_after_plan_retention_reuses_exact_packets(self):
        digest = self.malformed_index()
        plan_path = self.root.resolve() / INGEST._normalization_path("operation", "read-one")
        save = INGEST._save
        def crash(path, data):
            save(path, data)
            if path == plan_path:
                raise RuntimeError("Owned crash after durable normalization plan")
        with patch.object(INGEST, "_save", side_effect=crash), self.assertRaises(RuntimeError):
            self.omit_version(digest)
        retained = {path: path.read_bytes() for path in plan_path.parent.iterdir()}
        self.assertEqual(self.rows("captures"), [])
        self.assertEqual(self.omit_version(digest)["state"], "complete")
        self.assertEqual(retained, {path: path.read_bytes() for path in retained})
        self.assertTrue(self.omit_version(digest)["idempotent"])

    def test_version_omission_cannot_hide_another_invalid_field(self):
        self.operation(mutate_result=lambda result: result["sources"][0].update(
            version={"modified_at": "2020-04-07 17:19:52 PDT"}, title=123))
        raw = self.root / "operations/read-one/result.json"
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.omit_version(BUNDLE.digest(raw))
        self.assertEqual(caught.exception.code, "invalid_packet")
        self.assertFalse((self.root / INGEST._omission_path("read-one")).exists())
        self.assertEqual(self.rows("captures"), [])

    def test_version_omission_keeps_capture_validation_and_refuses_supported_metadata(self):
        self.operation(mutate_result=lambda result: (result["sources"][0].update(
            version={"modified_at": "2020-04-07 17:19:52 PDT"}), result["captures"][0].update(format="pdf")))
        raw = self.root / "operations/read-one/result.json"
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.omit_version(BUNDLE.digest(raw))
        self.assertEqual(caught.exception.code, "pending_dependency")
        self.assertFalse((self.root / INGEST._omission_path("read-one")).exists())
        self.operation("valid-search", query=True, sources=[dict(source_id="doc-a", version={"revision": "known"})])
        with self.assertRaises(GRAPH.GraphError):
            self.omit_version(BUNDLE.digest(self.root / "operations/valid-search/result.json"), key="valid-search", fields=["version"])
        self.assertFalse((self.root / INGEST._omission_path("valid-search")).exists())

    def test_concurrent_omissions_accept_one_intent_without_duplicate_observations(self):
        digest = self.malformed_index()
        def attempt(reason):
            try:
                return self.omit_version(digest, reason=reason)["state"]
            except GRAPH.GraphError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("First omission intent", "Second omission intent")))
        self.assertEqual(sorted(results), ["batch_conflict", "complete"])
        self.assertEqual(len(self.rows("captures")), 1)
        self.assertEqual(len([row for row in self.rows("discoveries") if row["operation_id"] == "read-one"]), 1)

    def test_bundle_sync_recovers_originals_without_repeating_admission(self):
        self.operation()
        self.returned()
        before = (self.root / "research-budget.json").read_bytes()
        self.sync_hook.stop()
        result = BUNDLE.sync_graph(self.root)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["complete"])
        self.assertEqual(before, (self.root / "research-budget.json").read_bytes())
        self.assertEqual(len(self.rows("followup_obligations")), 6)
        self.assertEqual(BUNDLE.sync_graph(self.root)["attempted"], [])

    def test_same_title_distinct_ids_and_all_routes_retained_concurrently(self):
        sources = [{"source_id": "route-a", "namespace": "fixture-doc", "external_id": "B", "title": "Same title"},
                   {"source_id": "route-b", "namespace": "fixture-doc", "external_id": "B", "title": "Same title"},
                   {"source_id": "other", "namespace": "fixture-doc", "external_id": "C", "title": "Same title"}]
        self.operation("search-one", sources=sources, query=True)
        self.operation("search-two", sources=sources, query=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: INGEST.index_operation(self.root, key), ("search-one", "search-two")))
        self.assertTrue(all(result["state"] == "complete" for result in results))
        self.assertEqual(len(self.rows("artifacts")), 3)
        self.assertEqual(len(self.rows("discoveries")), 7)
        self.assertTrue(INGEST.index_operation(self.root, "search-one")["idempotent"])

    def test_legacy_id_only_source_and_return_keep_limits(self):
        self.operation(native=False, captured=False)
        INGEST.index_operation(self.root, "read-one")
        self.returned(native=False)
        INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(self.rows("inspections")[0]["provenance_basis"], "legacy_attested")
        self.assertEqual(self.rows("sufficient_inspection"), [])
        self.assertTrue(self.rows("reader_returns")[0]["limitations"])
        self.operation("unknown", sources=[{"source_id": "unnamed"}], query=True)
        INGEST.index_operation(self.root, "unknown")
        self.assertTrue(any(row["artifact_type"] == "unknown" and row["title"] == "" for row in self.rows("artifacts")))

    def test_partial_chunk_restart_reuses_original_packet_bytes(self):
        sources = [{"source_id": "candidate-%d" % i, "namespace": "fixture-doc", "external_id": str(i), "title": "Candidate"} for i in range(30)]
        self.operation("large", sources=sources, query=True)
        original_ingest = GRAPH.ingest
        calls = []
        def interrupt(root, packet, **kwargs):
            calls.append(packet)
            if len(calls) == 2:
                raise RuntimeError("owned fault after first chunk")
            return original_ingest(root, packet, **kwargs)
        with patch.object(INGEST, "MAX_MUTATIONS", 10), patch.object(GRAPH, "ingest", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                INGEST.index_operation(self.root, "large")
        before = {p: p.read_bytes() for p in (self.root / "graph/normalizations").glob("*/*.json")}
        pending = INGEST.ingestion_status(self.root)["items"][0]
        self.assertEqual(pending["state"], "pending")
        self.assertTrue(pending["accepted_batches"])
        self.assertTrue(pending["pending_batches"])
        INGEST.index_operation(self.root, "large")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(len(self.rows("discoveries")), 31)

    def staged_normalization(self, stop_after):
        sources = [{"source_id": "candidate-%d" % i, "namespace": "fixture-doc", "external_id": "staged-%d" % i}
                   for i in range(30)]
        self.operation("first-staged", sources=sources, query=True)
        self.operation("later-overlap", sources=sources, query=True)
        original_save = INGEST._save
        def interrupted_install(path, payload):
            original_save(path, payload)
            if path.name == stop_after:
                raise RuntimeError("Owned interruption during normalization installation")
        with patch.object(INGEST, "MAX_MUTATIONS", 10), patch.object(INGEST, "_save", side_effect=interrupted_install):
            with self.assertRaises(RuntimeError):
                INGEST.index_operation(self.root, "first-staged")
        directory = (self.root / INGEST._normalization_path("operation", "first-staged")).parent
        original_files = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        status = INGEST.ingestion_status(self.root)
        self.assertEqual(next(item for item in status["items"] if item["original_id"] == "first-staged")["state"], "pending")
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "later-overlap")
        self.assertEqual(error.exception.code, "pending_dependency")
        self.assertFalse((self.root / INGEST._normalization_path("operation", "later-overlap")).parent.exists())
        # The reservation retains original chunk limits even though the runtime
        # has returned to its larger default since the interrupted first pass.
        resumed = INGEST.index_operation(self.root, "first-staged")
        self.assertEqual(resumed["state"], "complete")
        self.assertGreater(len(resumed["batches"]), 1)
        self.assertEqual(original_files, {p: p.read_bytes() for p in original_files})
        self.assertEqual(INGEST.index_operation(self.root, "later-overlap")["state"], "complete")
        self.assertEqual(len(self.rows("artifacts")), 31)
        self.assertEqual(len(self.rows("discoveries")), 61)
        self.assertTrue(INGEST.ingestion_status(self.root)["complete"])

    def test_normalization_reservation_survives_before_any_packet(self):
        self.staged_normalization("reservation.json")

    def test_normalization_reservation_blocks_overlap_before_manifest(self):
        self.staged_normalization("packet-00000.json")

    def test_normalization_reservation_survives_after_checksum_before_manifest(self):
        self.staged_normalization("plan.sha256")

    def test_normalization_reservation_survives_after_manifest_before_ingest(self):
        self.staged_normalization("plan.json")

    def test_result_payload_conflict_does_not_replan(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        path = self.root / "operations/read-one/result.json"
        payload = BUNDLE.read(path)
        payload["note"] = "Changed original"
        BUNDLE.write(path, payload)
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "batch_conflict")

    def test_return_event_file_pending_states(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        path = self.root / "reader-returns/reader-one.json"
        saved = path.read_bytes()
        path.unlink()
        item = next(item for item in INGEST.ingestion_status(self.root)["items"] if item["kind"] == "return")
        self.assertEqual(item["error"]["code"], "pending_dependency")
        path.write_bytes(saved)
        changed = BUNDLE.read(path)
        changed["return"]["coverage"] = "Changed original return"
        BUNDLE.write(path, changed)
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "pending_dependency")
        self.assertEqual(self.rows("inspections"), [])

    def test_inspection_cannot_exceed_capture_or_change_source(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned(lambda r: r["graph"]["inspections"][0]["captures"][0]["extent"]["body"]["ranges"][0].update(end=40))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("reader_returns"), [])

    def test_native_binding_missing_remains_pending(self):
        self.operation()
        self.returned(lambda r: r.update(graph_assignment_id="assignment-missing"))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "pending_dependency")
        self.assertEqual(self.rows("inspections"), [])

    def test_large_index_crosses_real_mutation_limit_without_loss(self):
        sources = [{"source_id": "candidate-%d" % i, "namespace": "fixture-doc", "external_id": "large-%d" % i} for i in range(2600)]
        self.operation("large-real", sources=sources, query=True)
        result = INGEST.index_operation(self.root, "large-real")
        self.assertGreater(len(result["batches"]), 1)
        self.assertEqual(len(self.rows("discoveries")), 2601)
        self.assertTrue(INGEST.ingestion_status(self.root)["complete"])

    def test_copy_of_stdout_is_bound_but_derived_text_is_rejected(self):
        copy_path = self.root / "viewer.html"
        copy_path.write_bytes((FIXTURES / "source.html").read_bytes())
        self.operation(mutate_result=lambda r: r["captures"][0].update(relative_path="viewer.html"))
        INGEST.index_operation(self.root, "read-one")
        self.assertEqual(self.rows("captures")[0]["relative_path"], "viewer.html")
        copy_path.write_text("A transformed summary of the original bytes")
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "integrity_error")

    def test_format_and_capture_source_mismatch_rejected(self):
        self.operation(mutate_result=lambda r: r["captures"][0].update(format="pdf"))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("captures"), [])

    def test_reference_scan_cannot_claim_uncaptured_extent(self):
        self.operation()
        self.returned(lambda r: r["graph"]["scans"][0].update(extent={"body": "all"}))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("reference_occurrences"), [])

    def test_evidence_locator_must_be_inside_inspected_range(self):
        self.operation()
        def narrow(returned):
            inspection = returned["graph"]["inspections"][0]
            inspection["captures"][0]["extent"]["body"]["ranges"][0]["end"] = 2
            inspection["scopes"][0]["coverage"] = "partial"
        self.returned(narrow)
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("assertions"), [])

    def node_evidence(self, identity, unit="node"):
        def captured_nodes(result):
            result["captures"][0]["extent"] = {"body": {"unit": "node", "ids": ["node-read", "node-unread"]}}
        self.operation(mutate_result=captured_nodes)
        INGEST.index_operation(self.root, "read-one")
        def partial_nodes(returned):
            returned["leads"] = []
            returned["graph"]["scans"] = []
            locator = returned["graph"]["locators"][0]
            locator.update(kind="node", position={"unit": unit, "id": identity})
            locator.pop("excerpt", None)
            inspection = returned["graph"]["inspections"][0]
            inspection["captures"][0]["extent"] = {"body": {"unit": "node", "ids": ["node-read"]}}
            inspection["scopes"][0]["coverage"] = "partial"
        self.returned(partial_nodes)

    def test_normalized_id_locator_inside_inspected_set_supplies_evidence(self):
        self.node_evidence("node-read")
        self.assertEqual(INGEST.ingest_reader_return(self.root, "reader-one")["state"], "complete")
        self.assertEqual(self.rows("assertion_evidence")[0]["basis"], "inspected")

    def test_normalized_id_locator_outside_inspected_set_is_rejected_atomically(self):
        self.node_evidence("node-unread")
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("assertions"), [])
        self.assertEqual(self.rows("inspections"), [])

    def test_normalized_id_locator_unit_must_match_inspected_set(self):
        self.node_evidence("node-read", unit="comment")
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("assertion_evidence"), [])

    def test_direct_forged_assertion_is_not_an_original_return(self):
        self.operation()
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        assertion = {k: v for k, v in self.rows("assertions")[0].items() if k != "created_batch_id"}
        for key in ("value_json", "temporal_scope_json"):
            assertion[key] = json.loads(assertion[key])
        assertion["assertion_id"] = "forged-finding"
        assertion["value_json"] = {"text": "The opposite of the actual returned observation"}
        with self.assertRaises(INGEST.GraphError) as error:
            self.packet("forged", [INGEST._insert("assertions", **assertion)], validator=INGEST.evidence_validator)
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(len(self.rows("assertions")), 1)

    def test_replay_rechecks_original_receipt_dependencies(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        path = self.root / "operations/read-one/stdout"
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "integrity_error")

    def test_return_file_without_budget_event_is_pending(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        path = self.root / "research-budget.json"
        journal = BUNDLE.read(path)
        journal["events"].pop()
        BUNDLE.write(path, journal)
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "pending_dependency")

    def test_wrong_reader_assignment_actor_is_rejected(self):
        self.operation()
        self.returned(lambda r: r.update(graph_assignment_id="assignment-retrieve"))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")

    def test_source_mismatch_in_original_inspection_is_rejected(self):
        self.packet("other-source", [INGEST._insert("artifacts", artifact_id="source-b", title="Other record", artifact_type="document"),
            INGEST._insert("artifact_keys", namespace="bundle-source:fixture-run", external_id="doc-b", artifact_id="source-b", basis="Supplied alias"),
            INGEST._insert("discoveries", discovery_id="seed-b", artifact_id="source-b", supplied_seed_id="fixture-b", observed_title="Other record")])
        self.operation()
        self.returned(lambda r: r["graph"]["inspections"][0].update(source_id="doc-b"))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.rows("inspections"), [])

    def test_complete_plan_checksum_detects_lost_pending_chunks(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        path = next((self.root / "graph/normalizations").glob("*/plan.json"))
        plan = BUNDLE.read(path)
        plan["packets"] = []
        BUNDLE.write(path, plan)
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "integrity_error")

    def test_known_provider_revision_binds_capture_to_version(self):
        self.operation(mutate_result=lambda r: r["sources"][0].update(version={"revision": "v17", "modified_at": "2026-01-01T00:00:00Z"}))
        INGEST.index_operation(self.root, "read-one")
        version = self.rows("artifact_versions")[0]
        self.assertEqual(version["provider_revision"], "v17")
        self.assertEqual(self.rows("captures")[0]["version_id"], version["version_id"])

    def test_missing_required_format_cannot_claim_sufficient_scope(self):
        work = self.rows("work_items")[1]
        work = {k: v for k, v in work.items() if k not in ("created_batch_id", "updated_batch_id", "revision")}
        work.update(work_id="work-multiformat", purpose="Review two representations", logical_key="temporary")
        work["request_json"] = json.loads(work["request_json"])
        work["request_json"]["formats"] = ["html", "pdf"]
        work["logical_key"] = GRAPH.logical_key(work)
        plan, ledger, _ = BUNDLE.plan_snapshot(self.root)
        plan.pop("_record")
        item = copy.deepcopy(next(item for item in plan["items"] if item["id"] == "inspect"))
        item["id"] = "inspect-multi"
        item["graph"].update(formats=["html", "pdf"], purpose=work["purpose"])
        plan["items"].append(item)
        # Keep this fixture's manual graph seam, but retain the exact native
        # plan intent before creating the multi-format assignment.
        with patch.object(BUNDLE, "sync_graph_plan", return_value=None), patch.object(BUNDLE, "graph_ledger_context", return_value=None):
            BUNDLE.save_plan(self.root, plan, ledger)
        plan_hash = BUNDLE.read(self.root / "bundle.json")["plan_sha256"]
        now = datetime.now(timezone.utc)
        self.packet("multiformat-assignment", [
            INGEST._insert("plan_snapshots", plan_sha256=plan_hash, contract_sha256=self.contract_hash,
                           relative_path="plans/" + plan_hash + ".json"),
            INGEST._insert("plan_items", plan_sha256=plan_hash, plan_item_id=item["id"]),
            INGEST._insert("work_items", **work),
            INGEST._insert("work_origins", origin_id="multi-origin", work_id=work["work_id"], root_basis="Inspect two representations", originating_generation=0),
            INGEST._insert("assignments", assignment_id="assignment-multi", work_id=work["work_id"], actor_id="reader",
                           claimed_at=now.isoformat(), lease_expires_at=(now + timedelta(hours=1)).isoformat(), generation_at_claim=0,
                           plan_item_id=item["id"], plan_sha256=plan_hash)], kind="reconcile", validator=BUNDLE.graph_evidence_validator)
        self.operation()
        self.returned(lambda r: r.update(graph_assignment_id="assignment-multi"))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.ingest_reader_return(self.root, "reader-one")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertIn("missing requested formats", str(error.exception))
        self.assertEqual(self.rows("sufficient_inspection"), [])

    def test_malformed_rich_fields_return_a_safe_graph_error(self):
        self.operation(mutate_result=lambda r: r.update(captures=["not an object"]))
        with self.assertRaises(INGEST.GraphError) as error:
            INGEST.index_operation(self.root, "read-one")
        self.assertEqual(error.exception.code, "invalid_packet")

    def test_imported_receipt_reconciles_admission_without_invented_dispatch(self):
        self.operation()
        with GRAPH.snapshot(self.root) as connection:
            operation = INGEST._operation(self.root, "read-one", connection)
        admitted = dict(operation_id="read-one", assignment_id="assignment-retrieve",
                        budget_event_id=str(operation["event"]["id"]), request_sha256=operation["request_hash"],
                        receipt_sha256=operation["receipt_hash"], execution_state="admitted", verification_state="verified")
        def earlier_admission(root, connection, packet, mutation):
            # Model G4's earlier mirror using the actual retained recorder proof.
            # The deliberately conservative graph state has not observed dispatch.
            checked = dict(mutation, after=dict(mutation["after"], execution_state="succeeded"))
            return INGEST.evidence_validator(root, connection, packet, checked)
        self.packet("prior-admission", [INGEST._insert("operation_links", **admitted)], validator=earlier_admission)
        INGEST.index_operation(self.root, "read-one")
        states = [json.loads(event["payload_json"])["after"]["execution_state"] for event in self.rows("graph_events")
                  if event["entity_kind"] == "operation_links"]
        self.assertEqual(states, ["admitted", "unknown", "succeeded"])
        self.assertEqual(self.rows("operation_links")[0]["revision"], 3)


if __name__ == "__main__":
    unittest.main()
