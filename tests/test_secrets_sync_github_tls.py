"""Real TLS verification against an owned loopback fixture, never GitHub.

Only connection routing is substituted: URL/SNI/hostname remain fixture.invalid,
while the socket is confined to this test's ephemeral loopback listener. The
production SSL and HTTP validation code runs unchanged with synthetic tokens.
"""
import http.server
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync import github_validate
from secrets_sync.errors import SyncError


class API(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append((self.path, self.headers.get("Authorization") == "token synthetic-tls"))
        mode = self.server.mode
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://fixture.invalid/forbidden")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("X-OAuth-Scopes", "repo, read:org, gist")
        body = b'{"login":"fixture"}' if mode == "match" else b'{"login":"other"}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        self.failed = True  # no source-bearing traceback or request logging


class TLSWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="envy-gh-tls-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name).resolve()
        for name in ("trusted", "untrusted"):
            config = cls.root / (name + ".cnf")
            config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                              "[dn]\nCN=fixture.invalid\n[ext]\nsubjectAltName=DNS:fixture.invalid\n"
                              "basicConstraints=critical,CA:TRUE\n")
            config.chmod(0o600)
            result = subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048",
                                     "-nodes", "-sha256", "-days", "1", "-config", str(config),
                                     "-keyout", str(cls.root / (name + ".key")),
                                     "-out", str(cls.root / (name + ".pem"))],
                                    cwd=cls.root, env={"HOME": str(cls.root), "PATH": "/usr/bin:/bin"},
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=20, umask=0o077)
            if result.returncode:
                raise AssertionError("owned TLS certificate fixture creation failed; output suppressed")
        cls.server = Server(("127.0.0.1", 0), API)
        cls.addClassCleanup(cls.server.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.root / "trusted.pem", cls.root / "trusted.key")
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)

    def setUp(self):
        self.server.calls = []
        self.server.failed = False
        self.server.mode = "match"

    def validate(self, *, host="fixture.invalid", ca="trusted"):
        connect = socket.create_connection
        def route(address, *args, **kwargs):
            if address != (host, 443):
                raise AssertionError("unexpected TLS fixture destination refused")
            return connect(self.server.server_address, *args, **kwargs)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(socket, "create_connection", side_effect=route), \
                patch.object(github_validate.urllib.request, "getproxies", return_value={}):
            github_validate.validate(host, "fixture", "synthetic-tls", str(self.root / (ca + ".pem")))

    def test_trusted_certificate_and_hostname_succeed(self):
        self.validate()
        self.assertEqual(self.server.calls, [("/api/v3/user", True)])
        self.assertFalse(self.server.failed)

    def test_untrusted_certificate_refused_before_authorization(self):
        with self.assertRaises(urllib.error.URLError) as caught:
            self.validate(ca="untrusted")
        self.assertTrue(isinstance(caught.exception.reason, ssl.SSLCertVerificationError))
        self.assertEqual(self.server.calls, [])

    def test_wrong_hostname_refused_before_authorization(self):
        with self.assertRaises(urllib.error.URLError) as caught:
            self.validate(host="other.invalid")
        self.assertTrue(isinstance(caught.exception.reason, ssl.SSLCertVerificationError))
        self.assertEqual(self.server.calls, [])

    def test_redirect_not_followed_even_on_same_host(self):
        self.server.mode = "redirect"
        with self.assertRaises(SyncError):
            self.validate()
        self.assertEqual(self.server.calls, [("/api/v3/user", True)])

    def test_valid_tls_but_wrong_account_refused(self):
        self.server.mode = "mismatch"
        with self.assertRaises(SyncError):
            self.validate()
        self.assertEqual(self.server.calls, [("/api/v3/user", True)])


if __name__ == "__main__":
    unittest.main()
