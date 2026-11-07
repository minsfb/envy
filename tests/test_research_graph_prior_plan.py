"""Prior imports become current work through authenticated generated plans.

All captures are local fictional files. No provider/network calls occur.
"""

import copy
from datetime import datetime, timezone
from pathlib import Path
import unittest

import test_research_graph_exports as fixture


GRAPH, EXPORTS, BUNDLE, WORK = fixture.GRAPH, fixture.EXPORTS, fixture.BUNDLE, fixture.WORK
INGEST = fixture.INGEST


class PriorGraphPlanningTests(unittest.TestCase):
    setUp = fixture.ResearchGraphExportsTests.setUp
    operation = fixture.ResearchGraphExportsTests.operation
    returned = fixture.ResearchGraphExportsTests.returned
    packet = fixture.ResearchGraphExportsTests.packet
    rows = fixture.ResearchGraphExportsTests.rows
    export = fixture.ResearchGraphExportsTests.export
    selection = fixture.ResearchGraphExportsTests.selection

    def imported(self, with_evidence=False):
        previous, _ = self.export(return_evidence=with_evidence)
        target = self.work / "reuse"
        contract = copy.deepcopy(self.contract)
        contract["prior_research"] = {"policy": "allow"}
        BUNDLE.init(target, contract)
        GRAPH.initialize(target, "coordinator", "reuse-run")
        EXPORTS.import_prior(target, previous, "prior", self.selection(policy="leads_only"))
        result = EXPORTS.export_snapshot(target, "imported")
        ledger = BUNDLE.read(Path(result["manifest_path"]).parent / "ledger.json")
        source = next(s for s in ledger["sources"] if s["title"] == "Orchard review")
        plan = copy.deepcopy(self.plan)
        plan["items"] = [copy.deepcopy(plan["items"][0])]
        plan["items"][0]["request"]["source_id"] = source["id"]
        return target, plan, ledger, source

    def test_imported_inventory_plans_and_admits_current_read_without_new_evidence(self):
        target, plan, ledger, source = self.imported()
        with GRAPH.snapshot(target) as connection:
            before = [dict(r) for r in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")]
            count = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            self.assertIsNone(WORK._artifact(connection, source["id"]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inspections").fetchone()[0], 0)
        saved = BUNDLE.save_plan(target, plan, ledger)
        plan_hash = BUNDLE.read(target / "bundle.json")["plan_sha256"]
        binding = saved["graph"]["bindings"][0]
        with GRAPH.snapshot(target) as connection:
            self.assertEqual(WORK._artifact(connection, source["id"]), source["graph_artifact_id"])
            self.assertEqual([dict(r) for r in connection.execute("SELECT * FROM discoveries ORDER BY discovery_id")], before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], count)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inspections").fetchone()[0], 0)
            work = dict(connection.execute("SELECT * FROM work_items WHERE work_id=?", (binding["work_id"],)).fetchone())
        claimed = WORK.claim(target, work["work_id"], "retriever", plan_hash, "retrieve", expected_revision=work["revision"])
        WORK.start_assignment(target, claimed["assignment_id"], expected_revision=1)
        BUNDLE.budget.activity(target, "retriever", "start")
        spec = dict(id="current-read", actor="retriever", provider="archive", kind="read", request=plan["items"][0]["request"],
                    plan_sha256=plan_hash, plan_item="retrieve", graph_assignment_id=claimed["assignment_id"],
                    research={"stage": work["stage"], "branch": work["branch"], "parent_operation": None,
                              "root_basis": "Explicitly allowed prior source, now rechecked"})
        WORK.prepare_operation(target, spec)
        BUNDLE.start(target, spec, "tool-import")
        WORK.record_admission(target, spec)
        WORK.record_dispatch(target, "current-read")
        BUNDLE.import_output(target, "current-read", fixture.fixture.FIXTURES / "source.html", None, "ok")
        WORK.record_completion(target, "current-read")
        index = dict(source_ids=[source["id"]], sources=[dict(source_id=source["id"], namespace="fixture-doc", external_id="A",
                     title="Orchard review", type="document")], pagination="complete", next_cursor=None,
                     note="Current fixture retrieval, not an inspection")
        BUNDLE.index_result(target, "current-read", index)
        INGEST.index_operation(target, "current-read")
        with GRAPH.snapshot(target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], count)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inspections").fetchone()[0], 0)
            observed = connection.execute("SELECT artifact_id FROM discoveries WHERE operation_id='current-read'").fetchone()
            self.assertEqual(observed[0], source["graph_artifact_id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM discoveries WHERE supplied_seed_id IS NOT NULL").fetchone()[0],
                             sum(r["supplied_seed_id"] is not None for r in before))
        GRAPH.validate(target)

    def test_changed_imported_graph_identity_cannot_bind_a_plan_alias(self):
        target, plan, ledger, source = self.imported()
        altered = copy.deepcopy(ledger)
        altered["sources"][0]["graph_artifact_id"] = "invented-identity"
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.save_plan(target, plan, altered)
        with GRAPH.snapshot(target) as connection:
            self.assertIsNone(WORK._artifact(connection, source["id"]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM plan_snapshots").fetchone()[0], 0)

    def test_prior_leads_survive_alias_changes_without_current_return_credit(self):
        target, plan, ledger, source = self.imported(with_evidence=True)
        self.assertTrue(source["leads"], "Imported organizing leads must be visible before a current source alias exists")
        # Reconcile a local source alias to an already imported stable identity.
        # This adds no retrieval, inspection or reader-return budget event.
        GRAPH.ingest(target, dict(schema_version=1, batch_id="current-source-alias", actor_id="coordinator",
            assignment_id=None, packet_kind="reconcile", contract_sha256=BUNDLE.digest(target / "contract.json"),
            submitted_at=datetime.now(timezone.utc).isoformat(), mutations=[INGEST._insert("artifact_keys",
                namespace="bundle-source:reuse-run", external_id="doc-a", artifact_id=source["graph_artifact_id"],
                basis="Local alias for the exact imported fixture provider identity")]))
        result = EXPORTS.export_snapshot(target, "current-alias")
        ledger = BUNDLE.read(Path(result["manifest_path"]).parent / "ledger.json")
        current = next(s for s in ledger["sources"] if s["id"] == "doc-a")
        self.assertEqual({lead["id"] for lead in source["leads"]}, {lead["id"] for lead in current["leads"]})
        with GRAPH.snapshot(target) as connection:
            prior_ids = {r[0] for r in connection.execute("""SELECT r.return_id FROM reader_returns r
                JOIN prior_run_imports p ON p.imported_batch_id=r.created_batch_id WHERE p.policy<>'legacy_attested'""")}
        for lead in current["leads"]:
            self.assertEqual(lead["return_refs"], [])
            self.assertTrue(lead["graph_prior_return_refs"])
            self.assertTrue(all(r["return_id"] in prior_ids for r in lead["graph_prior_return_refs"]))
        self.assertEqual(current["inspections"], [])
        plan["items"][0]["request"]["source_id"] = "doc-a"
        BUNDLE.save_plan(target, plan, ledger)
        self.assertEqual(list((target / "reader-returns").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
