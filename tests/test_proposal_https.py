"""Owned TLS/CONNECT servers, generated test-only key, fake credential executable.

No real gh or provider is used. The proxy only tunnels to this fixture's TLS
listener, never resolves a request destination, and never relays elsewhere.
Every worker inherits an explicitly synthetic environment, uses the actual
candidate HTTPS implementation, and is bounded/reaped by the actual runner.
The fixture launcher bypasses only source admission, not HTTP/TLS behavior.
"""

import contextlib
import copy
import http.server
import io
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_proposal_github import GH, CORE, CASES, FIXTURE, SCRIPT
from test_proposal_publication import GithubFakeAdapter


CREDENTIAL = "owned_synthetic_token"


class OwnedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Expected disconnect/timeout cases must not emit source or headers.
        self.errors += 1


class TLSHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        fixture = self.server.fixture
        fixture.requests.append({"path": self.path, "request": request,
                                 "auth_ok": self.headers.get("Authorization") == "Bearer " + CREDENTIAL})
        names = [name for name, query in GH.OPERATIONS.items() if request["query"] == query]
        if len(names) != 1:
            return
        name = names[0]
        repo = FIXTURE["context"]["repository"]
        if name == "create":
            fixture.creates += 1
            variables = request["variables"]
            fixture.artifacts.append({"__typename": "Issue", "id": "I_owned_" + str(fixture.creates),
                                      "databaseId": 3000 + fixture.creates, "number": fixture.creates,
                                      "title": variables["title"], "body": variables["body"],
                                      "url": "https://forge.example.invalid/example/proposal-demo/issues/" + str(fixture.creates),
                                      "repository": {key: repo[key] for key in ("id", "databaseId", "name", "owner")}})
        mode = fixture.mode
        if mode == "lost-create" and name == "create":
            self.close_connection = True
            return
        if mode == "stall":
            fixture.stop.wait(5)
            self.close_connection = True
            return
        if isinstance(mode, int):
            self.send_response(mode)
            self.send_header("Location", fixture.location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if name == "named":
            data = FIXTURE["context"]
        elif name == "context":
            data = {"viewer": FIXTURE["context"]["viewer"], "node": dict(repo, __typename="Repository")}
        elif name == "page":
            data = {"node": {"__typename": "Repository", "id": repo["id"],
                             "issues": {"nodes": fixture.artifacts, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        elif name == "read":
            matches = [item for item in fixture.artifacts if item["number"] == request["variables"]["number"]]
            data = {"node": {"__typename": "Repository", "id": repo["id"], "issue": matches[0] if len(matches) == 1 else None}}
        else:
            data = {"createIssue": {"issue": fixture.artifacts[-1]}}
        payload = b"not-json" if mode == "malformed" else GH.canonical({"data": data})
        if mode == "overflow":
            payload = b"x" * (GH.STDOUT_BYTES + 1)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload) + (10 if mode == "truncated" else 0)))
        if mode == "duplicate-length":
            self.send_header("Content-Length", str(len(payload)))
        if mode == "compressed":
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(payload)
        if mode == "truncated":
            self.close_connection = True


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        fixture = self.server.fixture
        fixture.connects.append({"target": self.path,
                                 "auth_present": "Authorization" in self.headers or "Proxy-Authorization" in self.headers})
        if fixture.proxy_status != 200:
            self.send_response(fixture.proxy_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path not in {"api.github.com:443", "forge.example.invalid:443", "wrong.example.invalid:443"}:
            self.send_error(403)
            return
        # Destination is always this owned listener, never the CONNECT text.
        with socket.create_connection(fixture.tls.server_address, timeout=3) as upstream:
            self.send_response(200)
            self.end_headers()
            pair = (self.connection, upstream)
            while not fixture.stop.is_set():
                readable, _, _ = select.select(pair, (), (), 0.1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        self.close_connection = True
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)


class HTTPSFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="proposal-https-owned-")
        cls.root = Path(cls.temporary.name).resolve()
        cls.root.chmod(0o700)
        cls.addClassCleanup(cls.temporary.cleanup)
        openssl = shutil.which("openssl")
        if openssl is None:
            raise unittest.SkipTest("owned TLS certificate generator unavailable")
        config = cls.root / "cert.cnf"
        config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                          "[dn]\nCN=api.github.com\n[ext]\n"
                          "subjectAltName=DNS:api.github.com,DNS:forge.example.invalid\n"
                          "basicConstraints=critical,CA:TRUE\n"
                          "keyUsage=critical,keyCertSign,digitalSignature,keyEncipherment\n")
        cls.cert, cls.key = cls.root / "cert.pem", cls.root / "key.pem"
        # No external account/key input. The generated private key is test-only,
        # inside this owned root, never printed or retained after the suite.
        result = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                                 "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
                                 "-config", str(config)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                                env={"PATH": "/usr/bin:/bin"})
        if result.returncode:
            raise RuntimeError("owned certificate generation failed")
        cls.key.chmod(0o600)
        cls.credential_client = cls.root / "fake-gh"
        cls.credential_script = cls.root / "fake-credential.py"
        cls.credential_client.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -S -B "
                                        + shlex.quote(str(cls.credential_script)) + ' "$@"\n')
        cls.credential_script.write_text("import json, os, sys, time\n"
                                        "from pathlib import Path\n"
                                        "root = Path(__file__).parent\n"
                                        "assert sys.argv[1:3] == ['auth', 'token']\n"
                                        "calls = json.loads((root / 'lookups.json').read_text())\n"
                                        "calls.append(sys.argv[1:])\n"
                                        "(root / 'lookups.json').write_text(json.dumps(calls))\n"
                                        "mode = os.environ.get('OWNED_CREDENTIAL_MODE', 'ok')\n"
                                        "if mode == 'stall': time.sleep(5)\n"
                                        "if mode == 'error':\n"
                                        " sys.stderr.write('owned synthetic error'); sys.exit(7)\n"
                                        "if mode == 'overflow': sys.stdout.write('x' * 8192)\n"
                                        "elif mode == 'multiline': sys.stdout.write('invalid\\noutput\\n')\n"
                                        "else: sys.stdout.write('" + CREDENTIAL + "\\n')\n")
        cls.credential_client.chmod(0o700)
        cls.worker = cls.root / "owned-worker.py"
        cls.worker.write_text("import importlib.util, sys\n"
                              "spec = importlib.util.spec_from_file_location('owned_https', " + repr(str(SCRIPT)) + ")\n"
                              "module = importlib.util.module_from_spec(spec)\n"
                              "spec.loader.exec_module(module)\n"
                              "sys.exit(module._https_worker())\n")

    def setUp(self):
        self.mode = "ok"
        self.location = "http://api.github.com/credential-must-not-follow"
        self.proxy_status = 200
        self.requests, self.connects = [], []
        self.artifacts = []
        self.creates = 0
        self.stop = threading.Event()
        (self.root / "lookups.json").write_text("[]")
        self.tls = OwnedServer(("127.0.0.1", 0), TLSHandler)
        self.tls.fixture = self
        self.tls.errors = 0
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.tls.socket = context.wrap_socket(self.tls.socket, server_side=True)
        self.proxy = OwnedServer(("127.0.0.1", 0), ProxyHandler)
        self.proxy.fixture = self
        self.proxy.errors = 0
        self.threads = []
        for server in (self.tls, self.proxy):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.addCleanup(self.close)
        # Explicit owned test context, not a filtered production invocation.
        self.environment = {"PATH": "/usr/bin:/bin", "SSL_CERT_FILE": str(self.cert),
                            "GH_CONFIG_DIR": str(self.root / "gh-config"),
                            "SSL_CERT_DIR": str(self.root / "empty-trust-directory"),
                            "HTTPS_PROXY": "http://127.0.0.1:" + str(self.proxy.server_port)}
        (self.root / "empty-trust-directory").mkdir(exist_ok=True)
        (self.root / "gh-config").mkdir(exist_ok=True)
        (self.root / "gh-config" / "config.yml").write_text("version: 1\nhttp_unix_socket:\n")

    def close(self):
        self.stop.set()
        for server in (self.proxy, self.tls):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    @contextlib.contextmanager
    def transport(self):
        with patch.dict(os.environ, self.environment, clear=True), \
             patch.object(GH, "_qualify_https_runtime"), patch.object(GH, "_gh_build"), \
             patch.object(GH, "_resolve_https_client", return_value=(self.credential_client, GH.GH_VERSION)), \
             patch.object(GH._HttpsTransport, "_worker_argv", return_value=[sys.executable, "-I", "-S", "-B", str(self.worker)]):
            yield GH._HttpsTransport()

    def call(self, transport, host="github.com", name="named", variables=None):
        return transport.call(host, name, variables or {"owner": "example", "name": "proposal-demo"})

    def test_actual_tls_connect_and_fixed_operation_preserve_secret_boundary(self):
        with self.transport() as transport:
            before = dict(os.environ)
            with patch.object(GH.subprocess, "Popen", wraps=subprocess.Popen) as launched:
                self.assertEqual(self.call(transport), FIXTURE["context"])
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(launched.call_args_list), 2)
        for call in launched.call_args_list:
            self.assertNotIn(CREDENTIAL, repr(call))
            self.assertNotIn("env", call.kwargs)
        self.assertEqual(json.loads((self.root / "lookups.json").read_text()),
                         [["auth", "token", "--hostname", "github.com"]])
        self.assertEqual(self.connects, [{"target": "api.github.com:443", "auth_present": False}])
        self.assertTrue(self.requests[0]["auth_ok"])
        self.assertEqual(self.requests[0]["path"], "/graphql")
        self.assertEqual(self.requests[0]["request"], {"query": GH.OPERATIONS["named"],
                                                     "variables": {"owner": "example", "name": "proposal-demo"}})
        self.assertTrue(transport.last["reaped"])
        with self.assertRaises(ChildProcessError):
            os.waitpid(transport.last["pid"], os.WNOHANG)

    def test_enterprise_endpoint_is_fixed_https_api_graphql(self):
        with self.transport() as transport:
            self.call(transport, host="forge.example.invalid")
        self.assertEqual(self.requests[0]["path"], "/api/graphql")
        self.assertEqual(self.connects[0]["target"], "forge.example.invalid:443")

    def test_redirects_and_http_errors_never_replay_a_possible_create(self):
        # Split synthetic credential-URL literals, as in the controller fixtures.
        for status in (300, 301, 302, 303, 304, 305, 307, 308, 399, 401, 403, 429, 500):
            for location in ("http://api.github.com/downgrade", "https://other.example.invalid/next",
                             "https" "://user:password@api.github.com/next", "/same-origin"):
                with self.subTest(status=status, location_kind=location.split(":")[0]):
                    self.mode, self.location = status, location
                    before = len(self.requests), len(self.connects), self.creates
                    with self.transport() as transport:
                        with self.assertRaisesRegex(GH.Error, "^client_failed$"):
                            self.call(transport, name="create", variables={"repositoryId": "R_fixture", "title": "Owned", "body": "Owned"})
                    self.assertEqual((len(self.requests), len(self.connects), self.creates), tuple(n + 1 for n in before))

    def test_certificate_and_hostname_verification_prevent_http_submission(self):
        for host, trusted in (("wrong.example.invalid", True), ("github.com", False)):
            with self.subTest(host=host, trusted=trusted):
                if not trusted:
                    self.environment.pop("SSL_CERT_FILE")
                with self.transport() as transport:
                    with self.assertRaisesRegex(GH.Error, "^client_failed$"):
                        self.call(transport, host=host)
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.connects), 2)

    def test_proxy_failure_has_no_direct_fallback_or_authorization(self):
        for status in (301, 403, 407, 500):
            self.proxy_status = status
            with self.transport() as transport:
                with self.assertRaisesRegex(GH.Error, "^client_failed$"):
                    self.call(transport)
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.connects), 4)
        self.assertFalse(any(item["auth_present"] for item in self.connects))

    def test_lost_create_preserves_state_machine_intent_and_no_second_create(self):
        self.mode = "lost-create"
        with self.transport() as transport, patch.object(GH, "_Transport", return_value=transport):
            adapter = GH.GitHubIssues(CASES["proposals"]["destination"])
            with tempfile.TemporaryDirectory(prefix="proposal-https-store-") as owned:
                store = Path(owned).resolve() / "store"
                prepared = CORE.prepare(store, CASES["proposals"], "owned prepare", binding=adapter.identity)
                with self.assertRaisesRegex(CORE.Stop, "^write_or_verification_uncertain$"):
                    CORE.apply(store, prepared["plan_digest"], "owned filing", adapter)
                receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                self.assertEqual(receipt["operations"][0]["state"], "attempting")
                self.assertEqual(receipt["operations"][0]["attempt"], 1)
                with self.assertRaisesRegex(CORE.Stop, "^reconciliation_required$"):
                    CORE.apply(store, prepared["plan_digest"], "owned filing", adapter)
                self.assertEqual(self.creates, 1)
                self.mode = "ok"
                CORE.reconcile(store, prepared["plan_digest"], "owned reconciliation", adapter)
                receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                self.assertEqual(receipt["operations"][0]["state"], "verified")
                self.assertEqual(self.creates, 1)
                CORE.apply(store, prepared["plan_digest"], "owned filing", adapter)
                receipt = CORE.read_json(store / "receipt.json", CORE.MAX_RECEIPT)
                self.assertTrue(all(row["state"] == "verified" for row in receipt["operations"]))
                self.assertEqual(self.creates, len(receipt["operations"]))

    def test_credentials_fail_before_any_https_child_and_never_echo_output(self):
        for mode in ("error", "overflow", "multiline"):
            self.environment["OWNED_CREDENTIAL_MODE"] = mode
            with self.transport() as transport:
                with self.assertRaises(GH.Error) as error:
                    self.call(transport)
                self.assertNotIn(CREDENTIAL, str(error.exception))
                self.assertTrue(transport.last["reaped"])
        self.assertEqual(self.connects, [])

    def test_total_deadline_includes_credential_lookup_and_tls_response(self):
        for credential_stall in (True, False):
            self.environment["OWNED_CREDENTIAL_MODE"] = "stall" if credential_stall else "ok"
            self.mode = "stall"
            with self.transport() as transport, patch.object(GH, "CALL_SECONDS", 0.5), patch.object(GH, "TERM_SECONDS", 0.1):
                started = time.monotonic()
                with self.assertRaisesRegex(GH.Error, "^client_timeout$"):
                    self.call(transport)
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(transport.last["reaped"])
                self.assertTrue(transport.last["term"])

    def test_response_overflow_and_malformed_json_fail_closed(self):
        for mode in ("overflow", "malformed", "truncated", "duplicate-length", "compressed"):
            self.mode = mode
            with self.transport() as transport:
                with self.assertRaises(GH.Error):
                    self.call(transport)
                self.assertTrue(transport.last["reaped"])

    def test_one_deadline_is_shared_without_credential_refresh_or_retry(self):
        with self.transport() as transport:
            with patch.object(transport, "_run", side_effect=[(CREDENTIAL + "\n").encode(), b'{"data":{}}']) as run:
                self.assertEqual(self.call(transport), {})
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[3], run.call_args_list[1].args[3])

    def test_owned_wrapper_executes_once_with_fixed_arguments_and_child_only_control(self):
        launcher = self.root / "owned-wrapper"
        observed = self.root / "wrapper-context.json"
        wrapper_script = self.root / "wrapper-context.py"
        wrapper_script.write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            + "Path(" + repr(str(observed)) + ").write_text(json.dumps({"
            "'args': sys.argv[1:], 'cwd': os.getcwd(), 'telemetry': os.environ.get('GH_TELEMETRY'),"
            "'config': os.environ.get('GH_CONFIG_DIR'), 'proxy': os.environ.get('HTTPS_PROXY'),"
            "'trust': os.environ.get('SSL_CERT_FILE')}))\n"
            + "os.execv(" + repr(str(self.credential_client)) + ", ["
            + repr(str(self.credential_client)) + "] + sys.argv[1:])\n")
        launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -S -B "
                            + shlex.quote(str(wrapper_script)) + ' "$@"\n')
        launcher.chmod(0o700)
        native = self.credential_client

        class OwnedTransport(GH._HttpsTransport):
            def _select_credential_client(self):
                return GH._CredentialCommand(native, GH.GH_VERSION, (wrapper_script,), launcher=launcher)

        with self.transport(), patch.dict(os.environ, {"GH_TELEMETRY": "log"}):
            before = dict(os.environ)
            transport = OwnedTransport()
            self.call(transport)
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(json.loads(observed.read_text()), {
                "args": ["auth", "token", "--hostname", "github.com"], "cwd": os.getcwd(),
                "telemetry": "disabled", "config": before["GH_CONFIG_DIR"],
                "proxy": before["HTTPS_PROXY"], "trust": before["SSL_CERT_FILE"]})
            self.assertEqual(len(json.loads((self.root / "lookups.json").read_text())), 1)
            self.assertEqual(len(self.requests), 1)
            launcher.write_text("#!/bin/sh\nexit 0\n")
            with patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
                with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                    self.call(transport)

    def test_unsupported_security_context_stops_before_credential_child(self):
        self.environment["SSLKEYLOGFILE"] = "untrusted-path"
        with self.transport() as transport, patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
            with self.assertRaisesRegex(GH.Error, "^unsupported_security_context$"):
                self.call(transport)
        self.assertEqual(self.connects, [])

    def test_changed_client_and_exhausted_budget_stop_before_credential_read(self):
        with self.transport() as transport:
            transport.identity["sha256"] = "0" * 64
            with patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
                with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                    self.call(transport)
        for key in ("MAX_CALLS", "TOTAL_BYTES", "INVOCATION_SECONDS"):
            with self.transport() as transport, patch.object(GH, key, 0), \
                 patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
                with self.assertRaisesRegex(GH.Error, "^invocation_limit$"):
                    self.call(transport)

    def test_expected_runtime_client_and_policy_are_compared_before_lookup(self):
        target = CASES["proposals"]["destination"]
        with self.transport() as transport, patch.object(GH, "_HttpsTransport", return_value=transport):
            expected = GH.HttpsGitHubIssues(target).identity
            before = len(self.connects), len(self.requests)
            lookups = (self.root / "lookups.json").read_bytes()
            for path in (("client", "sha256"), ("https", "interpreter", "sha256"),
                         ("https", "sources", "ssl", "sha256"), ("https", "policy", "proxy"),
                         ("https", "selection", "kind"),
                         ("source_sha256",)):
                changed = copy.deepcopy(expected)
                current = changed
                for key in path[:-1]:
                    current = current[key]
                current[path[-1]] = "changed"
                with self.assertRaisesRegex(GH.Error, "^provider_changed$"):
                    GH.HttpsGitHubIssues(target, expected=changed)
            self.assertEqual((len(self.connects), len(self.requests)), before)
            self.assertEqual((self.root / "lookups.json").read_bytes(), lookups)

    def test_public_cli_profile_and_lost_reply_recovery_use_actual_tls_worker(self):
        def run(arguments):
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                status = CORE.main(arguments)
            return status, json.loads(output.getvalue() or error.getvalue())

        with tempfile.TemporaryDirectory(prefix="proposal-https-cli-") as owned, self.transport(), \
             patch.object(CORE, "_github_provider", return_value=GH):
            root = Path(owned).resolve()
            source, store = root / "input.json", root / "store"
            source.write_bytes(CORE.canonical(CASES["proposals"]))
            source.chmod(0o600)
            status, prepared = run(["github-plan", "--input", str(source), "--store", str(store),
                                    "--prepare-authority", "owned CLI preparation", "--confirm-host", "forge.example.invalid"])
            self.assertEqual(status, 0)
            self.assertTrue(prepared["provider_available"])
            plan = CORE.read_json(store / "plan.json", CORE.MAX_PLAN)
            self.assertEqual((plan["version"], plan["provider"]["version"], plan["provider"]["profile"]),
                             (2, 2, GH.HTTPS_PROFILE))
            self.assertEqual(plan["provider"]["https"]["policy"], CORE.GITHUB_HTTPS_POLICY)
            common = ["--store", str(store), "--plan-digest", prepared["plan_digest"]]
            filing = ["github-apply", *common, "--filing-authority", "owned CLI filing"]
            self.mode = "lost-create"
            self.assertEqual(run(filing), (1, {"error": "write_or_verification_uncertain"}))
            self.assertEqual(self.creates, 1)
            lookups = (self.root / "lookups.json").read_bytes()
            self.assertEqual(run(filing), (1, {"error": "reconciliation_required"}))
            self.assertEqual((self.root / "lookups.json").read_bytes(), lookups)
            self.mode = "ok"
            status, reconciled = run(["github-reconcile", *common, "--reconciliation-authority", "owned CLI reconciliation"])
            self.assertEqual(status, 0)
            self.assertEqual(reconciled["counts"]["verified"], 1)
            self.assertEqual(self.creates, 1)
            status, completed = run(filing)
            self.assertEqual(status, 0)
            self.assertTrue(completed["complete"])
            self.assertEqual(self.creates, 2)


class HTTPSPublicationTests(unittest.TestCase):
    """Pure provider fixtures for exact preflight/factory ordering, no sockets."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="proposal-https-preflight-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.store = self.root / "store"
        self.proposals = copy.deepcopy(CASES["proposals"])
        self.adapter = GithubFakeAdapter(self.proposals["destination"])
        self.adapter.identity.update(version=2, profile=GH.HTTPS_PROFILE)
        self.adapter.identity["https"] = {
            "interpreter": {"executable": "/owned/python", "resolved": "/owned/python", "sha256": "1" * 64, "version": "owned-fixture"},
            "sources": {name: {"path": "/owned/" + name, "sha256": "2" * 64}
                        for name in ("http_client", "ssl", "socket", "ssl_extension")},
            "tls_version": "owned-fixture", "policy": copy.deepcopy(CORE.GITHUB_HTTPS_POLICY)}
        self.adapter.identity["https"]["selection"] = {
            "kind": "fixed-code-v2", "entry": "OwnedFixture.select",
            "source": {"path": "/owned/selector.py", "sha256": "3" * 64}, "artifacts": [],
            "launcher": copy.deepcopy(self.adapter.identity["client"]), "cwd": "/owned",
            "control": "inherit"}
        self.factories = []

        def factory(target, expected=None):
            self.factories.append((copy.deepcopy(target), copy.deepcopy(expected)))
            self.assertEqual(target, self.proposals["destination"])
            if expected is not None:
                self.assertEqual(expected, self.adapter.identity)
            return self.adapter

        self.provider = SimpleNamespace(HttpsGitHubIssues=factory)
        patcher = patch.object(CORE, "_github_provider", return_value=self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self):
        result = CORE.github_plan(self.store, self.proposals, "owned preparation", self.proposals["destination"]["host"])
        self.expected = result["plan_digest"]
        self.factories.clear()
        return result

    def test_plan_rejects_invalid_input_authority_host_and_store_before_factory(self):
        changed = copy.deepcopy(self.proposals)
        changed["proposals"][0]["detail"] = "x" * CORE.GITHUB_BODY
        for proposals, authority, host in ((self.proposals, "", self.proposals["destination"]["host"]),
                                           (self.proposals, "owned", "other.invalid"),
                                           (changed, "owned", self.proposals["destination"]["host"])):
            with self.assertRaises(CORE.Stop):
                CORE.github_plan(self.store, proposals, authority, host)
            self.assertFalse(self.store.exists())
        self.store.mkdir(mode=0o700)
        with self.assertRaisesRegex(CORE.Stop, "^store_exists$"):
            CORE.github_plan(self.store, self.proposals, "owned", self.proposals["destination"]["host"])
        self.assertEqual(self.factories, [])
        self.assertEqual((self.adapter.reads, self.adapter.writes), (0, 0))

    def test_new_profile_is_digest_bound_without_relabeling_old_versions(self):
        self.prepare()
        plan = CORE.read_json(self.store / "plan.json", CORE.MAX_PLAN)
        self.assertEqual(plan["provider"]["https"], self.adapter.identity["https"])
        changed = copy.deepcopy(plan)
        changed["provider"]["https"]["sources"]["ssl"]["sha256"] = "3" * 64
        changed["digest"] = CORE.digest({key: value for key, value in changed.items() if key != "digest"})
        CORE.plan_check(changed)
        with self.assertRaisesRegex(CORE.Stop, "^approval_mismatch$"):
            CORE.authorize(CORE.initial_receipt(changed), changed, self.expected, "owned", "filing")
        legacy = GithubFakeAdapter(self.proposals["destination"])
        legacy_plan = CORE.make_github_plan(self.proposals, "owned", legacy.identity)
        self.assertEqual(legacy_plan["provider"]["version"], 1)
        self.assertNotIn("https", legacy_plan["provider"])
        v1 = CORE.make_plan(self.proposals, "owned")
        self.assertEqual((v1["version"], v1["provider"]), (1, CORE.PROVIDER))

    def test_apply_authority_source_and_receipt_checks_precede_factory(self):
        self.prepare()
        before = (self.store / "receipt.json").read_bytes()
        for function in (CORE.github_apply, CORE.github_reconcile):
            with self.assertRaisesRegex(CORE.Stop, "^approval_mismatch$"):
                function(self.store, "0" * 64, "owned")
            with self.assertRaisesRegex(CORE.Stop, "^invalid_text$"):
                function(self.store, self.expected, "")
            with patch.object(CORE, "github_source_hash", return_value="0" * 64):
                with self.assertRaisesRegex(CORE.Stop, "^provider_source_changed$"):
                    function(self.store, self.expected, "owned")
        self.assertEqual(self.factories, [])
        self.assertEqual((self.store / "receipt.json").read_bytes(), before)
        (self.store / "receipt.json").write_text("invalid owned fixture")
        with self.assertRaisesRegex(CORE.Stop, "^invalid_json$"):
            CORE.github_apply(self.store, self.expected, "owned")
        self.assertEqual(self.factories, [])

    def test_one_constructed_adapter_completes_batch_and_noop_has_no_backend(self):
        self.prepare()
        result = CORE.github_apply(self.store, self.expected, "owned filing")
        self.assertTrue(result["complete"])
        self.assertTrue(result["provider_available"])
        self.assertFalse(CORE.status(self.store)["provider_available"])
        self.assertEqual(len(self.factories), 1)
        self.assertEqual(self.adapter.writes, 2)
        self.factories.clear()
        before = self.adapter.reads
        CORE.github_apply(self.store, self.expected, "owned continuation")
        CORE.github_reconcile(self.store, self.expected, "owned reconciliation")
        self.assertEqual(self.factories, [])
        self.assertEqual(self.adapter.reads, before)

    def test_locked_revalidation_prevents_create_after_concurrent_owned_intent(self):
        self.prepare()
        original = CORE.apply
        self.adapter.fail = "timeout-after-write"

        def competing_invocation(store, expected, authority, adapter):
            # Create a genuine durable intent/lost result between the wrappers'
            # locks, using another authorized owned call, not fabricated state.
            with self.assertRaisesRegex(CORE.Stop, "^write_or_verification_uncertain$"):
                original(store, expected, "owned competing filing", self.adapter)
            return original(store, expected, authority, adapter)

        with patch.object(CORE, "apply", side_effect=competing_invocation):
            with self.assertRaisesRegex(CORE.Stop, "^reconciliation_required$"):
                CORE.github_apply(self.store, self.expected, "owned filing")
        self.assertEqual(self.factories, [])
        self.assertEqual(self.adapter.writes, 1)

    def test_public_actions_refuse_legacy_profile_without_constructing_https(self):
        legacy = GithubFakeAdapter(self.proposals["destination"])
        prepared = CORE.prepare(self.store, self.proposals, "owned", binding=legacy.identity)
        for function in (CORE.github_apply, CORE.github_reconcile):
            with self.assertRaisesRegex(CORE.Stop, "^provider_unavailable$"):
                function(self.store, prepared["plan_digest"], "owned")
        self.assertEqual(self.factories, [])

    def test_runtime_binding_rejects_unknown_fields_and_policy_weakening(self):
        for key, value in (("extra", "untrusted"), ("policy", dict(CORE.GITHUB_HTTPS_POLICY, retries="enabled"))):
            identity = copy.deepcopy(self.adapter.identity)
            identity["https"][key] = value
            with self.assertRaises(CORE.Stop):
                CORE.make_github_plan(self.proposals, "owned", identity)
        identity = copy.deepcopy(self.adapter.identity)
        identity["profile"] = [GH.HTTPS_PROFILE]
        with self.assertRaisesRegex(CORE.Stop, "^provider_unavailable$"):
            CORE.make_github_plan(self.proposals, "owned", identity)


class HTTPSPureTests(unittest.TestCase):
    def test_normal_resolver_worker_and_public_authority_gate_stay_closed(self):
        with patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")), \
             patch.object(GH.sys, "platform", "darwin"), \
             patch.object(CORE, "locked", side_effect=AssertionError("unexpected store access")):
            with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                GH._HttpsTransport()
            for action in (CORE.github_plan, CORE.github_apply, CORE.github_reconcile):
                with self.assertRaisesRegex(CORE.Stop, "^github_compatibility_unavailable$"):
                    action("untrusted path", "untrusted authority")
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), "--https-worker"],
                                   input=b"untrusted input", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=3, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (1, b"", b""))

    def test_endpoint_and_operation_inputs_cannot_select_a_url_or_query(self):
        for host in ("http://github.com", "github.com:443", "user@github.com", "github.com/path",
                     "github.com?query", "github.com#fragment", "-bad.example.invalid", "bad..example.invalid"):
            with self.assertRaises(GH.Error):
                GH._endpoint(host)
        self.assertEqual(set(GH.OPERATIONS), {"named", "context", "page", "read", "create"})
        with self.assertRaises(GH.Error):
            GH.operation("query", {})

    def test_proxy_subset_is_explicit_and_does_not_silently_change_route(self):
        for environment in ({"HTTPS_PROXY": "http://proxy.invalid:8080", "https_proxy": "http://other.invalid"},
                            {"ALL_PROXY": "socks5://proxy.invalid"},
                            {"HTTPS_PROXY": "https://proxy.invalid"},
                            {"HTTPS_PROXY": "http" "://user:password@proxy.invalid"},
                            {"HTTPS_PROXY": "http://proxy.invalid/path"},
                            {"NO_PROXY": "bad/invalid"}, {"NO_PROXY": "*github.com"},
                            {"SSLKEYLOGFILE": "untrusted"}, {"GH_DEBUG": "api"}, {"PYTHONHTTPSVERIFY": "0"}):
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(GH.Error, "^unsupported_security_context$"):
                    GH._security_context("api.github.com")
        cases = [("github.com", None), (".github.com", None), ("github.com:443", None),
                 ("*.github.com", None), ("github.com:0443", ("proxy.invalid", 8080)),
                 ("localhost,127.0.0.1,::1,10.0.0.0/8,[::1]:443", ("proxy.invalid", 8080)),
                 ("github.com:80", ("proxy.invalid", 8080)), ("other.invalid", ("proxy.invalid", 8080))]
        for bypass, expected in cases:
            with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:8080", "NO_PROXY": bypass}, clear=True):
                self.assertEqual(GH._proxy("api.github.com"), expected)
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:8080"}, clear=True):
            self.assertIsNone(GH._proxy("api.github.com"))

    def test_worker_rejects_extra_query_and_sanitizes_errors(self):
        raw = GH.canonical({"host": "github.com", "operation": "named", "variables": {},
                            "credential": CREDENTIAL, "query": "untrusted"})
        with patch.object(GH.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw))), \
             patch.object(GH, "_https_exchange", side_effect=AssertionError("unexpected exchange")):
            self.assertEqual(GH._https_worker(), 1)


class HTTPSAdmissionTests(unittest.TestCase):
    """Synthetic binary metadata and configuration; no installed gh runs."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="proposal-https-admission-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.config = self.root / "config.yml"
        self.config.write_text("version: 1\nhttp_unix_socket:\n")
        environment = patch.dict(os.environ, {"GH_CONFIG_DIR": str(self.root)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    @contextlib.contextmanager
    def qualified_runtime(self):
        # Client-selection tests model admitted runtime metadata; they do not
        # qualify the host interpreter. Keep the real admission checks active.
        sources = {name: GH.hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                   for name, module in {"http_client": http.client, "ssl": ssl, "socket": socket}.items()}
        with patch.object(GH.sys, "platform", "linux"), \
             patch.object(GH.sys, "version_info", (3, 12, 14)), \
             patch.object(GH.sys, "implementation", SimpleNamespace(name="cpython")), \
             patch.object(GH, "QUALIFIED_SOURCES", sources), \
             patch.object(GH.ssl, "OPENSSL_VERSION_INFO", (1, 1, 1, 0, 0)):
            yield

    def test_runtime_fixture_still_rejects_changed_admission_inputs(self):
        with self.qualified_runtime(), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected process")):
            GH._qualify_https_runtime()
            cases = [(GH.sys, "platform", "darwin"),
                     (GH.sys, "version_info", (3, 12, 15)),
                     (GH.sys, "implementation", SimpleNamespace(name="pypy")),
                     (GH.ssl, "OPENSSL_VERSION_INFO", (3, 0, 0, 0, 0))]
            for owner, name, value in cases:
                with self.subTest(field=name), patch.object(owner, name, value):
                    with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                        GH._qualify_https_runtime()
            for name in GH.QUALIFIED_SOURCES:
                with self.subTest(source=name), patch.dict(GH.QUALIFIED_SOURCES, {name: "0" * 64}):
                    with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                        GH._qualify_https_runtime()

    @staticmethod
    def binary(version=b"2.86.0", dependency=b"v2.13.0"):
        module = (b"path\tgithub.com/cli/cli/v2/cmd/gh\n"
                  b"build\tGOOS=linux\n"
                  b'build\t-ldflags="-s -w -X github.com/cli/cli/v2/internal/build.Version=' + version + b'"\n'
                  b"dep\tgithub.com/cli/go-gh/v2\t" + dependency + b"\th1:fixture\n"
                  b"dep\tgithub.com/cli/safeexec\tv1.0.1\th1:fixture\n"
                  b"dep\tgithub.com/zalando/go-keyring\tv0.2.6\th1:fixture\n"
                  b"dep\tgithub.com/spf13/cobra\tv1.10.2\th1:fixture\n")

        def item(value):
            length, prefix = len(value), bytearray()
            while length >= 128:
                prefix.append((length & 127) | 128)
                length >>= 7
            prefix.append(length)
            return bytes(prefix) + value

        return (b"\x7fELF\x02\x01" + b"\x00" * 10 + b"\xff Go buildinf:\x08\x02" + b"\x00" * 16
                + item(b"go1.25.5") + item(b"s" * 16 + module + b"e" * 16))

    def test_exact_source_metadata_is_admitted_without_executing_version(self):
        selected = self.root / "gh"
        selected.write_bytes(self.binary())
        selected.chmod(0o700)
        with self.qualified_runtime(), patch.dict(os.environ, {"PATH": str(self.root)}), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected execution")):
            self.assertEqual(GH._resolve_https_client(), (str(selected), "gh 2.86.0"))

    def test_launchers_versions_replacements_and_truncated_metadata_are_not_admitted(self):
        good = self.binary()
        for raw in (b"#!/bin/sh\nexit 1\n", self.binary(b"2.83.3"), self.binary(dependency=b"v2.12.0"),
                    good[:-8], good.replace(b"GOOS=linux", b"GOOS=other"),
                    good.replace(b"go1.25.5", b"go1.25.4"), good.replace(b"\x08\x02", b"\x08\x00")):
            with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                GH._gh_build(raw)

    def test_newer_token_client_metadata_requires_the_distinct_wrapped_profile(self):
        raw = (self.binary(b"2.100.0", dependency=b"v2.16.0")
               .replace(b"go1.25.5", b"go1.26.8").replace(b"v0.2.6", b"v0.2.8"))
        GH._gh_build(raw, GH.WRAPPED_GH_VERSION)
        with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
            GH._gh_build(raw)
        selected = self.root / "gh"
        selected.write_bytes(raw)
        selected.chmod(0o700)

        class Unwrapped(GH._HttpsTransport):
            def _select_credential_client(self):
                return selected, GH.WRAPPED_GH_VERSION, ()

        with self.qualified_runtime(), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
            with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                Unwrapped()

    def test_unsupported_os_or_runtime_refuses_before_client_or_configuration_read(self):
        with patch.object(GH, "_fingerprint", side_effect=AssertionError("unexpected client read")), \
             patch.object(GH, "_credential_context", side_effect=AssertionError("unexpected config read")):
            for platform in ("darwin", "win32"):
                with patch.object(GH.sys, "platform", platform):
                    with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                        GH._resolve_https_client()
            with patch.object(GH.sys, "platform", "linux"), patch.object(GH.sys, "version_info", (3, 11, 9)):
                with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                    GH._resolve_https_client()

    def test_supported_config_subset_preserves_bytes_and_never_reads_hosts_contents(self):
        raw = (b'# Owned fixture\nversion: "1" # current schema\neditor: code --wait\n'
               b"http_unix_socket: ''\naliases:\n    co: pr checkout\n    example: '!echo owned'\n")
        self.config.write_bytes(raw)
        hosts = self.root / "hosts.yml"
        hosts.write_text("owned configuration contents must not be parsed by the guard")
        original = Path.open

        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, "hosts.yml")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            GH._credential_context()
        self.assertEqual(self.config.read_bytes(), raw)

    def test_legacy_and_ambiguous_configurations_are_refused_without_repair(self):
        for raw in (b"git_protocol: https\n", b"version: 2\n", b"version: 1\nversion: 0\n",
                    b'"version": 1\n', b"version: &version 1\n", b"version: |\n  1\n",
                    b"version: 1\n---\nversion: 0\n", b"version: 1\nother:\n  version: 0\n",
                    b"version: 1\r\n", b"version: 1\nhosts:\n", b"version: 1\naliases: {co: pr checkout}\n"):
            self.config.write_bytes(raw)
            with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
                GH._credential_context()
            self.assertEqual(self.config.read_bytes(), raw)
        self.config.write_text("version: 1\nhttp_unix_socket: /owned/route.sock\n")
        with self.assertRaisesRegex(GH.Error, "^unsupported_security_context$"):
            GH._credential_context()

    def test_missing_general_config_is_safe_only_with_empty_or_missing_hosts(self):
        self.config.unlink()
        GH._credential_context()
        hosts = self.root / "hosts.yml"
        hosts.write_bytes(b"")
        GH._credential_context()
        hosts.write_text("forge.example.invalid:\n    user: owned\n")
        with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
            GH._credential_context()
        self.assertFalse(self.config.exists())

    def test_config_permissions_bounds_and_special_files_fail_closed(self):
        self.config.chmod(0o666)
        with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
            GH._credential_context()
        self.config.chmod(0o600)
        self.config.write_bytes(b"x" * (GH.CONFIG_BYTES + 1))
        with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
            GH._credential_context()
        self.config.unlink()
        os.mkfifo(self.config, 0o600)
        with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
            GH._credential_context()

    def test_config_precedence_does_not_fall_back_from_explicit_invalid_input(self):
        alternate = self.root / "xdg" / "gh"
        alternate.mkdir(parents=True)
        (alternate / "config.yml").write_text("version: 1\n")
        with patch.dict(os.environ, {"GH_CONFIG_DIR": "", "XDG_CONFIG_HOME": str(alternate.parent)}):
            GH._credential_context()
        with patch.dict(os.environ, {"GH_CONFIG_DIR": "relative", "XDG_CONFIG_HOME": str(alternate.parent)}):
            with self.assertRaisesRegex(GH.Error, "^unsupported_gh_config$"):
                GH._credential_context()

    def test_trust_defaults_stay_unchanged_and_explicit_bad_paths_are_refused(self):
        before = dict(os.environ)
        context = GH._tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(dict(os.environ), before)
        for name, value in (("SSL_CERT_FILE", ""), ("SSL_CERT_FILE", "relative.pem"),
                            ("SSL_CERT_FILE", str(self.root / "missing.pem")),
                            ("SSL_CERT_DIR", str(self.root) + ":")):
            with patch.dict(os.environ, {name: value}):
                with self.assertRaisesRegex(GH.Error, "^unsupported_security_context$"):
                    GH._security_context("api.github.com")

    def test_trusted_selector_binds_source_and_artifacts_before_lookup(self):
        native, artifact = self.root / "native", self.root / "selection-artifact"
        native.write_bytes(self.binary())
        artifact.write_text("#!/bin/sh\nexit 1\n")
        native.chmod(0o700)
        artifact.chmod(0o700)

        class OwnedTransport(GH._HttpsTransport):
            def _select_credential_client(self):
                return native, GH.GH_VERSION, (artifact,)

        with self.qualified_runtime(), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected process")):
            transport = OwnedTransport()
            selection = transport.https["selection"]
            self.assertEqual(selection["source"]["path"], str(Path(__file__).resolve()))
            self.assertEqual(selection["source"]["sha256"], GH.hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            self.assertEqual(selection["artifacts"][0]["resolved"], str(artifact))
            artifact.write_text("#!/bin/sh\nexit 2\n")
            with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                transport.call("github.com", "named", {"owner": "owned", "name": "owned"})

    def test_trusted_selector_cannot_waive_native_version_or_artifact_checks(self):
        native = self.root / "native"
        native.write_bytes(self.binary(b"2.83.3"))
        native.chmod(0o700)

        class OwnedTransport(GH._HttpsTransport):
            def _select_credential_client(self):
                return native, GH.GH_VERSION, ()

        with self.qualified_runtime(), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected process")):
            with self.assertRaisesRegex(GH.Error, "^github_compatibility_unavailable$"):
                OwnedTransport()
            native.write_bytes(self.binary())
            native.chmod(0o777)
            with self.assertRaisesRegex(GH.Error, "^invalid_client_identity$"):
                OwnedTransport()

    def test_fixed_wrapper_identity_and_nonexecuting_artifact_are_bound(self):
        native, launcher, settings = (self.root / name for name in ("native", "launcher", "settings"))
        native.write_bytes(self.binary())
        native.chmod(0o700)
        launcher.write_text("#!/bin/sh\nexit 9\n")
        launcher.chmod(0o700)
        settings.write_text("owned-static-input\n")
        settings.chmod(0o600)

        class OwnedTransport(GH._HttpsTransport):
            def _select_credential_client(self):
                return GH._CredentialCommand(native, GH.GH_VERSION, (settings,), launcher=launcher)

        with self.qualified_runtime(), \
             patch.object(GH.subprocess, "Popen", side_effect=AssertionError("unexpected process")):
            transport = OwnedTransport()
            self.assertEqual(transport.identity["resolved"], str(native))
            self.assertEqual(transport.selection["launcher"]["executable"], str(launcher))
            self.assertEqual(transport.selection["control"], "telemetry-disabled")
            settings.write_text("changed\n")
            with self.assertRaisesRegex(GH.Error, "^client_changed$"):
                transport.call("github.com", "named", {"owner": "owned", "name": "owned"})

    def test_wrapper_cannot_select_arbitrary_arguments_or_environment(self):
        with self.assertRaises(TypeError):
            GH._CredentialCommand("/owned/gh", GH.GH_VERSION, argv=["untrusted"])
        with self.assertRaises(TypeError):
            GH._CredentialCommand("/owned/gh", GH.GH_VERSION, env={"GH_TOKEN": "synthetic"})

    def test_worker_admission_is_runtime_only_not_client_selection(self):
        with patch.object(GH, "_qualify_https_runtime") as runtime, \
             patch.object(GH, "_resolve_https_client", side_effect=AssertionError("unexpected selection")), \
             patch.object(GH, "_credential_context", side_effect=AssertionError("unexpected config read")), \
             patch.object(GH, "_https_worker", return_value=0) as worker:
            self.assertEqual(GH._worker_main(["--https-worker"]), 0)
            runtime.assert_called_once_with()
            worker.assert_called_once_with()
            self.assertEqual(GH._worker_main(["--https-worker", "untrusted"]), 1)
            runtime.assert_called_once_with()
            worker.assert_called_once_with()
        with patch.object(GH, "_qualify_https_runtime", side_effect=GH.Error("github_compatibility_unavailable")), \
             patch.object(GH, "_https_worker", side_effect=AssertionError("unexpected worker")):
            self.assertEqual(GH._worker_main(["--https-worker"]), 1)


if __name__ == "__main__":
    unittest.main()
