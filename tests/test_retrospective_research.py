#!/usr/bin/env python3
"""Isolated CLI acceptance and fault tests for research evidence recordkeeping.

All commands are local Python processes and all records are fictional. These
checks establish structural invariants, not source recall or semantic truth.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
HELPER = (ROOT / "assets/agent-assets/skills/retrospective-research/scripts/"
          "research_bundle.py")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(path):
    return {str(item.relative_to(path)): item.read_bytes()
            for item in path.rglob("*") if item.is_file()}


class ResearchBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="envy-research-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.bundle = self.work / "bundle"
        self.serial = 0
        self.contract = {
            "version": 1,
            "purpose": "Assess whether the fictional archive pilot should resume.",
            "scope": "Archive pilot decisions and measured outcomes.",
            "requirements_source": "Synthetic intake explicitly requests a short brief and candidate table.",
            "requirements": {"candidate_table": True, "brief_max_words": 80,
                             "destination": "Two local HTML documents."},
            "providers": {"archive": {"read_formats": ["html", "native-json"],
                                       "basis": "Fixture adapter preserves tables in these formats."}},
            "questions": [{"id": "decision", "text": "Why was the pilot deferred?", "central": True},
                          {"id": "outcomes", "text": "Were later outcomes measured?", "central": False}],
        }
        self.contract_path = self.json_file(self.contract)
        self.cli("init", "--contract", self.contract_path)

    def json_file(self, value):
        self.serial += 1
        path = self.work / ("input-%03d.json" % self.serial)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def text_file(self, name, value):
        path = self.work / name
        path.write_text(value, encoding="utf-8")
        return path

    def cli(self, action, *args, root=None, success=True):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(HELPER), action,
             str(root or self.bundle), *map(str, args)],
            cwd=self.work, env={"PATH": os.defpath},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.stderr, "", result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if success else 1, record)
        self.assertEqual(record["status"], "pass" if success else "fail")
        return record

    def search_spec(self, key="search-1", command=None, **changes):
        spec = {
            "id": key, "actor": "fixture-researcher", "provider": "archive", "kind": "search",
            "request": {"query": '"archive pilot" OR "old collection"',
                        "filters": {"before": "2025-01-01", "kind": ["doc", "slide"]},
                        "cap": 20, "cursor": None},
            "command": command or [sys.executable, "-I", "-B", "-c",
                                   "print('{\"ids\": [\"record-a\", \"record-b\"]}')"],
            "cwd": str(self.work), "timeout": 5,
        }
        spec.update(changes)
        return spec

    def read_spec(self, key="read-1", format="html", command=None):
        spec = self.search_spec(key, command=command or [
            sys.executable, "-I", "-B", "-c",
            "print('<p>The archive pilot was deferred pending a migration review.</p>')",
        ])
        spec.update(kind="read", request={"source_id": "record-a", "format": format,
                                         "extent": "body, including decision table"})
        return spec

    def capture(self, spec, success=True):
        self.cli("capture", "--spec", self.json_file(spec), success=success)
        return self.bundle / "operations" / spec["id"]

    def index(self, key, ids, pagination="complete", cursor=None):
        result = {"source_ids": ids, "pagination": pagination, "next_cursor": cursor,
                  "note": "IDs extracted from the retained fictional response."}
        self.cli("index", "--id", key, "--result", self.json_file(result))

    def valid_bundle(self):
        self.capture(self.search_spec())
        self.index("search-1", ["record-a", "record-b"])
        self.capture(self.read_spec())
        self.index("read-1", ["record-a"], "not-applicable")
        self.ledger = {
            "sources": [
                {"id": "record-a", "title": "Archive pilot review",
                 "url": "https://example.invalid/records/a", "coverage": "Body and decision table read",
                 "finding": "Deferred pending a migration review.", "contributor_basis": "A researcher is credited in the body.",
                 "discovered_by": ["search-1", "read-1"], "reads": ["read-1"]},
                {"id": "record-b", "title": "Archive follow-up index",
                 "url": "https://example.invalid/records/b", "coverage": "Metadata only; body unread",
                 "finding": "Potential later outcome source; content not established.",
                 "contributor_basis": "No contributor evidence in available metadata.",
                 "discovered_by": ["search-1"], "reads": []},
            ],
            "claims": [{"id": "claim-1", "text": "The pilot was deferred pending migration review.",
                        "basis": "Contemporaneous decision note.", "reads": ["read-1"],
                        "locator": "operations/read-1/stdout, paragraph 1"}],
            "questions": [{"id": "decision", "status": "answered", "claims": ["claim-1"],
                           "reason": "The decision note records a deferral."},
                          {"id": "outcomes", "status": "bounded", "claims": [],
                           "reason": "The later index is discovered but its body is unavailable."}],
            "semantic_review": {"reviewer": "fixture-editor", "note": "Checked deferral against the primary wording."},
        }
        return self.ledger

    def validate(self, ledger=None, phase="research", success=True, root=None):
        return self.cli("validate", "--ledger", self.json_file(ledger or self.ledger),
                        "--phase", phase, root=root, success=success)

    def clone_bundle(self, name):
        destination = self.work / name
        shutil.copytree(self.bundle, destination)
        return destination

    def publication_files(self, brief=None, companion=None):
        if brief is None:
            brief = ('<html><head><title>Draft</title></head><body><h1>Pilot decision</h1>'
                     '<p>The pilot remains deferred pending migration review.</p>'
                     '<p><a href="https://example.invalid/companion">Evidence companion</a></p></body></html>')
        if companion is None:
            companion = ('<html><body><h1>Evidence</h1>'
                         '<p><a href="https://example.invalid/brief">Decision brief</a></p>'
                         '<table><tr><th>ID</th><th>Source</th><th>Read extent</th><th>Finding</th><th>Contribution basis</th></tr>')
            for source in self.ledger["sources"]:
                companion += ('<tr><td>{id}</td><td><a href="{url}">{title}</a></td>'
                              '<td>{coverage}</td><td>{finding}</td><td>{contributor_basis}</td></tr>').format(**source)
            companion += ('</table><p><a href="https://example.invalid/records/a">Primary citation</a></p>'
                          '<p><a href="https://example.invalid/records/a">Repeated citation</a></p></body></html>')
        artifacts = []
        for key, text in (("brief", brief), ("companion", companion)):
            expected = self.text_file(key + "-expected.html", text)
            actual = self.text_file(key + "-actual.html", text)
            artifacts.append({"id": key, "url": "https://example.invalid/" + key,
                              "expected": [str(expected)], "actual": [str(actual)]})
        return {"artifacts": artifacts, "candidate_tables": [0]}

    def publish(self, manifest, success=True):
        return self.cli("publication", "--ledger", self.json_file(self.ledger),
                        "--manifest", self.json_file(manifest), success=success)

    def test_init_retains_contract_and_refuses_to_adopt_existing_directory(self):
        self.assertEqual(read_json(self.bundle / "contract.json"), self.contract)
        self.assertEqual(read_json(self.bundle / "bundle.json")["contract_sha256"],
                         file_hash(self.bundle / "contract.json"))
        before = snapshot(self.bundle)
        self.cli("init", "--contract", self.contract_path, success=False)
        self.assertEqual(snapshot(self.bundle), before)

    def test_invalid_contracts_fail_before_creating_a_bundle(self):
        variants = []
        missing_table = copy.deepcopy(self.contract)
        del missing_table["requirements"]["candidate_table"]
        variants.append(missing_table)
        invalid_limit = copy.deepcopy(self.contract)
        invalid_limit["requirements"]["brief_max_words"] = True
        variants.append(invalid_limit)
        no_format = copy.deepcopy(self.contract)
        no_format["providers"]["archive"]["read_formats"] = []
        variants.append(no_format)
        implicit_central = copy.deepcopy(self.contract)
        del implicit_central["questions"][0]["central"]
        variants.append(implicit_central)
        for number, contract in enumerate(variants):
            with self.subTest(number=number):
                target = self.work / ("invalid-%s" % number)
                self.cli("init", "--contract", self.json_file(contract), root=target, success=False)
                self.assertFalse(target.exists())

    def test_capture_retains_exact_query_filters_cap_and_executed_output(self):
        spec = self.search_spec()
        operation = self.capture(spec)
        request = read_json(operation / "request.json")
        self.assertEqual(request["spec"], spec)
        self.assertEqual(request["transport"], "subprocess")
        self.assertTrue(request["started"])
        self.assertEqual(read_json(operation / "stdout"), {"ids": ["record-a", "record-b"]})
        self.assertEqual((operation / "stderr").read_bytes(), b"")
        receipt = read_json(operation / "receipt.json")
        self.assertEqual(receipt["outcome"], "ok")
        self.assertEqual(receipt["provenance"], "captured-subprocess")
        self.assertEqual(receipt["returncode"], 0)
        for name in ("request.json", "stdout", "stderr"):
            self.assertEqual(receipt["files"][name], file_hash(operation / name))

    def test_incomplete_search_requests_do_not_execute(self):
        for field in ("query", "filters", "cap", "cursor"):
            with self.subTest(field=field):
                spec = self.search_spec("missing-" + field)
                del spec["request"][field]
                self.cli("capture", "--spec", self.json_file(spec), success=False)
                self.assertFalse((self.bundle / "operations" / spec["id"]).exists())

    def test_failed_capture_returns_failure_and_preserves_partial_output(self):
        operation = self.capture(self.search_spec(command=[
            sys.executable, "-I", "-B", "-c",
            "import sys; print('partial response'); print('fixture error', file=sys.stderr); sys.exit(7)",
        ]), success=False)
        receipt = read_json(operation / "receipt.json")
        self.assertEqual((receipt["outcome"], receipt["returncode"]), ("error", 7))
        self.assertEqual((operation / "stdout").read_text(), "partial response\n")
        self.assertEqual((operation / "stderr").read_text(), "fixture error\n")

    def test_timeout_preserves_flushed_output_and_finished_receipt(self):
        operation = self.capture(self.search_spec(command=[
            sys.executable, "-I", "-B", "-c",
            "import sys,time; print('first page', flush=True); print('partial diagnostic', file=sys.stderr, flush=True); time.sleep(10)",
        ], timeout=1), success=False)
        receipt = read_json(operation / "receipt.json")
        self.assertEqual(receipt["outcome"], "timeout")
        self.assertIsNone(receipt["returncode"])
        self.assertEqual((operation / "stdout").read_text(), "first page\n")
        self.assertEqual((operation / "stderr").read_text(), "partial diagnostic\n")
        self.assertTrue(receipt["finished"])

    def test_launch_failure_keeps_the_started_request(self):
        operation = self.capture(self.search_spec(command=[str(self.work / "missing-executable")]), success=False)
        receipt = read_json(operation / "receipt.json")
        self.assertEqual(receipt["outcome"], "launch-error")
        self.assertEqual(receipt["failure"], {"type": "FileNotFoundError", "errno": 2})
        self.assertTrue((operation / "request.json").is_file())

    def test_tool_import_is_distinct_from_observed_subprocess_execution(self):
        spec = self.search_spec("tool-search")
        del spec["command"]
        self.cli("begin", "--spec", self.json_file(spec))
        operation = self.bundle / "operations/tool-search"
        self.assertTrue((operation / "request.json").is_file())
        self.assertFalse((operation / "receipt.json").exists())
        self.cli("validate", "--ledger", self.json_file({"sources": []}), success=False)
        response = self.text_file("tool-output.json", '{"ids": []}\n')
        diagnostic = self.text_file("tool-error.txt", "fixture pagination diagnostic\n")
        self.cli("finish", "--id", "tool-search", "--output", response,
                 "--error", diagnostic, "--outcome", "ok")
        self.index("tool-search", [])
        receipt = read_json(operation / "receipt.json")
        self.assertEqual(receipt["provenance"], "agent-attested-tool-output")
        self.assertEqual((operation / "stdout").read_bytes(), response.read_bytes())
        self.assertEqual((operation / "stderr").read_bytes(), diagnostic.read_bytes())
        result = self.validate({"sources": []})
        self.assertEqual(result["result"]["tool_imports"], 1)
        before = snapshot(operation)
        self.cli("finish", "--id", "tool-search", "--output", response, "--outcome", "ok", success=False)
        self.assertEqual(snapshot(operation), before)

    def test_disallowed_read_format_is_rejected_before_execution(self):
        spec = self.read_spec(format="lossy-markdown")
        self.cli("capture", "--spec", self.json_file(spec), success=False)
        self.assertFalse((self.bundle / "operations/read-1").exists())
        self.cli("begin", "--spec", self.json_file(spec), success=False)
        self.assertFalse((self.bundle / "operations/read-1").exists())

    def test_duplicate_operation_cannot_overwrite_old_evidence(self):
        spec = self.search_spec()
        operation = self.capture(spec)
        before = snapshot(operation)
        self.capture(spec, success=False)
        self.assertEqual(snapshot(operation), before)

    def test_read_and_synthesis_validation_are_nonmutating(self):
        self.valid_bundle()
        before = snapshot(self.bundle)
        ledger_path = self.json_file(self.ledger)
        result = self.cli("validate", "--ledger", ledger_path, "--phase", "research")["result"]
        for field, count in (("operations", 2), ("sources", 2), ("claims", 1), ("tool_imports", 0)):
            self.assertEqual(result[field], count, field)
        self.assertEqual(result["contract_sha256"], file_hash(self.bundle / "contract.json"))
        self.assertEqual(result["ledger_sha256"], file_hash(ledger_path))
        self.assertEqual(result["checker_sha256"], file_hash(HELPER))
        self.validate(phase="synthesis")
        self.assertEqual(snapshot(self.bundle), before)

    def test_missing_request_receipt_output_or_index_fails_validation(self):
        self.valid_bundle()
        for name in ("request.json", "receipt.json", "stdout", "stderr", "result.json"):
            with self.subTest(name=name):
                clone = self.clone_bundle("missing-" + name)
                (clone / "operations/search-1" / name).unlink()
                self.validate(root=clone, success=False)

    def test_changed_request_output_and_receipt_are_detected(self):
        self.valid_bundle()
        for name in ("request.json", "stdout", "stderr", "receipt.json"):
            with self.subTest(name=name):
                clone = self.clone_bundle("changed-" + name)
                path = clone / "operations/search-1" / name
                if name.endswith(".json"):
                    value = read_json(path)
                    value["unexpected"] = "altered after capture"
                    path.write_text(json.dumps(value))
                else:
                    path.write_bytes(path.read_bytes() + b"altered after capture\n")
                self.validate(root=clone, success=False)

    def test_incomplete_receipt_does_not_accept_remaining_valid_hashes(self):
        self.valid_bundle()
        path = self.bundle / "operations/search-1/receipt.json"
        receipt = read_json(path)
        del receipt["files"]["stderr"]
        path.write_text(json.dumps(receipt))
        self.validate(success=False)

    def test_contract_cannot_be_silently_edited_after_execution(self):
        self.valid_bundle()
        contract = read_json(self.bundle / "contract.json")
        contract["requirements"]["candidate_table"] = False
        (self.bundle / "contract.json").write_text(json.dumps(contract))
        self.validate(success=False)
        self.capture(self.search_spec("later-search"), success=False)

    def test_amend_preserves_old_receipts_and_allows_new_operations(self):
        self.valid_bundle()
        old_hash = file_hash(self.bundle / "contract.json")
        old_contract = (self.bundle / "contract.json").read_bytes()
        before = snapshot(self.bundle / "operations")
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["brief_max_words"] = 60
        contract["requirements_source"] += " User subsequently requested a 60-word maximum."
        self.cli("amend", "--contract", self.json_file(contract))
        new_hash = file_hash(self.bundle / "contract.json")
        self.assertNotEqual(old_hash, new_hash)
        self.assertEqual((self.bundle / "contracts" / (old_hash + ".json")).read_bytes(), old_contract)
        self.assertEqual(read_json(self.bundle / "contracts" / (new_hash + ".json")), contract)
        self.assertEqual(snapshot(self.bundle / "operations"), before)
        self.validate(phase="synthesis")
        operation = self.capture(self.search_spec("later-search", command=[sys.executable, "-I", "-B", "-c", "print('[]')"]))
        self.index("later-search", [])
        self.assertEqual(read_json(operation / "request.json")["contract_sha256"], new_hash)
        self.assertEqual(read_json(self.bundle / "operations/search-1/request.json")["contract_sha256"], old_hash)
        self.validate(phase="synthesis")

    def test_invalid_amendment_preserves_the_entire_bundle(self):
        self.valid_bundle()
        before = snapshot(self.bundle)
        invalid = copy.deepcopy(self.contract)
        invalid["requirements"]["brief_max_words"] = 0
        self.cli("amend", "--contract", self.json_file(invalid), success=False)
        self.assertEqual(snapshot(self.bundle), before)

    def test_tightened_read_policy_requires_rereading_without_deleting_old_receipts(self):
        self.valid_bundle()
        before = snapshot(self.bundle / "operations")
        contract = copy.deepcopy(self.contract)
        contract["providers"]["archive"]["read_formats"] = ["native-json"]
        contract["providers"]["archive"]["basis"] = "A newly discovered table cell requires native export."
        self.cli("amend", "--contract", self.json_file(contract))
        self.validate(success=False)
        self.capture(self.read_spec("stale-format-read"), success=False)
        self.capture(self.read_spec("faithful-reread", format="native-json", command=[
            sys.executable, "-I", "-B", "-c",
            "print('{\"decision\": \"Deferred pending migration review\"}')",
        ]))
        self.index("faithful-reread", [], "not-applicable")
        self.ledger["sources"][0]["reads"] = ["faithful-reread"]
        self.ledger["claims"][0]["reads"] = ["faithful-reread"]
        self.ledger["claims"][0]["locator"] = "operations/faithful-reread/stdout, decision field"
        self.validate(phase="synthesis")
        after = snapshot(self.bundle / "operations")
        for name, value in before.items():
            self.assertEqual(after[name], value, name)

    def test_missing_or_tampered_historical_contract_invalidates_old_receipts(self):
        self.valid_bundle()
        old_hash = file_hash(self.bundle / "contract.json")
        contract = copy.deepcopy(self.contract)
        contract["requirements"]["brief_max_words"] = 60
        self.cli("amend", "--contract", self.json_file(contract))
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                clone = self.clone_bundle("contract-" + str(deleted))
                path = clone / "contracts" / (old_hash + ".json")
                if deleted:
                    path.unlink()
                else:
                    path.write_text(json.dumps(contract))
                self.validate(root=clone, success=False)

    def test_amended_questions_require_updated_coverage_before_synthesis(self):
        self.valid_bundle()
        contract = copy.deepcopy(self.contract)
        contract["questions"].append({"id": "ownership", "text": "Who owns the next review?", "central": False})
        self.cli("amend", "--contract", self.json_file(contract))
        self.validate()
        self.validate(phase="synthesis", success=False)
        self.ledger["questions"].append({"id": "ownership", "status": "bounded", "claims": [],
                                         "reason": "Ownership is absent from the retained records."})
        self.validate(phase="synthesis")

    def test_index_requires_completion_and_retains_cap_and_pagination_limits(self):
        spec = self.search_spec("paged-search")
        self.cli("begin", "--spec", self.json_file(spec))
        result = {"source_ids": ["record-a"], "pagination": "more", "next_cursor": "page-2",
                  "note": "One retained response page; another page is advertised."}
        self.cli("index", "--id", "paged-search", "--result", self.json_file(result), success=False)
        output = self.text_file("page-output.json", '{"ids": ["record-a"], "next": "page-2"}')
        self.cli("finish", "--id", "paged-search", "--output", output, "--outcome", "ok")
        self.cli("index", "--id", "paged-search", "--result", self.json_file(result))
        indexed = read_json(self.bundle / "operations/paged-search/result.json")
        for field in ("source_ids", "pagination", "next_cursor", "note"):
            self.assertEqual(indexed[field], result[field])
        self.assertEqual(indexed["provenance"], "agent-extracted")
        self.cli("index", "--id", "paged-search", "--result", self.json_file(result), success=False)

    def test_index_rejects_duplicate_ids_and_missing_pagination(self):
        self.capture(self.search_spec())
        invalid = [
            {"source_ids": ["record-a", "record-a"], "pagination": "complete", "next_cursor": None, "note": "Duplicate IDs."},
            {"source_ids": ["record-a"], "next_cursor": None, "note": "Missing pagination."},
            {"source_ids": ["record-a"], "pagination": "complete", "note": "Missing cursor."},
        ]
        for result in invalid:
            with self.subTest(result=result):
                self.cli("index", "--id", "search-1", "--result", self.json_file(result), success=False)
                self.assertFalse((self.bundle / "operations/search-1/result.json").exists())

    def test_every_indexed_candidate_requires_inventory_and_discovery_provenance(self):
        self.valid_bundle()
        missing = copy.deepcopy(self.ledger)
        missing["sources"].pop()
        self.validate(missing, success=False)
        unbacked = copy.deepcopy(self.ledger)
        unbacked["sources"][1]["discovered_by"] = ["seed:participant-link"]
        self.validate(unbacked, success=False)
        invented = copy.deepcopy(self.ledger)
        invented["sources"][1]["discovered_by"].append("nonexistent-search")
        self.validate(invented, success=False)

    def test_failed_read_cannot_establish_reading_or_support_a_claim(self):
        self.valid_bundle()
        self.capture(self.read_spec("failed-read", command=[sys.executable, "-I", "-B", "-c", "raise SystemExit(3)"]), success=False)
        self.index("failed-read", [], "unknown")
        failed_read = copy.deepcopy(self.ledger)
        failed_read["sources"][0]["reads"] = ["failed-read"]
        self.validate(failed_read, success=False)
        failed_claim = copy.deepcopy(self.ledger)
        failed_claim["claims"][0]["reads"] = ["failed-read"]
        self.validate(failed_claim, success=False)
        self.validate()  # Recorded failure may remain as a transparent limitation.

    def test_claims_need_read_evidence_and_a_locator(self):
        self.valid_bundle()
        for field, value in (("reads", ["search-1"]), ("reads", []), ("locator", "")):
            with self.subTest(field=field, value=value):
                ledger = copy.deepcopy(self.ledger)
                ledger["claims"][0][field] = value
                self.validate(ledger, success=False)

    def test_synthesis_requires_all_questions_and_a_recorded_semantic_review(self):
        self.valid_bundle()
        missing_question = copy.deepcopy(self.ledger)
        missing_question["questions"].pop()
        self.validate(missing_question, phase="synthesis", success=False)
        unresolved = copy.deepcopy(self.ledger)
        unresolved["questions"][0]["status"] = "unresolved"
        self.validate(unresolved, phase="synthesis", success=False)
        unsupported = copy.deepcopy(self.ledger)
        unsupported["questions"][0]["claims"] = []
        self.validate(unsupported, phase="synthesis", success=False)
        unreviewed = copy.deepcopy(self.ledger)
        del unreviewed["semantic_review"]
        self.validate(unreviewed, phase="synthesis", success=False)

    def test_bounded_central_gap_requires_executed_followups(self):
        self.valid_bundle()
        question = self.ledger["questions"][0]
        question.update(status="bounded", claims=[], reason="Decision approval is not in the available export.")
        self.validate(phase="synthesis", success=False)
        question["followups"] = ["planned-alias-search"]
        self.validate(phase="synthesis", success=False)
        self.capture(self.search_spec("alias-followup", command=[sys.executable, "-I", "-B", "-c", "print('[]')"]))
        self.index("alias-followup", [])
        question["followups"] = ["alias-followup"]
        self.validate(phase="synthesis")

    def test_publication_accepts_harmless_style_changes_and_returns_export_hashes(self):
        self.valid_bundle()
        manifest = self.publication_files()
        for artifact in manifest["artifacts"]:
            actual = Path(artifact["actual"][0])
            text = actual.read_text().replace("<table>", '<table style="width:100%">')
            text = text.replace("<p>", '<p class="normal" style="color:#111">')
            text = text.replace("<body>", '<body style="margin:24px">')
            actual.write_text(text)
        before = snapshot(self.bundle)
        result = self.publish(manifest)
        for key, sides in result["result"]["artifacts"].items():
            for files in sides.values():
                for path, expected_hash in files.items():
                    self.assertEqual(file_hash(Path(path)), expected_hash, key)
        self.assertEqual(snapshot(self.bundle), before)

    def test_candidate_checks_allow_source_ids_in_other_tables(self):
        self.valid_bundle()
        manifest = self.publication_files()
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            path.write_text(path.read_text().replace('</body>',
                '<table><tr><td>record-a</td><td>Read operation log</td></tr></table></body>'))
        self.publish(manifest)
        for selection in (None, [], [1], [0, 0], [True], [2]):
            with self.subTest(selection=selection):
                changed = copy.deepcopy(manifest)
                changed["candidate_tables"] = selection
                self.publish(changed, success=False)

    def compact_candidate_rows(self, manifest):
        mapping = [{"source_id": source["id"], "row_id": "S%04d" % number}
                   for number, source in enumerate(self.ledger["sources"], 1)]
        manifest["candidate_rows"] = mapping
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            text = path.read_text()
            for entry in mapping:
                text = text.replace("<td>" + entry["source_id"] + "</td>",
                                    "<td>" + entry["row_id"] + "</td>")
            path.write_text(text)
        return manifest

    def test_publication_accepts_explicit_compact_ids_without_mutating_sources(self):
        self.valid_bundle()
        before = copy.deepcopy(self.ledger)
        manifest = self.compact_candidate_rows(self.publication_files())
        check = self.publish(manifest)["result"]["candidate_check"]
        self.assertEqual(check["rows_checked"], 2)
        self.assertEqual(check["identity_basis"], "declared row IDs")
        self.assertEqual(check["navigation_url_normalizations"], [])
        self.assertEqual(self.ledger, before)
        del manifest["candidate_rows"]
        self.publish(manifest, success=False)

    def test_publication_rejects_incomplete_ambiguous_or_unknown_row_mapping(self):
        self.valid_bundle()
        manifest = self.compact_candidate_rows(self.publication_files())
        valid = manifest["candidate_rows"]
        variants = [None, {}, [], valid[:1], valid + [valid[0]], [valid[0], valid[0]]]
        for field, values in (("source_id", ("unknown", "record-a", None, [])),
                              ("row_id", ("S0001", " S0002 ", "", None, []))):
            for value in values:
                changed = copy.deepcopy(valid)
                changed[1][field] = value
                variants.append(changed)
        variants.append([valid[0], "not an object"])
        for mapping in variants:
            with self.subTest(mapping=mapping):
                manifest["candidate_rows"] = mapping
                self.publish(manifest, success=False)

    def test_publication_rejects_two_source_records_combined_into_one_row(self):
        self.valid_bundle()
        manifest = self.compact_candidate_rows(self.publication_files())
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            path.write_text(path.read_text().replace('</tr><tr><td>S0002', '<td>S0002'))
        self.publish(manifest, success=False)

    def test_publication_rejects_incorrect_mapping_and_wrong_per_row_url(self):
        self.valid_bundle()
        manifest = self.compact_candidate_rows(self.publication_files())
        manifest["candidate_rows"][0]["row_id"] = "S0002"
        manifest["candidate_rows"][1]["row_id"] = "S0001"
        self.publish(manifest, success=False)
        manifest = self.compact_candidate_rows(self.publication_files())
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            path.write_text(path.read_text().replace('/records/b', '/records/wrong'))
        self.publish(manifest, success=False)

    def test_publication_keeps_whitespace_aliases_as_distinct_rows(self):
        self.valid_bundle()
        alias = copy.deepcopy(self.ledger["sources"][1])
        alias.update(id=alias["id"] + " ", url=alias["url"] + " ",
                     discovered_by=["seed:separate retained search identity"])
        self.ledger["sources"].append(alias)
        before = copy.deepcopy(self.ledger)
        manifest = self.compact_candidate_rows(self.publication_files())
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            path.write_text(path.read_text().replace('/records/b ">', '/records/b">'))
        check = self.publish(manifest)["result"]["candidate_check"]
        self.assertEqual(check["rows_checked"], 3)
        self.assertEqual(len(check["navigation_url_normalizations"]), 1)
        self.assertEqual(check["navigation_url_normalizations"][0]["rule"],
                         "strip-surrounding-whitespace")
        self.assertEqual(check["navigation_url_normalizations"][0]["source_id_sha256"],
                         hashlib.sha256(alias["id"].encode()).hexdigest())
        self.assertEqual(check["navigation_url_normalizations"][0]["navigation_url_sha256"],
                         hashlib.sha256(alias["url"].strip().encode()).hexdigest())
        self.assertEqual(self.ledger, before)
        # Encoding a raw trailing blank changes the navigation target; it is not
        # equivalent to trimming incidental whitespace around a source URL.
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            path.write_text(path.read_text().replace(
                '<td>S0003</td><td><a href="https://example.invalid/records/b">',
                '<td>S0003</td><td><a href="https://example.invalid/records/b%20">'))
        failure = self.publish(manifest, success=False)
        self.assertEqual(failure["error"], "candidate row missing source link: " + alias["id"])

    def test_publication_detects_text_row_cell_link_and_link_multiplicity_losses(self):
        self.valid_bundle()
        mutations = {
            "text": lambda value: value.replace("Deferred pending a migration review.", "Approved without conditions."),
            "row": lambda value: value.replace('<tr><td>record-b</td>', '<tr><td>record-missing</td>'),
            "cell": lambda value: value.replace('<td>Metadata only; body unread</td>', ''),
            "link": lambda value: value.replace('href="https://example.invalid/records/b"', 'href="https://example.invalid/wrong"'),
            "multiplicity": lambda value: value.replace('<a href="https://example.invalid/records/a">Repeated citation</a>', 'Repeated citation'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                manifest = self.publication_files()
                actual = Path(manifest["artifacts"][1]["actual"][0])
                actual.write_text(mutate(actual.read_text()))
                self.publish(manifest, success=False)

    def test_publication_rejects_moved_cells_even_if_all_words_and_links_remain(self):
        self.valid_bundle()
        manifest = self.publication_files()
        actual = Path(manifest["artifacts"][1]["actual"][0])
        actual.write_text(actual.read_text().replace(
            '<td>Metadata only; body unread</td><td>Potential later outcome source; content not established.</td>',
            '<td>Metadata only; body</td><td>unread Potential later outcome source; content not established.</td>'))
        self.publish(manifest, success=False)

    def test_publication_rejects_swapped_citation_destinations(self):
        self.valid_bundle()
        brief = ('<p>The pilot was deferred. <a href="https://example.invalid/records/a">Decision note</a></p>'
                 '<p>Later outcomes remain unverified. <a href="https://example.invalid/records/b">Outcome index</a></p>'
                 '<p><a href="https://example.invalid/companion">Evidence companion</a></p>')
        manifest = self.publication_files(brief=brief)
        actual = Path(manifest["artifacts"][0]["actual"][0])
        actual.write_text(brief.replace("/records/a", "/records/temporary")
                         .replace("/records/b", "/records/a").replace("/records/temporary", "/records/b"))
        self.publish(manifest, success=False)

    def test_publication_rejects_moving_same_label_link_to_another_claim(self):
        self.valid_bundle()
        brief = ('<p>The pilot was deferred. <a href="https://example.invalid/records/a">Source</a></p>'
                 '<p>Later outcomes remain unverified. Source</p>'
                 '<p><a href="https://example.invalid/companion">Evidence companion</a></p>')
        manifest = self.publication_files(brief=brief)
        actual = Path(manifest["artifacts"][0]["actual"][0])
        actual.write_text(brief.replace('<a href="https://example.invalid/records/a">Source</a>', 'Source')
                         .replace('unverified. Source', 'unverified. <a href="https://example.invalid/records/a">Source</a>'))
        self.publish(manifest, success=False)

    def test_publication_rejects_moving_unchanged_content_across_tab_boundaries(self):
        self.valid_bundle()
        manifest = self.publication_files()
        companion = manifest["artifacts"][1]
        original = Path(companion["expected"][0]).read_text()
        expected_split = original.index("<table>")
        actual_split = original.index("<p>")
        for side, split in (("expected", expected_split), ("actual", actual_split)):
            Path(companion[side][0]).write_text(original[:split] + "</body></html>")
            rest = self.text_file("boundary-" + side + "-second.html", "<html><body>" + original[split:])
            companion[side].append(str(rest))
        self.publish(manifest, success=False)

    def test_publication_rejects_missing_mutual_links_even_in_matching_exports(self):
        self.valid_bundle()
        for key in (0, 1):
            with self.subTest(key=key):
                manifest = self.publication_files()
                artifact = manifest["artifacts"][key]
                other_url = manifest["artifacts"][1 - key]["url"]
                for side in ("expected", "actual"):
                    path = Path(artifact[side][0])
                    path.write_text(path.read_text().replace(other_url, "https://example.invalid/unrelated"))
                self.publish(manifest, success=False)

    def test_publication_rejects_unmet_table_requirement_despite_matching_exports(self):
        self.valid_bundle()
        companion = ('<p><a href="https://example.invalid/brief">Brief</a></p>'
                     '<p>record-a Archive pilot review. record-b Archive follow-up index.</p>')
        self.publish(self.publication_files(companion=companion), success=False)

    def test_publication_rejects_missing_or_duplicate_candidate_rows(self):
        self.valid_bundle()
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                manifest = self.publication_files()
                for side in ("expected", "actual"):
                    path = Path(manifest["artifacts"][1][side][0])
                    text = path.read_text()
                    start = text.index('<tr><td>record-b</td>')
                    end = text.index('</tr>', start) + len('</tr>')
                    row = text[start:end]
                    path.write_text(text[:start] + (row + row if duplicate else "") + text[end:])
                self.publish(manifest, success=False)

    def test_publication_requires_inventory_details_even_in_matching_exports(self):
        self.valid_bundle()
        for field in ("title", "coverage", "finding", "contributor_basis"):
            with self.subTest(field=field):
                manifest = self.publication_files()
                for side in ("expected", "actual"):
                    path = Path(manifest["artifacts"][1][side][0])
                    path.write_text(path.read_text().replace(self.ledger["sources"][1][field], "Omitted"))
                self.publish(manifest, success=False)

    def test_publication_checks_cell_spans_and_ordered_multiple_exports(self):
        self.valid_bundle()
        manifest = self.publication_files()
        companion = manifest["artifacts"][1]
        for side in ("expected", "actual"):
            path = Path(companion[side][0])
            original = path.read_text()
            split = original.index("<table>")
            path.write_text(original[:split])
            rest = self.text_file("companion-" + side + "-second.html", original[split:])
            companion[side].append(str(rest))
        self.publish(manifest)
        actual = Path(companion["actual"][1])
        actual.write_text(actual.read_text().replace("<td>record-a</td>", '<td colspan="2">record-a</td>'))
        self.publish(manifest, success=False)
        actual.write_text(actual.read_text().replace('<td colspan="2">record-a</td>', "<td>record-a</td>"))
        companion["actual"].reverse()
        self.publish(manifest, success=False)

    def test_publication_rejects_source_link_outside_its_candidate_row(self):
        self.valid_bundle()
        manifest = self.publication_files()
        for side in ("expected", "actual"):
            path = Path(manifest["artifacts"][1][side][0])
            text = path.read_text().replace(
                '<a href="https://example.invalid/records/b">Archive follow-up index</a>',
                'Archive follow-up index')
            text = text.replace('</table>', '</table><a href="https://example.invalid/records/b">Detached link</a>')
            path.write_text(text)
        self.publish(manifest, success=False)

    def test_publication_enforces_requested_word_limit(self):
        self.valid_bundle()
        brief = ('<p>' + 'word ' * 81 + '</p>'
                 '<a href="https://example.invalid/companion">Evidence companion</a>')
        self.publish(self.publication_files(brief=brief), success=False)

    def test_publication_rejects_same_expected_and_actual_file(self):
        self.valid_bundle()
        manifest = self.publication_files()
        manifest["artifacts"][0]["actual"] = manifest["artifacts"][0]["expected"]
        self.publish(manifest, success=False)

    def test_publication_cannot_skip_the_synthesis_gate(self):
        self.valid_bundle()
        manifest = self.publication_files()
        del self.ledger["semantic_review"]
        self.publish(manifest, success=False)


if __name__ == "__main__":
    unittest.main()
