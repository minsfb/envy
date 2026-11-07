"""Exercise graph inventories through actual planning and publication boundaries."""

import copy
from html import escape
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_graph_ingest as fixture
import test_research_graph_cli as cli_fixture
import test_research_graph_exports as export_fixture


INGEST, GRAPH, BUNDLE = fixture.INGEST, fixture.GRAPH, fixture.BUNDLE
EXPORTS = INGEST._sibling("research_graph_exports")


class GraphBundleTests(unittest.TestCase):
    setUp = fixture.ResearchGraphIngestTests.setUp
    operation = fixture.ResearchGraphIngestTests.operation
    returned = fixture.ResearchGraphIngestTests.returned
    packet = fixture.ResearchGraphIngestTests.packet

    def export(self, identity="current"):
        receipt = EXPORTS.export_snapshot(self.root, identity)
        return BUNDLE.read(Path(receipt["manifest_path"]).parent / "ledger.json")

    def test_new_plan_requires_generated_inventory_and_retains_historical_plan(self):
        original = BUNDLE.read(self.root / "plans" / (self.plan_hash + ".ledger.json"))
        with self.assertRaisesRegex(ValueError, "registered generated ledger"):
            BUNDLE.save_plan(self.root, self.plan, original)
        ledger = self.export()
        saved = BUNDLE.save_plan(self.root, self.plan, ledger)
        self.assertIn("graph", saved)
        # Registration is research history, so the saved snapshot is now stale.
        self.assertTrue(EXPORTS.validate_ledger(self.root, ledger)["stale"])
        BUNDLE.render_plan(self.root)
        with self.assertRaisesRegex(ValueError, "stale"):
            BUNDLE.save_plan(self.root, self.plan, ledger)
        BUNDLE.save_plan(self.root, self.plan, self.export("after-registration"))

    def test_edited_export_and_flags_cannot_bypass_planning_or_validation(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        ledger = self.export()
        self.assertEqual(BUNDLE.validate(self.root, ledger, "research")["operations"], 1)
        changed = copy.deepcopy(ledger)
        changed["sources"][0]["finding"] = "An invented conclusion."
        for action in (lambda: BUNDLE.save_plan(self.root, self.plan, changed),
                       lambda: BUNDLE.validate(self.root, changed, "research")):
            with self.assertRaises(GRAPH.GraphError):
                action()
        changed.pop("_graph")
        with self.assertRaisesRegex(ValueError, "registered generated ledger"):
            BUNDLE.validate(self.root, changed, "research")

    def test_returned_pending_leads_can_be_planned_without_invented_destinations(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        ledger = self.export()
        leads = ledger["sources"][0]["leads"]
        self.assertTrue(leads)
        self.assertTrue(all("plan_item" not in lead and "target_source_id" not in lead for lead in leads))
        BUNDLE.save_plan(self.root, self.plan, ledger)

    def test_reference_discovery_round_trip_does_not_manufacture_supplied_seeds(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        with GRAPH.snapshot(self.root) as connection:
            reference = dict(connection.execute("SELECT * FROM reference_occurrences WHERE kind='link' LIMIT 1").fetchone())
        self.packet("linked-candidate", [
            INGEST._insert("artifacts", artifact_id="appendix", title="migration appendix", artifact_type="unknown"),
            INGEST._insert("artifact_keys", namespace="bundle-source:fixture-run", external_id="unresolved-appendix",
                           artifact_id="appendix", basis="Referenced identity; target has not been retrieved"),
            INGEST._insert("discoveries", discovery_id="appendix-reference", artifact_id="appendix",
                           origin_reference_id=reference["reference_id"], observed_title="migration appendix",
                           observed_url=reference["original_value"])])
        with GRAPH.snapshot(self.root) as connection:
            before = [dict(row) for row in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")]
            references = [dict(row) for row in connection.execute("SELECT * FROM reference_occurrences ORDER BY reference_id")]
        ledger = self.export()
        linked = [source for source in ledger["sources"] if source["id"] != "doc-a"]
        self.assertTrue(linked)
        for source in linked:
            self.assertEqual(source["discovered_by"], ["graph:" + source["graph_artifact_id"]])
        BUNDLE.save_plan(self.root, self.plan, ledger)
        BUNDLE.save_plan(self.root, self.plan, self.export("round-trip"))
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(before, [dict(row) for row in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")])
            self.assertEqual(references, [dict(row) for row in connection.execute("SELECT * FROM reference_occurrences ORDER BY reference_id")])

    def test_older_generated_seed_labels_remain_views_during_plan_registration(self):
        original = EXPORTS._compatibility_ledger

        def legacy_projection(*args):
            ledger, limits = original(*args)
            for source in ledger["sources"]:
                source["discovered_by"] = ["seed:graph-" + value[6:] if value.startswith("graph:") else value
                                           for value in source["discovered_by"]]
            return ledger, limits

        with patch.object(EXPORTS, "_compatibility_ledger", side_effect=legacy_projection):
            ledger = self.export("older-format")
        self.assertTrue(ledger["sources"][0]["discovered_by"][0].startswith("seed:graph-"))
        with GRAPH.snapshot(self.root) as connection:
            before = [dict(row) for row in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")]
        BUNDLE.save_plan(self.root, self.plan, ledger)
        with GRAPH.snapshot(self.root) as connection:
            self.assertEqual(before, [dict(row) for row in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")])

    def test_graph_discovery_pointer_needs_verified_export(self):
        source = {"id": "doc-a", "graph_artifact_id": "source-a"}
        self.assertFalse(BUNDLE.backed_discovery("graph:source-a", source, {}, None))
        ledger = self.export()
        altered = copy.deepcopy(ledger)
        altered["sources"][0]["discovered_by"] = ["graph:invented"]
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.save_plan(self.root, self.plan, altered)

    def test_accepted_analysis_retains_reader_expansion_for_enabled_followups(self):
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        returned = self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        with GRAPH.snapshot(self.root) as connection:
            assertion = dict(connection.execute("SELECT * FROM assertions WHERE kind='finding'").fetchone())
        self.packet("accept-finding", [INGEST._insert("assertion_decisions", decision_id="accepted-finding",
            assertion_id=assertion["assertion_id"], expected_revision=0, disposition="accepted",
            actor_id="coordinator", reason="The owned fixture passage supports this finding.")], kind="reconcile")
        contract = BUNDLE.contract_at(self.root)
        contract["requirements"].update(search_routes=True, discovery_followups=True)
        BUNDLE.amend(self.root, contract)
        self.contract_hash = BUNDLE.digest(self.root / "contract.json")
        self.packet("followup-contract", [INGEST._insert("contract_snapshots", contract_sha256=self.contract_hash,
            contract_revision=2, relative_path="contracts/" + self.contract_hash + ".json")], kind="reconcile")
        plan = copy.deepcopy(self.plan)
        plan.update(wave_strategy="Inspect the supplied record, preserve every lead, then plan its follow-ups.", checkpoints=[],
            search_routes=[dict(id="archive-text", provider="archive", family="archive", basis="Owned fixture adapter",
                checked_at=GRAPH._now(), limitations="Focused regression uses an already supplied source.",
                artifact_types=["document"], locations=["archive"], availability="available", invocation={"tool":"owned-fixture"},
                modes=[dict(id="text", input_kinds=["term"], filter_fields=[])])],
            search_branches=[dict(id="archive-review", coverage=["history"], route="archive-text", mode="text",
                artifact_type="document", location="archive", work_type="review", status="omitted",
                reason="This focused fixture tests expansion after an already supplied source read.")])
        ledger = self.export("accepted-analysis")
        source = next(s for s in ledger["sources"] if s["id"] == "doc-a")
        self.assertEqual(source["analysis"]["status"], "complete")
        self.assertIn(returned["expansion_review"], source["expansion_review"])
        self.assertEqual(source["graph_expansion_reviews"], [dict(return_id="reader-one", read_id="read-one", actor="reader",
            text=returned["expansion_review"], provenance_basis="native")])
        # This exact production boundary previously rejected its own generated
        # projection as "analyzed source needs expansion_review".
        BUNDLE.save_plan(self.root, plan, ledger)
        unaccepted = copy.deepcopy(returned)
        unaccepted.update(id="later-unaccepted", expansion_review="A later unaccepted review must not enter the projection.")
        BUNDLE.budget.reader_return(self.root, unaccepted)
        newer = self.export("unaccepted-review")
        newer_source = next(s for s in newer["sources"] if s["id"] == "doc-a")
        self.assertEqual(newer_source["graph_expansion_reviews"], source["graph_expansion_reviews"])
        self.assertNotIn("later unaccepted", newer_source["expansion_review"])

    def test_separate_semantic_review_is_bound_and_does_not_edit_ledger(self):
        ledger = self.export()
        before = json.dumps(ledger, sort_keys=True)
        context = EXPORTS.validate_ledger(self.root, ledger)
        path = self.root / "reviews" / "semantic-review.json"
        path.parent.mkdir()
        review = {"reviewer": "independent-reader", "note": "Pivotal passages and open scopes reviewed.",
                  "ledger_sha256": context["ledger_sha256"], "graph_sequence": context["represented_sequence"]}
        BUNDLE.write(path, review)
        result = BUNDLE.checked_semantic_review(self.root, path, context)
        self.assertEqual(result["sha256"], BUNDLE.digest(path))
        self.assertEqual(json.dumps(ledger, sort_keys=True), before)
        with self.assertRaisesRegex(ValueError, "semantic-review"):
            BUNDLE.checked_semantic_review(self.root, None, context)
        with self.assertRaisesRegex(ValueError, "different generated ledger"):
            BUNDLE.checked_semantic_review(self.root, path, dict(context, represented_sequence=context["represented_sequence"] + 1))
        outside = self.work / "review.json"
        BUNDLE.write(outside, review)
        with self.assertRaisesRegex(ValueError, "inside the bundle"):
            BUNDLE.checked_semantic_review(self.root, outside, context)

    def test_public_import_and_pending_packet_use_historical_proof_validator(self):
        from unittest.mock import patch
        self.directory = self.work
        command = lambda *args, **kwargs: cli_fixture.GraphCommandTests.command(self, *args, **kwargs)
        self.operation()
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        self.export()
        exported = self.root / "graph/exports/current"
        target = self.work / "next-run"
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["research_plan"] = False
        contract["prior_research"] = {"policy": "allow"}
        path = self.work / "next-contract.json"
        BUNDLE.write(path, contract)
        command("init", target, "--contract", path)
        command("graph", "init", target, "--run-id", "next-run")
        selection = {"policy": "attested_evidence", "artifact_ids": ["source-a"],
                     "question_map": {"decision": "decision", "outcomes": "outcomes"},
                     "reason": "Reassess the earlier source in the new run."}
        path = self.work / "selection.json"
        BUNDLE.write(path, selection)
        imported = command("graph", "import", target, "--directory", exported,
                           "--id", "prior", "--selection", path)
        self.assertEqual(imported["status"], "pass")
        with patch.object(GRAPH, "ingest", side_effect=GRAPH.GraphError("busy", "Owned injected interruption")):
            with self.assertRaises(GRAPH.GraphError):
                EXPORTS.import_prior(target, exported, "pending", selection)
        pending = target / "graph/imports/pending/packet.json"
        self.assertTrue(pending.is_file())
        command("graph", "ingest", target, "--file", pending)
        command("graph", "validate", target)
        self.assertEqual(list((target / "operations").iterdir()), [])


class GraphExportCommandTests(unittest.TestCase):
    setUp = cli_fixture.GraphCommandTests.setUp
    command = cli_fixture.GraphCommandTests.command
    initialize = cli_fixture.GraphCommandTests.initialize

    def test_export_verify_and_generated_ledger_use_public_cli(self):
        self.initialize()
        exported = self.command("graph", "export", self.root, "--id", "first")
        directory = Path(exported["manifest_path"]).parent
        verified = self.command("graph", "verify-export", directory)
        self.assertEqual(exported["exported_graph_sequence"], verified["graph_sequence"])
        bound = self.command("graph", "validate-ledger", self.root, "--ledger", directory / "ledger.json")
        self.assertFalse(bound["stale"])
        self.assertTrue(self.command("graph", "export", self.root, "--id", "first")["idempotent"])
        changed = self.directory / "changed.json"
        ledger = json.loads((directory / "ledger.json").read_text())
        ledger["sources"] = [{"id": "invented"}]
        changed.write_text(json.dumps(ledger))
        failed = self.command("graph", "validate-ledger", self.root, "--ledger", changed, expected=1)
        self.assertEqual(failed["error"]["code"], "integrity_error")

    def test_public_migration_preserves_legacy_source(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        destination = self.directory / "migrated"
        self.command("graph", "migrate", self.root, "--destination", destination,
                     "--run-id", "legacy-copy", "--actor", "coordinator")
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.command("graph", "validate", destination)


class GraphPublicationTests(unittest.TestCase):
    setUp = fixture.ResearchGraphIngestTests.setUp
    operation = fixture.ResearchGraphIngestTests.operation
    returned = fixture.ResearchGraphIngestTests.returned
    packet = fixture.ResearchGraphIngestTests.packet
    rows = fixture.ResearchGraphIngestTests.rows
    settle_scopes = export_fixture.ResearchGraphExportsTests.settle_scopes
    exported_context = export_fixture.ResearchGraphExportsTests.exported_context
    semantic_review = export_fixture.ResearchGraphExportsTests.semantic_review

    def test_publication_binds_separate_review_to_exact_generated_inventory(self):
        self.operation(sources=[{"source_id": "doc-a", "namespace": "fixture-doc", "external_id": "A",
                                "title": "Orchard review", "url": "https://example.invalid/a", "type": "document"}])
        INGEST.index_operation(self.root, "read-one")
        self.returned()
        INGEST.ingest_reader_return(self.root, "reader-one")
        self.settle_scopes()
        ledger, context = self.exported_context("publication")
        review = self.semantic_review(context)
        rows = []
        for source in ledger["sources"]:
            cells = "".join("<td>" + escape(source[field]) + "</td>" for field in
                            ("id", "title", "coverage", "finding", "contributor_basis"))
            rows.append("<tr>" + cells + '<td><a href="' + escape(source["url"], quote=True) + '">Source</a></td></tr>')
        bodies = {"brief": '<p>The supplied record leaves wider outcomes unresolved.</p><a href="https://example.invalid/companion">Evidence</a>',
                  "companion": '<a href="https://example.invalid/brief">Brief</a><table>' + "".join(rows) + "</table>"}
        artifacts = []
        for name, body in bodies.items():
            files = {}
            for side in ("expected", "actual"):
                path = self.root / (name + "-" + side + ".html")
                path.write_text(body)
                files[side] = [str(path)]
            artifacts.append(dict(id=name, url="https://example.invalid/" + name, **files))
        manifest = {"artifacts": artifacts, "candidate_tables": [0],
                    "plan_review": {"reviewer": "fixture-reviewer", "note": "Owned fixture inventory and bounds match.",
                                    "companion_locator": "Source inventory", **BUNDLE.render_plan(self.root, ledger)}}
        original = json.dumps(ledger, sort_keys=True)
        result = BUNDLE.publication(self.root, ledger, manifest, review)
        self.assertEqual(result["semantic_review"]["sha256"], BUNDLE.digest(review))
        self.assertEqual(result["candidate_check"]["rows_checked"], len(ledger["sources"]))
        self.assertEqual(json.dumps(ledger, sort_keys=True), original)
        changed = BUNDLE.read(review)
        changed["ledger_sha256"] = "0" * 64
        BUNDLE.write(review, changed)
        with self.assertRaisesRegex(ValueError, "different generated ledger"):
            BUNDLE.publication(self.root, ledger, manifest, review)


if __name__ == "__main__":
    unittest.main()
