#!/usr/bin/env python3
"""G3 queries through the real G1 snapshot, using fictional G0 SQL fixtures.

The loader below is deliberately the frozen SQL fixture loader, not ingestion.
These tests exercise query semantics/read-only snapshot integration, not recorder
receipts, ingest permission checks or provider behavior.
"""

import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "assets/agent-assets/skills/retrospective-research/scripts"
sys.path.insert(0, str(SCRIPTS))
import research_graph
import research_graph_queries as queries

_spec = importlib.util.spec_from_file_location("graph_schema_fixture_loader", ROOT / "tests/test_research_graph_schema.py")
_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_schema)
NOW = _schema.NOW


class ResearchGraphQueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = _schema.ResearchGraphSchemaTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.temporary = tempfile.TemporaryDirectory(prefix="research-query-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "graph").mkdir()
        (self.root / ".research-graph.lock").touch()
        self.database = self.root / "graph/research.sqlite"
        self.save()

    def save(self):
        self.db.commit()
        with sqlite3.connect(self.database) as destination:
            self.db.backup(destination)

    def collect(self, name, filters=None, limit=50):
        items, cursor, seen = [], None, set()
        while True:
            result = queries.query(self.root, name, filters, limit, cursor)
            self.assertEqual(result["status"], "pass")
            self.assertLessEqual(len(result["items"]), limit)
            items.extend(result["items"])
            cursor = result["next_cursor"]
            if cursor is None:
                return items
            self.assertNotIn(cursor, seen)
            seen.add(cursor)

    def rows(self, name, filters, record_type):
        return [r for r in self.collect(name, filters) if r["record_type"] == record_type]

    def work(self, identity, **overrides):
        record = {"work_id": identity, "kind": "search", "question_id": "decision", "request_json": {"query": identity},
                  "logical_key": identity, "purpose": "Query fixture follow-up", "stage": "baseline", "branch": "pilot",
                  "generation": 0, "priority": 50, "transition_reason": "Fixture candidate"}
        record.update(overrides)
        self.fixture.insert("work_items", record)

    def test_all_query_envelopes_and_read_only_snapshot(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        calls = [("artifact", {"artifact_id": "hub"}), ("neighbors", {"artifact_id": "hub"}),
                 ("frontier", {}), ("leads", {}), ("coverage", {"question_id": "decision"}),
                 ("evidence", {"claim_id": "approval-scope"}), ("changes", {})]
        for name, filters in calls:
            with self.subTest(name=name):
                result = queries.query(self.root, name, filters)
                self.assertEqual(result["schema_version"], 1)
                self.assertEqual(result["run_id"], "fictional-run")
                self.assertEqual(result["command"], name)
                self.assertGreater(result["graph_sequence"], 0)
                self.assertFalse(result["limits"]["dispatch_authorized"])
                self.assertFalse(result["limits"]["scope_matches_prove_freshness"])
        with research_graph.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM artifacts")
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_same_title_ids_stay_distinct_then_verified_alias_is_preserved(self):
        first = self.rows("artifact", {"artifact_id": "review", "include": ["metadata"]}, "artifact")
        second = self.rows("artifact", {"artifact_id": "other-review", "include": ["metadata"]}, "artifact")
        self.assertEqual(first[0]["title"], second[0]["title"])
        self.assertNotEqual(first[0]["canonical_id"], second[0]["canonical_id"])
        self.fixture.update("artifacts", "artifact_id", "other-review", 1,
                            canonical_artifact_id="review", canonical_basis="Verified duplicate in this test only")
        self.save()
        facts = self.collect("artifact", {"artifact_id": "other-review", "include": ["metadata"]}, 1)
        artifacts = [r for r in facts if r["record_type"] == "artifact"]
        self.assertEqual({r["artifact_id"] for r in artifacts}, {"review", "other-review"})
        self.assertEqual({r["canonical_id"] for r in artifacts}, {"review"})
        self.assertEqual({r["external_id"] for r in facts if r["record_type"] == "identity"}, {"review", "other-review"})

    def test_unresolved_mentions_and_bidirectional_corrected_history(self):
        facts = self.collect("neighbors", {"artifact_id": "hub", "direction": "out", "relation": "observed"}, 1)
        mention = next(r for r in facts if r.get("reference_id") == "review-mention")
        self.assertEqual(mention["kind"], "mention")
        self.assertIsNone(mention["target_artifact_id"])
        self.assertEqual(mention["locator"]["capture"]["capture_id"], "capture-hub")
        self.assertEqual(mention["scan"]["scan_id"], mention["scan_id"])
        self.fixture.load_packet("reconciliation")
        self.save()
        incoming = self.collect("neighbors", {"artifact_id": "review", "direction": "in", "relation": "observed"}, 1)
        reference = next(r for r in incoming if r["record_type"] == "observed_reference")
        self.assertEqual(reference["from_artifact_id"], "hub")
        history = [r for r in incoming if r["record_type"] == "reference_resolution"]
        self.assertEqual(len(history), 2)
        self.assertEqual({r["target_artifact_id"] for r in history}, {"other-review", "review"})
        self.assertEqual(sum(r["current"] for r in history), 1)

    def test_semantic_scope_disagreement_and_explicit_filters(self):
        base = dict(self.db.execute("SELECT * FROM assertions WHERE assertion_id='scoped-approval'").fetchone())
        base.pop("created_batch_id")
        base["temporal_scope_json"] = json.loads(base["temporal_scope_json"])
        base.update(assertion_id="challenged-approval", basis="inference")
        self.fixture.insert("assertions", base)
        self.fixture.insert("assertion_decisions", {"decision_id": "challenge", "assertion_id": "challenged-approval",
                            "disposition": "challenged", "reason": "Export mode remains deferred", "actor_id": "coordinator", "expected_revision": 0})
        self.save()
        edges = self.rows("neighbors", {"artifact_id": "design", "direction": "in", "relation": "semantic"}, "assertion")
        self.assertEqual({e["disposition"] for e in edges}, {"proposed", "challenged"})
        self.assertEqual({e["subject_locator_id"] for e in edges}, {"review-decision"})
        self.assertEqual({e["object_locator_id"] for e in edges}, {"design-input"})
        self.assertTrue(all(e["temporal_scope_json"]["precision"] == "day" for e in edges))
        filtered = self.rows("neighbors", {"artifact_id": "design", "relation": "semantic", "disposition": "challenged", "predicate": "approves", "question_id": "decision"}, "assertion")
        self.assertEqual([e["assertion_id"] for e in filtered], ["challenged-approval"])

    def test_current_claim_revision_never_reuses_old_evidence(self):
        self.fixture.insert("claim_evidence", {"evidence_id": "counterevidence", "claim_id": "approval-scope", "claim_revision": 1,
                            "locator_id": "review-decision", "assertion_id": "export-deferral", "stance": "conflict", "rationale": "A broader approval contradicts the deferred mode"})
        self.save()
        evidence = self.rows("evidence", {"claim_id": "approval-scope"}, "claim_evidence")
        self.assertEqual({r["stance"] for r in evidence}, {"support", "qualify", "conflict"})
        self.assertTrue(all(r["locator"]["capture"]["sha256"] for r in evidence))
        self.assertTrue(all(r["assertion"]["disposition"] == "proposed" for r in evidence))
        self.fixture.update("claims", "claim_id", "approval-scope", 1, text="Narrower newly revised claim")
        self.save()
        self.assertEqual(self.rows("evidence", {"claim_id": "approval-scope"}, "claim_evidence"), [])
        retained = self.rows("evidence", {"claim_id": "approval-scope", "revision": 1}, "claim_evidence")
        self.assertEqual(len(retained), 3)
        claim = self.rows("evidence", {"claim_id": "approval-scope", "revision": 1}, "claim")[0]
        self.assertEqual((claim["revision"], claim["current_revision"]), (1, 2))

    def test_multi_question_unassigned_obligation_and_explicit_bound(self):
        self.fixture.insert("work_origins", {"origin_id": "resolved-q1", "work_id": "work-hub", "lead_id": "old-name", "originating_generation": 1})
        self.fixture.update("assignments", "assignment_id", "assignment-hub", 2, state="completed", finished_at=NOW)
        self.fixture.update("work_items", "work_id", "work-hub", 3, state="completed", terminal_reason="First question addressed", transition_reason="Verified inspection")
        self.fixture.update("followup_obligations", "obligation_id", "lead:old-name", 1, state="terminal", disposition="resolved", reason="First question addressed")
        self.fixture.update("leads", "lead_id", "old-name", 1, disposition="resolved", reason="First question addressed")
        self.fixture.insert("questions", {"question_id": "outcomes", "text": "Were outcomes measured?", "central": 1, "contract_revision": 1})
        self.fixture.insert("lead_origins", {"origin_id": "lead-q2", "lead_id": "old-name", "return_id": "return-hub", "returned_lead_id": "old-name-1",
                            "locator_id": "hub-scope", "question_id": "outcomes", "original_value": "Grove", "origin_basis": "Same wording concerns another question"})
        self.save()
        scopes = self.rows("leads", {"question_id": "outcomes", "unresolved_only": True}, "obligation_scope")
        self.assertEqual({(r["question_id"], r["status"]) for r in scopes}, {("decision", "resolved"), ("outcomes", "unassigned")})
        obligations = self.rows("coverage", {"question_id": "outcomes"}, "obligation")
        self.assertEqual(next(r for r in obligations if r["lead_id"] == "old-name")["scope_status"], "unassigned")
        self.work("q2-bound", question_id="outcomes", state="deferred", terminal_reason="Explicit question-specific budget bound")
        self.fixture.insert("work_origins", {"origin_id": "bound-origin", "work_id": "q2-bound", "lead_id": "old-name", "originating_generation": 1})
        self.save()
        obligations = self.rows("coverage", {"question_id": "outcomes"}, "obligation")
        self.assertEqual(next(r for r in obligations if r["lead_id"] == "old-name")["scope_status"], "bounded")
        bounds = self.rows("coverage", {"question_id": "outcomes"}, "work")
        self.assertEqual(bounds[0]["terminal_reason"], "Explicit question-specific budget bound")
        origins = self.rows("coverage", {"question_id": "outcomes"}, "work_origin")
        self.assertEqual([(r["lead_id"], r["work_id"]) for r in origins], [("old-name", "q2-bound")])

    def test_frontier_orders_waves_and_retains_scope_outcomes_and_ancestry(self):
        policy = self.fixture.policy_hash
        self.work("read-again", kind="read", artifact_id="hub", required_scope_key="body", format_policy_sha256=policy,
                  request_json={"version_id": "future-version", "freshness_basis": "Needs a newer revision"})
        self.work("deep-high", stage="deep", generation=1, priority=100)
        self.work("priority-high", stage="priority", priority=100)
        self.work("baseline-later", generation=1, priority=100)
        self.fixture.insert("work_origins", {"origin_id": "again-origin", "work_id": "read-again", "lead_id": "old-name", "originating_generation": 1})
        for outcome_id, readability in (("transient", "transient_retry_pending"), ("access", "access_blocked")):
            self.fixture.insert("source_outcomes", {"outcome_id": outcome_id, "artifact_id": "hub", "question_id": "decision", "required_scope_key": "body",
                                "readability": readability, "disposition": "deferred", "reason": "Independent retained observation", "actor_id": "coordinator", "observed_at": NOW})
        self.save()
        facts = self.collect("frontier", {}, 2)
        self.assertEqual([r["work_id"] for r in facts if r["record_type"] == "work"], ["read-again", "baseline-later", "priority-high", "deep-high"])
        matches = [r for r in facts if r["record_type"] == "historical_scope_match"]
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0]["freshness_checked"])
        self.assertEqual(matches[0]["request_json"]["version_id"], "future-version")
        self.assertEqual({r["readability"] for r in facts if r["record_type"] == "source_outcome"}, {"transient_retry_pending", "access_blocked"})
        self.assertIn("again-origin", [r.get("origin_id") for r in facts])
        roles = self.rows("frontier", {"role": "trunk", "max_generation": 0}, "work")
        self.assertEqual([r["work_id"] for r in roles], ["read-again"])
        self.work("different-policy", kind="read", artifact_id="hub", required_scope_key="body", format_policy_sha256="a" * 64)
        self.save()
        matches = self.rows("frontier", {}, "historical_scope_match")
        self.assertNotIn("different-policy", [r["work_id"] for r in matches])

    def test_coverage_unread_root_disagreement_and_branch_counts(self):
        self.work("unread-root", kind="read", artifact_id="measurements", required_scope_key="body", format_policy_sha256=self.fixture.policy_hash)
        self.fixture.insert("assertions", {"assertion_id": "irrelevant-hub", "kind": "relevance", "subject_artifact_id": "hub",
                            "predicate": "is_relevant", "value_json": "irrelevant", "question_id": "decision", "basis": "inference", "assessed_at": NOW})
        self.fixture.insert("assertions", {"assertion_id": "hub-content", "kind": "role", "subject_artifact_id": "hub", "predicate": "has_role",
                            "value_json": "content", "subject_locator_id": "hub-scope", "question_id": "decision", "basis": "inspected", "return_id": "return-hub", "assessed_at": NOW})
        self.save()
        facts = self.collect("coverage", {"question_id": "decision", "scope_key": "body", "format_policy_sha256": self.fixture.policy_hash}, 3)
        root = next(r for r in facts if r["record_type"] == "candidate_artifact" and r["artifact_id"] == "measurements")
        self.assertTrue(root["is_root"])
        self.assertEqual(root["historical_scope_match_count"], 0)
        assertions = [r for r in facts if r["record_type"] == "assertion" and r["subject_artifact_id"] == "hub"]
        self.assertEqual({r["value_json"] for r in assertions if r["kind"] == "relevance"}, {"relevant", "irrelevant"})
        self.assertEqual({r["value_json"] for r in assertions if r["kind"] == "role"}, {"content", "trunk"})
        self.assertEqual(next(r for r in facts if r["record_type"] == "branch_counts")["pending"], 1)
        self.assertTrue(any(r["record_type"] == "observed_reference" and r["target_artifact_id"] is None for r in facts))

    def test_2000_candidates_are_paged_in_sql_with_unique_stable_order(self):
        for index in range(2000):
            self.work("candidate-%04d" % index, priority=index % 101, branch="branch-" + str(index % 4), generation=index % 3)
        self.save()
        # Verify database-side bounds, rather than only a Python slice after loading.
        original = research_graph.snapshot
        fact_queries = []
        @contextmanager
        def observed_snapshot(root):
            with original(root) as connection:
                connection.set_trace_callback(lambda sql: fact_queries.append(sql) if sql.startswith("WITH facts AS") else None)
                yield connection
        with mock.patch.object(research_graph, "snapshot", observed_snapshot):
            facts = self.collect("frontier", {}, 137)
        self.assertEqual(len(facts), 2000)
        self.assertEqual(len({r["work_id"] for r in facts}), 2000)
        self.assertTrue(fact_queries)
        self.assertTrue(all(sql.endswith(" LIMIT 138") for sql in fact_queries))
        keys = [(r["generation"], -r["priority"], r["branch"], r["work_id"]) for r in facts]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(facts, self.collect("frontier", {}, 200))

    def test_prior_companion_and_imported_evidence_keep_separate_provenance(self):
        self.fixture.insert("artifacts", {"artifact_id": "prior-companion", "title": "Earlier fictional study", "artifact_type": "research_companion"})
        self.fixture.insert("prior_run_imports", {"import_id": "prior-import", "prior_run_id": "earlier-fictional-run", "prior_export_id": "earlier-export",
                            "manifest_sha256": "b" * 64, "source_artifact_id": "prior-companion", "policy": "attested_evidence",
                            "scope_json": {"question_id": "decision"}, "limitations": "Historical context; originals are separate evidence"})
        for kind, identity in (("artifact", "review"), ("lead", "old-name"), ("claim", "approval-scope")):
            self.fixture.insert("prior_run_records", {"record_id": "prior-" + kind, "import_id": "prior-import", "prior_record_kind": kind,
                                "prior_record_id": "earlier-" + kind, kind + "_id": identity, "original_namespace": "fictional-archive",
                                "original_external_id": "earlier-" + identity, "lineage_json": ["earlier-fictional-run"], "limitations": "Not independent corroboration"})
        inspection = dict(self.db.execute("SELECT * FROM inspections WHERE artifact_id='review'").fetchone())
        self.fixture.insert("reader_returns", {"return_id": "prior-return", "actor_id": inspection["actor_id"], "file_path": "prior/attested-return.json",
                            "payload_sha256": "c" * 64, "verification_state": "legacy_attested", "limitations": "Imported historical attestation"})
        inspection.update(inspection_id="prior-inspection", provenance_basis="prior_run", limitations="Historical inspection",
                          return_id="prior-return", attested_started_at=NOW, attested_finished_at=NOW)
        self.fixture.insert("inspections", inspection)
        self.fixture.insert("inspection_captures", {"inspection_id": "prior-inspection", "capture_id": "capture-review", "inspected_extent_json": {"body": "all"}})
        self.fixture.insert("inspection_scopes", {"inspection_id": "prior-inspection", "question_id": "decision", "required_scope_key": "body",
                            "format_policy_sha256": self.fixture.policy_hash, "coverage": "sufficient", "basis": "Historical attestation only"})
        self.save()
        companion = self.rows("artifact", {"artifact_id": "prior-companion"}, "prior_run_import")
        self.assertEqual(companion[0]["source_artifact_id"], "prior-companion")
        evidence = self.rows("evidence", {"claim_id": "approval-scope"}, "prior_run_record")
        self.assertEqual({r["prior_record_kind"] for r in evidence}, {"artifact", "claim"})
        self.assertTrue(all(r["import"]["source_artifact_id"] == "prior-companion" for r in evidence))
        coverage = self.collect("coverage", {"question_id": "decision", "scope_key": "body", "format_policy_sha256": self.fixture.policy_hash})
        review = next(r for r in coverage if r["record_type"] == "candidate_artifact" and r["artifact_id"] == "review")
        self.assertEqual(review["historical_scope_match_count"], 1)
        self.assertTrue(any(r["record_type"] == "inspection_scope" and r["inspection"]["provenance_basis"] == "prior_run" for r in coverage))
        self.assertEqual({r["prior_record_kind"] for r in coverage if r["record_type"] == "prior_run_record"}, {"artifact", "lead", "claim"})

    def test_unregistered_unresolved_names_and_search_only_candidates_remain_visible(self):
        # No separate lead record is required for an exposed mention to remain open.
        mentions = self.rows("leads", {"question_id": "decision", "unresolved_only": True}, "unresolved_reference")
        self.assertEqual([r["reference_id"] for r in mentions], ["review-mention"])
        obligations = self.rows("leads", {"question_id": "decision"}, "reference_obligation")
        self.assertEqual(obligations[0]["reference_id"], "review-mention")
        self.fixture.insert("artifacts", {"artifact_id": "search-only", "title": "Unread search match", "artifact_type": "document"})
        self.fixture.insert("discoveries", {"discovery_id": "search-match", "artifact_id": "search-only", "operation_id": "op-hub", "rank": 1, "observed_title": "Unread search match"})
        self.fixture.insert("artifacts", {"artifact_id": "outcome-only", "title": "Restricted fixture", "artifact_type": "document"})
        self.fixture.insert("source_outcomes", {"outcome_id": "restricted-only", "artifact_id": "outcome-only", "question_id": "decision", "required_scope_key": "body",
                            "readability": "access_blocked", "disposition": "deferred", "reason": "Access unavailable", "actor_id": "coordinator", "observed_at": NOW})
        self.save()
        facts = self.collect("coverage", {"question_id": "decision"})
        unread = [r for r in facts if r["record_type"] == "candidate_artifact" and r["artifact_id"] in {"search-only", "outcome-only"}]
        self.assertEqual(len(unread), 2)
        self.assertTrue(all(r["historical_scope_match_count"] == 0 for r in unread))
        self.assertTrue(any(r["record_type"] == "source_outcome" and r["outcome_id"] == "restricted-only" for r in facts))

    def test_optional_filter_paths_keep_selection_and_scoped_provenance(self):
        coverage = self.collect("coverage", {"question_id": "decision", "branch": "pilot", "scope_key": "body",
                                             "format_policy_sha256": self.fixture.policy_hash}, 2)
        self.assertEqual({r["branch"] for r in coverage if r["record_type"] == "work"}, {"pilot"})
        self.assertTrue(any(r["record_type"] == "lead" and r["lead_id"] == "old-name" for r in coverage))
        self.assertEqual([r["record_type"] for r in self.collect("coverage", {"question_id": "decision", "branch": "unassigned-branch"})], ["question"])
        evidence = next(r for r in coverage if r["record_type"] == "assertion_evidence")
        self.assertEqual(evidence["inspection_capture"]["capture_id"], evidence["locator"]["capture"]["capture_id"])
        self.assertIsInstance(evidence["inspection_capture"]["inspected_extent_json"], dict)
        observed = self.rows("neighbors", {"artifact_id": "hub", "relation": "observed", "direction": "out", "question_id": "decision"}, "observed_reference")
        self.assertEqual(len(observed), 2)
        selected = self.rows("leads", {"kind": "vocabulary", "disposition": "pending", "question_id": "decision", "unresolved_only": True}, "lead")
        self.assertEqual([r["lead_id"] for r in selected], ["old-name"])

    def test_cursor_binds_query_filters_run_and_sequence(self):
        page = queries.query(self.root, "artifact", {"artifact_id": "hub"}, 1)
        token = page["next_cursor"]
        self.assertIsNotNone(token)
        for name, filters in (("artifact", {"artifact_id": "design"}), ("neighbors", {"artifact_id": "hub"})):
            with self.assertRaises(GraphError := research_graph.GraphError) as raised:
                queries.query(self.root, name, filters, cursor=token)
            self.assertEqual(raised.exception.code, "invalid_packet")
        # Changing only page size is safe; the selected set and snapshot stay fixed.
        self.assertTrue(queries.query(self.root, "artifact", {"artifact_id": "hub"}, 2, token)["items"])
        self.fixture.load_packet("reconciliation")
        self.save()
        with self.assertRaises(GraphError) as raised:
            queries.query(self.root, "artifact", {"artifact_id": "hub"}, cursor=token)
        self.assertEqual(raised.exception.code, "stale_cursor")
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        payload["run_id"] = "different-run"
        payload["sequence"] = self.db.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0]
        other = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with self.assertRaises(GraphError) as raised:
            queries.query(self.root, "artifact", {"artifact_id": "hub"}, cursor=other)
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_event_sequence_receipts_and_range(self):
        all_events = self.collect("changes", {}, 17)
        self.assertEqual([r["sequence"] for r in all_events], list(range(1, len(all_events) + 1)))
        selected = self.collect("changes", {"after_sequence": 4, "through_sequence": 9}, 2)
        self.assertEqual([r["sequence"] for r in selected], [5, 6, 7, 8, 9])
        self.assertTrue(all(r["batch"]["payload_sha256"] and r["batch"]["packet_path"] for r in selected))
        self.assertTrue(all(isinstance(r["payload_json"], dict) for r in selected))

    def test_invalid_input_and_sql_text_are_bound(self):
        invalid = [("no-such-query", {}, 50, None), ("artifact", {}, 50, None),
                   ("frontier", {"sort": "DROP TABLE artifacts"}, 50, None),
                   ("frontier", {}, True, None), ("frontier", {}, 201, None),
                   ("frontier", {"max_generation": True}, 50, None),
                   ("artifact", {"artifact_id": "hub", "include": "metadata"}, 50, None),
                   ("leads", {"unresolved_only": 1}, 50, None),
                   ("neighbors", {"artifact_id": "hub", "direction": "sideways"}, 50, None),
                   ("changes", {"after_sequence": 8, "through_sequence": 1}, 50, None),
                   ("frontier", {}, 50, "!bad-cursor!")]
        for name, filters, limit, cursor in invalid:
            with self.subTest(name=name, filters=filters, limit=limit, cursor=cursor):
                with self.assertRaises(research_graph.GraphError) as raised:
                    queries.query(self.root, name, filters, limit, cursor)
                self.assertEqual(raised.exception.code, "invalid_packet")
        self.assertEqual(queries.query(self.root, "frontier", {"branch": "pilot' OR 1=1 --"})["items"], [])
        with self.assertRaises(research_graph.GraphError) as raised:
            queries.query(self.root, "artifact", {"artifact_id": "hub' OR 1=1 --"})
        self.assertEqual(raised.exception.code, "unknown_identity")
        self.assertEqual(len(self.rows("artifact", {"artifact_id": "hub", "include": ["metadata"]}, "artifact")), 1)


if __name__ == "__main__":
    unittest.main()
