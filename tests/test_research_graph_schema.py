#!/usr/bin/env python3
"""G0 SQL contracts on fictional records, with no providers or user state.

The fixture loader only materializes schema rows. It is deliberately not an
ingestion implementation: permissions, receipts, recovery and replay belong to
the G1/G2 helper tests. Every connection uses a private in-memory database.
"""

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "assets/agent-assets/skills/retrospective-research/scripts/research_graph_schema.sql"
FIXTURES = ROOT / "tests/fixtures/retrospective-research/graph"
NOW = "2026-01-02T10:00:00Z"


class ResearchGraphSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text())
        self.db.execute("INSERT INTO schema_migrations VALUES (1, ?, ?, ?)",
                        ("research-graph-v1", hashlib.sha256(SCHEMA.read_bytes()).hexdigest(), NOW))
        self.db.commit()
        self.load_packet("bootstrap")
        self.load_packet("observations")
        self.expected = json.loads((FIXTURES / "expected-queries.json").read_text())
        self.policy_hash = self.expected["policy_sha256"]

    def columns(self, table):
        # Table names here come from checked-in fixtures, never user input.
        return [row[1] for row in self.db.execute("PRAGMA table_info(" + table + ")")]

    def insert(self, table, record, batch="observations"):
        record = copy.deepcopy(record)
        for column in self.columns(table):
            if column in ("created_batch_id", "updated_batch_id", "registration_batch_id", "imported_batch_id"):
                record.setdefault(column, batch)
        for key, value in record.items():
            if key.endswith("_json") and value is not None:
                record[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
        keys = list(record)
        self.db.execute("INSERT INTO " + table + " (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")", tuple(record[key] for key in keys))

    def update(self, table, key, identity, expected_revision, **changes):
        changes.update(revision=expected_revision + 1, updated_batch_id="observations")
        keys = list(changes)
        return self.db.execute("UPDATE " + table + " SET " + ",".join(column + "=?" for column in keys) + " WHERE " + key + "=? AND revision=?", tuple(changes[column] for column in keys) + (identity, expected_revision)).rowcount

    def load_packet(self, name):
        data = (FIXTURES / (name + ".packet.json")).read_bytes()
        packet = json.loads(data)
        head = self.db.execute("SELECT COALESCE(MAX(sequence), 0) FROM graph_events").fetchone()[0]
        with self.db:
            self.db.execute("INSERT INTO ingest_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (packet["batch_id"], packet["actor_id"], packet["assignment_id"], packet["packet_kind"], hashlib.sha256(data).hexdigest(), "graph/updates/" + name + ".json", NOW, 1, head + len(packet["mutations"])))
            for offset, mutation in enumerate(packet["mutations"], 1):
                table = mutation["table"]
                if mutation["op"] == "insert":
                    self.insert(table, mutation["record"], name)
                    pk = [row[1] for row in self.db.execute("PRAGMA table_info(" + table + ")") if row[5]]
                    identity = ":".join(str(mutation["record"][key]) for key in pk)
                    prior, following = None, 1
                else:
                    pk = [row[1] for row in self.db.execute("PRAGMA table_info(" + table + ")") if row[5]][0]
                    identity = mutation["id"]
                    prior = mutation["expected_revision"]
                    following = prior + 1
                    self.assertEqual(self.update(table, pk, identity, prior, **mutation["changes"]), 1)
                self.db.execute("INSERT INTO graph_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (head + offset, name, "create" if prior is None else "revise", table, identity, prior, following, json.dumps({"before": None, "after": mutation})))
            if name == "bootstrap":
                self.db.execute("INSERT INTO graph_metadata VALUES (1, 'fictional-run', 1, ?, ?)", (packet["contract_sha256"], NOW))
        self.assertEqual(list(self.db.execute("PRAGMA foreign_key_check")), [])

    def rejected(self, statement, args=()):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(statement, args)

    def test_bootstrap_deferred_cycle_and_batch_tail(self):
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM graph_metadata").fetchone()[0], 1)
        # A batch without its event cannot be committed, even with a valid actor.
        self.db.execute("INSERT INTO ingest_batches VALUES ('orphan', 'reader', NULL, 'observe', ?, 'orphan.json', ?, 1, 999)", ("a" * 64, NOW))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT count(*) FROM ingest_batches WHERE batch_id='orphan'").fetchone()[0], 0)

    def test_unknown_identity_and_known_revision_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("artifact_keys", {"namespace": "archive-file", "external_id": "missing", "artifact_id": "not-in-inventory", "basis": "Unknown fixture"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("artifact_keys", {"namespace": "archive-file", "external_id": "hub", "artifact_id": "design", "basis": "Would conflate identities"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("artifact_versions", {"version_id": "duplicate-token", "artifact_id": "hub", "provider_revision": "r1", "first_observed_at": NOW, "version_basis": "Duplicate known token"})
        for version_id in ("unknown-1", "unknown-2"):
            self.insert("artifact_versions", {"version_id": version_id, "artifact_id": "hub", "provider_revision": None, "first_observed_at": NOW, "version_basis": "Revision token unavailable"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM artifacts WHERE title='Pilot review'").fetchone()[0], 2)

    def test_capture_revision_must_belong_to_same_artifact(self):
        capture = dict(self.db.execute("SELECT * FROM captures WHERE capture_id='capture-hub'").fetchone())
        capture.update(capture_id="wrong-version", version_id="version-review", capture_extent_json={"body": "all"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("captures", capture)

    def test_canonical_merge_preserves_keys_and_prevents_cycles(self):
        self.update("artifacts", "artifact_id", "other-review", 1, canonical_artifact_id="review", canonical_basis="Verified duplicate ID")
        self.assertEqual(self.db.execute("SELECT canonical_id FROM canonical_artifacts WHERE artifact_id='other-review'").fetchone()[0], "review")
        self.assertEqual(self.db.execute("SELECT artifact_id FROM artifact_keys WHERE external_id='other-review'").fetchone()[0], "other-review")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("artifacts", "artifact_id", "review", 1, canonical_artifact_id="other-review", canonical_basis="Would cycle")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("artifacts", "artifact_id", "design", 1, canonical_artifact_id="hub", canonical_basis=None)
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("artifacts", "artifact_id", "other-review", 2, canonical_artifact_id=None)

    def test_every_reference_and_lead_has_durable_obligation(self):
        actual = [row[0] for row in self.db.execute("SELECT obligation_id FROM followup_obligations ORDER BY obligation_id")]
        self.assertEqual(actual, self.expected["after_observations"]["pending_obligation_ids"])
        self.rejected("DELETE FROM followup_obligations WHERE obligation_id='lead:old-name'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("followup_obligations", "obligation_id", "lead:old-name", 1, state="terminal", disposition="bounded", reason=None)
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("followup_obligations", "obligation_id", "lead:old-name", 1, state="scheduled", work_id="work-hub")
        self.insert("work_origins", {"origin_id": "old-name-work", "work_id": "work-hub", "lead_id": "old-name", "originating_generation": 1})
        self.assertEqual(self.update("followup_obligations", "obligation_id", "lead:old-name", 1, state="scheduled", work_id="work-hub"), 1)

    def test_one_question_resolution_cannot_hide_another_question(self):
        self.insert("work_origins", {"origin_id": "name-question-one", "work_id": "work-hub", "lead_id": "old-name", "originating_generation": 1})
        self.update("assignments", "assignment_id", "assignment-hub", 2, state="completed", finished_at=NOW)
        self.update("work_items", "work_id", "work-hub", 3, state="completed", terminal_reason="Question one addressed", transition_reason="Verified inspection")
        self.update("followup_obligations", "obligation_id", "lead:old-name", 1, state="terminal", disposition="resolved", reason="First question addressed")
        self.update("leads", "lead_id", "old-name", 1, disposition="resolved", reason="First question addressed")
        self.insert("questions", {"question_id": "outcomes", "text": "Were outcomes measured?", "central": 1, "contract_revision": 1})
        self.insert("lead_origins", {"origin_id": "name-question-two", "lead_id": "old-name", "return_id": "return-hub", "returned_lead_id": "old-name-1", "locator_id": "hub-scope", "question_id": "outcomes", "original_value": "Grove", "origin_basis": "Same returned lead also affects the second question"})
        scopes = {row[0]: row[1] for row in self.db.execute("SELECT question_id,status FROM obligation_scope_status WHERE obligation_id='lead:old-name'")}
        self.assertEqual(scopes, {"decision": "resolved", "outcomes": "unassigned"})
        self.assertEqual(self.db.execute("SELECT state,revision FROM followup_obligations WHERE obligation_id='lead:old-name'").fetchone()[:], ("pending", 3))
        self.assertEqual(self.db.execute("SELECT disposition FROM leads WHERE lead_id='old-name'").fetchone()[0], "pending")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("followup_obligations", "obligation_id", "lead:old-name", 3, state="terminal", disposition="resolved", reason="Would drop the second question")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("leads", "lead_id", "old-name", 3, disposition="bounded", reason="Would silently bound an unaddressed question")
        self.insert("work_items", {"work_id": "outcomes-bound", "kind": "reconcile", "question_id": "outcomes", "request_json": {}, "logical_key": "outcomes-bound", "purpose": "Bound the unanswered outcomes question", "stage": "priority", "branch": "pilot", "generation": 1, "transition_reason": "Retain second-question frontier"})
        self.insert("work_origins", {"origin_id": "name-outcomes-bound", "work_id": "outcomes-bound", "lead_id": "old-name", "originating_generation": 1})
        self.update("work_items", "work_id", "outcomes-bound", 1, state="deferred", terminal_reason="Fixture research cap reached", transition_reason="Explicit question-specific bound")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("followup_obligations", "obligation_id", "lead:old-name", 3, state="terminal", disposition="resolved", reason="A bound is not a resolution")
        self.assertEqual(self.update("followup_obligations", "obligation_id", "lead:old-name", 3, state="terminal", disposition="bounded", reason="Second question remains bounded"), 1)

    def test_unresolved_mentions_and_resolution_corrections_retain_history(self):
        self.assertEqual([row[0] for row in self.db.execute("SELECT reference_id FROM unresolved_references")], ["review-mention"])
        self.load_packet("reconciliation")
        target = self.db.execute("SELECT target_artifact_id, original_value FROM effective_reference_targets WHERE reference_id='review-mention'").fetchone()
        self.assertEqual(tuple(target), ("review", "readiness review"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM reference_resolutions WHERE reference_id='review-mention'").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("reference_resolutions", {"resolution_id": "fork", "reference_id": "review-mention", "target_artifact_id": "hub", "basis": "Would fork history", "actor_id": "coordinator", "replaces_resolution_id": "initial-review-resolution"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("reference_resolutions", {"resolution_id": "cross-reference", "reference_id": "measurements-link", "target_artifact_id": "hub", "basis": "Wrong chain", "actor_id": "coordinator", "replaces_resolution_id": "corrected-review-resolution"})

    def test_occurrences_preserve_passages_but_reject_wrong_scan(self):
        record = {"reference_id": "second-passage", "from_artifact_id": "hub", "locator_id": "hub-scope", "scan_id": "hub-scan", "local_reference_key": "mention-2", "kind": "mention", "original_value": "readiness review", "extraction_method": "Reader observation"}
        self.insert("reference_occurrences", record)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reference_occurrences WHERE original_value='readiness review'").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("reference_occurrences", dict(record, reference_id="wrong-scan", local_reference_key="mention-3", locator_id="review-decision"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("reference_occurrences", dict(record, reference_id="duplicate-key"))

    def test_scoped_assertions_do_not_become_whole_artifact_approval(self):
        self.load_packet("reconciliation")
        row = self.db.execute("SELECT object_locator_id, disposition FROM reconciled_assertions WHERE assertion_id='scoped-approval'").fetchone()
        self.assertEqual(tuple(row), ("design-input", "accepted"))
        record = {"assertion_id": "bad-scope", "kind": "relationship", "subject_artifact_id": "review", "predicate": "approves", "object_artifact_id": "design", "object_locator_id": "hub-refs", "basis": "inspected", "assessed_at": NOW}
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertions", record)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertions", dict(record, assertion_id="unknown-predicate", object_locator_id="design-input", predicate="automatically_proves"))

    def test_role_and_relevance_reject_untyped_json_and_missing_values(self):
        record = {"assertion_id": "bad-role", "kind": "role", "subject_artifact_id": "hub", "predicate": "has_role", "value_json": {"role": "trunk"}, "basis": "metadata", "assessed_at": NOW}
        for value in (None, {"role": "trunk"}, "invented-role"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.insert("assertions", dict(record, value_json=value))
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertions", dict(record, kind="relevance", predicate="is_relevant", value_json="irrelevant"))

    def test_uninspected_locator_cannot_claim_inspected_evidence(self):
        record = {"evidence_id": "wrong-inspection", "assertion_id": "scoped-approval", "locator_id": "review-decision", "inspection_id": "inspection-hub", "stance": "support", "basis": "inspected", "rationale": "Should reject another source's capture"}
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertion_evidence", record)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertion_evidence", dict(record, inspection_id=None))
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("inspection_captures", {"inspection_id": "inspection-hub", "capture_id": "capture-review", "inspected_extent_json": {"body": "all"}})

    def test_revision_conflicts_and_disagreements_are_visible(self):
        self.load_packet("reconciliation")
        self.insert("assertion_decisions", {"decision_id": "challenge-trunk", "assertion_id": "hub-trunk", "disposition": "challenged", "reason": "Reviewer questions its organizing scope", "actor_id": "reviewer", "expected_revision": 1})
        self.assertEqual(self.db.execute("SELECT disposition,revision FROM artifact_roles WHERE assertion_id='hub-trunk'").fetchone()[:], ("challenged", 2))
        self.assertEqual(self.db.execute("SELECT count(*) FROM assertion_decisions WHERE assertion_id='hub-trunk'").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assertion_decisions", {"decision_id": "stale", "assertion_id": "hub-trunk", "disposition": "accepted", "reason": "Stale coordinator", "actor_id": "coordinator", "expected_revision": 1})
        self.assertEqual(self.update("leads", "lead_id", "old-name", 1, significance="Needs an earlier-name search"), 1)
        self.assertEqual(self.update("leads", "lead_id", "old-name", 1, significance="Stale write"), 0)
        self.rejected("UPDATE leads SET significance='silent overwrite' WHERE lead_id='old-name'")

    def test_claim_edits_retain_revision_bound_evidence(self):
        self.update("claims", "claim_id", "approval-scope", 1, text="Input check was approved; the export decision remains bounded.", reason="Clarified uncertainty")
        self.assertEqual(self.db.execute("SELECT count(*) FROM claim_revisions WHERE claim_id='approval-scope'").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM claim_evidence WHERE claim_id='approval-scope' AND claim_revision=2").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT stance FROM claim_evidence WHERE evidence_id='claim-qualification'").fetchone()[0], "qualify")
        self.rejected("UPDATE claim_revisions SET text='lost old wording' WHERE claim_id='approval-scope' AND revision=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("claim_evidence", {"evidence_id": "nonexistent-revision", "claim_id": "approval-scope", "claim_revision": 9, "locator_id": "review-decision", "stance": "support", "rationale": "Missing claim revision"})

    def test_one_active_assignment_includes_reconciliation(self):
        self.update("assignments", "assignment_id", "assignment-hub", 2, state="reconciling")
        existing = dict(self.db.execute("SELECT * FROM assignments WHERE assignment_id='assignment-hub'").fetchone())
        existing.update(assignment_id="duplicate-lease", state="leased", revision=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("assignments", existing)
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("work_items", "work_id", "work-hub", 3, state="pending", transition_reason="Lease expired")

    def test_unknown_dispatch_prevents_releasing_assignment(self):
        self.insert("operation_links", {"operation_id": "uncertain-call", "assignment_id": "assignment-hub", "budget_event_id": "budget-reserved", "request_sha256": "c" * 64, "execution_state": "unknown", "verification_state": "pending"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("assignments", "assignment_id", "assignment-hub", 2, state="released", finished_at=NOW)
        self.update("operation_links", "operation_id", "uncertain-call", 1, execution_state="cancelled")
        self.assertEqual(self.update("assignments", "assignment_id", "assignment-hub", 2, state="released", finished_at=NOW), 1)

    def test_interrupted_dispatch_also_requires_reconciliation(self):
        self.insert("operation_links", {"operation_id": "interrupted-call", "assignment_id": "assignment-hub", "budget_event_id": "budget-interrupted", "request_sha256": "d" * 64, "execution_state": "interrupted", "verification_state": "pending"})
        self.update("assignments", "assignment_id", "assignment-hub", 2, state="reconciling")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("assignments", "assignment_id", "assignment-hub", 3, state="released", finished_at=NOW)
        self.update("operation_links", "operation_id", "interrupted-call", 1, execution_state="cancelled")
        self.assertEqual(self.update("assignments", "assignment_id", "assignment-hub", 3, state="released", finished_at=NOW), 1)

    def test_generation_and_plan_binding_cannot_rewrite_dispatch(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("assignments", "assignment_id", "assignment-hub", 2, plan_item_id="read-design")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("work_items", "work_id", "work-hub", 3, generation=1)
        self.insert("work_items", {"work_id": "followup", "kind": "search", "question_id": "decision", "request_json": {"query": "Grove"}, "logical_key": "grove-followup", "purpose": "Find earlier-name review", "stage": "priority", "branch": "pilot", "generation": 2, "transition_reason": "Read-derived vocabulary"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("work_items", "work_id", "followup", 1, generation=0)

    def test_read_completion_requires_scope_and_finished_assignment(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("work_items", "work_id", "work-hub", 3, state="completed", terminal_reason="Read body", transition_reason="Read complete")
        self.update("assignments", "assignment_id", "assignment-hub", 2, state="completed", finished_at=NOW)
        self.update("work_items", "work_id", "work-hub", 3, required_scope_key="body-and-comments", transition_reason="Comments are required too")
        with self.assertRaises(sqlite3.IntegrityError):
            self.update("work_items", "work_id", "work-hub", 4, state="completed", terminal_reason="Body only", transition_reason="Wrong completion")
        self.update("work_items", "work_id", "work-hub", 4, required_scope_key="body", transition_reason="Restore original fixture scope")
        self.assertEqual(self.update("work_items", "work_id", "work-hub", 5, state="completed", terminal_reason="Verified body inspection", transition_reason="Completed original extent"), 1)

    def test_partial_and_historical_coverage_is_not_fresh_inspection(self):
        self.insert("reader_returns", {"return_id": "historical-return", "actor_id": "reader", "file_path": "legacy/return.json", "payload_sha256": "c" * 64, "verification_state": "legacy_attested", "limitations": "Original budget receipt unavailable"})
        self.insert("inspections", {"inspection_id": "historical-inspection", "artifact_id": "measurements", "return_id": "historical-return", "actor_id": "reader", "inspection_extent": "Legacy account of body", "method": "Retained attestation", "inspected_at": NOW, "provenance_basis": "prior_run"})
        self.insert("inspection_scopes", {"inspection_id": "historical-inspection", "question_id": "decision", "required_scope_key": "body", "format_policy_sha256": self.policy_hash, "coverage": "sufficient", "basis": "Earlier scope claim, unverified in current run"})
        self.insert("inspection_scopes", {"inspection_id": "inspection-hub", "question_id": "decision", "required_scope_key": "body-and-comments", "format_policy_sha256": self.policy_hash, "coverage": "partial", "basis": "Comments were not captured"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM sufficient_inspection WHERE artifact_id='measurements' OR required_scope_key='body-and-comments'").fetchone()[0], 0)

    def test_source_outcomes_retain_disagreement_and_require_reopening_basis(self):
        base = {"outcome_id": "blocked-visuals", "artifact_id": "design", "question_id": "decision", "required_scope_key": "visuals", "readability": "access_blocked", "reason": "Visual route denied in fixture", "actor_id": "reader", "observed_at": NOW}
        self.insert("source_outcomes", base)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("source_outcomes", dict(base, outcome_id="invalid-reopen", readability="readable", replaces_outcome_id="blocked-visuals"))
        self.insert("source_outcomes", dict(base, outcome_id="other-observation", readability="readable", actor_id="reviewer", reason="A different route returned a preview"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM current_source_outcomes WHERE artifact_id='design'").fetchone()[0], 2)
        self.insert("source_outcomes", dict(base, outcome_id="explicit-reopen", readability="transient_retry_pending", replaces_outcome_id="blocked-visuals", changed_basis="Access permission changed", reason="Retry the original required visual route"))
        self.assertEqual({row[0] for row in self.db.execute("SELECT readability FROM current_source_outcomes WHERE artifact_id='design'")}, {"readable", "transient_retry_pending"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM artifact_question_assessments WHERE artifact_id='design' AND relevance='irrelevant'").fetchone()[0], 0)

    def test_prior_run_identity_has_lineage_without_new_inspection(self):
        before = self.db.execute("SELECT count(*) FROM inspections").fetchone()[0]
        self.insert("prior_run_imports", {"import_id": "prior-map", "prior_run_id": "earlier-fictional-run", "prior_export_id": "export-2", "manifest_sha256": "e" * 64, "source_artifact_id": "hub", "policy": "leads_only", "scope_json": {"branches": ["pilot"]}})
        self.insert("prior_run_records", {"record_id": "prior-design", "import_id": "prior-map", "prior_record_kind": "artifact", "prior_record_id": "old-design-id", "artifact_id": "design", "original_namespace": "archive-file", "original_external_id": "design", "lineage_json": [{"run_id": "earlier-fictional-run", "export_id": "export-2"}]})
        self.insert("discoveries", {"discovery_id": "prior-discovery", "artifact_id": "design", "prior_import_id": "prior-map", "observed_title": "Pilot design"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM discoveries WHERE artifact_id='design'").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM inspections").fetchone()[0], before)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("prior_run_records", {"record_id": "dangling-prior", "import_id": "prior-map", "prior_record_kind": "assertion", "prior_record_id": "missing", "assertion_id": "not-here", "lineage_json": []})

    def test_trunk_query_preserves_unread_children_and_question_scope(self):
        self.load_packet("reconciliation")
        result = self.db.execute("""
            SELECT DISTINCT r.target_artifact_id FROM effective_reference_targets r
            JOIN artifact_roles role ON role.artifact_id=r.from_artifact_id
            JOIN artifact_question_assessments a ON a.artifact_id=r.from_artifact_id
            WHERE role.role='trunk' AND role.disposition='accepted'
              AND a.relevance='relevant' AND a.disposition='accepted' AND a.question_id=?
              AND (role.question_id IS NULL OR role.question_id=?)
              AND r.target_artifact_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM sufficient_inspection i WHERE
                  i.artifact_id=r.target_artifact_id AND i.question_id=?
                  AND i.required_scope_key='body' AND i.format_policy_sha256=?)
            ORDER BY r.target_artifact_id LIMIT 10
        """, ("decision", "decision", "decision", self.policy_hash)).fetchall()
        self.assertEqual([row[0] for row in result], ["measurements"])
        # These are historical scope matches; callers must also apply requested
        # revision/freshness policy before deciding to reuse them.
        self.assertEqual(self.db.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sufficient_inspection WHERE format_policy_sha256=?", ("f" * 64,)).fetchone()[0], 0)

    def test_many_metadata_candidates_do_not_inflate_inspections(self):
        for index in range(2000):
            self.insert("artifacts", {"artifact_id": "candidate-%04d" % index, "title": "Metadata candidate", "artifact_type": "document"})
        first = self.db.execute("SELECT artifact_id FROM artifacts WHERE artifact_id > ? ORDER BY artifact_id LIMIT ?", ("candidate-0099", 25)).fetchall()
        self.assertEqual(len(first), 25)
        self.assertEqual(first[0][0], "candidate-0100")
        self.assertEqual(self.db.execute("SELECT count(*) FROM inspections").fetchone()[0], 3)

    def test_audit_and_original_evidence_are_immutable(self):
        for table in ("graph_events", "ingest_batches", "captures", "reader_returns", "reference_occurrences", "assertions", "claim_revisions"):
            column = self.columns(table)[0]
            self.rejected("UPDATE " + table + " SET " + column + "=" + column)
            self.rejected("DELETE FROM " + table)
        self.rejected("INSERT INTO graph_events VALUES (999, 'observations', 'create', 'artifacts', 'ghost', NULL, 1, '{}')")

    def test_capture_and_return_fixture_hashes_match_retained_bytes(self):
        for path, expected in self.db.execute("SELECT relative_path,sha256 FROM captures UNION ALL SELECT file_path,payload_sha256 FROM reader_returns UNION ALL SELECT relative_path,contract_sha256 FROM contract_snapshots UNION ALL SELECT relative_path,plan_sha256 FROM plan_snapshots"):
            self.assertEqual(hashlib.sha256((FIXTURES / path).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
