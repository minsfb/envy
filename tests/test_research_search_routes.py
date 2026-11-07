"""Isolated acceptance checks for complementary retrieval coverage.

The two fictional search commands deliberately return different source IDs.
They only print supplied fixture data; no services or credentials are used.
"""

import copy
import unittest

import test_research_work_plan as fixture


class SearchRouteTests(unittest.TestCase):
    json_file = fixture.ResearchWorkPlanTests.json_file
    cli = fixture.ResearchWorkPlanTests.cli
    search_item = fixture.ResearchWorkPlanTests.search_item
    save_plan = fixture.ResearchWorkPlanTests.save_plan
    spec = fixture.ResearchWorkPlanTests.spec
    capture = fixture.ResearchWorkPlanTests.capture
    index = fixture.ResearchWorkPlanTests.index
    source = fixture.ResearchWorkPlanTests.source
    validate = fixture.ResearchWorkPlanTests.validate

    def setUp(self):
        fixture.ResearchWorkPlanTests.setUp(self)
        self.contract["requirements"]["search_routes"] = True
        self.cli("amend", "--contract", self.json_file(self.contract))
        self.plan["search_routes"] = [self.route("semantic"), self.route("native")]
        self.plan["search_branches"] = [self.branch("semantic"), self.branch("native")]
        self.plan["search_branches"].append(dict(self.branch("native"), id="evaluation",
                                                 work_type="evaluation", status="omitted",
                                                 reason="Fixture exercises decision retrieval only."))
        first = self.plan["items"][0]
        first.update(search_branch="semantic", input_kind="term")
        second = copy.deepcopy(first)
        second.update(id="find-native", search_branch="native", priority="low")
        self.plan["items"].append(second)

    def route(self, key):
        return {"id": key, "provider": "archive", "family": key,
                "artifact_types": ["document"], "locations": ["archive-store"],
                "availability": "available", "checked_at": "2026-01-01",
                "basis": "Owned fixture prints synthetic search results.",
                "limitations": "No claim about live search recall.",
                "invocation": {"command_prefix": [fixture.sys.executable, "-I", "-B", "-c"]},
                "modes": [{"id": "terms", "input_kinds": ["term", "title"],
                           "filter_fields": ["before"]}]}

    def branch(self, key):
        return {"id": key, "coverage": ["pilot-history"], "work_type": "decision",
                "artifact_type": "document", "location": "archive-store", "route": key,
                "mode": "terms", "status": "selected",
                "reason": "The fixture route can expose sources absent from the other route."}

    def routed_spec(self, item, key):
        spec = self.spec(item, key)
        spec.update(search_branch=item["search_branch"], input_kind=item["input_kind"])
        return spec

    def execute(self, index, key, ids):
        self.save_plan()
        item = self.plan["items"][index]
        spec = self.routed_spec(item, key)
        spec["command"][-1] = "print(" + repr(fixture.json.dumps({"ids": ids})) + ")"
        self.capture(spec)
        self.index(key, ids)
        by_id = {source["id"]: source for source in self.ledger["sources"]}
        for source_id in ids:
            if source_id not in by_id:
                source = self.source(source_id, source_id)
                source["discovered_by"] = []
                source["disposition"] = {"status": "excluded", "reason": "Metadata-only route fixture."}
                self.ledger["sources"].append(source)
                by_id[source_id] = source
            by_id[source_id]["discovered_by"].append(key)
        item["status"] = "done"
        self.save_plan()

    def test_complementary_searches_preserve_union_and_all_discovery_paths(self):
        self.execute(0, "semantic-01", ["shared", "semantic-only"])
        self.execute(1, "native-01", ["shared", "native-only"])
        self.validate()
        self.assertEqual(len(self.ledger["sources"]), 3)
        self.assertEqual(self.ledger["sources"][0]["discovered_by"], ["semantic-01", "native-01"])
        view = (self.bundle / "plan.md").read_text()
        for value in ["semantic-only", "native-only", "archive-store", "Selected and bounded"]:
            self.assertIn(value, view)

    def test_selected_route_without_search_cannot_save(self):
        self.plan["items"].pop()
        self.save_plan(success=False)

    def test_unmapped_coverage_cannot_save(self):
        extra = copy.deepcopy(self.plan["coverage"][0])
        extra["id"] = "unmapped"
        self.plan["coverage"].append(extra)
        self.save_plan(success=False)

    def test_unmapped_work_type_cannot_save(self):
        self.plan["coverage"][0]["work_types"].append("prototype")
        result = self.save_plan(success=False)
        self.assertIn("pilot-history/prototype", str(result))

    def test_unsupported_input_mode_filter_or_scope_cannot_save(self):
        for target, field, value in [("item", "input_kind", "topic"),
                                     ("branch", "mode", "unknown"),
                                     ("branch", "location", "different-store"),
                                     ("branch", "artifact_type", "board"),
                                     ("branch", "work_type", "prototype"),
                                     ("item", "provider", "register")]:
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                plan["items" if target == "item" else "search_branches"][0][field] = value
                self.save_plan(plan=plan, success=False)
        self.plan["items"][0]["request"]["filters"]["unsupported"] = True
        self.save_plan(success=False)

    def test_command_cannot_execute_under_different_route(self):
        self.save_plan()
        spec = self.routed_spec(self.plan["items"][0], "wrong-command")
        spec["command"] = ["unverified-command"]
        self.capture(spec, success=False)
        self.assertFalse((self.bundle / "operations" / "wrong-command").exists())

    def test_operation_cannot_switch_branch_or_input_kind(self):
        self.save_plan()
        for field, value in [("search_branch", "native"), ("input_kind", "title")]:
            spec = self.routed_spec(self.plan["items"][0], "wrong-" + field)
            spec[field] = value
            self.capture(spec, success=False)

    def test_tool_import_requires_matching_tool(self):
        self.plan["search_routes"][0]["invocation"] = {"tool": "fixture.search"}
        self.save_plan()
        spec = self.routed_spec(self.plan["items"][0], "tool-operation")
        spec.pop("command")
        spec["tool"] = "different.search"
        self.cli("begin", "--spec", self.json_file(spec), success=False)
        spec["tool"] = "fixture.search"
        self.cli("begin", "--spec", self.json_file(spec))

    def test_bound_unavailable_route_remains_visible(self):
        self.plan["search_routes"][1].update(availability="unavailable", invocation=None)
        self.plan["search_branches"][1].update(status="bounded", reason="Fixture route inaccessible.")
        self.plan["items"][1].update(status="bounded", reason="Fixture route inaccessible.")
        self.save_plan()
        self.assertIn("Fixture route inaccessible.", (self.bundle / "plan.md").read_text())

    def test_stale_assignment_cannot_execute_after_route_becomes_unavailable(self):
        self.save_plan()
        spec = self.routed_spec(self.plan["items"][0], "stale")
        self.plan["search_routes"][0]["availability"] = "unavailable"
        self.plan["search_branches"][0]["status"] = "bounded"
        self.plan["items"][0]["status"] = "bounded"
        self.save_plan()
        self.capture(spec, success=False)

    def test_selected_unverified_route_cannot_save(self):
        self.plan["search_routes"][0]["availability"] = "unverified"
        self.save_plan(success=False)

    def test_routes_and_branches_cannot_vanish_or_change_identity(self):
        self.save_plan()
        for collection, field, value in [("search_routes", "family", "replacement"),
                                         ("search_branches", "work_type", "evaluation")]:
            plan = copy.deepcopy(self.plan)
            plan[collection][0][field] = value
            self.save_plan(plan=plan, success=False)
        # An omitted branch also must be retained, even with no associated work.
        self.plan["search_branches"].append(dict(self.branch("semantic"), id="omitted", status="omitted"))
        self.save_plan()
        self.plan["search_branches"].pop()
        self.save_plan(success=False)

    def test_low_priority_selected_search_still_blocks_synthesis(self):
        self.execute(0, "semantic-01", [])
        result = self.validate(phase="synthesis", success=False)
        self.assertIn("selected branch has pending", str(result))

    def test_dropped_search_does_not_satisfy_selected_branch(self):
        self.execute(0, "semantic-01", [])
        self.plan["items"][1]["status"] = "dropped"
        self.save_plan()
        result = self.validate(phase="synthesis", success=False)
        self.assertIn("selected branch has no executed", str(result))

    def test_search_routes_requires_plan(self):
        self.contract["requirements"]["research_plan"] = False
        self.cli("amend", "--contract", self.json_file(self.contract), success=False)


if __name__ == "__main__":
    unittest.main()
