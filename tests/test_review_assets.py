"""Owned installation checks, not model-behavior evaluations."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "assets/agent-assets/skills/review"
EVALUATIONS = ROOT / "tests/fixtures/review-teams/evals.json"
# Reuse the accepted install-fixture boundary, not a second environment policy.
SPEC = importlib.util.spec_from_file_location("asset_fixture", ROOT / "tests/asset_fixture.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the owned asset fixture")
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class ReviewAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-assets-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"schema_version": 3, "installed": True,
            "features": {"agent-assets": {"enabled": True}}}))

    def run_assets(self, action="converge", manifest=None, platform="linux"):
        manifest = manifest or ROOT / "assets/agent-assets.json"
        ownership = self.root / "ownership.json"
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "features/agent-assets.py"), action,
             "--manifest", str(manifest), "--state", str(self.state),
             "--ownership", str(ownership), "--json"],
            cwd=ROOT, env=FIXTURE.asset_environment(self.home, manifest, self.state, ownership, platform),
            capture_output=True, timeout=15,
        )

    def links(self):
        return [self.home / harness / "skills/review" for harness in (".claude", ".agents")]

    def qa_mapping_target(self, qa_link, review_link, mapping):
        # Normalize the installed path before following QA's source symlink.
        target = Path(os.path.normpath(qa_link / mapping.split("#", 1)[0]))
        if target != review_link / "references/review-bundle.md":
            self.fail("QA mapping must address its installed review destination")
        return target

    def routed_references(self, skill):
        """Follow inline Markdown links; check orphan Markdown only in references/."""
        skill = skill.resolve()
        pending = [skill / "SKILL.md"]
        visited, routes = set(), set()
        while pending:
            document = pending.pop()
            if document in visited:
                continue
            visited.add(document)
            for target in re.findall(r"\[[^\]]+\]\(([^)#]+\.md)(?:#[^)]*)?\)", document.read_text()):
                path = (document.parent / target).resolve()
                try:
                    relative = path.relative_to(skill)
                except ValueError:
                    # Workflow skills share versioned references with siblings.
                    # Keep traversal inside this skill collection and require an
                    # actual sibling skill, not an arbitrary adjacent directory.
                    try:
                        shared = path.relative_to(skill.parent)
                    except ValueError:
                        self.fail("Reference target escapes skill: " + target)
                    if len(shared.parts) < 2 or not (skill.parent / shared.parts[0] / "SKILL.md").is_file():
                        self.fail("Reference target escapes skill: " + target)
                    relative = Path(os.path.relpath(path, skill))
                if not path.is_file():
                    self.fail("Reference target is not an existing file")
                routes.add(relative)
                pending.append(path)
        unreachable = {path.relative_to(skill) for path in (skill / "references").rglob("*.md")
                       if path.resolve() not in visited}
        self.assertFalse(unreachable, "Unreachable references: " + ", ".join(map(str, sorted(unreachable))))
        return routes

    def reference_fixture(self, name, documents):
        skill = self.root / name
        for relative, body in documents.items():
            path = skill / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return skill

    def test_skill_frontmatter_has_name_and_description(self):
        # Small source-contract check, not a replacement for the canonical validator.
        match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", (SKILL / "SKILL.md").read_text(), re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must have delimited frontmatter")
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name: review$")
        description = re.search(r"(?m)^description:[ \t]*(.*)((?:\n[ \t]+.*)*)", frontmatter)
        self.assertIsNotNone(description, "description is required")
        value = description.group(1).strip()
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            value = description.group(2).strip()
        self.assertTrue(value.strip("'\""), "description must not be empty")

    def test_both_harness_links_and_helper_are_usable(self):
        expected = (SKILL / "SKILL.md").read_bytes()
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                self.assertEqual(self.run_assets(platform=platform).returncode, 0)
                self.assertEqual(self.run_assets(platform=platform).returncode, 0)
                for link in self.links():
                    self.assertEqual(link.resolve(), SKILL)
                    self.assertEqual((link / "SKILL.md").read_bytes(), expected)
                    result = subprocess.run([sys.executable, "-I", "-B", str(link / "scripts/review_bundle.py"), "--help"],
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0)
                self.assertEqual(self.run_assets("verify", platform=platform).returncode, 0)

    def test_owned_previous_source_upgrades_without_duplicate_skill(self):
        expected = (SKILL / "references/team-review.md").read_bytes()
        old = self.root / "old-assets"
        shutil.copytree(ROOT / "assets", old)
        old_skill = old / "agent-assets/skills/review"
        (old_skill / "SKILL.md").write_text(
            "---\nname: review\ndescription: Previous fixture skill.\n---\n")
        old_reference = old_skill / "references/team-review.md"
        old_reference.write_text("Previous fixture team instructions.\n")
        previous = old_reference.read_bytes()
        # This old fixture moves review, not an unrelated protected triage pair.
        old_manifest = old / "agent-assets.json"
        declared = json.loads(old_manifest.read_text(encoding="utf-8"))
        common = next(group for group in declared["skill_groups"] if group["name"] == "common")
        common.setdefault("exclude_names", []).append("pr-triage")
        declared["skill_contracts"].remove({"group": "common", "name": "pr-triage", "version": 1})
        old_manifest.write_text(json.dumps(declared), encoding="utf-8")
        self.assertEqual(self.run_assets(manifest=old_manifest).returncode, 0)
        triage_links = [self.home / harness / "skills/pr-triage"
                        for harness in (".claude", ".agents")]
        for link in triage_links:
            self.assertFalse(os.path.lexists(link))
        owned_sources = {SKILL.resolve(), old_skill.resolve()}

        def assert_review_links(link):
            # The installer owns symlinks to these sources, not every review* name.
            links = {entry for entry in link.parent.iterdir()
                     if entry.is_symlink() and entry.resolve() in owned_sources}
            self.assertTrue(links == {link}, "Expected only the canonical link to owned review sources")

        neighbours = []
        for link in self.links():
            self.assertEqual(link.resolve(), old_skill)
            unrelated = link.parent / "review-notes"
            unrelated.mkdir()
            (unrelated / "SKILL.md").write_text(
                "---\nname: review-notes\ndescription: Unrelated owned fixture skill.\n---\n")
            unrelated_before = (unrelated / "SKILL.md").read_bytes()
            unrelated_link = link.parent / "review-notes-link"
            unrelated_link.symlink_to(unrelated)
            neighbours.append((link, unrelated, unrelated_before, unrelated_link,
                               os.readlink(unrelated_link)))

        for action in ("converge", "verify"):
            self.assertEqual(self.run_assets(action).returncode, 0,
                             "Owned review upgrade and verification must succeed")
            for link in triage_links:
                self.assertEqual(os.readlink(link), str(ROOT / "assets/agent-assets/skills/pr-triage"))
            for link, unrelated, unrelated_before, unrelated_link, link_text in neighbours:
                self.assertEqual(link.resolve(), SKILL)
                assert_review_links(link)
                self.assertTrue(unrelated.is_dir() and not unrelated.is_symlink(),
                                "Unrelated prefix-sharing skill directory must be preserved")
                self.assertTrue(unrelated_link.is_symlink()
                                and os.readlink(unrelated_link) == link_text
                                and unrelated_link.resolve() == unrelated,
                                "Unrelated prefix-sharing skill link must be preserved")
                self.assertTrue((unrelated / "SKILL.md").read_bytes() == unrelated_before,
                                "Unrelated prefix-sharing skill content must be preserved")
                self.assertEqual((link / "references/team-review.md").read_bytes(), expected)

        for link in self.links():
            assert_review_links(link)
            for label, source in (("current", SKILL), ("previous", old_skill)):
                with self.subTest(source=label):
                    duplicate = link.parent / "review-alias"
                    duplicate.symlink_to(source)
                    try:
                        with self.assertRaisesRegex(AssertionError,
                                "Expected only the canonical link to owned review sources$"):
                            assert_review_links(link)
                    finally:
                        duplicate.unlink()  # Owned negative-control alias only.
            assert_review_links(link)
        self.assertEqual(old_reference.read_bytes(), previous)

    def test_references_subtree_is_complete_through_both_harnesses(self):
        routes = self.routed_references(SKILL)
        expected = {relative: (SKILL / relative).read_bytes() for relative in routes}
        qa_source = SKILL.parent / "qa"
        expected_qa = (qa_source / "SKILL.md").read_bytes()
        mapping_relative = Path("references/review-bundle.md")
        self.assertGreaterEqual(set(routes), {Path("references") / name for name in
            ("checklist.md", "continuation.md", "model-selection.md", "review-bundle.md", "team-review.md", "worksets.md")})
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                self.assertEqual(self.run_assets(platform=platform).returncode, 0)
                for link in self.links():
                    for relative in routes:
                        # Resolve .. in the installed namespace before following
                        # a source symlink, so absent sibling links cannot pass.
                        installed = Path(os.path.normpath(link / relative))
                        self.assertEqual(installed.resolve(), (SKILL / relative).resolve())
                        self.assertEqual(installed.read_bytes(), expected[relative])
                    # Pin this cross-skill handoff, not a general dependency parser.
                    qa_link = link.parent / "qa"
                    self.assertTrue(qa_link.is_symlink())
                    self.assertEqual(qa_link.resolve(), qa_source)
                    qa_body = (qa_link / "SKILL.md").read_bytes()
                    self.assertTrue(qa_body == expected_qa,
                                    "Installed QA must match its pre-convergence source snapshot")
                    mappings = re.findall(
                        r"\[blocked/unresolved/recommended-only mapping\]\(([^)]+)\)",
                        qa_body.decode("utf-8"),
                    )
                    self.assertTrue(mappings == ["../review/references/review-bundle.md#results-and-import"],
                                    "QA must retain exactly one expected review evidence-mapping link")
                    target = self.qa_mapping_target(qa_link, link, mappings[0])
                    self.assertEqual(target.resolve(), (link / mapping_relative).resolve())
                    mapping_body = target.read_bytes()
                    self.assertTrue(mapping_body == expected[mapping_relative],
                                    "QA's installed mapping target must match the review source snapshot")
                    self.assertTrue(b"\n## Results and import\n" in mapping_body,
                                    "QA's mapping fragment must name the installed review section")

    def test_qa_mapping_rejects_split_installed_destinations(self):
        together, split = self.root / "together", self.root / "split"
        together.mkdir()
        split.mkdir()
        qa_link, review_link, split_qa = together / "qa", together / "review", split / "qa"
        qa_link.symlink_to(SKILL.parent / "qa")
        review_link.symlink_to(SKILL)
        split_qa.symlink_to(SKILL.parent / "qa")
        mapping = "../review/references/review-bundle.md#results-and-import"
        target = self.qa_mapping_target(qa_link, review_link, mapping)
        self.assertTrue(target.is_file(), "Co-located QA mapping must reach the installed reference")
        # The former comparison passes even though the installed destinations differ.
        self.assertTrue((split_qa / mapping.split("#", 1)[0]).resolve() == target.resolve(),
                        "Split fixture must retain identical source targets")
        with self.assertRaisesRegex(AssertionError,
                "^QA mapping must address its installed review destination$"):
            self.qa_mapping_target(split_qa, review_link, mapping)
        self.assertTrue(self.qa_mapping_target(qa_link, review_link, mapping) == target,
                        "The original co-located mapping must remain valid")

    def test_reference_routing_follows_transitive_links_and_reachable_cycles(self):
        skill = self.reference_fixture("reachable-cycle", {
            "SKILL.md": "[first](references/first.md)\n",
            "references/first.md": "[nested](nested/third.md)\n",
            "references/second.md": "[first](first.md)\n",
            "references/nested/third.md": "[second](../second.md#details)\n",
        })
        self.assertEqual(self.routed_references(skill),
                         {Path("references/first.md"), Path("references/second.md"),
                          Path("references/nested/third.md")})

    def test_reference_routing_ignores_markdown_suffix_in_fragment(self):
        skill = self.reference_fixture("markdown-fragment", {
            "SKILL.md": "[first](references/first.md#notes.md)\n",
            "references/first.md": "Reachable reference.\n",
        })
        self.assertEqual(self.routed_references(skill), {Path("references/first.md")})

    def test_reference_routing_rejects_missing_and_escaping_targets(self):
        outside = self.root / "outside.md"
        outside.write_text("Owned sibling reference.\n")
        for name, target, diagnostic in (
            ("missing", "references/missing.md", r"^Reference target is not an existing file$"),
            ("escaping", "../outside.md", r"^Reference target escapes skill:"),
        ):
            with self.subTest(name=name):
                skill = self.reference_fixture(name, {"SKILL.md": "[target](" + target + ")\n"})
                with self.assertRaisesRegex(AssertionError, diagnostic):
                    self.routed_references(skill)
        self.assertEqual(outside.read_text(), "Owned sibling reference.\n")

    def test_reference_routing_rejects_orphans_and_unreachable_cycles(self):
        for name, documents in (
            ("orphan", {"references/orphan.md": "Unlinked reference.\n"}),
            ("nested-orphan", {"references/nested/orphan.md": "Unlinked nested reference.\n"}),
            ("unreachable-cycle", {"references/second.md": "[third](third.md)\n",
                                   "references/third.md": "[second](second.md)\n"}),
        ):
            with self.subTest(name=name):
                skill = self.reference_fixture(name, {
                    "SKILL.md": "[first](references/first.md)\n",
                    "references/first.md": "Reachable reference.\n",
                    **documents,
                })
                with self.assertRaisesRegex(AssertionError, "Unreachable references:"):
                    self.routed_references(skill)

    def test_unowned_modified_install_is_preserved(self):
        self.assertEqual(self.run_assets().returncode, 0)
        local, other = self.links()
        local.unlink()  # Fixture-owned link only.
        local.mkdir()
        marker = local / "SKILL.md"
        marker.write_text("User-owned skill, preserve.")
        other.unlink()
        result = self.run_assets()
        self.assertEqual(result.returncode, 1)
        expected_error = (
            "Issues:\n  ERROR   ownership-drift: "
            f"recorded target is changed: {local}; inspect agent-assets status "
            "and the recorded link before reconciling it; preserve foreign entries "
            "and do not delete the ownership ledger\n"
            "  ERROR   target-conflict: "
            f"refusing to replace unmanaged target: {local}\n"
        ).encode("utf-8")
        self.assertTrue(
            result.stderr == expected_error,
            "Expected exact ordered ownership-drift and target-conflict diagnostics",
        )
        self.assertTrue(result.stdout == b"", "Target conflict must not emit stdout")
        self.assertEqual(marker.read_text(), "User-owned skill, preserve.")
        self.assertFalse(other.exists())
        self.assertFalse(other.is_symlink())

    def test_fixture_owned_path_overrides_do_not_touch_outside_sentinels(self):
        # This tests the fixture's replacement of inherited paths. Do not pass
        # the poisoned values through to the installer to test another boundary.
        outside = self.root / "outside"
        outside.mkdir()
        context = {}
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                     "ENVY_CONFIG_DIR", "ENVY_STATE_DIR", "ENVY_DATA_DIR", "ENVY_BIN_DIR",
                     "ENVY_DIR", "ENVY_MANIFEST", "ENVY_AGENT_ASSETS_MANIFEST",
                     "ENVY_STATE_FILE", "ENVY_AGENT_ASSETS_OWNERSHIP"):
            target = outside / name
            target.mkdir()
            (target / "sentinel").write_text("Preserve")
            context[name] = str(target)
        before = FIXTURE.snapshot(outside)
        with patch.dict(os.environ, context):
            expected = {
                "HOME": self.home,
                "XDG_CONFIG_HOME": self.home / ".config",
                "XDG_STATE_HOME": self.home / ".local/state",
                "XDG_DATA_HOME": self.home / ".local/share",
                "ENVY_CONFIG_DIR": self.home / ".config/envy",
                "ENVY_STATE_DIR": self.home / ".local/state/envy",
                "ENVY_DATA_DIR": self.home / ".local/share/envy",
                "ENVY_BIN_DIR": self.home / ".local/bin",
                "ENVY_DIR": ROOT,
                "ENVY_MANIFEST": ROOT / "envy.json",
                "ENVY_AGENT_ASSETS_MANIFEST": ROOT / "assets/agent-assets.json",
                "ENVY_STATE_FILE": self.state,
                "ENVY_AGENT_ASSETS_OWNERSHIP": self.root / "ownership.json",
            }
            bound = FIXTURE.asset_environment(self.home, expected["ENVY_AGENT_ASSETS_MANIFEST"],
                self.state, expected["ENVY_AGENT_ASSETS_OWNERSHIP"], "linux")
            self.assertEqual(set(expected), set(context))
            self.assertTrue(all(bound[name] == str(path) for name, path in expected.items()),
                            "Fixture must replace every installer-owned path")
            self.assertEqual(self.run_assets().returncode, 0)
            self.assertEqual(self.run_assets("verify").returncode, 0)
        self.assertEqual(FIXTURE.snapshot(outside), before)
        for link in self.links():
            self.assertEqual(link.resolve(), SKILL)


class ReviewTeamFixtureTests(unittest.TestCase):
    """Fixture mechanics only; these do not evaluate an agent's decisions."""

    def setUp(self):
        self.evaluations = json.loads(EVALUATIONS.read_text())

    def scenario_index_ids(self, document):
        """Parse the one fixed index table, not arbitrary Markdown documents."""
        sections = re.findall(r"(?ms)^## Scenario index[ \t]*\n(.*?)(?=^#{1,2} |\Z)", document)
        self.assertEqual(len(sections), 1, "One Scenario index section is required")
        tables = [block for block in re.split(r"\n[ \t]*\n", sections[0].strip())
                  if block.lstrip().startswith("|")]
        self.assertEqual(len(tables), 1, "One Scenario index table is required")
        lines = [line.strip() for line in tables[0].splitlines()]
        self.assertGreaterEqual(len(lines), 3,
                                "Scenario index requires a header, separator and data row")
        self.assertTrue(re.fullmatch(r"\|\s*Case ID\s*\|\s*Focus\s*\|", lines[0]) is not None,
                        "Invalid Scenario index header")
        self.assertTrue(re.fullmatch(r"\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|", lines[1]) is not None,
                        "Invalid Scenario index separator")
        identifiers = []
        for line in lines[2:]:
            row = re.fullmatch(r"\|\s*`([a-z][a-z0-9-]*)`\s*\|([^|]+)\|", line)
            self.assertIsNotNone(row, "Expected a backticked case ID and focus table row")
            self.assertTrue(row.group(2).strip(), "Scenario focus must not be empty")
            identifiers.append(row.group(1))
        self.assertEqual(len(identifiers), len(set(identifiers)), "Duplicate scenario index IDs")
        return identifiers

    def test_documented_scenario_index_matches_fixture_cases(self):
        document = (ROOT / "tests/review-team-evaluations.md").read_text()
        self.assertEqual(set(self.scenario_index_ids(document)),
                         {case["id"] for case in self.evaluations["cases"]})

    def test_scenario_index_rejects_duplicate_ids(self):
        document = ("## Scenario index\n\n| Case ID | Focus |\n| --- | --- |\n"
                    "| `interactive-default` | First row |\n"
                    "| `interactive-default` | Duplicate row |\n")
        with self.assertRaisesRegex(AssertionError, "Duplicate scenario index IDs"):
            self.scenario_index_ids(document)

    def test_scenario_index_rejects_missing_duplicate_sections_and_invalid_rows(self):
        heading = "## Scenario index\n\n"
        header = "| Case ID | Focus |\n| --- | --- |\n"
        row = "| `interactive-default` | First row |\n"
        valid = heading + header + row
        self.assertEqual(self.scenario_index_ids(valid), ["interactive-default"])
        cases = (
            ("missing-section", "## Other section\n\n" + header + row,
             "One Scenario index section is required"),
            ("duplicate-section", valid + "\n" + valid,
             "One Scenario index section is required"),
            ("multiple-tables", valid + "\n" + header + row,
             "One Scenario index table is required"),
            ("short-table", heading + header,
             "Scenario index requires a header, separator and data row"),
            ("invalid-header", heading + header.replace("Case ID", "Case") + row,
             "Invalid Scenario index header"),
            ("invalid-separator", heading + header.replace("| --- | --- |", "| -- | --- |") + row,
             "Invalid Scenario index separator"),
            ("malformed-row", heading + header + "| interactive-default | First row |\n",
             "Expected a backticked case ID and focus table row"),
            ("empty-focus", heading + header + "| `interactive-default` |   |\n",
             "Scenario focus must not be empty"),
        )
        for name, document, diagnostic in cases:
            with self.subTest(case=name):
                with self.assertRaisesRegex(AssertionError, re.escape(diagnostic) + "$"):
                    self.scenario_index_ids(document)

    def test_checked_in_recovery_event_retains_placeholder(self):
        case = next(case for case in self.evaluations["cases"] if case["id"] == "compaction-and-truncation")
        event = next(event for event in case["events"] if event["when"] == "initial input")

        def assert_placeholder(value):
            # Materialized actual/unknown-run variants remain operator-owned.
            self.assertTrue(value["saved_state"]["in_flight_run"] == "REPLACE_WITH_OPERATOR_SUPPLIED_REAL_RUN_ID",
                            "Checked-in recovery event must retain its unknown-run placeholder")

        assert_placeholder(event)
        invalid = copy.deepcopy(event)
        invalid["saved_state"]["in_flight_run"] = "fixture-not-a-native-run"
        with self.assertRaisesRegex(AssertionError,
                "Checked-in recovery event must retain its unknown-run placeholder$"):
            assert_placeholder(invalid)

    def test_checked_in_continuation_event_retains_fictional_unobserved_state(self):
        case = next(case for case in self.evaluations["cases"]
                    if case["id"] == "same-session-synthesis-continuation")
        diagnostic = "Checked-in continuation event must remain fictional and unobserved"

        def assert_fictional(value):
            self.assertTrue(value["request"] == (
                "Walk through reconciliation of the supplied fictional v3 review state and the later launch failure. "
                "Explain the next supported actions; do not run a provider, modify a real ledger or invent native identities."
            ), diagnostic)
            events = value["events"]
            self.assertTrue(len(events) == 3, diagnostic)
            for event, (when, payload) in zip(events, (
                ("initial input", "synthetic_review_state"),
                ("evaluator identifies remaining authorization requirements",
                 "synthetic_operator_decision"),
                ("evaluator describes a properly authorized continuation checkpoint",
                 "synthetic_launch_result"),
            )):
                self.assertTrue(set(event) == {"when", payload}, diagnostic)
                self.assertTrue(event["when"] == when, diagnostic)
            state = events[0]["synthetic_review_state"]
            self.assertTrue(set(state) == {
                "version", "latest_attempt_verdict", "candidate_and_policy", "original_invocation",
                "lead_session", "researchers", "coverage", "additional_resources",
            }, diagnostic)
            self.assertTrue(state["lead_session"] == (
                "operator has an observed recoverable same-native-session identity and admissible launch evidence; "
                "no real ID is supplied in this fictional exercise"
            ), diagnostic)
            self.assertTrue(events[1]["synthetic_operator_decision"] == (
                "For this fictional scenario only, the operator records explicit additional time for lead-only synthesis, "
                "preserving all prior resource consumption and allowing no new or resumed researchers. "
                "This event authorizes no actual provider call."
            ), diagnostic)
            self.assertTrue(events[2]["synthetic_launch_result"] == (
                "The resumed invocation fails before any resumed native runtime is observed. "
                "The original attempt and reports remain intact."
            ), diagnostic)

        assert_fictional(case)
        invalid_session = copy.deepcopy(case)
        invalid_session["events"][0]["synthetic_review_state"]["lead_session"] = "00000000-0000-0000-0000-000000000000"
        added_run = copy.deepcopy(case)
        added_run["events"][0]["synthetic_review_state"]["run_id"] = "00000000-0000-0000-0000-000000000000"
        resumed = copy.deepcopy(case)
        resumed["events"][2]["synthetic_launch_result"] = "The session resumed successfully."
        unqualified_authority = copy.deepcopy(case)
        event = unqualified_authority["events"][1]
        event["synthetic_operator_decision"] = event["synthetic_operator_decision"].replace(
            " This event authorizes no actual provider call.", "")
        unqualified_request = copy.deepcopy(case)
        unqualified_request["request"] = "Explain the next supported actions."
        invalid_cases = [
            ("concrete-session", invalid_session), ("added-run", added_run),
            ("observed-resume", resumed), ("unqualified-authority", unqualified_authority),
            ("unqualified-request", unqualified_request),
        ]
        for index in range(3):
            event_run = copy.deepcopy(case)
            event_run["events"][index]["run_id"] = "00000000-0000-0000-0000-000000000000"
            invalid_cases.append(("event-run-" + str(index), event_run))
            event_when = copy.deepcopy(case)
            event_when["events"][index]["when"] = "after all events"
            invalid_cases.append(("event-when-" + str(index), event_when))
        for name, invalid in invalid_cases:
            with self.subTest(case=name):
                with self.assertRaisesRegex(AssertionError, re.escape(diagnostic) + "$"):
                    assert_fictional(invalid)

    def test_selection_request_model_tokens_resolve_in_fixture_catalog(self):
        models = {row["id"] for row in self.evaluations["capability_fixture"]["models"]}

        def request_models(cases):
            references = {}
            for case in cases:
                if "capability_fixture" not in case["inputs"]:
                    continue
                tokens = set(re.findall(r"\bfixture-(?:gpt|claude)-[a-z0-9]+(?:-[a-z0-9]+)*\b", case["request"]))
                self.assertTrue(tokens <= models, "Selection request names an absent fixture model")
                references[case["id"]] = tokens
            return references

        references = request_models(self.evaluations["cases"])
        self.assertTrue(references["explicit-selection"] == {"fixture-gpt-small", "fixture-claude-small"},
                        "Explicit selection must exercise both supplied fixture model references")
        invalid = copy.deepcopy(next(case for case in self.evaluations["cases"] if case["id"] == "explicit-selection"))
        invalid["request"] = invalid["request"].replace("fixture-gpt-small", "fixture-gpt-missing")
        with self.assertRaisesRegex(AssertionError, "Selection request names an absent fixture model$"):
            request_models([invalid])
        # This consistency check does not qualify capabilities for live cases.
        without_catalog = copy.deepcopy(invalid)
        without_catalog["inputs"].remove("capability_fixture")
        self.assertTrue(request_models([without_catalog]) == {}, "Cases without simulated capabilities must remain out of scope")

    def test_candidate_preserves_unchanged_context_and_selected_change_kinds(self):
        files = self.evaluations["candidate"]["files"]
        changed = {name for name, row in files.items() if row["before"] != row["after"]}
        self.assertEqual(changed, {"src/service.py", "tests/test_service.py", "docs/cache.md",
                                   "legacy-cache-notes.txt", "schema-source.json", "generated/quote-schema.json"})
        self.assertEqual(files["src/api.py"]["before"], files["src/api.py"]["after"])
        incomplete = next(case for case in self.evaluations["cases"] if case["id"] == "incomplete-pass")
        report = incomplete["events"][0]["report"]
        self.assertIn("src/api.py", report["unresolved"])
        self.assertEqual(set(report["reviewed"]) | set(report["unresolved"]), set(files))
        self.assertFalse(set(report["reviewed"]) & set(report["unresolved"]))
        self.assertIsNone(files["legacy-cache-notes.txt"]["after"])
        for name, row in files.items():
            self.assertFalse(Path(name).is_absolute())
            self.assertNotIn("..", Path(name).parts)
            self.assertEqual(set(row), {"before", "after"})
            for version, body in row.items():
                if body is not None and name.endswith(".py"):
                    compile(body, name + ":" + version, "exec")
        for version in ("before", "after"):
            self.assertEqual(json.loads(files["schema-source.json"][version]),
                             json.loads(files["generated/quote-schema.json"][version]))

    def test_contradictory_identity_fixture_retains_model_mismatch(self):
        case = next(case for case in self.evaluations["cases"] if case["id"] == "contradictory-identity")
        identities = [event["synthetic_identity_evidence"] for event in case["events"]
                      if "synthetic_identity_evidence" in event]
        self.assertTrue(len(identities) == 1,
                        "Contradictory identity case must retain one supplied identity event")
        identity = identities[0]
        keys = ("selected_model", "actual_launch_model", "independently_observed_served_model")
        diagnostic = "Contradictory identity fixture must retain matching selection and launch with a different served model"

        def assert_contradiction(value):
            selected, launched, served = (value.get(key) for key in keys)
            if not (all(isinstance(model, str) and model.startswith("fixture-")
                        for model in (selected, launched, served))
                    and selected == launched and launched != served):
                self.fail(diagnostic)

        assert_contradiction(identity)
        renamed = copy.deepcopy(identity)
        for key in keys:
            renamed[key] += "-alternate"
        assert_contradiction(renamed)  # Ad-hoc synthetic labels need no catalog membership.
        for label, key, value in (
            ("served-matches-launch", "independently_observed_served_model", identity["actual_launch_model"]),
            ("selection-mismatches-launch", "selected_model", identity["independently_observed_served_model"]),
        ):
            with self.subTest(case=label):
                invalid = copy.deepcopy(identity)
                invalid[key] = value
                with self.assertRaisesRegex(AssertionError, "^" + re.escape(diagnostic) + "$"):
                    assert_contradiction(invalid)
        assert_contradiction(identity)

    def test_synthetic_change_has_real_cross_tenant_regression(self):
        body = self.evaluations["candidate"]["files"]["src/service.py"]
        results = {}
        for version in ("before", "after"):
            # Repo-owned pure fixture; minimal builtins for clarity, not a sandbox.
            namespace = {"__builtins__": {"dict": dict}}
            exec(compile(body[version], "synthetic-service", "exec"), namespace)
            catalog = {"alpha": {"book": 12}, "beta": {"book": 21}}
            first = namespace["quote"]("alpha", "book", catalog)
            first["price"] = 999
            self.assertEqual(namespace["quote"]("alpha", "book", catalog)["price"], 12)
            results[version] = namespace["quote"]("beta", "book", catalog)
        self.assertEqual(results["before"], {"tenant": "beta", "sku": "book", "price": 21})
        self.assertEqual(results["after"], {"tenant": "alpha", "sku": "book", "price": 12})

    def test_evaluation_inputs_are_separate_from_observer_rubrics(self):
        cases = self.evaluations["cases"]
        case_ids = {case["id"] for case in cases}
        self.assertEqual(len(case_ids), len(cases))
        self.assertEqual(case_ids, {
            "interactive-default", "explicit-selection", "autonomous-full-coverage",
            "status-preserves-autonomy", "adaptive-research", "incomplete-pass",
            "classification-and-disposition", "compaction-and-truncation",
            "delegation-unavailable", "accepted-risk-is-not-clean-pass",
            "contradictory-identity", "same-session-synthesis-continuation", "lead-failure",
            "workset-in-progress", "workset-nonblocking-followups",
            "workset-delta-review", "workset-redesign",
        })
        for case in cases:
            self.assertEqual(set(case), {"id", "request", "inputs", "events", "observe"})
            self.assertTrue(case["request"])
            self.assertTrue(case["observe"])
            self.assertIn("candidate", case["inputs"])
            self.assertLessEqual(set(case["inputs"]), {"candidate", "capability_fixture"})
            for event in case["events"]:
                self.assertIn("when", event)
                self.assertNotIn("observe", event)
        models = self.evaluations["capability_fixture"]["models"]
        self.assertEqual(len({row["id"] for row in models}), len(models))
        for row in models:
            self.assertIsInstance(row["id"], str)
            self.assertTrue(row["id"].startswith("fixture-"))
        by_id = {row["id"]: row for row in models}
        for family in ("GPT", "Claude"):
            strong, small, announced = [by_id["fixture-" + family.lower() + "-" + name]
                                        for name in ("strong", "small", "announced")]
            for row in (strong, small, announced):
                self.assertEqual(row["family"], family)
                self.assertIs(type(row["capability_rank"]), int)
            self.assertIs(strong["selectable"], True)
            self.assertIs(small["selectable"], True)
            self.assertIs(announced["selectable"], False)
            self.assertGreater(strong["capability_rank"], small["capability_rank"])
            self.assertGreater(announced["capability_rank"], strong["capability_rank"])
        identities = [event["synthetic_identity_evidence"] for case in cases for event in case["events"]
                      if "synthetic_identity_evidence" in event]
        self.assertTrue(identities)
        for identity in identities:
            for key in ("selected_model", "actual_launch_model", "independently_observed_served_model"):
                self.assertIsInstance(identity[key], str)
                self.assertTrue(identity[key].startswith("fixture-"))
        # Identity fields use synthetic naming only; selection requests are checked separately.
        # This naming convention neither qualifies a provider nor isolates execution.


if __name__ == "__main__":
    unittest.main()
