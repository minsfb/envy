"""Explicit frozen exclusions permit unlinked rows without research credit.

These tests use the actual recorder, registered export and publication validator
with owned fictional fixtures. They do not contact providers or user services.
"""

import copy
from html import escape
import unittest

import test_research_graph_bundle as fixture


GRAPH, INGEST, EXPORTS, BUNDLE = fixture.GRAPH, fixture.INGEST, fixture.EXPORTS, fixture.BUNDLE
REASON = "Protected prior output excluded"


class NavigationExclusionTests(unittest.TestCase):
    setUp = fixture.GraphPublicationTests.setUp
    operation = fixture.GraphPublicationTests.operation
    returned = fixture.GraphPublicationTests.returned
    packet = fixture.GraphPublicationTests.packet
    rows = fixture.GraphPublicationTests.rows
    settle_scopes = fixture.GraphPublicationTests.settle_scopes
    exported_context = fixture.GraphPublicationTests.exported_context
    semantic_review = fixture.GraphPublicationTests.semantic_review

    def policy(self, value, *, register=True):
        contract = BUNDLE.contract_at(self.root)
        contract["prior_research"] = value
        BUNDLE.amend(self.root, contract)
        self.contract_hash = BUNDLE.digest(self.root / "contract.json")
        if register:
            revision = max(row["contract_revision"] for row in self.rows("contract_snapshots")) + 1
            self.packet("navigation-policy-" + str(revision), [INGEST._insert(
                "contract_snapshots", contract_sha256=self.contract_hash,
                contract_revision=revision, relative_path="contracts/" + self.contract_hash + ".json")], kind="reconcile")
            self.synchronize_policy_plan()

    def synchronize_policy_plan(self):
        # Even an output-navigation amendment needs a current saved plan before
        # publication. Preserve explicitly bounded research; add no reading credit.
        self.policy_plan_serial = getattr(self, "policy_plan_serial", 0) + 1
        ledger, context = self.exported_context("policy-plan-" + str(self.policy_plan_serial))
        BUNDLE.save_plan(self.root, self.plan, ledger)
        if context["stop_checks"]["settled"]:
            work = INGEST._sibling("research_graph_work")
            changes = [work._update("work_items", row, state="deferred",
                terminal_reason="The navigation amendment retains the explicitly bounded fictional research.",
                transition_reason="Preserve existing research bounds after updating publication policy.")
                for row in self.rows("work_items") if row["state"] == "pending"]
            if changes:
                self.packet("bound-policy-plan-" + str(self.policy_plan_serial), changes,
                            kind="reconcile", validator=work.evidence_validator)

    def prepare(self, policy, *, aliases=False, inspected=True, settled=True):
        self.operation(sources=[
            {"source_id": "doc-a", "namespace": "fixture-doc", "external_id": "A",
             "title": "Orchard review", "url": "https://example.invalid/a", "type": "document"},
            {"source_id": "doc-b", "namespace": "fixture-doc", "external_id": "B",
             "title": "Ordinary record", "url": "https://example.invalid/b", "type": "document"},
        ])
        INGEST.index_operation(self.root, "read-one")
        if inspected:
            self.returned()
            INGEST.ingest_reader_return(self.root, "reader-one")
        if aliases:
            self.packet("known-navigation-alias", [
                INGEST._insert("artifacts", artifact_id="source-a-alias", title="Orchard duplicate identity",
                    artifact_type="document", canonical_artifact_id="source-a",
                    canonical_basis="Verified duplicate of the same fictional record"),
                INGEST._insert("artifact_keys", namespace="fixture-alias", external_id="A-old",
                    artifact_id="source-a-alias", basis="Retained alternate provider identity"),
                INGEST._insert("artifact_keys", namespace="bundle-source:fixture-run", external_id="doc-a-alias",
                    artifact_id="source-a-alias", basis="Known recorder source alias"),
                INGEST._insert("discoveries", discovery_id="seed-alias", artifact_id="source-a-alias",
                    supplied_seed_id="fixture-alias", observed_title="Orchard duplicate identity"),
            ], kind="reconcile")
        if settled:
            self.settle_scopes()
            ordinary = next(row["artifact_id"] for row in self.rows("artifact_keys")
                            if row["namespace"] == "fixture-doc" and row["external_id"] == "B")
            self.packet("ordinary-scope-bounds", [INGEST._insert(
                "source_outcomes", outcome_id="ordinary-" + question, artifact_id=ordinary,
                question_id=question, required_scope_key="body", readability="readable",
                disposition="deferred", actor_id="coordinator", observed_at=GRAPH._now(),
                reason="The fictional ordinary source is explicitly outside further assessment.")
                for question in ("decision", "outcomes")], kind="reconcile")
        self.policy(policy)
        return self.exported_context("navigation-ready")

    def manifest(self, ledger, *, reasons=None, omitted_links=None, displayed_reasons=None, extra_links=None):
        reasons = reasons or {}
        omitted_links = set(reasons) if omitted_links is None else set(omitted_links)
        displayed_reasons, extra_links = displayed_reasons or {}, extra_links or {}
        rows, mappings = [], []
        for number, source in enumerate(ledger["sources"], 1):
            source_id, marker = source["id"], "candidate-" + str(number)
            mapping = {"source_id": source_id, "row_id": marker}
            if source_id in reasons:
                mapping["navigation_exclusion_reason"] = reasons[source_id]
            mappings.append(mapping)
            cells = "<td>" + marker + "</td>"
            cells += "".join("<td>" + escape(source[field]) + "</td>"
                             for field in ("title", "coverage", "finding", "contributor_basis"))
            if source_id not in omitted_links:
                cells += '<td><a href="' + escape(source["url"].strip(), quote=True) + '">Source</a></td>'
            if source_id in reasons:
                text = displayed_reasons.get(source_id, reasons[source_id])
                cells += "<td>" + escape(text) + "</td>"
            for link in extra_links.get(source_id, []):
                cells += '<td><a href="' + escape(link, quote=True) + '">Related record</a></td>'
            rows.append("<tr>" + cells + "</tr>")
        bodies = {
            "brief": '<p>The supplied record leaves wider outcomes unresolved.</p><a href="https://example.invalid/companion">Evidence</a>',
            "companion": '<a href="https://example.invalid/brief">Brief</a><table>' + "".join(rows) + "</table>",
        }
        artifacts = []
        for name, body in bodies.items():
            files = {}
            for side in ("expected", "actual"):
                path = self.root / ("navigation-" + name + "-" + side + ".html")
                path.write_text(body)
                files[side] = [str(path)]
            artifacts.append(dict(id=name, url="https://example.invalid/" + name, **files))
        return {"artifacts": artifacts, "candidate_tables": [0], "candidate_rows": mappings,
                "plan_review": {"reviewer": "fixture-reviewer", "note": "Owned inventory and bounds match.",
                                "companion_locator": "Source inventory", **BUNDLE.render_plan(self.root, ledger)}}

    def publish(self, ledger, context, **kwargs):
        manifest = self.manifest(ledger, **kwargs)
        return BUNDLE.publication(self.root, ledger, manifest, self.semantic_review(context))

    def test_exact_provider_identity_allows_unlinked_row_and_keeps_ordinary_link(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_keys": [
            {"namespace": "fixture-doc", "external_id": "A"}]})
        self.assertEqual(set(context["navigation_exclusions"]), {"doc-a"})
        original_ledger = copy.deepcopy(ledger)
        budget = (self.root / "research-budget.json").read_bytes()
        inspections, returns = self.rows("inspections"), self.rows("reader_returns")
        result = self.publish(ledger, context, reasons={"doc-a": REASON},
                              displayed_reasons={"doc-a": REASON + ": retained only to account for the candidate."})
        self.assertEqual(result["candidate_check"]["rows_checked"], 2)
        excluded = result["candidate_check"]["navigation_exclusions"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["reason"], REASON)
        self.assertEqual(excluded[0]["contract_sha256"], self.contract_hash)
        self.assertEqual(ledger, original_ledger)
        self.assertEqual(self.rows("inspections"), inspections)
        self.assertEqual(self.rows("reader_returns"), returns)
        self.assertEqual((self.root / "research-budget.json").read_bytes(), budget)
        with self.assertRaisesRegex(ValueError, "candidate row missing source link: doc-b"):
            self.publish(ledger, context, reasons={"doc-a": REASON}, omitted_links={"doc-a", "doc-b"})

    def test_actual_graph_artifact_id_authorizes_but_source_id_does_not(self):
        ledger, context = self.prepare({"policy": "exclude", "excluded_artifact_ids": ["source-a"]})
        self.publish(ledger, context, reasons={"doc-a": REASON})
        self.policy({"policy": "exclude", "excluded_artifact_ids": ["doc-a"]})
        ledger, context = self.exported_context("source-id-is-not-graph-id")
        self.assertEqual(context["navigation_exclusions"], {})
        with self.assertRaisesRegex(ValueError, "explicit current frozen contract identity: doc-a"):
            self.publish(ledger, context, reasons={"doc-a": REASON})

    def test_policy_only_and_unmatched_exact_identity_never_authorize(self):
        ledger, context = self.prepare({"policy": "exclude"})
        policies = [
            {"policy": "exclude"},
            {"policy": "allow", "excluded_artifact_keys": [{"namespace": "different-provider", "external_id": "A"}]},
            {"policy": "allow", "excluded_artifact_keys": [{"namespace": "fixture-doc", "external_id": "a"}]},
            {"policy": "allow", "excluded_artifact_keys": [{"namespace": "fixture-doc", "external_id": "A-extra"}]},
        ]
        for number, policy in enumerate(policies):
            with self.subTest(policy=policy):
                if number:
                    self.policy(policy)
                    ledger, context = self.exported_context("unmatched-" + str(number))
                self.assertEqual(context["navigation_exclusions"], {})
                with self.assertRaisesRegex(ValueError, "explicit current frozen contract identity: doc-a"):
                    self.publish(ledger, context, reasons={"doc-a": REASON})
                self.publish(ledger, context)

    def test_canonical_and_source_aliases_inherit_explicit_exclusion(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_keys": [
            {"namespace": "fixture-alias", "external_id": "A-old"}]}, aliases=True)
        self.assertEqual(set(context["navigation_exclusions"]), {"doc-a", "doc-a-alias"})
        self.assertEqual({s["graph_artifact_id"] for s in ledger["sources"] if s["id"] != "doc-b"}, {"source-a"})
        result = self.publish(ledger, context, reasons={"doc-a": REASON, "doc-a-alias": REASON})
        self.assertEqual(len(result["candidate_check"]["navigation_exclusions"]), 2)
        self.policy({"policy": "allow", "excluded_artifact_keys": [
            {"namespace": "bundle-source:fixture-run", "external_id": "doc-a-alias"}]})
        ledger, context = self.exported_context("known-source-alias")
        self.assertEqual(set(context["navigation_exclusions"]), {"doc-a", "doc-a-alias"})
        self.publish(ledger, context, reasons={"doc-a": REASON, "doc-a-alias": REASON})

    def test_reason_must_be_normalized_and_its_exact_phrase_visible(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_ids": ["source-a"]})
        for reason in ("", " " + REASON, REASON + "\n", "Protected  prior output excluded"):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "nonempty normalized text"):
                self.publish(ledger, context, reasons={"doc-a": reason})
        for displayed in ("", "Protected prior output included", "A different exclusion explanation"):
            with self.subTest(displayed=displayed), self.assertRaisesRegex(ValueError, "missing navigation exclusion reason: doc-a"):
                self.publish(ledger, context, reasons={"doc-a": REASON}, displayed_reasons={"doc-a": displayed})
        self.publish(ledger, context, reasons={"doc-a": REASON},
                     displayed_reasons={"doc-a": "Note: " + REASON + "; the inventory row remains visible."})

    def test_excluded_row_rejects_source_and_unrelated_hyperlinks(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_ids": ["source-a"]})
        with self.assertRaisesRegex(ValueError, "excluded candidate row must contain no hyperlinks: doc-a"):
            self.publish(ledger, context, reasons={"doc-a": REASON}, omitted_links=set())
        with self.assertRaisesRegex(ValueError, "excluded candidate row must contain no hyperlinks: doc-a"):
            self.publish(ledger, context, reasons={"doc-a": REASON},
                         extra_links={"doc-a": ["https://example.invalid/unrelated"]})

    def test_ordinary_source_cannot_borrow_another_sources_exclusion(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_ids": ["source-a"]})
        with self.assertRaisesRegex(ValueError, "explicit current frozen contract identity: doc-b"):
            self.publish(ledger, context, reasons={"doc-a": REASON, "doc-b": REASON})
        # Authority alone does not remove normal navigation unless this row
        # explicitly declares the visible exclusion reason.
        self.publish(ledger, context)
        with self.assertRaisesRegex(ValueError, "candidate row missing source link: doc-a"):
            self.publish(ledger, context, omitted_links={"doc-a"})

    def test_unregistered_or_changed_ledger_flags_cannot_create_authority(self):
        ledger, context = self.prepare({"policy": "exclude"})
        manifest = self.manifest(ledger, reasons={"doc-a": REASON})
        review = self.semantic_review(context)
        changed = copy.deepcopy(ledger)
        changed["_graph"]["navigation_exclusions"] = {"doc-a": {"contract_sha256": self.contract_hash}}
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.publication(self.root, changed, manifest, review)
        unregistered = copy.deepcopy(ledger)
        unregistered["_graph"]["graph_sequence"] = 999999
        with self.assertRaises(GRAPH.GraphError):
            BUNDLE.publication(self.root, unregistered, manifest, review)

    def test_old_export_keeps_frozen_policy_but_cannot_authorize_current_publication(self):
        old, before = self.prepare({"policy": "allow", "excluded_artifact_ids": ["source-a"]})
        manifest = self.manifest(old, reasons={"doc-a": REASON})
        review = self.semantic_review(before)
        old_hash = self.contract_hash
        # Even before graph synchronization creates a newer research sequence,
        # current recorder policy must not borrow the old contract's authority.
        self.policy({"policy": "exclude"}, register=False)
        historical = EXPORTS.validate_ledger(self.root, old)
        self.assertEqual(historical["navigation_exclusions"], before["navigation_exclusions"])
        self.assertEqual(historical["navigation_exclusions"]["doc-a"]["contract_sha256"], old_hash)
        self.assertNotEqual(self.contract_hash, old_hash)
        with self.assertRaisesRegex(ValueError, "earlier contract"):
            BUNDLE.publication(self.root, old, manifest, review)
        revision = max(row["contract_revision"] for row in self.rows("contract_snapshots")) + 1
        self.packet("synchronize-current-policy", [INGEST._insert(
            "contract_snapshots", contract_sha256=self.contract_hash, contract_revision=revision,
            relative_path="contracts/" + self.contract_hash + ".json")], kind="reconcile")
        historical = EXPORTS.validate_ledger(self.root, old)
        self.assertTrue(historical["stale"])
        self.assertEqual(historical["navigation_exclusions"], before["navigation_exclusions"])
        self.synchronize_policy_plan()
        current, context = self.exported_context("current-policy")
        self.assertEqual(context["navigation_exclusions"], {})
        self.publish(current, context)

    def test_exclusion_does_not_create_inspection_or_settle_research(self):
        ledger, context = self.prepare({"policy": "allow", "excluded_artifact_ids": ["source-a"]},
                                       inspected=False, settled=False)
        self.assertEqual(set(context["navigation_exclusions"]), {"doc-a"})
        self.assertEqual(self.rows("reader_returns"), [])
        self.assertEqual(self.rows("inspections"), [])
        self.assertTrue(all(not source["inspections"] for source in ledger["sources"]))
        self.assertFalse(context["stop_checks"]["settled"])
        with self.assertRaisesRegex(ValueError, "unresolved work"):
            BUNDLE.validate(self.root, ledger, "synthesis", semantic_review=self.semantic_review(context))


if __name__ == "__main__":
    unittest.main()
