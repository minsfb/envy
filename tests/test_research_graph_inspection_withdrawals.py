"""Withdrawal storage/recovery on owned fictional recorder and legacy fixtures."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_research_graph_store as storage_fixture
import test_research_graph_ingest as native_fixture


CRASH = r'''
import importlib.util, os, pathlib, sys
spec = importlib.util.spec_from_file_location("withdrawal_crash", sys.argv[1])
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)
def crash(point):
    if point == sys.argv[3]:
        os._exit(77)
graph._fault = crash
graph.upgrade_schema(pathlib.Path(sys.argv[2]), target_revision=2)
'''


class InspectionWithdrawalStorageTests(unittest.TestCase):
    def legacy(self):
        fixture = storage_fixture.GraphStoreTests()
        with patch.object(storage_fixture.graph, "STORAGE_REVISION", 1):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_legacy_upgrade_preserves_originals_and_replays_exact_history(self):
        fixture = self.legacy()
        graph, root = storage_fixture.graph, fixture.root
        graph.ingest(root, fixture.packet(fixture.seed()))
        (root / "research-budget.json").write_bytes(b'{"events":[]}\n')
        original_files = {p.relative_to(root): p.read_bytes() for p in root.rglob("*")
                          if p.is_file() and not p.name.startswith("research.sqlite")}
        with graph.snapshot(root) as conn:
            self.assertEqual(graph.storage_revision(conn), 1)
            self.assertEqual(graph.withdrawn_inspection_ids(conn), set())
            before = [dict(row) for row in conn.execute("SELECT * FROM graph_events ORDER BY sequence")]
            metadata = dict(conn.execute("SELECT * FROM graph_metadata").fetchone())
        upgraded = graph.upgrade_schema(root, target_revision=2)
        self.assertEqual(upgraded["storage_revision"], 2)
        self.assertTrue(upgraded["upgraded"])
        for relative, data in original_files.items():
            self.assertEqual((root / relative).read_bytes(), data, relative)
        with graph.snapshot(root) as conn:
            events = [dict(row) for row in conn.execute("SELECT * FROM graph_events ORDER BY sequence")]
            self.assertEqual(events[:-1], before)
            self.assertEqual(dict(conn.execute("SELECT * FROM graph_metadata").fetchone()), metadata)
            self.assertEqual(graph.storage_tables(conn), graph.TABLES | graph.AUTOMATIC)
        self.assertTrue(graph.upgrade_schema(root, target_revision=2)["idempotent"])
        self.assertEqual(graph.validate(root)["graph_sequence"], len(events))
        graph.checkpoint(root, "upgraded")
        graph.recover(root, apply=True)
        with graph.snapshot(root) as conn:
            self.assertEqual([dict(row) for row in conn.execute("SELECT * FROM graph_events ORDER BY sequence")], events)
        self.assertEqual(graph.validate(root)["storage_revision"], 2)

    def test_legacy_export_and_checkpoint_remain_exact_after_upgrade(self):
        fixture = self.legacy()
        graph, root = storage_fixture.graph, fixture.root
        exported = fixture.export_files("legacy")
        graph.register_export(root, exported["export_id"], exported["path"], exported["sha256"])
        checkpoint = graph.checkpoint(root, "legacy")
        protected = {p.relative_to(root): p.read_bytes() for base in (root / "graph/exports", root / "graph/checkpoints")
                     for p in base.rglob("*") if p.is_file()}
        graph.upgrade_schema(root, target_revision=2)
        self.assertTrue(graph.register_export(root, exported["export_id"], exported["path"], exported["sha256"])["idempotent"])
        self.assertEqual(graph.checkpoint(root, "legacy")["manifest_sha256"], checkpoint["manifest_sha256"])
        with graph.snapshot(root) as conn:
            historical = graph._export_snapshot_rows(conn, fixture.initial["graph_sequence"])
            self.assertNotIn("inspection_withdrawals", historical)
        graph.recover(root, apply=True)
        for relative, data in protected.items():
            self.assertEqual((root / relative).read_bytes(), data, relative)
        self.assertEqual(graph.validate(root)["status"], "pass")

    def test_upgrade_process_crashes_are_atomic_and_resumable(self):
        graph = storage_fixture.graph
        for point in ("after_packet_fsync", "after_begin", "after_schema_ddl", "after_mutations", "after_commit"):
            with self.subTest(point=point):
                fixture = self.legacy()
                root = fixture.root
                result = subprocess.run([sys.executable, "-I", "-B", "-c", CRASH,
                    str(storage_fixture.MODULE), str(root), point], env={"PATH": os.defpath},
                    cwd=fixture.directory, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 77, result.stderr)
                self.assertEqual(result.stdout + result.stderr, "")
                with graph.snapshot(root) as conn:
                    self.assertEqual(graph.storage_revision(conn), 2 if point == "after_commit" else 1)
                    self.assertEqual("inspection_withdrawals" in graph.storage_tables(conn), point == "after_commit")
                graph.recover(root, "schema-upgrade-inspection-withdrawals-v2", apply=True)
                self.assertEqual(graph.validate(root)["storage_revision"], 2)
                graph.recover(root, apply=True)
                self.assertEqual(graph.validate(root)["storage_revision"], 2)

    def test_pending_migration_cannot_smuggle_an_unrelated_mutation(self):
        fixture = self.legacy()
        graph, root = storage_fixture.graph, fixture.root
        packet = fixture.packet(fixture.seed(), name="schema-upgrade-inspection-withdrawals-v2", kind="migrate")
        (root / "graph/updates/schema-upgrade-inspection-withdrawals-v2.json").write_bytes(storage_fixture.encoded(packet))
        with self.assertRaises(graph.GraphError):
            graph.upgrade_schema(root, target_revision=2)
        with graph.snapshot(root) as conn:
            self.assertEqual(graph.storage_revision(conn), 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)

    def test_ordinary_packets_cannot_create_migration_history(self):
        fixture = self.legacy()
        graph = storage_fixture.graph
        packet = fixture.packet([storage_fixture.insert("schema_migrations", schema_version=2,
            migration_id="research-graph-inspection-withdrawals-v2",
            migration_sha256=graph._sha(graph.INSPECTION_WITHDRAWALS_SCHEMA.read_bytes()), applied_at=graph._now())], kind="migrate")
        fixture.error("permission_denied", graph.ingest, fixture.root, packet)

    def test_concurrent_upgrades_admit_one_migration(self):
        fixture = self.legacy()
        graph = storage_fixture.graph
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(graph.upgrade_schema, [fixture.root, fixture.root]))
        self.assertEqual(sum(result["upgraded"] for result in results), 1)
        with graph.snapshot(fixture.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations WHERE schema_version=2").fetchone()[0], 1)
        self.assertEqual(graph.validate(fixture.root)["status"], "pass")

    def test_malformed_frozen_migrations_raise_bounded_graph_errors(self):
        graph = storage_fixture.graph
        v1 = dict(schema_version=1, migration_id="research-graph-v1", migration_sha256=graph._sha(graph.SCHEMA.read_bytes()))
        malformed = [None, [], {}, {"schema_migrations": None}, {"schema_migrations": [{}]},
                     {"schema_migrations": [dict(v1, schema_version=True)]},
                     {"schema_migrations": [dict(v1, schema_version="1")]},
                     {"schema_migrations": [v1, v1]}, {"schema_migrations": [dict(v1, migration_sha256="0" * 64)]},
                     {"schema_migrations": [v1, dict(v1, schema_version=2)]}]
        for tables in malformed:
            with self.subTest(tables=tables), self.assertRaises(graph.GraphError) as error:
                graph.schema_revision_from_tables(tables)
            self.assertEqual(error.exception.code, "unsupported_schema")


class InspectionWithdrawalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_fixture.ResearchGraphIngestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.graph, self.root = native_fixture.GRAPH, self.fixture.root
        self.fixture.operation()
        native_fixture.INGEST.index_operation(self.root, "read-one")
        returned = self.fixture.returned()
        native_fixture.INGEST.ingest_reader_return(self.root, returned["id"])
        self.inspection = self.fixture.rows("inspections")[0]
        self.evidence = "reviews/withdrawal.json"
        path = self.root / self.evidence
        path.parent.mkdir()
        path.write_text('{"basis":"Fictional reader retracts the declared inspection."}\n')

    def withdrawal(self, **changes):
        record = dict(withdrawal_id="withdrawal-one", inspection_id=self.inspection["inspection_id"],
            actor_id="coordinator", reason="Reader corrected the declared inspection", evidence_path=self.evidence,
            evidence_sha256=self.graph._sha((self.root / self.evidence).read_bytes()),
            withdrawn_at=datetime.now(timezone.utc).isoformat())
        record.update(changes)
        return native_fixture.INGEST._insert("inspection_withdrawals", **record)

    def test_withdrawal_is_append_only_and_removes_only_effective_credit(self):
        original = (self.root / "reader-returns/reader-one.json").read_bytes()
        budget = (self.root / "research-budget.json").read_bytes()
        before = {name: self.fixture.rows(name) for name in ("inspections", "inspection_scopes", "inspection_captures", "reference_occurrences", "leads")}
        with self.graph.snapshot(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 1)
        mutation = self.withdrawal()
        self.fixture.packet("withdraw", [mutation], kind="reconcile")
        self.assertTrue(self.graph.ingest(self.root, self.root / "graph/updates/withdraw.json")["idempotent"])
        with self.graph.snapshot(self.root) as conn:
            self.assertEqual(self.graph.withdrawn_inspection_ids(conn), {self.inspection["inspection_id"]})
            self.assertEqual(conn.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM effective_inspections").fetchone()[0], 0)
        for name, rows in before.items():
            self.assertEqual(self.fixture.rows(name), rows)
        self.assertEqual((self.root / "reader-returns/reader-one.json").read_bytes(), original)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)
        with self.assertRaises(self.graph.GraphError):
            self.fixture.packet("duplicate", [self.withdrawal(withdrawal_id="another")], kind="reconcile")
        with sqlite3.connect(self.root / "graph/research.sqlite") as conn:
            for sql in ("DELETE FROM inspection_withdrawals", "UPDATE inspection_withdrawals SET reason='changed'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
        self.graph.recover(self.root, apply=True)
        self.assertEqual(self.graph.validate(self.root)["status"], "pass")

    def test_withdrawal_requires_coordinator_reconciliation_and_retained_basis(self):
        for name, changes, kind, code in (
            ("observation", {}, "observe", "permission_denied"),
            ("other-actor", {"actor_id": "reader"}, "reconcile", "permission_denied"),
            ("bad-hash", {"evidence_sha256": "0" * 64}, "reconcile", "integrity_error"),
            ("unknown", {"inspection_id": "unknown-inspection"}, "reconcile", "unknown_identity"),
            ("before-reading", {"withdrawn_at": "2001-01-01T00:00:00Z"}, "reconcile", "scope_mismatch"),
        ):
            with self.subTest(name=name), self.assertRaises(self.graph.GraphError) as error:
                self.fixture.packet(name, [self.withdrawal(**changes)], kind=kind)
            self.assertEqual(error.exception.code, code)
        packet = dict(schema_version=1, batch_id="worker-withdrawal", actor_id="reader", assignment_id="assignment-inspect",
            packet_kind="reconcile", contract_sha256=self.fixture.contract_hash, submitted_at=self.graph._now(),
            mutations=[self.withdrawal(actor_id="reader")])
        with self.assertRaises(self.graph.GraphError) as error:
            self.graph.ingest(self.root, packet)
        self.assertEqual(error.exception.code, "permission_denied")
        self.fixture.packet("withdraw", [self.withdrawal()], kind="reconcile")
        (self.root / self.evidence).write_text("changed basis\n")
        with self.assertRaises(self.graph.GraphError) as error:
            self.graph.validate(self.root)
        self.assertEqual(error.exception.code, "integrity_error")

    def test_withdrawn_inspection_cannot_support_new_evidence(self):
        self.fixture.packet("withdraw", [self.withdrawal()], kind="reconcile")
        evidence = dict(self.fixture.rows("assertion_evidence")[0])
        evidence.pop("created_batch_id")
        evidence["evidence_id"] = "new-inspected-support"
        with self.assertRaises(self.graph.GraphError) as error:
            self.fixture.packet("new-support", [native_fixture.INGEST._insert("assertion_evidence", **evidence)],
                                kind="reconcile", validator=native_fixture.INGEST.evidence_validator)
        self.assertEqual(error.exception.code, "scope_mismatch")

    def accepted_decision(self, assertion_id, decision_id, expected_revision=0):
        return native_fixture.INGEST._insert("assertion_decisions", assertion_id=assertion_id,
            decision_id=decision_id, disposition="accepted", reason="Synthetic inspected assessment",
            actor_id="coordinator", expected_revision=expected_revision)

    def test_finding_cannot_be_reaccepted_after_withdrawal_preserving_old_decision(self):
        assertion = self.fixture.rows("assertions")[0]
        self.assertEqual(assertion["kind"], "finding")
        self.fixture.packet("accept-finding", [self.accepted_decision(assertion["assertion_id"], "original-acceptance")], kind="reconcile")
        historical = self.fixture.rows("assertion_decisions")
        self.fixture.packet("withdraw", [self.withdrawal()], kind="reconcile")
        self.assertEqual(self.fixture.rows("assertion_decisions"), historical)
        with self.assertRaises(self.graph.GraphError) as error:
            self.fixture.packet("reaccept-finding", [self.accepted_decision(assertion["assertion_id"], "new-acceptance", 1)], kind="reconcile")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.fixture.rows("assertion_decisions"), historical)
        self.graph.recover(self.root, apply=True)
        self.assertEqual(self.fixture.rows("assertion_decisions"), historical)

    def test_acceptance_before_withdrawal_in_same_packet_rolls_back(self):
        assertion = self.fixture.rows("assertions")[0]
        with self.assertRaises(self.graph.GraphError) as error:
            self.fixture.packet("accept-then-withdraw", [self.accepted_decision(assertion["assertion_id"], "new-acceptance"),
                                                       self.withdrawal()], kind="reconcile")
        self.assertEqual(error.exception.code, "scope_mismatch")
        self.assertEqual(self.fixture.rows("assertion_decisions"), [])
        self.assertEqual(self.fixture.rows("inspection_withdrawals"), [])
        with self.graph.snapshot(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sufficient_inspection").fetchone()[0], 1)

    def test_role_acceptance_cannot_depend_on_withdrawn_inspection(self):
        draft = self.fixture.candidate("role-reading")
        draft["graph"]["assertions"][0].update(kind="role", predicate="has_role", value="trunk")
        native_fixture.BUDGET.reader_return(self.root, draft)
        native_fixture.INGEST.ingest_reader_return(self.root, draft["id"])
        assertion = next(row for row in self.fixture.rows("assertions") if row["kind"] == "role")
        self.inspection = next(row for row in self.fixture.rows("inspections") if row["return_id"] == draft["id"])
        self.fixture.packet("accept-role", [self.accepted_decision(assertion["assertion_id"], "original-role-acceptance")], kind="reconcile")
        self.fixture.packet("withdraw-role", [self.withdrawal()], kind="reconcile")
        with self.assertRaises(self.graph.GraphError) as error:
            self.fixture.packet("reaccept-role", [self.accepted_decision(assertion["assertion_id"], "new-role-acceptance", 1)], kind="reconcile")
        self.assertEqual(error.exception.code, "scope_mismatch")


if __name__ == "__main__":
    unittest.main()
