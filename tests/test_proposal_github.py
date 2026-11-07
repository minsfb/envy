"""Synthetic protocol + one owned executable; no ambient gh/auth/network.

Candidate modules are imported on load. The executable is a Python script under
each test's owned root; it reads/writes only three precreated fixture JSON files,
uses pipes and may sleep/ignore TERM for bounded cleanup tests. It never forks,
launches another program, opens a socket or reads credentials/configuration.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_proposal_publication import CORE, CASES


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets/agent-assets/skills/publish-proposals/scripts/github_issues.py"
SPEC = importlib.util.spec_from_file_location("proposal_github_protocol", SCRIPT)
GH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GH)
FIXTURE = json.loads((ROOT / "tests/fixtures/proposal-publication/github-cases.json").read_bytes())


FAKE = r'''
import sys
sys.dont_write_bytecode = True
import json, os, signal, time
from pathlib import Path
root = Path(__file__).resolve().parent
settings = json.loads((root / "settings.json").read_bytes())
mode = settings["mode"]
if mode == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
raw = sys.stdin.buffer.read(1048577)
request = json.loads(raw)
calls = json.loads((root / "calls.json").read_bytes())
calls.append({"argv": sys.argv[1:], "request": request,
              "synthetic_environment_preserved": os.environ.get("P2_SYNTHETIC_CONTEXT") == "kept"})
(root / "calls.json").write_text(json.dumps(calls))
if mode in {"sleep", "ignore-term"}:
    time.sleep(60)
if mode == "stdout-overflow":
    os.write(1, b"x" * 4096)
    sys.exit(0)
if mode == "stderr-overflow":
    os.write(2, b"SYNTHETIC_SECRET" * 256)
    sys.exit(0)
if mode == "failed":
    os.write(2, b"SYNTHETIC_SECRET")
    sys.exit(7)
if mode == "partial":
    print(json.dumps({"data": {}, "errors": [{"message": "SYNTHETIC_SECRET"}]}))
    sys.exit(0)
names = [key for key, query in settings["operations"].items() if query == request["query"]]
assert len(names) == 1
name = names[0]
variables = request["variables"]
context = settings["context"]
repo = context["repository"]
state = json.loads((root / "state.json").read_bytes())
if name == "named":
    result = context
elif name == "context":
    result = {"viewer": context["viewer"], "node": dict(repo, __typename="Repository")}
elif name == "page":
    result = {"node": {"__typename": "Repository", "id": repo["id"],
                      "issues": {"nodes": state, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
elif name == "read":
    matches = [item for item in state if item["number"] == variables["number"]]
    result = {"node": {"__typename": "Repository", "id": repo["id"], "issue": matches[0] if len(matches) == 1 else None}}
else:
    assert name == "create" and variables["repositoryId"] == repo["id"]
    number = len(state) + 1
    issue = {"__typename": "Issue", "id": "I_fixture_" + str(number), "databaseId": 3000 + number,
             "number": number, "title": variables["title"], "body": variables["body"],
             "url": "https://forge.example.invalid/example/proposal-demo/issues/" + str(number),
             "repository": {key: repo[key] for key in ("id", "databaseId", "name", "owner")}}
    state.append(issue)
    (root / "state.json").write_text(json.dumps(state))
    if mode == "lost-create":
        print("malformed after owned fake creation")
        sys.exit(0)
    result = {"createIssue": {"issue": issue}}
print(json.dumps({"data": result}))
'''


class QueueTransport:
    def __init__(self, replies):
        self.replies = copy.deepcopy(replies)
        self.calls = []

    def call(self, host, operation, variables):
        self.calls.append((host, operation, copy.deepcopy(variables)))
        return self.replies.pop(0)


class GithubProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="proposal-gh-owned-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); self.root.chmod(0o700)
        self.client = self.root / "fake-gh"
        self.client.write_text("#!" + sys.executable + " -B\n" + FAKE)
        self.client.chmod(0o700)
        self.settings = {"mode": "service", "context": copy.deepcopy(FIXTURE["context"]), "operations": GH.OPERATIONS}
        for name, value in (("settings.json", self.settings), ("calls.json", []), ("state.json", [])):
            path = self.root / name; path.write_text(json.dumps(value)); path.chmod(0o600)
        self.target = copy.deepcopy(CASES["proposals"]["destination"])

    def mode(self, name):
        self.settings["mode"] = name
        (self.root / "settings.json").write_text(json.dumps(self.settings))

    def transport(self):
        # Trusted test-code patch only; not a CLI/data/environment enable flag.
        with patch.object(GH, "_resolve_client", return_value=(self.client, "owned-fixture-v1")):
            return GH._Transport()

    def adapter(self):
        with patch.object(GH, "_resolve_client", return_value=(self.client, "owned-fixture-v1")):
            return GH.GitHubIssues(self.target)

    def calls(self):
        return json.loads((self.root / "calls.json").read_bytes())

    def request(self):
        proposal = CASES["proposals"]["proposals"][1]
        marker = "proposal-v1:" + "a" * 32 + ":" + proposal["id"]
        return {"kind": "issue.create", "destination": copy.deepcopy(self.target), "marker": marker,
                "title": proposal["title"], "body": CORE.body(proposal, marker, [])}

    def raw_issue(self, number=1, body=None):
        repo = FIXTURE["context"]["repository"]
        request = self.request()
        return {"__typename": "Issue", "id": "I_fixture_" + str(number), "databaseId": 3000 + number,
                "number": number, "url": "https://forge.example.invalid/example/proposal-demo/issues/" + str(number),
                "title": request["title"], "body": request["body"] if body is None else body,
                "repository": {key: copy.deepcopy(repo[key]) for key in ("id", "databaseId", "name", "owner")}}

    def page(self, nodes, more=False, cursor=None):
        return {"node": {"__typename": "Repository", "id": "R_fixture",
                         "issues": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}}

    def pure_adapter(self, replies):
        # Isolate normalization/pagination; constructor has separate fake-client tests.
        adapter = GH.GitHubIssues.__new__(GH.GitHubIssues)
        adapter.target = copy.deepcopy(self.target); adapter.repository_id = "R_fixture"
        adapter.transport = QueueTransport(replies)
        return adapter

    def number_conflict_pages(self, exact, marker_at, paginated):
        first, second = copy.deepcopy(exact), copy.deepcopy(exact)
        second.update(id="I_distinct_number_conflict", databaseId=9001)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["databaseId"], second["databaseId"])
        if marker_at != 0: first["body"] = "Unrelated first issue."
        if marker_at != 1: second["body"] = "Unrelated second issue."
        pages = ([self.page([first], True, "next"), self.page([second])]
                 if paginated else [self.page([first, second])])
        return pages, first if marker_at != 1 else second

    def assert_reaped(self, transport):
        self.assertTrue(transport.last["reaped"])
        with self.assertRaises(ChildProcessError):
            os.waitpid(transport.last["pid"], os.WNOHANG)

    def test_default_resolver_and_constructor_never_launch(self):
        with patch.object(GH.subprocess, "Popen", side_effect=AssertionError("ambient launch")):
            for call in (GH._resolve_client, GH._Transport, lambda: GH.GitHubIssues(self.target)):
                with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                    call()
        self.assertEqual(self.calls(), [])

    def test_fixed_argv_and_compiled_stdin_preserve_environment(self):
        transport = self.transport(); before = dict(os.environ)
        with patch.dict(os.environ, {"P2_SYNTHETIC_CONTEXT": "kept"}), patch.object(GH.os, "killpg", wraps=os.killpg) as killed:
            observed = transport.call(self.target["host"], "named", {"owner": "example", "name": "proposal-demo"})
            killed.assert_not_called()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(observed, FIXTURE["context"])
        call = self.calls()[0]
        self.assertEqual(call["argv"], ["api", "--hostname", "forge.example.invalid", "--method", "POST", "graphql", "--input", "-"])
        self.assertEqual(call["request"], {"query": GH.OPERATIONS["named"], "variables": {"owner": "example", "name": "proposal-demo"}})
        self.assertTrue(call["synthetic_environment_preserved"])
        self.assert_reaped(transport)

    def test_mutation_has_only_typed_variables_not_query_or_argv_text(self):
        text = 'Synthetic "quote"; $(never)\nquery-like text'
        payload = json.loads(GH.operation("create", {"repositoryId": "R_fixture", "title": "Synthetic", "body": text}))
        self.assertEqual(payload["query"], GH.OPERATIONS["create"])
        self.assertEqual(payload["variables"]["body"], text)
        for name, value in (("unknown", {}), ("create", {"repositoryId": "R_fixture", "title": "x", "body": "x", "query": "untrusted"}),
                            ("read", {"id": "R_fixture", "number": True})):
            with self.assertRaises(GH.Error): GH.operation(name, value)
        self.assertEqual(self.calls(), [])

    def test_context_identity_permissions_and_closed_fields(self):
        for case in FIXTURE["context_mutations"]:
            value = copy.deepcopy(FIXTURE["context"]); target = value
            for part in case["path"][:-1]: target = target[part]
            target[case["path"][-1]] = case["value"]
            with self.subTest(path=case["path"], value=case["value"]):
                with self.assertRaises(GH.Error): GH.context(value, self.target)
        value = copy.deepcopy(FIXTURE["context"]); value["extra"] = True
        with self.assertRaises(GH.Error): GH.context(value, self.target)

    def test_public_visibility_requires_independent_internet_audience(self):
        value = copy.deepcopy(FIXTURE["context"]); value["repository"]["visibility"] = "PUBLIC"
        with self.assertRaisesRegex(GH.Error, "^audience_mismatch$"): GH.context(value, self.target)
        target = copy.deepcopy(self.target); target["audience"] = {"kind": "internet", "realm": "internet", "id": "public"}
        self.assertEqual(GH.context(value, target)["visibility"], "PUBLIC")

    def test_pinned_repository_type_and_node_are_exact(self):
        value = {"viewer": FIXTURE["context"]["viewer"], "node": dict(FIXTURE["context"]["repository"], __typename="Repository")}
        self.assertEqual(GH.context(value, self.target, "R_fixture")["repository_node_id"], "R_fixture")
        for key, replacement in (("id", "R_other"), ("__typename", "User")):
            changed = copy.deepcopy(value); changed["node"][key] = replacement
            with self.assertRaises(GH.Error): GH.context(changed, self.target, "R_fixture")

    def test_issue_normalization_rejects_wrong_type_target_and_url(self):
        raw = self.raw_issue()
        self.assertEqual(GH.issue(raw, self.target, "R_fixture", self.request()["marker"])["id"], "3001")
        for key, value in (("__typename", "PullRequest"), ("databaseId", True), ("number", 0),
                           ("url", "https://other.example.invalid/issues/1"), ("unexpected", "data")):
            changed = copy.deepcopy(raw); changed[key] = value
            with self.assertRaises(GH.Error): GH.issue(changed, self.target, "R_fixture", self.request()["marker"])
        changed = copy.deepcopy(raw); changed["repository"]["id"] = "R_other"
        with self.assertRaises(GH.Error): GH.issue(changed, self.target, "R_fixture", self.request()["marker"])

    def test_marker_matching_retains_edited_body_and_all_observed_matches(self):
        edited = self.raw_issue(body=self.request()["body"] + "Independent edit.")
        adapter = self.pure_adapter([self.page([edited, self.raw_issue(2)])])
        result = adapter.find(self.request())
        self.assertEqual(len(result["matches"]), 2)
        self.assertIn("Independent edit", result["matches"][0]["body"])
        self.assertEqual(result["consistency"], "not-guaranteed")

    def test_complete_pagination_does_not_claim_consistent_absence(self):
        adapter = self.pure_adapter([self.page([self.raw_issue(body="Unrelated.")], True, "first"), self.page([])])
        self.assertEqual(adapter.find(self.request()), {"traversal_complete": True, "consistency": "not-guaranteed", "matches": []})
        self.assertEqual(adapter.transport.calls[1][2], {"id": "R_fixture", "after": "first"})

    def test_pagination_cycles_duplicate_ids_incomplete_and_caps_stop(self):
        replies = ([self.page([self.raw_issue()], True, "same"), self.page([self.raw_issue(2)], True, "same")],
                   [self.page([self.raw_issue(), self.raw_issue()])],
                   [self.page([], True, "next")], [self.page([], True, None)])
        for values in replies:
            with self.assertRaises(GH.Error): self.pure_adapter(values).find(self.request())
        with patch.object(GH, "MAX_PAGES", 1):
            with self.assertRaisesRegex(GH.Error, "^discovery_limit$"):
                self.pure_adapter([self.page([self.raw_issue()], True, "next")]).find(self.request())
        with patch.object(GH, "DISCOVERY_BYTES", 1):
            with self.assertRaisesRegex(GH.Error, "^discovery_limit$"):
                self.pure_adapter([self.page([])]).find(self.request())

    def test_partial_errors_duplicate_json_and_invalid_numbers_refuse(self):
        for raw in (b'{"data":{},"errors":[]}', b'{"data":null}', b'{"data":{},"data":{}}',
                    b'{"data":{"number":1.0}}', b'{"data":{"number":NaN}}',
                    b'{"data":{"number":' + b'9' * 50000 + b'}}', b'not JSON'):
            with self.assertRaises(GH.Error): GH.parse(raw)

    def test_owned_fake_end_to_end_v2_store_has_one_create_per_operation(self):
        adapter = self.adapter(); store = self.root / "store"
        plan = CORE.prepare(store, CASES["proposals"], "synthetic prepare", binding=adapter.identity)
        self.assertTrue(CORE.apply(store, plan["plan_digest"], "synthetic filing", adapter)["complete"])
        self.assertTrue(CORE.apply(store, plan["plan_digest"], "synthetic filing", adapter)["complete"])
        queries = [call["request"]["query"] for call in self.calls()]
        self.assertEqual(queries.count(GH.OPERATIONS["create"]), 2)
        self.assertEqual(len(json.loads((self.root / "state.json").read_bytes())), 2)
        self.assert_reaped(adapter.transport)

    def test_owned_fake_lost_mutation_response_reconciles_without_second_create(self):
        adapter = self.adapter(); store = self.root / "store"
        plan = CORE.prepare(store, CASES["proposals"], "synthetic prepare", binding=adapter.identity)
        self.mode("lost-create")
        with self.assertRaisesRegex(CORE.Stop, "^write_or_verification_uncertain$"):
            CORE.apply(store, plan["plan_digest"], "synthetic filing", adapter)
        before = (store / "receipt.json").read_bytes()
        with self.assertRaisesRegex(CORE.Stop, "^reconciliation_required$"):
            CORE.apply(store, plan["plan_digest"], "synthetic filing", adapter)
        self.assertEqual((store / "receipt.json").read_bytes(), before)
        self.mode("service")
        result = CORE.reconcile(store, plan["plan_digest"], "synthetic reconciliation", adapter)
        self.assertFalse(result["complete"])
        self.assertEqual(sum(call["request"]["query"] == GH.OPERATIONS["create"] for call in self.calls()), 1)

    def test_client_change_stops_before_another_launch(self):
        transport = self.transport()
        self.client.write_text(self.client.read_text() + "\n# owned fixture mutation\n")
        with patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected launch")):
            with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                transport.call(self.target["host"], "context", {"id": "R_fixture"})

    def test_client_fingerprint_ignores_access_time_only(self):
        raw = self.client.read_bytes()
        original = self.client.stat()
        os.utime(self.client, ns=(1_000_000_000, original.st_mtime_ns))
        old = self.client.stat()
        self.assertEqual(old.st_atime_ns, 1_000_000_000)
        expected = {"executable": str(self.client), "resolved": str(self.client),
                    "sha256": GH.hashlib.sha256(raw).hexdigest(), "version": "owned-fixture-v1"}
        self.assertEqual(GH._fingerprint(self.client, "owned-fixture-v1"), expected)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        current = self.client.stat()
        self.assertEqual(tuple(getattr(old, name) for name in stable),
                         tuple(getattr(current, name) for name in stable))
        # A noatime mount may leave real atime unchanged. Always exercise an
        # atime-only difference around a real owned-file read as well.
        values = {name: getattr(current, name) for name in (*stable, "st_atime", "st_atime_ns")}
        before = SimpleNamespace(**values)
        after = SimpleNamespace(**dict(values, st_atime=values["st_atime"] + 1,
                                       st_atime_ns=values["st_atime_ns"] + 1_000_000_000))
        self.assertNotEqual(before, after)
        original_stat = Path.stat
        observations = []
        def leaf_stat(path, *args, **kwargs):
            if path == self.client and not args and not kwargs:
                observations.append(path)
                self.assertLessEqual(len(observations), 2)
                return (before, after)[len(observations) - 1]
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", autospec=True, side_effect=leaf_stat):
            self.assertEqual(GH._fingerprint(self.client, "owned-fixture-v1"), expected)
        self.assertEqual(observations, [self.client, self.client])
        self.assertEqual(self.calls(), [])

    def test_client_replacement_during_fingerprint_is_rejected(self):
        original_read = Path.read_bytes
        raw = original_read(self.client)
        original_inode = self.client.stat().st_ino
        replacement = self.root / "replacement-gh"
        replacement.write_bytes(raw); replacement.chmod(0o700)
        reads = []
        def replace_after_read(path):
            data = original_read(path)
            if path == self.client:
                reads.append(path)
                os.replace(replacement, self.client)
            return data
        with patch.object(Path, "read_bytes", autospec=True, side_effect=replace_after_read):
            with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                GH._fingerprint(self.client, "owned-fixture-v1")
        self.assertEqual(reads, [self.client])
        self.assertNotEqual(self.client.stat().st_ino, original_inode)
        self.assertEqual(original_read(self.client), raw)
        # Every retained field must still reject drift, even with equal bytes.
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        current = self.client.stat()
        values = {name: getattr(current, name) for name in stable}
        original_stat = Path.stat
        for field in stable:
            with self.subTest(field=field):
                before = SimpleNamespace(**values)
                after = SimpleNamespace(**dict(values, **{field: values[field] + 1}))
                observations = []
                def leaf_stat(path, *args, **kwargs):
                    if path == self.client and not args and not kwargs:
                        observations.append(path)
                        self.assertLessEqual(len(observations), 2)
                        return (before, after)[len(observations) - 1]
                    return original_stat(path, *args, **kwargs)
                with patch.object(Path, "stat", autospec=True, side_effect=leaf_stat):
                    with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                        GH._fingerprint(self.client, "owned-fixture-v1")
                self.assertEqual(observations, [self.client, self.client])
        self.assertEqual(self.calls(), [])

    def test_discovery_rejects_repeated_node_or_database_id(self):
        for key in ("id", "databaseId"):
            for paginated in (False, True):
                with self.subTest(identity=key, paginated=paginated):
                    first = self.raw_issue(1, body="Unrelated first issue.")
                    second = self.raw_issue(2, body="Unrelated second issue.")
                    second[key] = first[key]
                    replies = ([self.page([first], True, "next"), self.page([second])]
                               if paginated else [self.page([first, second])])
                    adapter = self.pure_adapter(replies)
                    with self.assertRaisesRegex(GH.Error, "^duplicate_issue_id$"):
                        adapter.find(self.request())
                    self.assertEqual(len(adapter.transport.calls), 2 if paginated else 1)
                    self.assertTrue(all(call[1] == "page" for call in adapter.transport.calls))
        self.assertEqual(self.calls(), [])

    def test_discovery_rejects_repeated_number_before_marker_filtering(self):
        for marker_at in (0, 1, None):
            for paginated in (False, True):
                with self.subTest(marker_at=marker_at, paginated=paginated):
                    pages, _ = self.number_conflict_pages(self.raw_issue(), marker_at, paginated)
                    adapter = self.pure_adapter(pages)
                    with self.assertRaisesRegex(GH.Error, "^duplicate_issue_id$"):
                        adapter.find(self.request())
                    self.assertEqual([call[1] for call in adapter.transport.calls],
                                     ["page"] * (2 if paginated else 1))
        self.assertEqual(self.calls(), [])

    def test_number_conflict_preflight_has_no_intent_or_create(self):
        for marker_at in (0, 1, None):
            for paginated in (False, True):
                with self.subTest(marker_at=marker_at, paginated=paginated):
                    adapter = self.adapter()
                    store = self.root / ("preflight-" + str(marker_at) + "-" + str(paginated))
                    prepared = CORE.prepare(store, CASES["proposals"], "synthetic prepare", binding=adapter.identity)
                    plan = CORE.read_json(store / "plan.json", CORE.MAX_PLAN)
                    receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                    request = CORE.request_for(plan, receipt, plan["operations"][0])
                    exact = self.raw_issue()
                    exact.update(title=request["title"], body=request["body"])
                    pages, _ = self.number_conflict_pages(exact, marker_at, paginated)
                    context = {"viewer": FIXTURE["context"]["viewer"],
                               "node": dict(FIXTURE["context"]["repository"], __typename="Repository")}
                    adapter.transport = QueueTransport([context, *pages])
                    with self.assertRaisesRegex(CORE.Stop, "^preflight_paused$"):
                        CORE.apply(store, prepared["plan_digest"], "synthetic filing", adapter)
                    receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                    self.assertTrue(all(row["state"] == "planned" and row["attempt"] == 0
                                        and row["artifact"] is None for row in receipt["operations"]))
                    self.assertEqual([event["event"] for event in receipt["history"]], ["preflight-paused"])
                    self.assertEqual([call[1] for call in adapter.transport.calls],
                                     ["context"] + ["page"] * (2 if paginated else 1))
        self.assertFalse(any(call["request"]["query"] == GH.OPERATIONS["create"] for call in self.calls()))

    def test_number_conflict_reconciliation_never_verifies_or_recreates(self):
        for marker_at in (0, 1, None):
            for paginated in (False, True):
                with self.subTest(marker_at=marker_at, paginated=paginated):
                    # Each case owns its original lost-create state; no fabricated receipt.
                    (self.root / "state.json").write_text("[]")
                    self.mode("lost-create")
                    adapter = self.adapter()
                    store = self.root / ("reconcile-" + str(marker_at) + "-" + str(paginated))
                    prepared = CORE.prepare(store, CASES["proposals"], "synthetic prepare", binding=adapter.identity)
                    with self.assertRaisesRegex(CORE.Stop, "^write_or_verification_uncertain$"):
                        CORE.apply(store, prepared["plan_digest"], "synthetic filing", adapter)
                    before_creates = sum(call["request"]["query"] == GH.OPERATIONS["create"] for call in self.calls())
                    original = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                    self.assertEqual((original["operations"][0]["attempt"], original["operations"][0]["artifact"]), (1, None))
                    exact = json.loads((self.root / "state.json").read_bytes())[0]
                    pages, selected = self.number_conflict_pages(exact, marker_at, paginated)
                    context = {"viewer": FIXTURE["context"]["viewer"],
                               "node": dict(FIXTURE["context"]["repository"], __typename="Repository")}
                    direct = {"node": {"__typename": "Repository", "id": "R_fixture", "issue": selected}}
                    # A matching direct read is available: rejecting the contradictory
                    # traversal, not a missing queued reply, must stop reconciliation.
                    adapter.transport = QueueTransport([context, *pages, direct])
                    with self.assertRaisesRegex(CORE.Stop, "^reconciliation_unresolved$"):
                        CORE.reconcile(store, prepared["plan_digest"], "synthetic reconciliation", adapter)
                    receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                    row = receipt["operations"][0]
                    self.assertEqual((row["state"], row["attempt"], row["artifact"]), ("uncertain", 1, None))
                    self.assertNotIn("reconciled", [event["event"] for event in receipt["history"]])
                    self.assertEqual([call[1] for call in adapter.transport.calls],
                                     ["context"] + ["page"] * (2 if paginated else 1))
                    self.assertEqual(adapter.transport.replies, [direct])
                    before = (store / "receipt.json").read_bytes()
                    with self.assertRaisesRegex(CORE.Stop, "^reconciliation_required$"):
                        CORE.apply(store, prepared["plan_digest"], "synthetic filing", adapter)
                    self.assertEqual((store / "receipt.json").read_bytes(), before)
                    self.assertEqual(sum(call["request"]["query"] == GH.OPERATIONS["create"] for call in self.calls()),
                                     before_creates)

    def test_call_and_elapsed_budget_stop_before_launch(self):
        transport = self.transport()
        for name, value in (("MAX_CALLS", 0), ("INVOCATION_SECONDS", 0), ("TOTAL_BYTES", 0)):
            with patch.object(GH, name, value), patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected launch")):
                with self.assertRaisesRegex(GH.Error, "^invocation_limit$"):
                    transport.call(self.target["host"], "context", {"id": "R_fixture"})

    def test_stdout_and_stderr_overflow_are_bounded_sanitized_and_reaped(self):
        for mode in ("stdout-overflow", "stderr-overflow"):
            self.mode(mode); transport = self.transport()
            with patch.object(GH, "STDOUT_BYTES", 512), patch.object(GH, "STDERR_BYTES", 512):
                with self.assertRaisesRegex(GH.Error, "^client_output_limit$"):
                    transport.call(self.target["host"], "context", {"id": "R_fixture"})
            self.assert_reaped(transport)

    def test_timeout_term_and_kill_are_owned_and_reaped(self):
        for mode in ("sleep", "ignore-term"):
            self.mode(mode); transport = self.transport()
            with patch.object(GH, "CALL_SECONDS", 0.5), patch.object(GH, "TERM_SECONDS", 0.1):
                with self.assertRaisesRegex(GH.Error, "^client_timeout$"):
                    transport.call(self.target["host"], "context", {"id": "R_fixture"})
            self.assert_reaped(transport)
            self.assertTrue(transport.last["term"])
            if mode == "ignore-term": self.assertTrue(transport.last["kill"])

    def test_interruption_reaps_owned_child_without_retry(self):
        self.mode("sleep"); transport = self.transport()
        selector = GH.selectors.DefaultSelector()
        with patch.object(GH.selectors, "DefaultSelector", return_value=selector), patch.object(selector, "select", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                transport.call(self.target["host"], "context", {"id": "R_fixture"})
        self.assert_reaped(transport)
        self.assertEqual(transport.calls, 1)

    def test_failed_and_partial_responses_never_expose_stderr(self):
        for mode in ("failed", "partial"):
            self.mode(mode); transport = self.transport()
            with self.assertRaises(GH.Error) as caught:
                transport.call(self.target["host"], "context", {"id": "R_fixture"})
            self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
            self.assert_reaped(transport)


if __name__ == "__main__":
    unittest.main()
