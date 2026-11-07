#!/usr/bin/env python3
"""Isolated G1 storage, authorization, concurrency and real process-crash tests.

All bundles are owned temporary fixtures. Child interpreters receive only PATH;
no providers, user accounts, credentials, or budget admissions are exercised.
Native recorder acceptance belongs to the G2 integration suite.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "assets/agent-assets/skills/retrospective-research/scripts/research_graph.py"
spec = importlib.util.spec_from_file_location("research_graph_store_test_module", MODULE)
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def insert(table, **record):
    return {"op": "insert", "table": table, "record": record}


def update(table, identity, revision, **changes):
    return {"op": "update", "table": table, "id": identity, "expected_revision": revision, "changes": changes}


def storage_lifecycle(root, connection, packet, mutation):
    """Only storage fixtures without any recorder admission use this adapter."""
    if mutation["table"] not in {"assignments", "work_items"} or (root / "research-budget.json").exists() or connection.execute("SELECT 1 FROM operation_links").fetchone():
        raise AssertionError("Storage-only lifecycle adapter cannot attest native execution")
    return True


CRASH_PROCESS = r'''
import importlib.util, json, os, pathlib, sys
spec = importlib.util.spec_from_file_location("graph_crash_fixture", sys.argv[1])
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)
def crash(point):
    if point == sys.argv[4]:
        os._exit(77)
graph._fault = crash
root = pathlib.Path(sys.argv[2])
if sys.argv[3] == "ingest":
    graph.ingest(root, pathlib.Path(sys.argv[5]))
elif sys.argv[3] == "init":
    graph.initialize(root, "coordinator", "fixture-run")
elif sys.argv[3] == "checkpoint":
    graph.checkpoint(root, "crashed-checkpoint")
elif sys.argv[3] == "export":
    record = json.loads(pathlib.Path(sys.argv[5]).read_bytes())
    graph.register_export(root, record["export_id"], record["path"], record["sha256"])
else:
    graph.recover(root, apply=True)
'''


class GraphStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="envy-graph-store-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.serial = 0
        self.root = self.bundle("main")
        self.initial = graph.initialize(self.root, "coordinator", "fixture-run")

    def bundle(self, name):
        root = self.directory / name
        root.mkdir()
        for directory in ("contracts", "operations", "reader-returns", "plans"):
            (root / directory).mkdir()
        contract = {"version": 1, "purpose": "Explain the fictional Orchard pilot.",
                    "scope": "Synthetic local records only.", "requirements_source": "G1 fixture",
                    "requirements": {"candidate_table": True},
                    "providers": {"archive": {"read_formats": ["html"], "basis": "Fictional adapter"}},
                    "questions": [{"id": "why", "text": "Why was the pilot paused?", "central": True},
                                  {"id": "outcomes", "text": "Were outcomes measured?", "central": False}]}
        data = encoded(contract)
        (root / "contract.json").write_bytes(data)
        (root / "contracts" / (digest(data) + ".json")).write_bytes(data)
        (root / "bundle.json").write_bytes(encoded({"version": 1, "contract_sha256": digest(data)}))
        return root

    def packet(self, mutations, name=None, actor="coordinator", assignment=None, kind="observe", root=None):
        self.serial += 1
        return {"schema_version": 1, "batch_id": name or "batch-%d" % self.serial,
                "actor_id": actor, "assignment_id": assignment, "packet_kind": kind,
                "contract_sha256": digest(((root or self.root) / "contract.json").read_bytes()),
                "submitted_at": "2001-01-01T00:00:00Z", "mutations": mutations}

    def seed(self, identity="pilot"):
        return [insert("artifacts", artifact_id=identity, title="Fictional " + identity, artifact_type="document"),
                insert("artifact_keys", namespace="archive-file", external_id=identity, artifact_id=identity, basis="Stable fictional ID"),
                insert("discoveries", discovery_id="discovery-" + identity, artifact_id=identity,
                       supplied_seed_id="intake-" + identity, observed_title="Fictional " + identity)]

    def row(self, table, identity, root=None):
        with graph.snapshot(root or self.root) as connection:
            key = {"artifacts": "artifact_id", "leads": "lead_id", "work_items": "work_id",
                   "followup_obligations": "obligation_id", "assignments": "assignment_id"}[table]
            result = connection.execute("SELECT * FROM " + table + " WHERE " + key + "=?", (identity,)).fetchone()
            return dict(result) if result else None

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(graph.GraphError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.response("test"))
        return caught.exception

    def test_whole_component_requires_attested_complete_numeric_capture(self):
        body = {"unit": "line", "ranges": [{"start": 1, "end": 8}], "complete": True,
                "attestation": "Adapter reports the complete body representation.", "limitations": "Comments and visuals were not exposed."}
        captured = {"body": body}
        actual = {"body": {"unit": "line", "ranges": [{"start": 1, "end": 4}, {"start": 5, "end": 8}]}}
        self.assertTrue(graph.satisfies_scope(captured, actual, {"body": "all"}))
        for incomplete in ({"body": {"unit": "line", "ranges": [{"start": 1, "end": 7}]}},
                           {"body": {"unit": "line", "ranges": [{"start": 1, "end": 4}, {"start": 6, "end": 8}]}},
                           {"body": "all"}):
            self.assertFalse(graph.satisfies_scope(captured, incomplete, {"body": "all"}))
        self.assertFalse(graph.satisfies_scope({"body": dict(body, complete=False)}, actual, {"body": "all"}))
        self.assertFalse(graph.satisfies_scope(captured, actual, {"body": "all", "comments": "all"}))
        self.assertFalse(graph.satisfies_scope(captured, actual, {"body": {"attestation": "entire archive text", "limitations": "Opaque original request"}}))
        self.assertFalse(graph.satisfies_scope({"body": "all"}, {"body": "all"}, {"body": "all"}))
        for changes in ({"complete": "yes"}, {"attestation": ""}, {"limitations": ""}, {"ranges": []}):
            self.error("invalid_packet", graph._extent, {"body": dict(body, **changes)})
        seconds = dict(body, unit="seconds", ranges=[{"start": 0, "end": 2}])
        self.assertFalse(graph.satisfies_scope({"visuals": seconds}, {"visuals": {"unit": "seconds", "ranges": [{"start": 0, "end": 1}, {"start": 1.1, "end": 2}]}}, {"visuals": "all"}))

    def test_empty_required_components_cannot_establish_sufficient_scope(self):
        empty = ({}, {"provider_metadata": {}}, {"body": "none"}, {"body": {}},
                 {"body": {"unit": "line", "ranges": []}}, {"comments": {"ids": []}},
                 {"body": {"attestation": "", "limitations": "Unknown extent"}},
                 {"body": {"ids": ["paragraph-1"]}, "comments": {"ids": []}})
        for extent in empty:
            with self.subTest(extent=extent):
                graph._extent(extent)  # Empty capture metadata remains valid.
                self.assertFalse(graph.satisfies_scope(extent, extent, extent))
        positions = {"body": {"ids": ["paragraph-1"]}, "comments": "unavailable"}
        self.assertTrue(graph.satisfies_scope(positions, positions, positions))
        opaque = {"body": {"attestation": "Original opaque body scope", "limitations": "No numeric positions retained"}}
        self.assertTrue(graph.satisfies_scope(opaque, opaque, opaque))

    def crash(self, action, point, packet=None, root=None):
        root = root or self.root
        path = self.directory / ("crash-%d.json" % self.serial)
        path.write_bytes(encoded(packet or {}))
        process = subprocess.run([sys.executable, "-I", "-B", "-c", CRASH_PROCESS,
                                  str(MODULE), str(root), action, point, str(path)],
                                 cwd=self.directory, env={"PATH": os.defpath},
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 77, process.stderr)
        self.assertEqual(process.stdout, "")
        self.assertEqual(process.stderr, "")

    def work(self, identity, question="why", kind="reconcile", artifact=None):
        record = {"work_id": identity, "kind": kind, "artifact_id": artifact,
                  "question_id": question, "request_json": {}, "purpose": "Review fictional scope",
                  "stage": "baseline", "branch": "pilot", "generation": 0,
                  "transition_reason": "Explicit fixture work"}
        record["logical_key"] = graph.logical_key(record)
        return record

    def assign(self, role="reader", actor="reader"):
        graph.ingest(self.root, self.packet(self.seed()))
        ledger = encoded({"sources": []})
        plan = {"version": 1, "owner": "coordinator", "coverage": [{"id": "pilot", "questions": ["why", "outcomes"]}],
                "items": [{"id": "inspect-pilot", "kind": "review", "actor": actor, "coverage": ["pilot"]}],
                "_record": {"contract_sha256": digest((self.root / "contract.json").read_bytes()),
                            "ledger_sha256": digest(ledger), "parent_sha256": None}}
        data = encoded(plan)
        plan_hash = digest(data)
        (self.root / "plans" / (plan_hash + ".json")).write_bytes(data)
        (self.root / "plans" / (plan_hash + ".ledger.json")).write_bytes(ledger)
        now = datetime.now(timezone.utc)
        mutations = [insert("actors", actor_id=actor, parent_actor_id="coordinator", role=role,
                            branch_scope="pilot", stage_scope="baseline"),
                     insert("plan_snapshots", plan_sha256=plan_hash, contract_sha256=plan["_record"]["contract_sha256"], relative_path="plans/" + plan_hash + ".json"),
                     insert("plan_items", plan_sha256=plan_hash, plan_item_id="inspect-pilot"),
                     insert("work_items", **self.work("work-pilot", kind="review", artifact="pilot")),
                     insert("work_origins", origin_id="origin-pilot", work_id="work-pilot", root_basis="Supplied source", originating_generation=0),
                     insert("assignments", assignment_id="assignment-pilot", work_id="work-pilot", actor_id=actor,
                            claimed_at=now.isoformat(), lease_expires_at=(now + timedelta(hours=1)).isoformat(),
                            generation_at_claim=0, plan_item_id="inspect-pilot", plan_sha256=plan_hash),
                     update("work_items", "work-pilot", 1, state="leased", transition_reason="Assigned fixture reader")]
        graph.ingest(self.root, self.packet(mutations, kind="reconcile"), evidence_validator=storage_lifecycle)
        return plan_hash

    def lead(self, identity="old-name", question="why", origin="lead-origin"):
        return [insert("leads", lead_id=identity, kind="vocabulary", original_value="Grove", normalized_key="grove", significance="May locate earlier records"),
                insert("lead_origins", origin_id=origin, lead_id=identity, question_id=question,
                       original_value="Grove", origin_basis="Retained fictional observation")]

    def export_files(self, export_id):
        directory = self.root / "graph/exports" / export_id
        directory.mkdir()
        (directory / "evidence").mkdir()
        with graph.snapshot(self.root) as connection:
            stamp = {"schema_version": 1, "run_id": "fixture-run", "graph_sequence": graph._head(connection)}
            tables = {table: [graph._decoded(row) for row in connection.execute("SELECT * FROM " + table)]
                      for table in graph.storage_tables(connection)}
            schema = graph.schema_bytes(graph.storage_revision(connection))
        contents = {"graph.json": encoded(dict(stamp, tables=tables)), "schema.sql": schema,
                    "evidence/contract.json": (self.root / "contract.json").read_bytes()}
        originals = {row["relative_path"] for row in tables["contract_snapshots"] + tables["plan_snapshots"] + tables["captures"]}
        originals.update("plans/" + row["plan_sha256"] + ".ledger.json" for row in tables["plan_snapshots"])
        originals.update(row["packet_path"] for row in tables["ingest_batches"])
        originals.update(row["file_path"] for row in tables["reader_returns"])
        for row in tables["operation_links"]:
            originals.add("operations/" + row["operation_id"] + "/request.json")
            if row["receipt_sha256"] is not None:
                originals.add("operations/" + row["operation_id"] + "/receipt.json")
        contents.update({"evidence/" + name: (self.root / name).read_bytes() for name in originals})
        contents.update({name: encoded(stamp) for name in ("ledger.json", "sources.json", "references.json", "worklist.json")})
        if not tables["operation_links"]:
            # First controller registration now verifies the generated ledger.
            # Deliberately synthetic native-corruption fixtures still fail the
            # earlier retained-evidence checks before reaching this boundary.
            from test_research_graph_exports import EXPORTS
            ledger, _ = EXPORTS._compatibility_ledger(self.root, tables, stamp)
            contents["ledger.json"] = graph._bytes(ledger)
        files = []
        for relative, data in contents.items():
            (directory / relative).parent.mkdir(parents=True, exist_ok=True)
            (directory / relative).write_bytes(data)
            files.append({"relative_path": relative, "sha256": digest(data), "format": Path(relative).suffix[1:], "size_bytes": len(data)})
        manifest = dict(stamp, export_id=export_id, created_at="2026-01-01T00:00:00Z",
                        contract_sha256=digest(contents["evidence/contract.json"]), plan_sha256=None, budget_event_head=None,
                        schema_sha256=digest(contents["schema.sql"]), files=files, limitations=["Synthetic controller registration fixture"])
        data = encoded(manifest)
        (directory / "manifest.json").write_bytes(data)
        return {"export_id": export_id, "path": str((directory / "manifest.json").relative_to(self.root)), "sha256": digest(data)}

    def test_atomic_initialization_is_idempotent_and_binds_real_contract(self):
        second = graph.initialize(self.root, "coordinator", "fixture-run")
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["accepted_at"], self.initial["accepted_at"])
        self.assertEqual(second["graph_sequence"], self.initial["graph_sequence"])
        self.error("batch_conflict", graph.initialize, self.root, "coordinator", "another-run")
        self.assertEqual(graph.validate(self.root)["accepted_batches"], 1)
        with graph.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden(value)")

    def test_standalone_wal_database_opens_with_a_readonly_snapshot(self):
        root = self.directory / "standalone-wal"
        shutil.copytree(self.root, root, ignore=shutil.ignore_patterns("research.sqlite-wal", "research.sqlite-shm"))
        database = root / "graph/research.sqlite"
        original = database.read_bytes()
        self.assertEqual(original[18:20], b"\x02\x02")
        self.assertFalse(database.with_name("research.sqlite-wal").exists())
        with graph.snapshot(root) as connection:
            self.assertEqual(graph._head(connection), self.initial["graph_sequence"])
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            # The underlying connection remains mode=ro even if SQL's guard is disabled.
            connection.execute("PRAGMA query_only=OFF")
            with self.assertRaises(sqlite3.OperationalError) as raised:
                connection.execute("CREATE TABLE forbidden(value)")
            if hasattr(raised.exception, "sqlite_errorcode"):
                self.assertEqual(raised.exception.sqlite_errorcode, getattr(sqlite3, "SQLITE_READONLY", 8))
            else:
                self.assertEqual(str(raised.exception), "attempt to write a readonly database")
        self.assertEqual(database.read_bytes(), original)

    def test_readonly_snapshots_see_commits_held_in_the_current_wal(self):
        database = self.root / "graph/research.sqlite"
        with graph.snapshot(self.root) as held:
            original_head = graph._head(held)
            original_bytes = database.read_bytes()
            with ThreadPoolExecutor(max_workers=1) as pool:
                receipt = pool.submit(graph.ingest, self.root, self.packet(self.seed())).result(timeout=10)
            self.assertEqual(database.read_bytes(), original_bytes)
            self.assertGreater(database.with_name("research.sqlite-wal").stat().st_size, 0)
            with graph.snapshot(self.root) as current:
                self.assertEqual(graph._head(current), receipt["graph_sequence"])
                self.assertIsNotNone(current.execute("SELECT 1 FROM artifacts WHERE artifact_id='pilot'").fetchone())
            self.assertEqual(graph._head(held), original_head)

    def test_readonly_wal_bootstrap_stops_after_one_persistent_open_failure(self):
        database = self.directory / "unavailable.sqlite"
        database.write_bytes((self.root / "graph/research.sqlite").read_bytes())
        failure = sqlite3.OperationalError("unable to open database file")
        failure.sqlite_errorcode = getattr(sqlite3, "SQLITE_CANTOPEN", 14)
        reader, opener = Mock(), Mock()
        reader.execute.return_value.fetchone.return_value = (1,)
        # Simulate a build that still cannot open after a successful bootstrap
        # read, with no WAL created. Retrying must not recurse without a bound.
        with patch.object(graph, "_check_version", side_effect=failure), patch.object(
                graph.sqlite3, "connect", side_effect=[reader, opener, reader]) as connect:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                graph._connect(database, readonly=True)
        self.assertIs(raised.exception, failure)
        self.assertEqual(connect.call_count, 3)
        opener.close.assert_called_once()

    def test_legacy_readonly_open_error_recovers_without_result_code(self):
        database = self.directory / "legacy-open.sqlite"
        database.write_bytes((self.root / "graph/research.sqlite").read_bytes())
        failure = sqlite3.OperationalError("unable to open database file")
        self.assertFalse(hasattr(failure, "sqlite_errorcode"))
        reader, opener, recovered = Mock(), Mock(), Mock()
        reader.execute.return_value.fetchone.return_value = (1,)
        recovered.execute.return_value.fetchone.return_value = (1,)
        with patch.object(graph, "_check_version", side_effect=[failure, None]), patch.object(
                graph.sqlite3, "connect", side_effect=[reader, opener, recovered]) as connect:
            self.assertIs(graph._connect(database, readonly=True), recovered)
        self.assertEqual([call.args[0].split("?")[-1] for call in connect.call_args_list],
                         ["mode=ro", "mode=rw", "mode=ro"])
        reader.close.assert_called_once()
        opener.close.assert_called_once()

    def test_readonly_bootstrap_does_not_retry_other_sqlite_errors(self):
        database = self.directory / "unrelated-open.sqlite"
        database.write_bytes((self.root / "graph/research.sqlite").read_bytes())
        coded = sqlite3.OperationalError("unable to open database file")
        coded.sqlite_errorcode = getattr(sqlite3, "SQLITE_BUSY", 5)
        for failure in (sqlite3.OperationalError("database is locked"), coded):
            with self.subTest(error=str(failure), code=getattr(failure, "sqlite_errorcode", None)):
                reader = Mock()
                reader.execute.return_value.fetchone.return_value = (1,)
                with patch.object(graph, "_check_version", side_effect=failure), patch.object(
                        graph.sqlite3, "connect", return_value=reader) as connect:
                    with self.assertRaises(sqlite3.OperationalError) as raised:
                        graph._connect(database, readonly=True)
                self.assertIs(raised.exception, failure)
                self.assertEqual(connect.call_count, 1)

    def test_init_process_crash_never_installs_partial_graph(self):
        root = self.bundle("crashed-init")
        self.crash("init", "init_before_install", root=root)
        self.assertFalse((root / "graph").exists())
        graph.initialize(root, "coordinator", "fixture-run")
        self.assertEqual(graph.validate(root)["accepted_batches"], 1)

    def test_same_payload_replays_original_receipt_after_later_changes(self):
        packet = self.packet(self.seed(), "seed")
        first = graph.ingest(self.root, packet)
        graph.ingest(self.root, self.packet(self.seed("other")))
        second = graph.ingest(self.root, packet)
        self.assertTrue(second["idempotent"])
        self.assertEqual({k: v for k, v in first.items() if k != "idempotent"}, {k: v for k, v in second.items() if k != "idempotent"})
        self.assertNotEqual(first["accepted_at"], packet["submitted_at"])
        changed = dict(packet, submitted_at="2002-01-01T00:00:00Z")
        self.error("batch_conflict", graph.ingest, self.root, changed)
        self.assertEqual(graph.validate(self.root)["accepted_batches"], 3)

    def test_strict_json_and_controller_owned_fields_fail_closed(self):
        packet = self.packet(self.seed())
        data = encoded(packet).replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
        self.error("invalid_packet", graph.ingest, self.root, data)
        self.error("unsupported_schema", graph.ingest, self.root, dict(packet, schema_version=True))
        self.error("invalid_packet", graph.ingest, self.root, dict(packet, batch_id="../escape"))
        self.error("invalid_packet", graph.ingest, self.root, dict(packet, packet_kind=[]))
        self.error("permission_denied", graph.ingest, self.root, self.packet([insert("graph_events", sequence=999)]))
        forged = insert("artifacts", artifact_id="forged", title="Fixture", artifact_type="document", revision=7)
        self.error("permission_denied", graph.ingest, self.root, self.packet([forged]))
        malformed = self.packet([insert("assertions", assertion_id="bad", kind="finding", subject_artifact_id="missing",
                                        predicate="states", value_json={"text": "Fixture", "unsafe_extension": True},
                                        basis="metadata", assessed_at="2026-01-01T00:00:00Z")])
        self.error("invalid_packet", graph.ingest, self.root, malformed)
        invalid_unit = self.work("bad-extent")
        invalid_unit["request_json"] = {"extent": {"body": {"unit": [], "ranges": []}}}
        self.error("invalid_packet", graph.ingest, self.root, self.packet([insert("work_items", **invalid_unit)]))

    def test_atomic_failure_retains_pending_packet_without_partial_rows(self):
        packet = self.packet(self.seed() + [insert("artifact_keys", namespace="archive-file", external_id="unknown", artifact_id="missing", basis="Unknown fixture")], "bad-fk")
        self.error("unknown_identity", graph.ingest, self.root, packet)
        self.assertIsNone(self.row("artifacts", "pilot"))
        status = graph.validate(self.root)
        self.assertEqual(status["accepted_batches"], 1)
        self.assertEqual(status["pending_packets"][0]["batch_id"], "bad-fk")
        self.error("batch_conflict", graph.ingest, self.root, dict(packet, mutations=self.seed()))

    def test_native_execution_and_inspected_claims_require_recorder_validator(self):
        self.assign()
        operation = insert("operation_links", operation_id="unverified", assignment_id="assignment-pilot",
                           request_sha256="a" * 64, execution_state="reserved", verification_state="pending")
        self.error("pending_dependency", graph.ingest, self.root, self.packet([operation]))
        assertion = insert("assertions", assertion_id="unread", kind="finding", subject_artifact_id="pilot", predicate="states",
                           value_json={"text": "Unverified claim"}, basis="inspected", assessed_at="2026-01-01T00:00:00Z")
        self.error("pending_dependency", graph.ingest, self.root, self.packet([assertion]))
        self.error("pending_dependency", graph.ingest, self.root, self.packet([operation]), evidence_validator=lambda *args: False)
        with graph.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operation_links").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM assertions").fetchone()[0], 0)

    def test_explicit_pending_retry_forwards_validator_but_history_replay_does_not(self):
        self.assign()
        operation = self.root / "operations/retry"
        operation.mkdir()
        request = encoded({"id": "retry", "purpose": "Synthetic callback boundary"})
        (operation / "request.json").write_bytes(request)
        packet = self.packet([insert("operation_links", operation_id="retry", assignment_id="assignment-pilot",
                                     request_sha256=digest(request), execution_state="reserved", verification_state="pending")], "native-pending")
        self.error("pending_dependency", graph.ingest, self.root, packet)
        self.error("pending_dependency", graph.recover, self.root, packet["batch_id"], True)
        checked = []
        def fixture_validator(root, connection, original, mutation):
            self.assertEqual(root, self.root.resolve())
            self.assertEqual(original, packet)
            self.assertIsNone(mutation["before"])
            self.assertIsNone(connection.execute("SELECT 1 FROM operation_links WHERE operation_id='retry'").fetchone())
            checked.append(mutation["after"]["operation_id"])
            return mutation["after"]["request_sha256"] == digest((operation / "request.json").read_bytes())
        accepted = graph.recover(self.root, packet["batch_id"], True, evidence_validator=fixture_validator)
        self.assertEqual(checked, ["retry"])
        self.assertEqual(accepted["recovery_action"], "explicit_packet_retry")
        replayed = graph.recover(self.root, apply=True, evidence_validator=fixture_validator)
        self.assertEqual(checked, ["retry"])
        self.assertEqual(replayed["recovery_action"], "accepted_history_replay")
        self.assertEqual(graph.ingest(self.root, packet)["accepted_at"], accepted["accepted_at"])
        graph.validate(self.root)

    def test_validate_checks_accepted_external_bytes_and_reports_integrity_scope(self):
        plan_hash = self.assign()
        operation = self.root / "operations/read-pilot"
        operation.mkdir()
        request = encoded({"id": "read-pilot", "source": "pilot"})
        receipt = encoded({"outcome": "synthetic", "purpose": "G1 retained-byte fixture"})
        capture = b"The fictional pilot paused.\n"
        returned = encoded({"id": "return-pilot", "purpose": "G1 retained-byte fixture"})
        paths = {"operations/read-pilot/request.json": request,
                 "operations/read-pilot/receipt.json": receipt,
                 "operations/read-pilot/stdout": capture,
                 "reader-returns/return-pilot.json": returned}
        for relative, data in paths.items():
            (self.root / relative).write_bytes(data)
        rows = [insert("operation_links", operation_id="read-pilot", assignment_id="assignment-pilot",
                       request_sha256=digest(request), execution_state="dispatched", verification_state="pending"),
                update("operation_links", "read-pilot", 1, execution_state="succeeded", verification_state="verified",
                       budget_event_id="synthetic-admission", receipt_sha256=digest(receipt)),
                insert("captures", capture_id="capture-pilot", artifact_id="pilot", operation_id="read-pilot",
                       format="text", relative_path="operations/read-pilot/stdout", sha256=digest(capture),
                       capture_extent_json={}, extraction_status="complete", receipt_sha256=digest(receipt),
                       captured_at="2026-01-01T00:00:00Z"),
                insert("locators", locator_id="locator-pilot", capture_id="capture-pilot", locator_kind="line",
                       locator_json={"unit": "line", "start": 1, "end": 1}, excerpt=capture.decode().strip(),
                       excerpt_hash=digest(capture.strip())),
                insert("reader_returns", return_id="return-pilot", actor_id="reader", assignment_id="assignment-pilot",
                       file_path="reader-returns/return-pilot.json", payload_sha256=digest(returned), receipt_sha256=digest(receipt),
                       recorded_budget_event="synthetic-return", verification_state="verified")]
        # The G1 test callback admits only these synthetic rows; production uses
        # the G2/G4 recorder validators rather than this storage-test fixture.
        graph.ingest(self.root, self.packet(rows, kind="reconcile"), evidence_validator=lambda *args: True)
        status = graph.validate(self.root)
        self.assertEqual(status["external_files_checked"], 7)
        self.assertEqual(status["locator_excerpts_checked"], 1)
        self.assertFalse(status["fresh_evidence_validation"])
        self.assertEqual(status["validation_scope"], "accepted_history_and_retained_file_integrity")
        exported = self.export_files("corrupt-copied-evidence")
        export_directory = (self.root / exported["path"]).parent
        copied = export_directory / "evidence/operations/read-pilot/stdout"
        copied.write_bytes(b"different captured content")
        manifest = json.loads((export_directory / "manifest.json").read_bytes())
        for entry in manifest["files"]:
            if entry["relative_path"] == "evidence/operations/read-pilot/stdout":
                entry.update(sha256=digest(copied.read_bytes()), size_bytes=copied.stat().st_size)
        data = encoded(manifest)
        (export_directory / "manifest.json").write_bytes(data)
        self.error("integrity_error", graph.register_export, self.root, exported["export_id"], exported["path"], digest(data))
        retained_contract = "contracts/" + digest((self.root / "contract.json").read_bytes()) + ".json"
        for relative in [retained_contract, "plans/" + plan_hash + ".json", "plans/" + plan_hash + ".ledger.json"]:
            paths[relative] = (self.root / relative).read_bytes()
        for relative, data in paths.items():
            with self.subTest(path=relative):
                path = self.root / relative
                path.write_bytes(b"tampered")
                self.error("integrity_error", graph.validate, self.root)
                path.unlink()
                error = self.error("integrity_error", graph.validate, self.root)
                self.assertEqual(error.details["path"], relative)
                path.write_bytes(data)
        graph.validate(self.root)

    def test_validate_does_not_require_deleted_pending_evidence(self):
        self.assign()
        pending_file = self.root / "reader-returns/pending.json"
        pending_file.write_bytes(encoded({"id": "pending"}))
        packet = self.packet([insert("reader_returns", return_id="pending", actor_id="reader",
                                     file_path="reader-returns/pending.json", payload_sha256=digest(pending_file.read_bytes()),
                                     verification_state="legacy_attested", limitations="Unaccepted fixture")], "pending-evidence")
        self.error("pending_dependency", graph.ingest, self.root, packet)
        pending_file.unlink()
        status = graph.validate(self.root)
        self.assertEqual(status["status"], "pass")
        self.assertEqual(status["pending_packets"][0]["batch_id"], "pending-evidence")

    def test_reader_cannot_merge_prioritize_or_accept_shared_assessments(self):
        self.assign()
        assertion = insert("assertions", assertion_id="role", kind="role", subject_artifact_id="pilot", predicate="has_role",
                           value_json="trunk", question_id="why", basis="metadata", assessed_at="2026-01-01T00:00:00Z")
        graph.ingest(self.root, self.packet([assertion], actor="reader", assignment="assignment-pilot"))
        forbidden = [update("artifacts", "pilot", 1, canonical_artifact_id="other", canonical_basis="Global merge"),
                     update("work_items", "work-pilot", 2, priority=100),
                     insert("assertion_decisions", decision_id="accept-role", assertion_id="role", disposition="accepted",
                            reason="Worker attempts self-acceptance", actor_id="reader", expected_revision=0)]
        for mutation in forbidden:
            with self.subTest(table=mutation["table"]):
                self.error("permission_denied", graph.ingest, self.root, self.packet([mutation], actor="reader", assignment="assignment-pilot", kind="reconcile"))

    def test_cross_question_lead_proposal_is_retained_without_dispatch_authority(self):
        self.assign()
        graph.ingest(self.root, self.packet(self.lead(question="outcomes"), actor="reader", assignment="assignment-pilot"))
        with graph.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("SELECT question_id,status FROM obligation_scope_status WHERE obligation_id='lead:old-name'").fetchone()[:], ("outcomes", "unassigned"))
        work = self.work("outside-question", question="outcomes")
        work["generation"] = 1
        self.error("permission_denied", graph.ingest, self.root, self.packet([insert("work_items", **work)], actor="reader", assignment="assignment-pilot"))

    def test_delegated_child_registration_cannot_expand_branch_or_role(self):
        self.assign(role="research_lead", actor="lead")
        outside = insert("actors", actor_id="child", parent_actor_id="lead", role="reader", branch_scope="other", stage_scope="baseline")
        self.error("permission_denied", graph.ingest, self.root, self.packet([outside], actor="lead", assignment="assignment-pilot"))
        valid = insert("actors", actor_id="child", parent_actor_id="lead", role="reader", branch_scope="pilot/subtree", stage_scope="baseline")
        graph.ingest(self.root, self.packet([valid], actor="lead", assignment="assignment-pilot"))
        root_role = insert("actors", actor_id="other-coordinator", parent_actor_id="lead", role="coordinator", branch_scope="pilot", stage_scope="baseline")
        self.error("permission_denied", graph.ingest, self.root, self.packet([root_role], actor="lead", assignment="assignment-pilot"))

    def test_plan_hash_path_and_symlink_checks_prevent_unproven_bindings(self):
        external = self.directory / "external.json"
        external.write_bytes(b"{}")
        alias = self.root / "contracts/alias.json"
        alias.symlink_to(external)
        record = insert("contract_snapshots", contract_sha256=digest(b"{}"), contract_revision=2, relative_path="contracts/alias.json")
        self.error("integrity_error", graph.ingest, self.root, self.packet([record]))
        path_escape = insert("contract_snapshots", contract_sha256=digest(b"{}"), contract_revision=2, relative_path="../external.json")
        self.error("integrity_error", graph.ingest, self.root, self.packet([path_escape]))
        bad_plan = encoded({"version": 1, "items": [{"id": "one"}]})
        plan_hash = digest(bad_plan)
        (self.root / "plans" / (plan_hash + ".json")).write_bytes(bad_plan)
        record = insert("plan_snapshots", plan_sha256=plan_hash, contract_sha256=digest((self.root / "contract.json").read_bytes()), relative_path="plans/" + plan_hash + ".json")
        self.error("integrity_error", graph.ingest, self.root, self.packet([record]))

    def test_concurrent_duplicate_batch_and_compare_and_swap(self):
        packet = self.packet(self.seed(), "concurrent-seed")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: graph.ingest(self.root, packet), range(2)))
        self.assertEqual(sorted(item["idempotent"] for item in results), [False, True])
        mutations = [self.packet([update("artifacts", "pilot", 1, title=title)], kind="reconcile") for title in ("First proposal", "Second proposal")]
        def apply(packet):
            try:
                return graph.ingest(self.root, packet)["status"]
            except graph.GraphError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, mutations))
        self.assertEqual(sorted(results), ["pass", "revision_conflict"])
        self.assertEqual(self.row("artifacts", "pilot")["revision"], 2)

    def test_concurrent_claims_cannot_create_a_second_active_assignment(self):
        self.assign()
        assignment = self.row("assignments", "assignment-pilot")
        new_work = self.work("another-work", question="outcomes", kind="review", artifact="pilot")
        graph.ingest(self.root, self.packet([insert("work_items", **new_work),
                     insert("work_origins", origin_id="another-origin", work_id="another-work", root_basis="Second fixture question", originating_generation=0)]))
        proposed = {key: value for key, value in assignment.items() if key not in {"created_batch_id", "updated_batch_id", "revision"}}
        proposed["work_id"] = "another-work"
        packets = [self.packet([insert("assignments", **dict(proposed, assignment_id=name)),
                               update("work_items", "another-work", 1, state="leased", transition_reason="Claimed the same work")], kind="reconcile")
                   for name in ("claim-one", "claim-two")]
        def claim(packet):
            try:
                return graph.ingest(self.root, packet, evidence_validator=storage_lifecycle)["status"]
            except graph.GraphError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, packets))
        self.assertEqual(sorted(results), ["lease_conflict", "pass"])
        with graph.snapshot(self.root) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM assignments WHERE work_id='another-work' AND state='leased'").fetchone()[0], 1)

    def test_state_transitions_after_merge_retain_original_work_intent(self):
        self.assign()
        original = self.row("work_items", "work-pilot")
        assignment = self.row("assignments", "assignment-pilot")
        graph.ingest(self.root, self.packet(self.seed("canonical") + [
            update("artifacts", "pilot", 1, canonical_artifact_id="canonical", canonical_basis="Same fictional document")], kind="reconcile"))
        graph.ingest(self.root, self.packet([
            update("assignments", "assignment-pilot", 1, state="released", finished_at=datetime.now(timezone.utc).isoformat()),
            update("work_items", "work-pilot", 2, state="deferred", terminal_reason="Explicit fixture bound", transition_reason="Bound after identity reconciliation")], kind="reconcile"), evidence_validator=storage_lifecycle)
        bounded = self.row("work_items", "work-pilot")
        self.assertEqual(bounded["logical_key"], original["logical_key"])
        self.assertEqual(bounded["artifact_id"], "pilot")
        self.assertEqual(self.row("assignments", "assignment-pilot")["work_id"], assignment["work_id"])
        self.error("invalid_packet", graph.ingest, self.root, self.packet([
            update("work_items", "work-pilot", 3, purpose="Changed intent without a matching key")], kind="reconcile"), evidence_validator=storage_lifecycle)
        graph.validate(self.root)

    def test_writer_is_reentrant_and_snapshot_remains_consistent(self):
        with graph.writer(self.root):
            with graph.snapshot(self.root) as connection:
                original = connection.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0]
            graph.ingest(self.root, self.packet(self.seed()))
        with graph.snapshot(self.root) as connection:
            held = connection.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0]
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(graph.ingest, self.root, self.packet(self.seed("other"))).result(timeout=10)
            self.assertEqual(connection.execute("SELECT MAX(sequence) FROM graph_events").fetchone()[0], held)
            self.assertGreater(held, original)

    def test_process_crashes_before_commit_leave_explicit_pending_packets(self):
        for point in ("after_packet_fsync", "after_begin", "after_mutations"):
            with self.subTest(point=point):
                identity = point.replace("_", "-")
                packet = self.packet(self.seed(identity), point)
                self.crash("ingest", point, packet)
                self.assertIsNone(self.row("artifacts", identity))
                status = graph.recover(self.root, point)
                self.assertEqual(status["batch"]["state"], "pending")
                graph.recover(self.root, point, True)
                self.assertIsNotNone(self.row("artifacts", identity))
        self.assertEqual(graph.validate(self.root)["accepted_batches"], 4)

    def test_crash_after_commit_preserves_original_acceptance_receipt(self):
        packet = self.packet(self.seed(), "committed-without-response")
        self.crash("ingest", "after_commit", packet)
        status = graph.recover(self.root, packet["batch_id"])
        self.assertEqual(status["batch"]["state"], "accepted")
        result = graph.ingest(self.root, packet)
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["accepted_at"], status["batch"]["accepted_at"])
        self.assertEqual(result["graph_sequence"], status["batch"]["resulting_sequence"])

    def test_trigger_derived_reopenings_survive_event_replay(self):
        graph.ingest(self.root, self.packet(self.lead()))
        work = self.work("bound-old-name")
        mutation = [insert("work_items", **work), insert("work_origins", origin_id="lead-work", work_id="bound-old-name", lead_id="old-name", originating_generation=0),
                    update("work_items", "bound-old-name", 1, state="deferred", terminal_reason="Fixture cap reached", transition_reason="Explicit question bound"),
                    update("followup_obligations", "lead:old-name", 1, state="terminal", disposition="bounded", reason="First question bounded"),
                    update("leads", "old-name", 1, disposition="bounded", reason="First question bounded")]
        graph.ingest(self.root, self.packet(mutation, kind="reconcile"), evidence_validator=storage_lifecycle)
        packet = self.packet([insert("lead_origins", origin_id="other-question", lead_id="old-name", question_id="outcomes", original_value="Grove", origin_basis="The original term also affects outcomes")])
        receipt = graph.ingest(self.root, packet)
        self.assertEqual({item["table"] for item in receipt["affected"]}, {"lead_origins", "leads", "followup_obligations"})
        self.assertEqual(self.row("leads", "old-name")["revision"], 3)
        graph.validate(self.root)
        graph.recover(self.root, apply=True)
        self.assertEqual(self.row("followup_obligations", "lead:old-name")["state"], "pending")
        self.assertEqual(graph.ingest(self.root, packet)["accepted_at"], receipt["accepted_at"])
        graph.validate(self.root)

    def test_checkpoint_restores_only_proven_history_and_retains_unknown_tail(self):
        first = self.packet(self.seed(), "before-checkpoint")
        receipt = graph.ingest(self.root, first)
        checkpoint = graph.checkpoint(self.root, "baseline")
        self.assertTrue(graph.checkpoint(self.root, "baseline")["idempotent"])
        graph.ingest(self.root, self.packet(self.seed("later"), "after-checkpoint"))
        (self.root / "graph/research.sqlite").unlink()
        status = graph.recover(self.root)
        self.assertEqual(status["graph_sequence"], checkpoint["graph_sequence"])
        self.assertEqual(status["pending_packets"][0]["state"], "unproven_after_checkpoint")
        graph.recover(self.root, apply=True)
        self.assertIsNone(self.row("artifacts", "later"))
        self.assertIsNotNone(self.row("artifacts", "pilot"))
        self.assertEqual(graph.ingest(self.root, first)["accepted_at"], receipt["accepted_at"])

    def test_export_registration_audits_older_snapshot_and_replays_original_receipt(self):
        graph.ingest(self.root, self.packet(self.seed()))
        exported = self.export_files("old-snapshot")
        represented = graph.validate(self.root)["graph_sequence"]
        graph.ingest(self.root, self.packet(self.seed("later")))
        accepted = graph.register_export(self.root, exported["export_id"], exported["path"], exported["sha256"])
        self.assertEqual(accepted["exported_graph_sequence"], represented)
        self.assertGreater(accepted["graph_sequence"], represented)
        packet = self.root / "graph/updates" / (accepted["batch_id"] + ".json")
        copied = json.loads(packet.read_bytes())
        copied["batch_id"] = "forged-export-controller"
        self.error("permission_denied", graph.ingest, self.root, copied)
        with graph.snapshot(self.root) as connection:
            row = graph._decoded(connection.execute("SELECT * FROM export_manifests").fetchone())
            self.assertEqual(row["graph_sequence"], represented)
            self.assertIn(exported["path"], [item["relative_path"] for item in row["files_json"]])
        graph.recover(self.root, apply=True)
        replay = graph.register_export(self.root, exported["export_id"], exported["path"], exported["sha256"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["accepted_at"], accepted["accepted_at"])
        graph.validate(self.root)
        path = self.root / exported["path"]
        original = path.read_bytes()
        changed = json.loads(original)
        changed["limitations"].append("Different bytes under the same identity")
        data = encoded(changed)
        path.write_bytes(data)
        self.error("batch_conflict", graph.register_export, self.root, exported["export_id"], exported["path"], digest(data))
        path.write_bytes(original)
        path.write_bytes(b"changed")
        self.error("integrity_error", graph.validate, self.root)

    def test_export_registration_rejects_forged_snapshot_and_path_escape(self):
        exported = self.export_files("forged-snapshot")
        directory = (self.root / exported["path"]).parent
        snapshot_data = json.loads((directory / "graph.json").read_bytes())
        snapshot_data["tables"]["questions"][0]["text"] = "Unaccepted invented question"
        changed = encoded(snapshot_data)
        (directory / "graph.json").write_bytes(changed)
        manifest = json.loads((directory / "manifest.json").read_bytes())
        for entry in manifest["files"]:
            if entry["relative_path"] == "graph.json":
                entry.update(sha256=digest(changed), size_bytes=len(changed))
        data = encoded(manifest)
        (directory / "manifest.json").write_bytes(data)
        self.error("integrity_error", graph.register_export, self.root, exported["export_id"], exported["path"], digest(data))
        self.error("integrity_error", graph.register_export, self.root, exported["export_id"], "../manifest.json", digest(data))
        self.assertEqual(graph.validate(self.root)["accepted_batches"], 1)

    def test_export_process_crashes_resume_controller_packet_without_unaudited_rows(self):
        for point in ("after_packet_fsync", "after_mutations", "after_commit"):
            with self.subTest(point=point):
                exported = self.export_files(point)
                self.crash("export", point, exported)
                status = graph.validate(self.root)
                accepted = graph.register_export(self.root, exported["export_id"], exported["path"], exported["sha256"])
                self.assertEqual(accepted["idempotent"], point == "after_commit")
                if point != "after_commit":
                    self.assertIn(accepted["batch_id"], [item["batch_id"] for item in status["pending_packets"]])
                else:
                    self.assertEqual(accepted["graph_sequence"], status["graph_sequence"])
        graph.validate(self.root)

    def test_imported_capture_needs_lineage_callback_and_retained_historical_files(self):
        graph.ingest(self.root, self.packet(self.seed("prior-export") + self.seed("prior-artifact")))
        prefix = "graph/imports/prior/"
        manifest = encoded({"export_id": "previous", "run_id": "old-run"})
        request, receipt, capture = b"original request", b"original receipt", b"original capture"
        files = {prefix + "manifest.json": manifest, prefix + "operations/old-op/request.json": request,
                 prefix + "operations/old-op/receipt.json": receipt, prefix + "captures/old-capture/content": capture,
                 prefix + "captures/old-capture/receipt.json": receipt}
        for relative, data in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        rows = [insert("prior_run_imports", import_id="prior", prior_run_id="old-run", prior_export_id="previous",
                       manifest_sha256=digest(manifest), source_artifact_id="prior-export", policy="attested_evidence",
                       scope_json={"artifact_ids": ["original-artifact"]}, limitations="Historical fixture"),
                insert("prior_run_records", record_id="mapped-artifact", import_id="prior", prior_record_kind="artifact",
                       prior_record_id="original-artifact", artifact_id="prior-artifact",
                       lineage_json=[{"run_id": "old-run", "export_id": "previous", "manifest_sha256": digest(manifest)}], limitations="Original mapping"),
                insert("operation_links", operation_id="old-op", request_sha256=digest(request), receipt_sha256=digest(receipt),
                       execution_state="succeeded", verification_state="legacy_attested"),
                insert("captures", capture_id="old-capture", artifact_id="prior-artifact", operation_id="old-op", format="text",
                       relative_path=prefix + "captures/old-capture/content", sha256=digest(capture), capture_extent_json={},
                       extraction_status="legacy_attested", receipt_sha256=digest(receipt), captured_at="2001-01-01T00:00:00Z", limitations="Historical capture only")]
        packet = self.packet(rows, "historical-capture", kind="import")
        self.error("pending_dependency", graph.ingest, self.root, packet)
        missing_mapping = self.packet([rows[0], *rows[2:]], kind="import")
        self.error("integrity_error", graph.ingest, self.root, missing_mapping, evidence_validator=lambda *args: True)
        mislabeled = self.packet(rows, kind="observe")
        self.error("permission_denied", graph.ingest, self.root, mislabeled, evidence_validator=lambda *args: True)
        upgraded = self.packet([*rows[:-1], {**rows[-1], "record": dict(rows[-1]["record"], extraction_status="complete")}], kind="import")
        self.error("permission_denied", graph.ingest, self.root, upgraded, evidence_validator=lambda *args: True)
        # Fixture callback stands in for G5's exact retained manifest/packet proof.
        graph.ingest(self.root, packet, evidence_validator=lambda *args: True)
        self.assertFalse((self.root / "operations/old-op").exists())
        graph.validate(self.root)
        graph.recover(self.root, apply=True)
        graph.validate(self.root)
        for relative in files:
            with self.subTest(path=relative):
                path = self.root / relative
                old = path.read_bytes()
                path.write_bytes(b"tampered historical file")
                self.error("integrity_error", graph.validate, self.root)
                path.write_bytes(old)

    def test_pending_files_alone_do_not_establish_accepted_order(self):
        graph.ingest(self.root, self.packet(self.seed()))
        (self.root / "graph/research.sqlite").unlink()
        self.error("pending_dependency", graph.recover, self.root, apply=True)
        self.assertFalse((self.root / "graph/research.sqlite").exists())

    def test_checkpoint_crash_leaves_no_named_partial_checkpoint(self):
        self.crash("checkpoint", "checkpoint_before_install")
        self.assertFalse((self.root / "graph/checkpoints/crashed-checkpoint").exists())
        self.assertEqual(graph.validate(self.root)["accepted_batches"], 1)
        graph.checkpoint(self.root, "crashed-checkpoint")

    def test_recovery_install_crashes_keep_accepted_candidate_and_block_old_main(self):
        for point in ("recovery_before_install", "recovery_after_wal", "recovery_after_replace"):
            with self.subTest(point=point):
                root = self.bundle(point)
                graph.initialize(root, "coordinator", "fixture-run")
                packet = self.packet(self.seed("wal-committed"), "wal-commit", root=root)
                self.crash("ingest", "after_commit", packet, root=root)
                accepted = graph.recover(root, "wal-commit")["batch"]
                self.crash("recover", point, root=root)
                self.assertTrue((root / "graph/recovery-install.json").exists())
                self.error("pending_dependency", graph.validate, root)
                pending = graph.recover(root)
                self.assertEqual(pending["recovery_basis"], "pending_recovery_install")
                self.assertEqual(pending["graph_sequence"], accepted["resulting_sequence"])
                graph.recover(root, apply=True)
                self.assertFalse((root / "graph/recovery-install.json").exists())
                self.assertEqual(graph.ingest(root, packet)["accepted_at"], accepted["accepted_at"])
                graph.validate(root)

    def test_unknown_schema_cannot_be_downgraded_by_checkpoint_recovery(self):
        graph.checkpoint(self.root, "known")
        connection = sqlite3.connect(self.root / "graph/research.sqlite")
        connection.execute("PRAGMA user_version=99")
        connection.close()
        self.error("unsupported_schema", graph.validate, self.root)
        self.error("unsupported_schema", graph.recover, self.root, apply=True)

    def test_current_row_corruption_is_detected_and_rebuilt_from_accepted_events(self):
        graph.ingest(self.root, self.packet(self.seed()))
        connection = sqlite3.connect(self.root / "graph/research.sqlite")
        connection.execute("DROP TRIGGER artifacts_revision")
        connection.execute("UPDATE artifacts SET title='Unrecorded damage' WHERE artifact_id='pilot'")
        connection.commit()
        connection.close()
        self.error("integrity_error", graph.validate, self.root)
        graph.recover(self.root, apply=True)
        self.assertEqual(self.row("artifacts", "pilot")["title"], "Fictional pilot")
        graph.validate(self.root)


if __name__ == "__main__":
    unittest.main()
