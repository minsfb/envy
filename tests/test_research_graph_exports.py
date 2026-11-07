"""Isolated graph export/migration/reuse tests using actual recorder evidence."""

import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_research_graph_ingest as fixture


INGEST, GRAPH, BUNDLE = fixture.INGEST, fixture.GRAPH, fixture.BUNDLE
EXPORTS = INGEST._sibling("research_graph_exports")
WORK = INGEST._sibling("research_graph_work")
FIXTURES = fixture.ROOT / "tests/fixtures/retrospective-research/graph-exports"


class ResearchGraphExportsTests(unittest.TestCase):
    setUp = fixture.ResearchGraphIngestTests.setUp
    operation = fixture.ResearchGraphIngestTests.operation
    returned = fixture.ResearchGraphIngestTests.returned
    candidate = fixture.ResearchGraphIngestTests.candidate
    rejected = fixture.ResearchGraphIngestTests.rejected
    correction = fixture.ResearchGraphIngestTests.correction
    packet = fixture.ResearchGraphIngestTests.packet
    rows = fixture.ResearchGraphIngestTests.rows
    malformed_index = fixture.ResearchGraphIngestTests.malformed_index
    omit_version = fixture.ResearchGraphIngestTests.omit_version

    def export(self, return_evidence=False):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        if return_evidence:
            self.returned()
            INGEST.ingest_reader_return(self.root, "reader-one")
        result = EXPORTS.export_snapshot(self.root, "snapshot-one")
        return Path(result["manifest_path"]).parent, result

    def target(self, policy=None):
        root = self.work / "target"
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["research_plan"] = False
        contract["prior_research"] = policy or {"policy": "allow"}
        BUNDLE.init(root, contract)
        GRAPH.initialize(root, "coordinator", "target-run")
        return root

    def selection(self, **fields):
        selection = json.loads((FIXTURES / "selection.json").read_text())
        selection.update(fields)
        return selection

    def target_rows(self, root, table):
        with GRAPH.snapshot(root) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM " + table)]

    def test_export_one_sequence_and_separate_registration(self):
        directory, receipt = self.export()
        verified = EXPORTS.verify_export(directory)
        self.assertEqual(verified["graph_sequence"], receipt["exported_graph_sequence"])
        self.assertGreater(receipt["graph_sequence"], receipt["exported_graph_sequence"])
        manifest = BUNDLE.read(directory / "manifest.json")
        for name in ("sources.json", "references.json", "worklist.json", "graph.json"):
            self.assertEqual(BUNDLE.read(directory / name)["graph_sequence"], manifest["graph_sequence"])
        self.assertEqual(BUNDLE.read(directory / "ledger.json")["_graph"]["graph_sequence"], manifest["graph_sequence"])
        self.assertEqual(len(self.rows("export_manifests")), 1)
        replay = EXPORTS.export_snapshot(self.root, "snapshot-one")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["manifest_sha256"], receipt["manifest_sha256"])

    def test_export_preserves_index_version_omission_and_older_pending_snapshot(self):
        digest = self.malformed_index()
        raw = (self.root / "operations/read-one/result.json").read_bytes()
        previous = Path(EXPORTS.export_snapshot(self.root, "before-omission")["manifest_path"]).parent
        self.assertFalse(BUNDLE.read(previous / "ingestion.json")["complete"])
        result = self.omit_version(digest)
        current = Path(EXPORTS.export_snapshot(self.root, "after-omission")["manifest_path"]).parent
        EXPORTS.verify_export(previous)
        EXPORTS.verify_export(current)
        self.assertFalse(BUNDLE.read(previous / "ingestion.json")["complete"])
        self.assertTrue(BUNDLE.read(current / "ingestion.json")["complete"])
        self.assertEqual((current / "evidence/operations/read-one/result.json").read_bytes(), raw)
        proof = current / "evidence" / result["omission_path"]
        self.assertTrue(proof.is_file())
        self.assertFalse((previous / "evidence" / result["omission_path"]).exists())
        proof.write_bytes(proof.read_bytes() + b" ")
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.verify_export(current)

    def test_observed_metadata_replaces_seed_display_without_inventing_inspection(self):
        self.operation(key="metadata", query=True, sources=[dict(source_id="doc-a", namespace="fixture-doc",
            external_id="A", title="Orchard fault notebook", type="document", url="https://example.invalid/notebook")])
        INGEST.index_operation(self.root, "metadata")
        # A saved plan can retain an older generated placeholder as a seed.
        # Neither its later batch nor its lexically later ID overrides a fetch.
        self.packet("later-seed", [INGEST._insert("discoveries", discovery_id="zzz-seed", artifact_id="source-a",
            supplied_seed_id="generated-plan", observed_title="Unverified linked ID", observed_url="Not recorded; see graph identity keys.")])
        result = EXPORTS.export_snapshot(self.root, "observed-metadata")
        directory = Path(result["manifest_path"]).parent
        source = BUNDLE.read(directory / "ledger.json")["sources"][0]
        self.assertEqual(source["title"], "Orchard fault notebook")
        self.assertEqual(source["url"], "https://example.invalid/notebook")
        discovery = next(r for r in self.rows("discoveries") if r["operation_id"] == "metadata")
        self.assertEqual(source["graph_metadata_discoveries"], {"title": discovery["discovery_id"], "url": discovery["discovery_id"]})
        self.assertEqual(source["reads"], [])
        self.assertEqual(source["inspections"], [])
        self.assertEqual(source["analysis"]["status"], "pending")
        self.assertEqual(self.rows("artifacts")[0]["title"], "Orchard review")
        self.assertIn("source-a: Orchard fault notebook", (directory / "companion.md").read_text())
        EXPORTS.validate_ledger(self.root, BUNDLE.read(directory / "ledger.json"))

    def test_metadata_uses_capture_recency_not_late_ingestion_and_keeps_known_fields(self):
        self.operation(key="older-metadata", query=True, sources=[dict(source_id="doc-a", namespace="fixture-doc",
            external_id="A", title="Earlier title", url="https://example.invalid/earlier")])
        self.operation(key="newer-metadata", query=True, sources=[dict(source_id="doc-a", namespace="fixture-doc",
            external_id="A", title="Current title", url="https://example.invalid/current")])
        INGEST.index_operation(self.root, "newer-metadata")
        INGEST.index_operation(self.root, "older-metadata")
        self.operation(key="empty-metadata", query=True, sources=[dict(source_id="doc-a", namespace="fixture-doc",
            external_id="A", title="", url=None)])
        INGEST.index_operation(self.root, "empty-metadata")
        result = EXPORTS.export_snapshot(self.root, "metadata-recency")
        directory = Path(result["manifest_path"]).parent
        source = BUNDLE.read(directory / "ledger.json")["sources"][0]
        self.assertEqual((source["title"], source["url"]), ("Current title", "https://example.invalid/current"))
        current = next(r for r in self.rows("discoveries") if r["operation_id"] == "newer-metadata")
        self.assertEqual(source["graph_metadata_discoveries"], {"title": current["discovery_id"], "url": current["discovery_id"]})
        GRAPH.validate(self.root)
        EXPORTS.validate_ledger(self.root, BUNDLE.read(directory / "ledger.json"))

    def unregistered_export(self):
        # A real complete snapshot survives interruption before registration.
        with patch.object(GRAPH, "register_export", side_effect=RuntimeError("before registration")):
            with self.assertRaisesRegex(RuntimeError, "before registration"):
                self.export()
        return self.root / "graph/exports/snapshot-one"

    def test_large_export_inventory_keeps_bounded_registration_and_full_file_checks(self):
        directory = self.unregistered_export()
        manifest = BUNDLE.read(directory / "manifest.json")
        # Ancillary export files are not captures or inspections. Many such
        # legitimate entries reproduce a large manifest without fake research.
        for number in range(1100):
            relative = "notes/" + str(number).zfill(4) + "-" + "n" * 120 + ".json"
            data = GRAPH._bytes({"note": number})
            EXPORTS._save(directory / relative, data)
            manifest["files"].append(EXPORTS._file_entry(relative, data, "json"))
        (directory / "manifest.json").write_bytes(GRAPH._bytes(manifest))
        relative_manifest = str((directory / "manifest.json").relative_to(self.root))
        digest = GRAPH._sha((directory / "manifest.json").read_bytes())
        with GRAPH.snapshot(self.root) as connection:
            expanded, _ = GRAPH._export_registration(self.root, connection, "snapshot-one", relative_manifest, digest)
        self.assertGreater(len(GRAPH._bytes(expanded)), GRAPH.MAX_RECORD)
        receipt = EXPORTS.export_snapshot(self.root, "snapshot-one")
        record = self.rows("export_manifests")[0]
        self.assertEqual(len(json.loads(record["files_json"])), 1)
        self.assertLess(len(GRAPH._bytes(record)), 1024)
        self.assertEqual(EXPORTS.verify_export(directory)["files"], len(manifest["files"]))
        ledger = BUNDLE.read(directory / "ledger.json")
        self.assertEqual(EXPORTS.validate_ledger(self.root, ledger)["export_id"], "snapshot-one")
        GRAPH.validate(self.root)
        self.assertEqual(EXPORTS.export_snapshot(self.root, "snapshot-one")["manifest_sha256"], receipt["manifest_sha256"])

        # The compact row must not stop validation at the manifest itself.
        target = directory / relative
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        for check in (lambda: GRAPH.validate(self.root), lambda: EXPORTS.validate_ledger(self.root, ledger),
                      lambda: EXPORTS.verify_export(directory)):
            with self.assertRaises(GRAPH.GraphError):
                check()
        target.write_bytes(original)
        with self.assertRaises(GRAPH.GraphError) as caught:
            self.packet("oversized-caller-record", [INGEST._insert("artifacts", artifact_id="oversized",
                title="x" * GRAPH.MAX_RECORD, artifact_type="unknown")])
        self.assertEqual(caught.exception.code, "invalid_packet")
        self.assertEqual(caught.exception.details["table"], "artifacts")
        GRAPH.validate(self.root)

    def test_expanded_legacy_registration_survives_pending_and_accepted_replay(self):
        directory = self.unregistered_export()
        relative = str((directory / "manifest.json").relative_to(self.root))
        digest = GRAPH._sha((directory / "manifest.json").read_bytes())
        with GRAPH.snapshot(self.root) as connection:
            record, _ = GRAPH._export_registration(self.root, connection, "snapshot-one", relative, digest)
        self.assertGreater(len(record["files_json"]), 1)
        batch_id = "export-" + GRAPH._sha(b"snapshot-one")
        old_packet = {"schema_version": GRAPH.VERSION, "batch_id": batch_id, "actor_id": "coordinator",
                      "assignment_id": None, "packet_kind": "reconcile", "contract_sha256": record["contract_sha256"],
                      "submitted_at": record["created_at"],
                      "mutations": [{"op": "insert", "table": "export_manifests", "record": record}]}
        _, old_bytes = GRAPH._packet(old_packet)
        path = self.root / "graph/updates" / (batch_id + ".json")
        GRAPH._retain(path, old_bytes)  # Older controller stopped after packet fsync.
        def after_commit(stage):
            if stage == "after_commit":
                raise RuntimeError("after commit")
        with patch.object(GRAPH, "_fault", side_effect=after_commit):
            with self.assertRaisesRegex(RuntimeError, "after commit"):
                EXPORTS.export_snapshot(self.root, "snapshot-one")
        self.assertEqual(path.read_bytes(), old_bytes)
        stored = self.rows("export_manifests")[0]
        self.assertEqual(json.loads(stored["files_json"]), record["files_json"])
        self.assertTrue(EXPORTS.export_snapshot(self.root, "snapshot-one")["idempotent"])
        self.assertEqual(path.read_bytes(), old_bytes)
        self.assertEqual(self.rows("export_manifests")[0], stored)
        GRAPH.validate(self.root)
        EXPORTS.validate_ledger(self.root, BUNDLE.read(directory / "ledger.json"))

    def test_compact_registration_crash_keeps_original_packet_bytes(self):
        def after_packet(stage):
            if stage == "after_packet_fsync":
                raise RuntimeError("after packet")
        # Operation setup is outside the injected export-registration fault.
        directory = self.unregistered_export()
        with patch.object(GRAPH, "_fault", side_effect=after_packet):
            with self.assertRaisesRegex(RuntimeError, "after packet"):
                EXPORTS.export_snapshot(self.root, "snapshot-one")
        path = self.root / "graph/updates" / ("export-" + GRAPH._sha(b"snapshot-one") + ".json")
        original = path.read_bytes()
        self.assertEqual(len(GRAPH._read_json(original)["mutations"][0]["record"]["files_json"]), 1)
        self.assertEqual(self.rows("export_manifests"), [])
        EXPORTS.export_snapshot(self.root, "snapshot-one")
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(EXPORTS.export_snapshot(self.root, "snapshot-one")["idempotent"])
        self.assertEqual(path.read_bytes(), original)
        GRAPH.validate(self.root)

    def test_rejected_and_corrected_exports_preserve_snapshot_authority(self):
        original, digest, rejection = self.rejected()
        raw = (self.root / "reader-returns/reader-one.json").read_bytes()
        prior = EXPORTS.export_snapshot(self.root, "before-correction")
        before_directory = Path(prior["manifest_path"]).parent
        before_ledger = BUNDLE.read(before_directory / "ledger.json")
        before = EXPORTS.validate_ledger(self.root, before_ledger)
        self.assertEqual(before["return_corrections"], {})
        self.assertIn(dict(kind="return", original_id="reader-one", state="rejected"), before["stop_checks"]["unhandled_originals"])
        item = next(i for i in before["ingestion_status"]["items"] if i["kind"] == "return")
        self.assertEqual(item["unaccepted_inventory"]["leads"], original["leads"])
        self.assertEqual(item["unaccepted_inventory"]["graph"], original["graph"])
        self.assertEqual((before_directory / "evidence/reader-returns/reader-one.json").read_bytes(), raw)
        self.assertTrue((before_directory / "evidence" / rejection["relative_path"]).is_file())
        candidate = self.correction(original, digest)
        BUNDLE.budget.reader_return(self.root, candidate)
        INGEST.ingest_reader_return(self.root, candidate["id"])
        later = EXPORTS.export_snapshot(self.root, "after-correction")
        after_directory = Path(later["manifest_path"]).parent
        after_ledger = BUNDLE.read(after_directory / "ledger.json")
        after = EXPORTS.validate_ledger(self.root, after_ledger)
        self.assertEqual(after["return_corrections"]["reader-one"]["correction_return_id"], candidate["id"])
        self.assertEqual(after["stop_checks"]["unhandled_originals"], [])
        self.assertTrue(after["ingestion_status"]["complete"])
        self.assertEqual(len(after["source_bindings"][0]["inspections"]), 1)
        self.assertEqual(len(after["lead_bindings"]), 3)
        self.assertEqual(BUNDLE.validate(self.root, after_ledger, "research")["operations"], 1)
        legacy_budget = BUNDLE.budget.validate(self.root, after_ledger, "research")
        self.assertEqual(legacy_budget["corrected_reader_returns"], 0)
        self.assertEqual(legacy_budget["returned_leads_unreconciled"], len(original["leads"]))
        budget = BUNDLE.validate(self.root, after_ledger, "research")["research_budget"]
        self.assertEqual(budget["corrected_reader_returns"], 1)
        self.assertEqual(budget["returned_leads_unreconciled"], 0)
        still_before = EXPORTS.validate_ledger(self.root, before_ledger)
        self.assertTrue(still_before["stale"])
        self.assertEqual(still_before["return_corrections"], {})
        self.assertFalse(still_before["ingestion_status"]["complete"])
        self.assertEqual(still_before["stop_checks"]["unhandled_originals"], before["stop_checks"]["unhandled_originals"])
        self.assertEqual((self.root / "reader-returns/reader-one.json").read_bytes(), raw)
        EXPORTS.verify_export(before_directory)
        EXPORTS.verify_export(after_directory)

    def test_export_synchronization_claim_must_match_frozen_originals(self):
        self.rejected()
        result = EXPORTS.export_snapshot(self.root, "bad-claim")
        directory = Path(result["manifest_path"]).parent
        status = BUNDLE.read(directory / "ingestion.json")
        status["complete"] = True
        payload = GRAPH._bytes(status)
        (directory / "ingestion.json").write_bytes(payload)
        manifest = BUNDLE.read(directory / "manifest.json")
        entry = next(e for e in manifest["files"] if e["relative_path"] == "ingestion.json")
        entry.update(sha256=GRAPH._sha(payload), size_bytes=len(payload))
        (directory / "manifest.json").write_bytes(GRAPH._bytes(manifest))
        with self.assertRaises(GRAPH.GraphError) as caught:
            EXPORTS.verify_export(directory)
        self.assertEqual(caught.exception.code, "integrity_error")

    def test_older_export_without_synchronization_inventory_remains_readable(self):
        directory, _ = self.export()
        manifest = BUNDLE.read(directory / "manifest.json")
        manifest["files"] = [f for f in manifest["files"] if f["relative_path"] != "ingestion.json"]
        (directory / "ingestion.json").unlink()
        (directory / "manifest.json").write_bytes(GRAPH._bytes(manifest))
        self.assertEqual(EXPORTS.verify_export(directory)["status"], "pass")

    def test_bundle_reader_return_preflight_precedes_immutable_retention(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        journal = (self.root / "research-budget.json").read_bytes()
        with GRAPH.snapshot(self.root) as connection:
            head = GRAPH._head(connection)
        with self.assertRaises(GRAPH.GraphError) as caught:
            BUNDLE.record_reader_return(self.root, self.candidate(invalid=True))
        self.assertEqual(caught.exception.code, "scope_mismatch")
        self.assertFalse((self.root / "reader-returns/reader-one.json").exists())
        self.assertEqual((self.root / "research-budget.json").read_bytes(), journal)
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(GRAPH._head(connection), head)

    def test_compatibility_ledger_passes_existing_recorder_research_validation(self):
        directory, _ = self.export()
        ledger = BUNDLE.read(directory / "ledger.json")
        result = BUNDLE.validate(self.root, ledger, "research")
        self.assertEqual(result["operations"], 1)
        self.assertEqual(ledger["sources"][0]["inspections"], [])
        self.assertEqual(ledger["sources"][0]["analysis"]["status"], "pending")

    def test_return_leads_preserve_original_fields_and_pending_obligations(self):
        directory, _ = self.export(return_evidence=True)
        ledger = BUNDLE.read(directory / "ledger.json")
        source = next(s for s in ledger["sources"] if s["id"] == "doc-a")
        self.assertEqual(len(source["leads"]), 3)
        self.assertTrue(all(lead["graph_obligation_id"].startswith("lead:") for lead in source["leads"]))
        self.assertTrue(all(lead["status"] == "pending" for lead in source["leads"]))
        self.assertTrue(all("target_source_id" not in lead and "plan_item" not in lead for lead in source["leads"]))
        budget = BUNDLE.budget.validate(self.root, ledger, "research")
        self.assertEqual(budget["returned_leads_unreconciled"], 0)

    def test_registered_ledger_binding_rejects_edits_and_reports_stale_scopes(self):
        directory, _ = self.export(return_evidence=True)
        ledger = BUNDLE.read(directory / "ledger.json")
        proof = EXPORTS.validate_ledger(self.root, ledger)
        self.assertFalse(proof["stale"])
        self.assertEqual(len(proof["lead_bindings"]), 3)
        self.assertTrue(any({s["question_id"] for s in lead["question_scopes"]} == {"decision", "outcomes"} for lead in proof["lead_bindings"]))
        self.assertTrue(all(s["status"] == "unassigned" for lead in proof["lead_bindings"] for s in lead["question_scopes"]))
        changed = copy.deepcopy(ledger)
        changed["sources"][0]["finding"] = "An edited conclusion"
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.validate_ledger(self.root, changed)
        self.packet("later-observation", [INGEST._insert("artifacts", artifact_id="later", title="Later", artifact_type="unknown"),
                                           INGEST._insert("discoveries", discovery_id="later-seed", artifact_id="later", supplied_seed_id="later", observed_title="Later")])
        self.assertTrue(EXPORTS.validate_ledger(self.root, ledger)["stale"])

    def settle_scopes(self, *, omit_question=None, omit_reference=None):
        """Bound every actual scope without manufacturing additional reads."""
        for assignment in self.rows("assignments"):
            WORK.bound_work(self.root, assignment["assignment_id"], expected_revision=assignment["revision"],
                            reason="Owned fixture stops after the retained source; follow-ups explicitly bounded.")
        obligations = self.rows("followup_obligations")
        scopes = self.rows("obligation_scope_status")
        mutations = []
        for qid in ("decision", "outcomes"):
            if qid == omit_question:
                continue
            work = dict(work_id="bound-" + qid, kind="review", question_id=qid, request_json={}, purpose="Bound remaining fixture follow-ups",
                        stage="baseline", branch="archive", generation=0, transition_reason="Explicit bounded follow-up review")
            work["logical_key"] = GRAPH.logical_key(work)
            mutations.append(INGEST._insert("work_items", **work))
            mutations.append(INGEST._insert("work_origins", origin_id="bound-root-" + qid, work_id=work["work_id"], root_basis="Fixture scope reconciliation", originating_generation=0))
            for obligation in obligations:
                if obligation["reference_id"] == omit_reference and omit_reference is not None:
                    continue
                if not any(s["obligation_id"] == obligation["obligation_id"] and s["question_id"] == qid for s in scopes):
                    continue
                mutations.append(INGEST._insert("work_origins", origin_id="bound-origin-" + qid + "-" + obligation["obligation_id"],
                    work_id=work["work_id"], lead_id=obligation["lead_id"], reference_id=obligation["reference_id"], originating_generation=0))
            mutations.append(INGEST._insert("source_outcomes", outcome_id="outcome-" + qid, artifact_id="source-a", question_id=qid,
                required_scope_key="body", readability="readable", disposition="deferred", actor_id="coordinator", observed_at=GRAPH._now(),
                reason="This exact question/body scope is explicitly bounded after the supplied inspection."))
        self.packet("bound-followup-work", mutations, kind="reconcile")
        changes = [WORK._update("work_items", row, state="deferred", terminal_reason="Explicit fixture limit", transition_reason="Explicit fixture limit")
                   for row in self.rows("work_items") if row["work_id"].startswith("bound-")]
        changes += [WORK._update("questions", row, current_status="bounded", reason="Retained read and explicit scope bounds delimit the answer.")
                    for row in self.rows("questions") if row["question_id"] != omit_question]
        self.packet("bound-states", changes, kind="reconcile", validator=WORK.evidence_validator)
        with GRAPH.snapshot(self.root) as connection:
            changes = []
            for obligation in self.rows("followup_obligations"):
                states = [r[0] for r in connection.execute("SELECT status FROM obligation_scope_status WHERE obligation_id=?", (obligation["obligation_id"],))]
                if states and all(state in {"bounded", "resolved"} for state in states):
                    changes.append(WORK._update("followup_obligations", obligation, state="terminal", disposition="bounded", reason="Every known question scope is explicitly bounded."))
        if changes:
            self.packet("bound-obligations", changes, kind="reconcile")
        for actor in ("retriever", "reader"):
            BUNDLE.budget.activity(self.root, actor, "stop")

    def exported_context(self, export_id):
        result = EXPORTS.export_snapshot(self.root, export_id)
        ledger = BUNDLE.read(Path(result["manifest_path"]).parent / "ledger.json")
        return ledger, EXPORTS.validate_ledger(self.root, ledger)

    def semantic_review(self, context):
        path = self.root / "semantic-review.json"
        BUNDLE.write(path, {"reviewer": "fixture-reviewer", "note": "Explicit synthetic scope-bound review; no truth score.",
                            "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]})
        return path

    def test_synthesis_uses_frozen_scopes_and_actual_question_followups(self):
        self.export(return_evidence=True)
        self.settle_scopes()
        ledger, context = self.exported_context("settled")
        self.assertTrue(context["stop_checks"]["settled"], context["stop_checks"])
        self.assertEqual(len(context["obligation_bindings"]), 6)
        self.assertEqual(sum(o["reference_id"] is not None for o in context["obligation_bindings"]), 3)
        self.assertEqual(next(q for q in ledger["questions"] if q["id"] == "decision")["followups"], ["read-one"])
        self.assertEqual(next(q for q in ledger["questions"] if q["id"] == "outcomes")["followups"], [])
        source = next(s for s in context["source_bindings"] if s["artifact_id"] == "source-a")
        self.assertEqual({s["question_id"] for s in source["required_scopes"]}, {"decision", "outcomes"})
        self.assertFalse(source["freshness_checked"])
        self.assertEqual(ledger["sources"][0]["disposition"]["status"], "pending")
        result = BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))
        self.assertTrue(result["graph"]["stop_checks"]["settled"])

    def test_synthesis_retains_cancelled_admission_beside_real_followup(self):
        self.export(return_evidence=True)
        spec = copy.deepcopy(BUNDLE.read(self.root / "operations/read-one/request.json")["spec"])
        spec.pop("research_admission")
        spec["id"] = "cancelled-refresh"
        spec["research"].update(refresh_of="read-one", refresh_basis="Check a reported later revision of the fictional source.")
        BUNDLE.start(self.root, spec, "tool-import")
        WORK.record_admission(self.root, spec)
        WORK.reconcile_operation(self.root, spec["id"], expected_revision=1,
            worker_identity=spec["actor"], worker_stopped=True, cancel=True,
            reason="Reader checked stopped before a result was retained.")
        self.settle_scopes()
        ledger, context = self.exported_context("settled-with-cancellation")
        self.assertTrue(context["stop_checks"]["settled"], context["stop_checks"])
        self.assertEqual(next(q for q in ledger["questions"] if q["id"] == "decision")["followups"], ["read-one"])
        result = BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))
        self.assertEqual(result["operations"], 2)
        self.assertEqual(result["executed_operations"], 1)
        self.assertEqual(result["cancelled_operations"], ["cancelled-refresh"])
        self.assertEqual(result["research_budget"]["read_attempts"], 2)
        self.assertEqual({p.name for p in (self.root / "operations/cancelled-refresh").iterdir()}, {"request.json"})

    def test_one_missed_question_blocks_synthesis(self):
        self.export(return_evidence=True)
        self.settle_scopes(omit_question="outcomes")
        ledger, context = self.exported_context("incomplete")
        stops = context["stop_checks"]
        self.assertFalse(stops["settled"])
        self.assertEqual(stops["open_question_ids"], ["outcomes"])
        self.assertTrue(any(s["question_id"] == "outcomes" for s in stops["unresolved_obligation_scopes"]))
        with self.assertRaisesRegex(ValueError, "unresolved work, source scopes, questions or follow-ups"):
            BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))

    def test_one_missed_raw_reference_blocks_synthesis_after_questions_settle(self):
        self.export(return_evidence=True)
        reference = self.rows("reference_occurrences")[0]["reference_id"]
        self.settle_scopes(omit_reference=reference)
        ledger, context = self.exported_context("missed-reference")
        stops = context["stop_checks"]
        self.assertFalse(stops["settled"])
        self.assertEqual(stops["open_question_ids"], [])
        ref = next(o for o in context["obligation_bindings"] if o["reference_id"] == reference)
        self.assertTrue(all(s["status"] == "unassigned" for s in ref["question_scopes"]))
        self.assertEqual(stops["nonterminal_obligation_ids"], [ref["obligation_id"]])
        self.assertTrue(all(s["obligation_id"] == ref["obligation_id"] for s in stops["unresolved_obligation_scopes"]))
        with self.assertRaisesRegex(ValueError, "unresolved work, source scopes, questions or follow-ups"):
            BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))

    def test_current_outcome_conflicts_survive_newer_readability_and_bounds(self):
        self.export(return_evidence=True)
        self.settle_scopes()
        self.packet("contradicting-outcome", [INGEST._insert("source_outcomes", outcome_id="blocked-again", artifact_id="source-a", question_id="decision",
            required_scope_key="body", readability="access_blocked", actor_id="coordinator", observed_at=GRAPH._now(), reason="Independent unresolved access observation.")])
        _, context = self.exported_context("conflicting")
        scope = next(s for source in context["source_bindings"] for s in source["required_scopes"] if s["question_id"] == "decision")
        self.assertEqual({o["outcome_id"] for o in scope["current_outcomes"]}, {"outcome-decision", "blocked-again"})
        self.assertTrue(scope["conflicting"])
        self.assertFalse(scope["outcome_or_bound"])
        self.assertFalse(context["stop_checks"]["settled"])
        self.assertEqual(len(context["stop_checks"]["conflicting_source_scopes"]), 1)

    def test_unassigned_discovery_never_disappears_from_stopping_checks(self):
        self.export()
        self.packet("unassessed", [INGEST._insert("artifacts", artifact_id="unassessed", title="Unassessed source", artifact_type="document"),
            INGEST._insert("discoveries", discovery_id="unassessed-seed", artifact_id="unassessed", supplied_seed_id="unreviewed-seed", observed_title="Unassessed source")])
        _, context = self.exported_context("unassessed")
        self.assertIn("unassessed", context["stop_checks"]["unassessed_source_ids"])
        source = next(s for s in context["source_bindings"] if s["artifact_id"] == "unassessed")
        self.assertEqual(source["required_scopes"], [])
        self.assertFalse(source["all_required_scopes_settled"])

    def test_only_current_accepted_support_is_projected_and_challenges_remain(self):
        self.export(return_evidence=True)
        locator = self.rows("locators")[0]["locator_id"]
        rows = []
        for claim_id, status, stance in (("accepted", "accepted", "support"), ("challenged", "challenged", "support"),
                                         ("draft", "draft", "support"), ("conflict-only", "challenged", "conflict")):
            rows += [INGEST._insert("claims", claim_id=claim_id, text="A synthetic claim", basis="documented", status=status, reason="Explicit review state"),
                     INGEST._insert("claim_evidence", evidence_id="e-" + claim_id, claim_id=claim_id, claim_revision=1,
                                    locator_id=locator, stance=stance, rationale="Retained exact locator"),
                     INGEST._insert("claim_questions", claim_id=claim_id, claim_revision=1, question_id="decision", relationship="answers")]
        self.packet("claims", rows, kind="reconcile")
        ledger, context = self.exported_context("claim-states")
        self.assertEqual([c["id"] for c in ledger["claims"]], ["accepted"])
        question = next(q for q in context["question_bindings"] if q["question_id"] == "decision")
        self.assertEqual(question["claims_by_status"]["challenged"], ["challenged", "conflict-only"])
        self.assertEqual(question["projected_claim_ids"], ["accepted"])
        claim = next(c for c in self.rows("claims") if c["claim_id"] == "accepted")
        self.packet("challenge-accepted", [WORK._update("claims", claim, status="challenged", reason="New evidence challenges the accepted revision")], kind="reconcile")
        newer, _ = self.exported_context("claim-challenged")
        self.assertEqual(newer["claims"], [])
        historical = EXPORTS.validate_ledger(self.root, ledger)
        self.assertTrue(historical["stale"])
        self.assertEqual(historical["question_bindings"][0]["projected_claim_ids"], ["accepted"])

    def uncertain_native_operation(self, outcome, *, historical=False):
        spec = dict(id="read-one", actor="retriever", provider="archive", kind="read", request=copy.deepcopy(self.request),
                    plan_sha256=self.plan_hash, plan_item="retrieve", graph_assignment_id="assignment-retrieve",
                    research={"stage": "baseline", "branch": "archive", "parent_operation": None, "root_basis": "Supplied root source"})
        BUNDLE.start(self.root, spec, "tool-import")
        BUNDLE.import_output(self.root, "read-one", fixture.FIXTURES / "source.html", None, outcome)
        BUNDLE.index_result(self.root, "read-one", {"source_ids": ["doc-a"], "pagination": "complete", "next_cursor": None,
                                                  "note": "Interrupted observation; no worker termination is implied."})
        if historical:
            # Produce accepted history under the old outcome policy, retaining
            # actual recorder admission, request, outputs and receipt files.
            with patch.object(INGEST, "receipt_execution_state", return_value="failed"):
                INGEST.index_operation(self.root, "read-one")
        else:
            INGEST.index_operation(self.root, "read-one")

    def test_imported_timeout_binding_stays_unknown_until_frozen_stop_attestation(self):
        self.uncertain_native_operation("timeout")
        ledger, context = self.exported_context("timeout-unresolved")
        self.assertEqual(context["stop_checks"]["unresolved_operation_ids"], ["read-one"])
        operation = next(op for work in context["work_bindings"] for a in work["assignments"] for op in a["operations"])
        proof = operation["receipt_proof"]
        self.assertEqual((operation["execution_state"], proof["receipt_execution_state"], proof["termination_basis"]), ("unknown", "unknown", "unresolved"))
        self.assertFalse(proof["worker_termination_confirmed"])
        self.assertIsNone(proof["worker_stop_proof"])
        self.assertTrue(proof["limitations"])
        self.assertEqual(ledger["questions"][0]["followups"], [])
        row = self.rows("operation_links")[0]
        WORK.reconcile_operation(self.root, "read-one", expected_revision=row["revision"], worker_identity="retriever", worker_stopped=True,
                                 cancel=True, reason="Original fixture worker explicitly checked and stopped.")
        self.settle_scopes()
        current, proof = self.exported_context("timeout-stopped")
        self.assertTrue(proof["stop_checks"]["settled"], proof["stop_checks"])
        followup = next(q for q in proof["question_bindings"] if q["question_id"] == "decision")["followups"][0]
        self.assertEqual((followup["execution_state"], followup["termination_basis"]), ("cancelled", "coordinator_attestation"))
        self.assertTrue(followup["worker_stop_proof"]["statement"]["worker_stopped"])
        self.assertEqual(followup["worker_stop_proof"]["coordinator_actor_id"], "coordinator")
        self.assertEqual(EXPORTS.validate_ledger(self.root, ledger)["stop_checks"]["unresolved_operation_ids"], ["read-one"])
        self.assertTrue(BUNDLE.validate(self.root, current, "synthesis", semantic_review=self.semantic_review(proof))["graph"]["stop_checks"]["settled"])

    def test_old_failed_interruption_needs_exact_accepted_stop_proof_in_each_snapshot(self):
        self.uncertain_native_operation("interrupted", historical=True)
        original = self.rows("operation_links")[0]
        receipt_bytes = (self.root / "operations/read-one/receipt.json").read_bytes()
        old, before = self.exported_context("historical-failed")
        self.assertEqual(original["execution_state"], "failed")
        self.assertEqual(before["stop_checks"]["unresolved_operation_ids"], ["read-one"])
        self.assertEqual(old["questions"][0]["followups"], [])
        base = dict(operation_id="read-one", request_sha256=original["request_sha256"], receipt_sha256=original["receipt_sha256"],
                    worker_identity="retriever", worker_stopped=True, reason="Exact original worker check", checked_at=GRAPH._now())
        wrong = [{"operation_id": "different-operation"}, {"request_sha256": "0" * 64}, {"receipt_sha256": "1" * 64},
                 {"worker_identity": "different-worker"}, {"worker_stopped": False}, {"checked_at": "2000-01-01T00:00:00Z"},
                 {"checked_at": "2999-01-01T00:00:00Z"}]
        for number, changes in enumerate(wrong):
            work = next(w for w in self.rows("work_items") if w["work_id"] == "work-retrieve")
            statement = dict(base, **changes)
            self.packet("wrong-stop-" + str(number), [WORK._update("work_items", work,
                transition_reason="Worker reconciliation: " + json.dumps(statement))], kind="reconcile")
        _, weak = self.exported_context("wrong-proofs")
        self.assertEqual(weak["stop_checks"]["unresolved_operation_ids"], ["read-one"])
        WORK.reconcile_operation(self.root, "read-one", expected_revision=original["revision"], worker_identity="retriever", worker_stopped=True,
                                 cancel=True, reason="Explicit worker check after the original imported interruption.")
        self.assertEqual(self.rows("operation_links")[0], original)
        self.settle_scopes()
        ledger, after = self.exported_context("historical-stopped")
        self.assertTrue(after["stop_checks"]["settled"], after["stop_checks"])
        followup = next(q for q in after["question_bindings"] if q["question_id"] == "decision")["followups"][0]
        self.assertEqual((followup["execution_state"], followup["receipt_execution_state"], followup["termination_basis"]),
                         ("failed", "unknown", "coordinator_attestation"))
        self.assertEqual(followup["worker_stop_proof"]["statement"]["receipt_sha256"], original["receipt_sha256"])
        historical = EXPORTS.validate_ledger(self.root, old)
        self.assertTrue(historical["stale"])
        self.assertEqual(historical["stop_checks"]["unresolved_operation_ids"], ["read-one"])
        self.assertEqual((self.root / "operations/read-one/receipt.json").read_bytes(), receipt_bytes)
        self.assertEqual(BUNDLE.budget.status(self.root)["read_attempts"], 1)
        self.assertTrue(BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(after))["graph"]["stop_checks"]["settled"])

    def test_ledger_flags_without_registration_do_not_authorize_pending_leads(self):
        fake = {"sources": [], "_graph": {"schema_version": 1, "run_id": "fixture-run", "graph_sequence": 1, "generated": True, "read_only": True}}
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.validate_ledger(self.root, fake)

    def test_export_recovers_files_before_registration_without_new_snapshot(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        with patch.object(GRAPH, "register_export", side_effect=GRAPH.GraphError("busy", "injected interruption")):
            with self.assertRaises(GRAPH.GraphError):
                EXPORTS.export_snapshot(self.root, "interrupted")
        directory = self.root / "graph/exports/interrupted"
        manifest = (directory / "manifest.json").read_bytes()
        self.packet("later-observation", [INGEST._insert("artifacts", artifact_id="later", title="Later", artifact_type="unknown"),
                                           INGEST._insert("discoveries", discovery_id="later-seed", artifact_id="later", supplied_seed_id="later", observed_title="Later")])
        result = EXPORTS.export_snapshot(self.root, "interrupted")
        self.assertEqual((directory / "manifest.json").read_bytes(), manifest)
        self.assertLess(result["exported_graph_sequence"], result["graph_sequence"])

    def test_verify_rejects_changed_files_and_symlink_escape(self):
        directory, _ = self.export()
        path = directory / "ledger.json"
        saved = path.read_bytes()
        path.write_bytes(saved + b"changed")
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.verify_export(directory)
        path.unlink()
        outside = self.work / "outside.json"
        outside.write_bytes(saved)
        path.symlink_to(outside)
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.verify_export(directory)

    def test_verify_rejects_copied_capture_corruption_even_with_new_manifest_hash(self):
        directory, _ = self.export()
        manifest = BUNDLE.read(directory / "manifest.json")
        relative = "evidence/operations/read-one/stdout"
        data = b"changed capture"
        (directory / relative).write_bytes(data)
        entry = next(row for row in manifest["files"] if row["relative_path"] == relative)
        entry.update(sha256=EXPORTS._sha(data), size_bytes=len(data))
        BUNDLE.write(directory / "manifest.json", manifest)
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.verify_export(directory)

    def test_unsynchronized_originals_are_retained_without_changing_snapshot_inventory(self):
        self.operation(sources=[{"source_id": "doc-a", "namespace": "fixture-doc", "external_id": "A"},
                                {"source_id": "new-child", "namespace": "fixture-doc", "external_id": "B"}])
        result = EXPORTS.export_snapshot(self.root, "before-sync")
        directory = Path(result["manifest_path"]).parent
        ledger = BUNDLE.read(directory / "ledger.json")
        self.assertEqual({row["id"] for row in ledger["sources"]}, {"doc-a"})
        self.assertEqual(BUNDLE.read(directory / "evidence/operations/read-one/result.json")["source_ids"], ["doc-a", "new-child"])
        self.assertTrue(any("not normalized" in limit for limit in result["limitations"]))

    def test_bounded_prior_reuse_preserves_scopes_without_fresh_reading(self):
        directory, _ = self.export(return_evidence=True)
        snapshot = BUNDLE.read(directory / "graph.json")
        assertion_id = snapshot["tables"]["assertions"][0]["assertion_id"]
        root = self.target()
        before_budget = (root / "research-budget.json").read_bytes()
        result = EXPORTS.import_prior(root, directory, "prior-one", self.selection(record_ids={"assertion": [assertion_id]}))
        self.assertEqual(result["status"], "pass")
        self.assertEqual((root / "research-budget.json").read_bytes(), before_budget)
        self.assertEqual(list((root / "operations").iterdir()), [])
        self.assertTrue(all(i["provenance_basis"] == "prior_run" for i in self.target_rows(root, "inspections")))
        self.assertEqual(self.target_rows(root, "sufficient_inspection"), [])
        self.assertEqual(len(self.target_rows(root, "reference_occurrences")), 3)
        findings = [a for a in self.target_rows(root, "assertions") if a["kind"] == "finding"]
        self.assertEqual(findings[0]["basis"], "prior_run")
        self.assertIsNotNone(findings[0]["subject_locator_id"])
        self.assertEqual(self.target_rows(root, "assertion_decisions"), [])
        self.assertTrue(EXPORTS.import_prior(root, directory, "prior-one", self.selection(record_ids={"assertion": [assertion_id]}))["idempotent"])
        self.assertEqual(GRAPH.validate(root)["status"], "pass")
        exported = EXPORTS.export_snapshot(root, "historical-context")
        ledger = BUNDLE.read(Path(exported["manifest_path"]).parent / "ledger.json")
        context = EXPORTS.validate_ledger(root, ledger)
        historical = [i for source in context["source_bindings"] for i in source["inspections"]]
        self.assertTrue(historical)
        self.assertTrue(all(i["provenance_basis"] == "prior_run" and i["scopes"] == [] for i in historical))
        self.assertFalse(context["stop_checks"]["settled"])
        self.assertTrue(all(not source["freshness_checked"] for source in context["source_bindings"]))

    def test_repeated_imports_deduplicate_source_and_keep_historical_lineage(self):
        directory, _ = self.export(return_evidence=True)
        root = self.target()
        EXPORTS.import_prior(root, directory, "prior-one", self.selection())
        EXPORTS.import_prior(root, directory, "prior-two", self.selection())
        keys = [row for row in self.target_rows(root, "artifact_keys") if row["namespace"] == "fixture-doc" and row["external_id"] == "A"]
        self.assertEqual(len(keys), 1)
        mappings = [row for row in self.target_rows(root, "prior_run_records") if row["prior_record_kind"] == "artifact"]
        self.assertEqual({row["artifact_id"] for row in mappings}, {keys[0]["artifact_id"]})
        self.assertEqual(len(self.target_rows(root, "captures")), 2)
        self.assertEqual(self.target_rows(root, "sufficient_inspection"), [])
        self.assertEqual(GRAPH.validate(root)["status"], "pass")

    def test_import_retry_rechecks_copied_originals_and_rejects_native_upgrade(self):
        directory, _ = self.export(return_evidence=True)
        snapshot = BUNDLE.read(directory / "graph.json")
        assertion_id = snapshot["tables"]["assertions"][0]["assertion_id"]
        root = self.target()
        selection = self.selection(record_ids={"assertion": [assertion_id]})
        with patch.object(GRAPH, "ingest", side_effect=GRAPH.GraphError("busy", "injected interruption")):
            with self.assertRaises(GRAPH.GraphError):
                EXPORTS.import_prior(root, directory, "interrupted", selection)
        packet_path = root / "graph/imports/interrupted/packet.json"
        packet = BUNDLE.read(packet_path)
        capture = next(row["record"] for row in packet["mutations"] if row["table"] == "captures")
        path = root / capture["relative_path"]
        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.import_prior(root, directory, "interrupted", selection)
        self.assertEqual(self.target_rows(root, "prior_run_imports"), [])
        path.write_bytes(original)
        next(row["record"] for row in packet["mutations"] if row["table"] == "inspections")["provenance_basis"] = "native"
        packet_path.write_bytes(EXPORTS._bytes(packet))
        proof_path = root / "graph/imports/interrupted/proof.json"
        proof = BUNDLE.read(proof_path)
        proof["packet_sha256"] = EXPORTS._sha(EXPORTS._bytes(packet))
        proof_path.write_bytes(EXPORTS._bytes(proof))
        with self.assertRaises(GRAPH.GraphError):
            GRAPH.ingest(root, packet_path, evidence_validator=EXPORTS.evidence_validator)
        self.assertEqual(self.target_rows(root, "inspections"), [])

    def test_prior_export_import_preserves_ancestors_and_enforces_exclusions(self):
        directory, _ = self.export(return_evidence=True)
        root = self.target()
        EXPORTS.import_prior(root, directory, "prior-one", self.selection())
        second = EXPORTS.export_snapshot(root, "second")
        second_dir = Path(second["manifest_path"]).parent
        aid = next(row["artifact_id"] for row in self.target_rows(root, "artifact_keys") if row["namespace"] == "fixture-doc")
        third = self.work / "third"
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["research_plan"] = False
        contract["prior_research"] = {"policy": "allow", "excluded_run_ids": ["fixture-run"]}
        BUNDLE.init(third, contract)
        GRAPH.initialize(third, "coordinator", "third-run")
        with self.assertRaises(GRAPH.GraphError) as caught:
            EXPORTS.import_prior(third, second_dir, "excluded-ancestor", self.selection(artifact_ids=[aid]))
        self.assertEqual(caught.exception.code, "permission_denied")
        contract["prior_research"] = {"policy": "allow"}
        fourth = self.work / "fourth"
        BUNDLE.init(fourth, contract)
        GRAPH.initialize(fourth, "coordinator", "fourth-run")
        EXPORTS.import_prior(fourth, second_dir, "allowed-ancestor", self.selection(artifact_ids=[aid]))
        prior = next(row for row in self.target_rows(fourth, "prior_run_records") if row["prior_record_kind"] == "artifact")
        self.assertEqual({row["run_id"] for row in json.loads(prior["lineage_json"])}, {"fixture-run", "target-run"})
        self.assertEqual(GRAPH.validate(fourth)["status"], "pass")

    def test_import_requires_explicit_contract_policy_and_bounded_selection(self):
        directory, _ = self.export()
        root = self.target({"policy": "exclude"})
        with self.assertRaises(GRAPH.GraphError) as error:
            EXPORTS.import_prior(root, directory, "denied", self.selection())
        self.assertEqual(error.exception.code, "permission_denied")
        self.assertEqual(self.target_rows(root, "prior_run_imports"), [])
        root2 = self.work / "target2"
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["research_plan"] = False
        contract["prior_research"] = {"policy": "allow"}
        BUNDLE.init(root2, contract)
        GRAPH.initialize(root2, "coordinator", "target2")
        with self.assertRaises(GRAPH.GraphError):
            EXPORTS.import_prior(root2, directory, "empty", self.selection(artifact_ids=[]))

    def test_prior_access_outcome_remains_historical_without_current_disposition(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.packet("historical-access", [INGEST._insert("source_outcomes", outcome_id="old-access", artifact_id="source-a",
                    question_id="decision", required_scope_key="comments", readability="access_blocked", disposition="deferred",
                    reason="Fictional prior comments permission was unavailable", actor_id="coordinator", observed_at=GRAPH._now())])
        result = EXPORTS.export_snapshot(self.root, "with-access")
        root = self.target()
        EXPORTS.import_prior(root, Path(result["manifest_path"]).parent, "prior-access", self.selection())
        self.assertEqual(self.target_rows(root, "source_outcomes"), [])
        historical = next(row for row in self.target_rows(root, "prior_run_records") if row["prior_record_kind"] == "artifact")
        self.assertIn("access_blocked", historical["limitations"])
        self.assertIn("comments", historical["limitations"])

    def legacy(self, with_return=False):
        self.operation()
        if with_return:
            self.returned()
        old = self.work / "legacy"
        shutil.copytree(self.root, old)
        shutil.rmtree(old / "graph")
        ledger = BUNDLE.read(old / "plans" / (self.plan_hash + ".ledger.json"))
        ledger["sources"][0]["reads"] = ["read-one"]
        ledger["sources"][0]["finding"] = "Historical claim about the fictional pause"
        ledger["sources"][0]["roles"] = ["trunk", "critical"]
        BUNDLE.write(old / "ledger.json", ledger)
        return old

    def test_migration_preserves_originals_and_does_not_turn_read_flags_into_inspection(self):
        old = self.legacy()
        before = EXPORTS._tree(old)
        destination = self.work / "migrated"
        result = EXPORTS.migrate_legacy(old, destination, run_id="migrated-run", actor_id="coordinator")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(before, EXPORTS._tree(old))
        self.assertEqual(self.target_rows(destination, "inspections"), [])
        self.assertEqual(self.target_rows(destination, "sufficient_inspection"), [])
        self.assertTrue(self.target_rows(destination, "captures"))
        self.assertTrue(all(a["basis"] == "legacy_attested" for a in self.target_rows(destination, "assertions")))
        self.assertEqual((destination / "operations/read-one/stdout").read_bytes(), before["operations/read-one/stdout"])

    def test_migration_preserves_explicit_reader_attestation_and_pending_leads(self):
        old = self.legacy(with_return=True)
        destination = self.work / "migrated"
        EXPORTS.migrate_legacy(old, destination, run_id="migrated-run", actor_id="coordinator")
        inspections = self.target_rows(destination, "inspections")
        self.assertEqual(len(inspections), 1)
        self.assertEqual(inspections[0]["provenance_basis"], "legacy_attested")
        self.assertEqual(len(self.target_rows(destination, "leads")), 3)
        self.assertTrue(all(r["state"] == "pending" for r in self.target_rows(destination, "followup_obligations")))

    def test_migration_missing_receipt_retains_candidates_and_limitations(self):
        old = self.legacy()
        (old / "operations/read-one/receipt.json").unlink()
        destination = self.work / "migrated"
        result = EXPORTS.migrate_legacy(old, destination, run_id="migrated-run", actor_id="coordinator")
        self.assertTrue(result["pending"])
        self.assertEqual(self.target_rows(destination, "captures"), [])
        self.assertGreaterEqual(len(self.target_rows(destination, "discoveries")), 2)
        self.assertEqual(self.target_rows(destination, "inspections"), [])

    def test_migration_can_export_historical_plan_without_creating_work(self):
        old = self.legacy(with_return=True)
        destination = self.work / "migrated"
        EXPORTS.migrate_legacy(old, destination, run_id="migrated-run", actor_id="coordinator")
        self.assertEqual(self.target_rows(destination, "work_items"), [])
        result = EXPORTS.export_snapshot(destination, "historical")
        manifest = BUNDLE.read(result["manifest_path"])
        self.assertEqual(manifest["plan_sha256"], self.plan_hash)
        self.assertTrue(any("legacy plan" in item for item in manifest["limitations"]))

    def test_migration_keeps_unknown_question_lead_without_inventing_scope(self):
        old = self.legacy()
        ledger = BUNDLE.read(old / "ledger.json")
        ledger["sources"][0]["leads"] = [{"id": "unknown-scope", "kind": "vocabulary", "value": "Old exact term", "questions": ["missing-question"], "reason": "Historical vocabulary"}]
        BUNDLE.write(old / "ledger.json", ledger)
        destination = self.work / "migrated"
        result = EXPORTS.migrate_legacy(old, destination, run_id="migrated-run", actor_id="coordinator")
        self.assertEqual(self.target_rows(destination, "leads"), [])
        self.assertTrue(any("Old exact term" in row["value_json"] for row in self.target_rows(destination, "assertions")))
        self.assertEqual(self.target_rows(destination, "lead_origins"), [])
        self.assertTrue(any(row.get("unknown_questions") == ["missing-question"] for row in result["pending"]))


if __name__ == "__main__":
    unittest.main()
