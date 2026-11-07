#!/usr/bin/env python3
"""Exercise the public graph command boundary in owned local bundles."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "assets/agent-assets/skills/retrospective-research/scripts/research_bundle.py"


class GraphCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-graph-cli-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "bundle"
        contract = {
            "version": 1, "purpose": "Explain the fictional Orchard pilot.",
            "scope": "Owned synthetic records only.", "requirements_source": "CLI fixture",
            "requirements": {"candidate_table": True, "destination": "Owned local fixture"},
            "providers": {"archive": {"read_formats": ["html"], "basis": "Fictional adapter"}},
            "questions": [{"id": "why", "text": "Why was the pilot paused?", "central": True}],
        }
        path = self.directory / "contract.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        self.command("init", self.root, "--contract", path)

    def command(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(CLI), *map(str, arguments)],
            cwd=self.directory, env={"PATH": os.defpath},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "pass" if expected == 0 else "fail")
        return value

    def initialize(self):
        return self.command("graph", "init", self.root, "--run-id", "fixture-cli")

    def test_ingest_recovery_and_validation_use_the_same_bundle(self):
        initialized = self.initialize()
        packet = {
            "schema_version": 1, "batch_id": "source-intake", "actor_id": "coordinator",
            "assignment_id": None, "packet_kind": "observe",
            "contract_sha256": hashlib.sha256((self.root / "contract.json").read_bytes()).hexdigest(),
            "submitted_at": "2026-01-02T10:00:00Z",
            "mutations": [{"op": "insert", "table": "artifacts", "record": {
                "artifact_id": "orchard", "title": "Orchard pilot", "artifact_type": "document"}},
                {"op": "insert", "table": "discoveries", "record": {
                    "discovery_id": "intake-orchard", "artifact_id": "orchard",
                    "supplied_seed_id": "intake", "observed_title": "Orchard pilot"}}],
        }
        path = self.directory / "packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        accepted = self.command("graph", "ingest", self.root, "--file", path)
        self.assertGreater(accepted["graph_sequence"], initialized["graph_sequence"])
        replay = self.command("graph", "ingest", self.root, "--file", path)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(accepted["graph_sequence"], replay["graph_sequence"])
        self.command("graph", "checkpoint", self.root, "--id", "before-recovery")
        self.assertFalse(self.command("graph", "recover", self.root)["applied"])
        self.assertTrue(self.command("graph", "recover", self.root, "--apply")["applied"])
        checked = self.command("graph", "validate", self.root)
        self.assertEqual(checked["graph_sequence"], accepted["graph_sequence"])
        found = self.command("graph", "artifact", self.root, "--artifact-id", "orchard",
                             "--include", "metadata", "--limit", 1)
        self.assertEqual(len(found["items"]), 1)
        self.assertIsNotNone(found["next_cursor"])
        continued = self.command("graph", "artifact", self.root, "--artifact-id", "orchard",
                                 "--include", "metadata", "--limit", 1,
                                 "--cursor", found["next_cursor"])
        self.assertNotEqual(found["items"], continued["items"])
        coverage = self.command("graph", "coverage", self.root, "--question-id", "why")
        self.assertTrue(coverage["items"])
        invalid = self.command("graph", "coverage", self.root, "--question-id", "why", "--limit", 201,
                               expected=1)
        self.assertEqual(invalid["error"]["code"], "invalid_packet")

    def test_malformed_reconcile_has_safe_structured_failure(self):
        self.initialize()
        path = self.directory / "invalid.json"
        for contents in ('[]', '{"source_body":"sensitive fixture text"', 'null'):
            with self.subTest(contents=contents):
                path.write_text(contents, encoding="utf-8")
                result = self.command("graph", "reconcile", self.root, "--file", path, expected=1)
                self.assertEqual(result["command"], "reconcile")
                self.assertEqual(result["error"]["code"], "invalid_packet")
                self.assertNotIn("sensitive fixture text", json.dumps(result))

    def test_duplicate_json_keys_are_not_lost_at_cli_boundary(self):
        self.initialize()
        path = self.directory / "duplicate.json"
        path.write_text('{"packet_kind":"reconcile","packet_kind":"reconcile"}', encoding="utf-8")
        result = self.command("graph", "reconcile", self.root, "--file", path, expected=1)
        self.assertEqual(result["error"]["code"], "invalid_packet")
        self.assertIn("Duplicate", result["error"]["message"])

    def test_index_preflight_and_retained_version_omission_commands(self):
        # Retain a fictional operation through public recorder commands before
        # initializing the graph; no provider process is launched.
        spec = self.directory / "search.json"
        spec.write_text(json.dumps(dict(id="search-one", actor="reader", kind="search", provider="archive",
                                       request=dict(query="Orchard", filters={}, cap=5, cursor=None))))
        output = self.directory / "provider.json"
        output.write_text('{"items": [{"id": "fictional-source"}]}')
        self.command("begin", self.root, "--spec", spec)
        self.command("finish", self.root, "--id", "search-one", "--output", output, "--outcome", "ok")
        draft = self.directory / "index.json"
        draft.write_text(json.dumps(dict(source_ids=["fictional-source"], sources=[dict(source_id="fictional-source",
            version={"modified_at": "2020-04-07 17:19:52 PDT"})], pagination="complete", next_cursor=None, note="Owned fixture metadata")))
        # This graphless recorder preserves a historical malformed index.
        self.command("index", self.root, "--id", "search-one", "--result", draft)
        original = self.root / "operations/search-one/result.json"
        raw = original.read_bytes()
        second = json.loads(spec.read_text())
        second["id"] = "search-two"
        spec.write_text(json.dumps(second))
        self.command("begin", self.root, "--spec", spec)
        self.command("finish", self.root, "--id", "search-two", "--output", output, "--outcome", "ok")
        self.initialize()
        failed = self.command("graph", "check-index", self.root, "--id", "search-two", "--file", draft, expected=1)
        self.assertEqual(failed["error"]["code"], "invalid_packet")
        self.assertFalse((self.root / "operations/search-two/result.json").exists())
        failed = self.command("graph", "sync", self.root, "--operation-id", "search-one", expected=1)
        self.assertEqual(failed["error"]["code"], "invalid_packet")
        omissions = self.directory / "omissions.json"
        omissions.write_text(json.dumps([dict(source_index=0, source_id="fictional-source", fields=["modified_at"])]))
        corrected = self.command("graph", "omit-index-version", self.root, "--id", "search-one", "--sha256",
                                 hashlib.sha256(raw).hexdigest(), "--file", omissions, "--reason", "Unsupported optional timestamp")
        self.assertEqual(corrected["state"], "complete")
        self.assertEqual(original.read_bytes(), raw)
        # Public preflight refuses to treat an already retained result as a new draft.
        failed = self.command("graph", "check-index", self.root, "--id", "search-one", "--file", draft, expected=1)
        self.assertEqual(failed["command"], "check-index")
        valid = json.loads(draft.read_text())
        del valid["sources"][0]["version"]
        draft.write_text(json.dumps(valid))
        checked = self.command("graph", "check-index", self.root, "--id", "search-two", "--file", draft)
        self.assertTrue(checked["valid"])
        self.assertFalse(checked["original_retained"])
        self.assertFalse((self.root / "operations/search-two/result.json").exists())
        self.command("index", self.root, "--id", "search-two", "--result", draft)


if __name__ == "__main__":
    unittest.main()
