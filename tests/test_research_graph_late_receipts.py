"""Late original receipts and explicit storage upgrades, using owned fixtures.

All public recorder hooks remain active. Faults interrupt local persistence;
children inherit only PATH and execute owned fictional files without providers.
"""

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_research_graph_work as fixture
import test_research_graph_store as storage_fixture


WORK, GRAPH, INGEST, BUNDLE, BUDGET = fixture.WORK, fixture.GRAPH, fixture.INGEST, fixture.BUNDLE, fixture.BUDGET
EXPORTS = INGEST._sibling("research_graph_exports")


CRASH = r'''
import importlib.util, os, pathlib, sys
spec = importlib.util.spec_from_file_location("research_graph_work", sys.argv[1])
work = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = work
spec.loader.exec_module(work)
def crash(point):
    if point == sys.argv[3]:
        os._exit(77)
work.graph._fault = crash
root = pathlib.Path(sys.argv[2])
if sys.argv[4] == "upgrade":
    work.graph.upgrade_schema(root)
else:
    work.bundle.import_output(root, "read-one", pathlib.Path(sys.argv[4]), None, "ok")
'''


class LateReceiptTests(unittest.TestCase):
    source = fixture.ResearchGraphWorkTests.source
    item = fixture.ResearchGraphWorkTests.item
    save_plan = fixture.ResearchGraphWorkTests.save_plan
    rows = fixture.ResearchGraphWorkTests.rows
    row = fixture.ResearchGraphWorkTests.row
    binding = fixture.ResearchGraphWorkTests.binding
    claim = fixture.ResearchGraphWorkTests.claim
    spec = fixture.ResearchGraphWorkTests.spec

    def setUp(self):
        self.setup_revision(3)

    def setup_revision(self, revision):
        hooks = {name: getattr(BUNDLE, name) for name in ("sync_graph_work", "sync_graph_evidence", "sync_graph_plan")}
        with patch.object(GRAPH, "STORAGE_REVISION", revision):
            fixture.ResearchGraphWorkTests.setUp(self)
        # The shared fixture disables hooks for its manual-seam cases. These
        # regressions restore the actual functions before any operation exists.
        for name, original in hooks.items():
            wrapper = patch.object(BUNDLE, name, original)
            wrapper.start()
            self.addCleanup(wrapper.stop)

    def cancelled(self, restored=False):
        assignment = self.claim()
        spec = self.spec(assignment)
        if restored:
            writer = BUNDLE.write
            def interrupted(path, data):
                if path.name == "request.json":
                    raise OSError("Owned request-persistence interruption")
                return writer(path, data)
            with patch.object(BUNDLE, "write", side_effect=interrupted), self.assertRaises(OSError):
                BUNDLE.start(self.root, spec, "tool-import")
            WORK.recover_admission(self.root, "read-one", worker_identity="reader", worker_stopped=True,
                                   reason="Original fixture worker checked stopped")
        else:
            BUNDLE.start(self.root, spec, "tool-import")
            WORK.record_dispatch(self.root, "read-one")
            WORK.reconcile_operation(self.root, "read-one", expected_revision=2, worker_identity="reader",
                                     worker_stopped=True, cancel=True, reason="Stopped worker; original output has not arrived")
        WORK.recover_retry(self.root, "read-one", expected_revision=self.row("operation_links", "read-one")["revision"],
                           reason="Observed local result synchronization failure")
        self.original_events = self.rows("graph_events")
        self.original_budget = (self.root / "research-budget.json").read_bytes()
        self.original_request = (self.root / "operations/read-one/request.json").read_bytes()
        return assignment

    def index(self, outcome="ok"):
        result = dict(source_ids=["doc-a"], pagination="complete", next_cursor=None, note="Exact original fictional output")
        if outcome == "ok":
            result["captures"] = [dict(id="html", source_id="doc-a", format="html",
                relative_path="operations/read-one/stdout", extent=copy.deepcopy(fixture.EXTENT),
                extraction_status="complete", limitations="Owned fictional lines")]
        return result

    def returned(self, assignment):
        work = self.row("work_items", self.row("assignments", assignment)["work_id"])
        return dict(id="late-return", read_id="read-one", actor="reader", graph_assignment_id=assignment,
            inspection="partial", relevance="relevant", coverage="Inspected retained body lines",
            expansion_review="No other fixture links", leads=[], graph=dict(inspected_at=GRAPH._now(), locators=[], scans=[], assertions=[],
                inspections=[dict(id="body", source_id="doc-a", extent="Two fictional body lines", method="Actual fixture inspection attestation",
                    limitations="Meaning remains a reader attestation", captures=[dict(capture_id="html", extent=copy.deepcopy(fixture.EXTENT))],
                    scopes=[dict(question_id=work["question_id"], required_scope_key=work["required_scope_key"],
                                 format_policy_sha256=work["format_policy_sha256"], coverage="sufficient", basis="Both retained lines inspected")])]))

    def assert_preserved(self, state):
        self.assertEqual(self.row("operation_links", "read-one")["execution_state"], state)
        self.assertEqual(self.rows("graph_events")[:len(self.original_events)], self.original_events)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), self.original_budget)
        self.assertEqual((self.root / "operations/read-one/request.json").read_bytes(), self.original_request)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)
        self.assertEqual(GRAPH.validate(self.root)["status"], "pass")

    def test_automatic_success_import_index_and_return_require_separate_inspection(self):
        assignment = self.cancelled()
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        self.assert_preserved("succeeded")
        self.assertEqual(self.rows("inspections"), [])
        self.assertEqual(BUNDLE.index_result(self.root, "read-one", self.index())["state"], "complete")
        self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
        self.assert_preserved("succeeded")
        with self.assertRaises(GRAPH.GraphError) as error:
            WORK.complete_work(self.root, assignment, expected_revision=2, reason="Receipt alone is insufficient")
        self.assertEqual(error.exception.code, "pending_dependency")
        retry = self.spec(assignment, "forbidden-retry")
        retry["research"]["retry_of"] = "read-one"
        with self.assertRaisesRegex(ValueError, "retry requires observed transient failure"):
            BUNDLE.start(self.root, retry, "tool-import")
        BUNDLE.record_reader_return(self.root, self.returned(assignment))
        WORK.complete_work(self.root, assignment, expected_revision=2, reason="Original retained output actually inspected")
        self.assertEqual(self.row("assignments", assignment)["state"], "completed")
        self.assertEqual(len(self.rows("inspections")), 1)
        self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
        old = json.loads(self.original_budget)["events"]
        self.assertEqual(BUNDLE.read(self.root / "research-budget.json")["events"][:len(old)], old)
        self.assertEqual(BUDGET.status(self.root)["read_attempts"], 1)

    def test_automatic_failed_receipt_preserves_spending_without_completion_credit(self):
        assignment = self.cancelled()
        BUNDLE.import_output(self.root, "read-one", self.output, None, "error")
        BUNDLE.index_result(self.root, "read-one", self.index("error"))
        self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
        self.assert_preserved("failed")
        state = BUNDLE.read(self.root / "research-budget.json")
        admission = next(e for e in state["events"] if e["type"] == "admit")
        self.assertEqual(BUDGET.classification(self.root, state, admission), "unknown")
        self.assertEqual(self.rows("captures"), [])
        self.assertEqual(self.rows("inspections"), [])
        with self.assertRaises(GRAPH.GraphError):
            WORK.complete_work(self.root, assignment, expected_revision=2, reason="Failed retrieval cannot complete reading")

    def test_restored_intent_late_receipt_never_manufactures_dispatch(self):
        self.cancelled(restored=True)
        proof = (self.root / "admission-recoveries/read-one.json").read_bytes()
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        BUNDLE.index_result(self.root, "read-one", self.index())
        self.assert_preserved("succeeded")
        self.assertEqual((self.root / "admission-recoveries/read-one.json").read_bytes(), proof)
        self.assertFalse(any(e["entity_kind"] == "operation_links" and e["payload_json"]["after"]["execution_state"] == "dispatched"
                             for e in self.rows("graph_events")))
        with self.assertRaises(GRAPH.GraphError):
            WORK.record_dispatch(self.root, "read-one")

    def test_completion_is_idempotent_and_concurrent(self):
        self.cancelled()
        self.retain_before_completion()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: WORK.record_completion(self.root, "read-one"), range(2)))
        self.assertEqual(sum(bool(r["idempotent"]) for r in results), 1)
        events = self.rows("graph_events")
        self.assertTrue(WORK.record_completion(self.root, "read-one")["idempotent"])
        self.assertEqual(self.rows("graph_events"), events)
        self.assert_preserved("succeeded")

    def retain_before_completion(self):
        def interrupted(point):
            if point == "after_packet_fsync":
                raise GRAPH.GraphError("integrity_error", "Owned completion persistence interruption")
        with patch.object(GRAPH, "_fault", side_effect=interrupted), self.assertRaises(GRAPH.GraphError):
            BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        self.assertEqual(self.row("operation_links", "read-one")["execution_state"], "cancelled")
        self.assertTrue((self.root / "operations/read-one/receipt.json").is_file())

    def test_missing_tampered_and_mismatched_originals_are_rejected(self):
        for kind in ("request", "stdout", "stderr", "missing-output", "receipt-request", "receipt-output", "outcome", "provenance", "admission"):
            with self.subTest(kind=kind):
                self.setup_revision(3)
                self.cancelled()
                self.retain_before_completion()
                directory = self.root / "operations/read-one"
                if kind in {"request", "stdout", "stderr"}:
                    path = directory / ("request.json" if kind == "request" else kind)
                    path.write_bytes(path.read_bytes() + b"tampered")
                elif kind == "missing-output":
                    (directory / "stdout").unlink()
                elif kind == "admission":
                    path = self.root / "research-budget.json"
                    state = BUNDLE.read(path)
                    next(e for e in state["events"] if e["type"] == "admit")["fingerprint"] = "0" * 64
                    BUNDLE.write(path, state)
                else:
                    path = directory / "receipt.json"
                    receipt = BUNDLE.read(path)
                    if kind.startswith("receipt-"):
                        receipt["files"]["request.json" if kind == "receipt-request" else "stdout"] = "0" * 64
                    else:
                        receipt[kind] = "invalid"
                    BUNDLE.write(path, receipt)
                before = self.rows("graph_events")
                with self.assertRaises((GRAPH.GraphError, OSError)):
                    WORK.record_completion(self.root, "read-one")
                self.assertEqual(self.rows("graph_events"), before)
                self.assertEqual(self.row("operation_links", "read-one")["execution_state"], "cancelled")
                self.assertTrue((directory / "receipt.json").exists())

    def test_raw_packet_cannot_reconcile_without_real_receipt_or_stop_proof(self):
        self.cancelled()
        with GRAPH.snapshot(self.root) as conn:
            operation = GRAPH._get(conn, "operation_links", "read-one")
            packet = WORK._packet(self.root, conn, [WORK._update("operation_links", operation,
                execution_state="succeeded", receipt_sha256="0" * 64)])
        with self.assertRaises((GRAPH.GraphError, OSError)):
            GRAPH.ingest(self.root, packet, evidence_validator=WORK.evidence_validator)
        self.retain_before_completion()
        # Remove the stop evidence in a private in-memory view, leaving the real
        # accepted graph untouched. Both public validator adapters must refuse it.
        with GRAPH.snapshot(self.root) as conn:
            tables = {t: [GRAPH._decoded(r) for r in conn.execute("SELECT * FROM " + t)] for t in GRAPH.storage_tables(conn)}
        for event in tables["graph_events"]:
            after = event["payload_json"]["after"]
            if after.get("transition_reason", "").startswith("Worker reconciliation: "):
                after["transition_reason"] = "No worker-stop evidence"
        memory = EXPORTS._frozen_connection(tables)
        self.addCleanup(memory.close)
        original = WORK._admission(self.root, "read-one", True)
        mutation = dict(op="update", table="operation_links", before=operation,
                        after=dict(operation, **WORK._operation_row(original, "succeeded")))
        for validator in (WORK.evidence_validator, INGEST.evidence_validator):
            with self.assertRaises(GRAPH.GraphError) as error:
                validator(self.root, memory, packet, mutation)
            self.assertEqual(error.exception.code, "pending_dependency")

    def test_old_storage_requires_explicit_upgrade_and_index_can_reconcile(self):
        for revision in (1, 2):
            with self.subTest(revision=revision):
                self.setup_revision(revision)
                self.cancelled()
                with self.assertRaises(GRAPH.GraphError) as error:
                    BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
                self.assertEqual(error.exception.code, "unsupported_schema")
                self.assertIn("upgrade-schema", str(error.exception))
                originals = {p.name: p.read_bytes() for p in (self.root / "operations/read-one").iterdir()}
                self.assertEqual(set(originals), {"request.json", "receipt.json", "stdout", "stderr"})
                self.assert_preserved("cancelled")
                with GRAPH.snapshot(self.root) as conn:
                    self.assertEqual(GRAPH.storage_revision(conn), revision)
                GRAPH.recover(self.root, apply=True)
                with GRAPH.snapshot(self.root) as conn:
                    self.assertEqual(GRAPH.storage_revision(conn), revision)
                self.assertEqual(GRAPH.upgrade_schema(self.root)["storage_revision"], 3)
                BUNDLE.index_result(self.root, "read-one", self.index())
                self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
                self.assert_preserved("succeeded")
                for name, data in originals.items():
                    self.assertEqual((self.root / "operations/read-one" / name).read_bytes(), data)

    def test_direct_sync_reconciles_retained_receipt_and_index(self):
        self.cancelled()
        self.retain_before_completion()
        # Model a recorder interruption after its original result was retained.
        result = BUNDLE.prepare_index_result(self.root, "read-one", self.index())
        BUNDLE.write(self.root / "operations/read-one/result.json", result)
        self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
        self.assert_preserved("succeeded")

    def test_old_pending_normalization_resumes_after_explicit_upgrade(self):
        self.setup_revision(2)
        self.cancelled()
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        prepared = BUNDLE.prepare_index_result(self.root, "read-one", self.index())
        BUNDLE.write(self.root / "operations/read-one/result.json", prepared)
        pending = BUNDLE.sync_graph(self.root)
        self.assertEqual(pending["status"], "fail")
        self.assertEqual(pending["attempted"][0]["error"]["code"], "unsupported_schema")
        retained = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "graph/normalizations").rglob("*") if p.is_file()}
        self.assertTrue(retained)
        GRAPH.upgrade_schema(self.root)
        self.assertTrue(BUNDLE.sync_graph(self.root)["complete"])
        for path, data in retained.items():
            self.assertEqual((self.root / path).read_bytes(), data)
        self.assert_preserved("succeeded")

    def test_late_original_after_reassignment_cannot_complete_replacement(self):
        original = self.cancelled()
        WORK.release_assignment(self.root, original, expected_revision=2, reason="Stopped original worker released")
        replacement = self.claim()
        self.assertNotEqual(original, replacement)
        BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
        BUNDLE.index_result(self.root, "read-one", self.index())
        self.assertEqual(self.row("operation_links", "read-one")["assignment_id"], original)
        self.assert_preserved("succeeded")
        with self.assertRaises(GRAPH.GraphError) as error:
            WORK.complete_work(self.root, replacement, expected_revision=2, reason="Old receipt is not a new inspection")
        self.assertEqual(error.exception.code, "pending_dependency")

    def test_import_process_crashes_replay_and_preserve_exact_receipt(self):
        for point in ("after_packet_fsync", "after_begin", "after_mutations", "after_commit"):
            with self.subTest(point=point):
                self.setup_revision(3)
                self.cancelled()
                child = subprocess.run([sys.executable, "-I", "-B", "-c", CRASH,
                    str(fixture.SCRIPTS / "research_graph_work.py"), str(self.root), point, str(self.output)],
                    env={"PATH": os.defpath}, cwd=self.directory, capture_output=True, text=True, timeout=20)
                self.assertEqual(child.returncode, 77, child.stderr)
                self.assertEqual(child.stdout + child.stderr, "")
                receipt = (self.root / "operations/read-one/receipt.json").read_bytes()
                self.assert_preserved("succeeded" if point == "after_commit" else "cancelled")
                GRAPH.recover(self.root, apply=True)
                WORK.record_completion(self.root, "read-one")
                GRAPH.checkpoint(self.root, "late")
                events = self.rows("graph_events")
                GRAPH.recover(self.root, apply=True)
                self.assertEqual(self.rows("graph_events"), events)
                self.assertEqual((self.root / "operations/read-one/receipt.json").read_bytes(), receipt)
                self.assert_preserved("succeeded")

    def test_frozen_cancellation_and_late_snapshots_remain_exact(self):
        for revision in (1, 2, 3):
            with self.subTest(revision=revision):
                self.setup_revision(revision)
                self.cancelled()
                old = Path(EXPORTS.export_snapshot(self.root, "cancelled")["manifest_path"]).parent
                original = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
                GRAPH.upgrade_schema(self.root)
                BUNDLE.import_output(self.root, "read-one", self.output, None, "ok")
                BUNDLE.index_result(self.root, "read-one", self.index())
                current = Path(EXPORTS.export_snapshot(self.root, "late")["manifest_path"]).parent
                self.assertEqual(BUNDLE.read(old / "ingestion.json")["items"][0]["state"], "cancelled")
                self.assertTrue(BUNDLE.read(current / "ingestion.json")["complete"])
                self.assertEqual(BUNDLE.read(current / "graph.json")["tables"]["operation_links"][0]["execution_state"], "succeeded")
                EXPORTS.verify_export(old)
                EXPORTS.verify_export(current)
                GRAPH.recover(self.root, apply=True)
                GRAPH.validate(self.root)
                for relative, data in original.items():
                    self.assertEqual((old / relative).read_bytes(), data)


class LateReceiptStorageTests(unittest.TestCase):
    def fixture(self, revision):
        value = storage_fixture.GraphStoreTests()
        with patch.object(storage_fixture.graph, "STORAGE_REVISION", revision):
            value.setUp()
        self.addCleanup(value.doCleanups)
        return value

    def test_all_revisions_preserve_exact_snapshots_checkpoints_and_migrations(self):
        graph = storage_fixture.graph
        for revision in (1, 2, 3):
            with self.subTest(revision=revision):
                value = self.fixture(revision)
                exported = value.export_files("original")
                graph.register_export(value.root, exported["export_id"], exported["path"], exported["sha256"])
                checkpoint = graph.checkpoint(value.root, "original")
                protected = {p.relative_to(value.root): p.read_bytes() for base in (value.root / "graph/exports", value.root / "graph/checkpoints")
                             for p in base.rglob("*") if p.is_file()}
                with graph.snapshot(value.root) as conn:
                    before = [dict(r) for r in conn.execute("SELECT * FROM graph_events ORDER BY sequence")]
                    old_migrations = [dict(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY schema_version")]
                upgraded = graph.upgrade_schema(value.root)
                self.assertEqual(upgraded["storage_revision"], 3)
                self.assertEqual(upgraded["upgraded"], revision != 3)
                graph.recover(value.root, apply=True)
                with graph.snapshot(value.root) as conn:
                    events = [dict(r) for r in conn.execute("SELECT * FROM graph_events ORDER BY sequence")]
                    self.assertEqual(events[:len(before)], before)
                    migrations = [dict(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY schema_version")]
                    self.assertEqual(migrations[:revision], old_migrations)
                    self.assertEqual([r["schema_version"] for r in migrations], [1, 2, 3])
                    self.assertEqual(graph._storage_revision_at(conn, value.initial["graph_sequence"]), revision)
                    frozen = graph._export_snapshot_rows(conn, value.initial["graph_sequence"])
                    self.assertEqual("inspection_withdrawals" in frozen, revision >= 2)
                self.assertEqual(graph.checkpoint(value.root, "original")["manifest_sha256"], checkpoint["manifest_sha256"])
                self.assertTrue(graph.register_export(value.root, exported["export_id"], exported["path"], exported["sha256"])["idempotent"])
                for relative, data in protected.items():
                    self.assertEqual((value.root / relative).read_bytes(), data)
                self.assertEqual(graph.validate(value.root)["status"], "pass")

    def test_revision_three_upgrade_crashes_are_atomic_and_recoverable(self):
        graph = storage_fixture.graph
        for point in ("after_packet_fsync", "after_begin", "after_schema_ddl", "after_mutations", "after_commit"):
            with self.subTest(point=point):
                value = self.fixture(2)
                child = subprocess.run([sys.executable, "-I", "-B", "-c", CRASH,
                    str(fixture.SCRIPTS / "research_graph_work.py"), str(value.root), point, "upgrade"],
                    env={"PATH": os.defpath}, cwd=value.directory, capture_output=True, text=True, timeout=20)
                self.assertEqual(child.returncode, 77, child.stderr)
                self.assertEqual(child.stdout + child.stderr, "")
                self.assertEqual(graph.validate(value.root)["storage_revision"], 3 if point == "after_commit" else 2)
                graph.recover(value.root, "schema-upgrade-late-receipts-v3", apply=True)
                self.assertEqual(graph.validate(value.root)["storage_revision"], 3)
                graph.recover(value.root, apply=True)
                self.assertEqual(graph.validate(value.root)["storage_revision"], 3)

    def test_concurrent_upgrade_and_explicit_older_retry_preserve_destination(self):
        graph = storage_fixture.graph
        value = self.fixture(1)
        graph.recover(value.root, "schema-upgrade-inspection-withdrawals-v2", apply=True)
        self.assertEqual(graph.validate(value.root)["storage_revision"], 2)
        packet = (value.root / "graph/updates/schema-upgrade-inspection-withdrawals-v2.json").read_bytes()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(graph.upgrade_schema, [value.root, value.root]))
        self.assertEqual(sum(r["upgraded"] for r in results), 1)
        self.assertEqual(graph.validate(value.root)["storage_revision"], 3)
        self.assertEqual((value.root / "graph/updates/schema-upgrade-inspection-withdrawals-v2.json").read_bytes(), packet)
        with graph.snapshot(value.root) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE schema_version=3").fetchone()[0], 1)

    def test_revision_three_migration_rejects_wrong_identity_and_raw_creation(self):
        graph = storage_fixture.graph
        value = self.fixture(2)
        migration = storage_fixture.insert("schema_migrations", schema_version=3,
            migration_id="research-graph-late-receipts-v3", migration_sha256=graph._sha(graph.LATE_RECEIPTS_SCHEMA.read_bytes()),
            applied_at=graph._now())
        packet = value.packet([migration], name="schema-upgrade-late-receipts-v3", kind="migrate")
        value.error("permission_denied", graph.ingest, value.root, packet)
        migration["record"]["migration_sha256"] = "0" * 64
        path = value.root / "graph/updates/schema-upgrade-late-receipts-v3.json"
        path.write_bytes(storage_fixture.encoded(packet))
        with self.assertRaises(graph.GraphError) as error:
            graph.upgrade_schema(value.root)
        self.assertEqual(error.exception.code, "unsupported_schema")
        self.assertEqual(graph.validate(value.root)["storage_revision"], 2)


if __name__ == "__main__":
    unittest.main()
