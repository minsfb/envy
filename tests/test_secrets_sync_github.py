"""Backend tests with owned fixtures; no native gh, Keychain or external API."""
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync import github, github_validate
from secrets_sync.errors import Cancelled, CommittedError, SyncError
from secrets_sync.github_config import Executable, parse_github
from secrets_sync.github_metadata import Metadata
from secrets_sync.github_process import Result, run
from secrets_sync.inventory import Ref, TOKEN_OVERRIDES

REF = Ref("github", host="fixture.invalid", account="fixture")
OTHER = Ref("github", host="fixture.invalid", account="other")


class MetadataTest(unittest.TestCase):
    def test_account_protocol_is_accepted_without_inline_token(self):
        for protocol in ("https", "ssh", '"https"'):
            raw = ("fixture.invalid:\n    users:\n        fixture:\n"
                   f"            git_protocol: {protocol}\n    user: fixture\n").encode()
            metadata = Metadata.parse(b"version: 1\n", raw)
            self.assertEqual(metadata.users(REF.host), {REF.account})
            self.assertEqual(metadata.active(REF.host), REF.account)

    def test_account_protocol_rejects_invalid_or_ambiguous_values(self):
        for value in ("", "{}", "null", "ftp", "https://fixture.invalid"):
            raw = ("fixture.invalid:\n    users:\n        fixture:\n"
                   f"            git_protocol: {value}\n    user: fixture\n").encode()
            with self.subTest(value=value), self.assertRaises(SyncError):
                Metadata.parse(b"version: 1\n", raw)

    def test_unknown_account_fields_are_still_refused(self):
        raw = (b"fixture.invalid:\n    users:\n        fixture:\n"
               b"            api_host: elsewhere.invalid\n    user: fixture\n")
        with self.assertRaises(SyncError):
            Metadata.parse(b"version: 1\n", raw)

    def test_selected_account_protocol_survives_token_replacement(self):
        raw = (b"fixture.invalid:\n    users:\n        fixture:\n"
               b"            git_protocol: ssh\n            oauth_token: synthetic-before\n"
               b"    user: fixture\n")
        before = Metadata.parse(b"version: 1\n", raw)
        updated = raw.replace(b"synthetic-before", b"synthetic-after")
        after = Metadata.parse(b"version: 1\n", updated)
        self.assertTrue(before.preserved_except(after, REF.host, REF.account))
        for changed in (updated.replace(b"git_protocol: ssh", b"git_protocol: https"),
                        updated.replace(b"            git_protocol: ssh\n", b"")):
            self.assertFalse(before.preserved_except(Metadata.parse(b"version: 1\n", changed), REF.host, REF.account))

    def test_native_empty_user_and_quoted_keys(self):
        value = Metadata.parse(b'version: "1"\n',
                               b'"fixture.invalid":\n    users:\n        fixture:\n'
                               b'        other: {}\n    user: fixture\n    git_protocol: https\n')
        self.assertEqual(value.users(REF.host), {"fixture", "other"})
        self.assertEqual(value.active(REF.host), "fixture")

    def test_fresh_empty_configuration_is_safe_builtin_schema(self):
        self.assertEqual(Metadata.parse(b"", b"").hosts, {})

    def test_migration_is_refused(self):
        for general, hosts in ((b"prompt: disabled\n", b""),
                               (b"", b"fixture.invalid:\n"),
                               (b"version: 2\n", b"")):
            with self.assertRaises(SyncError):
                Metadata.parse(general, hosts)

    def test_endpoint_and_unsupported_yaml_are_refused(self):
        cases = [b"http_unix_socket: /fixture.sock\n", b"api_host: elsewhere.invalid\n",
                 b"version: 1\n", b"aliases: &anchor\n", b"aliases: *anchor\n",
                 b"aliases: !!map\n", b"editor: |\n", b"editor: [one, two]\n",
                 b"editor: {foo: bar}\n", b"editor: plain # comment\n",
                 b"editor: value\n  nested: value\n", b"\teditor: value\n",
                 b" aliases:\n", b"aliases:\n    a: one\n  b: two\n",
                 b"aliases:\n  auth: dangerous\n", b'editor: "\\u000a"\n']
        for number, extra in enumerate(cases):
            with self.subTest(case=number), self.assertRaises(SyncError):
                Metadata.parse(b"version: 1\n" + extra, b"")

    def test_inconsistent_or_unkeyed_only_authentication_is_refused(self):
        for extra in (b"    user: absent\n", b"    oauth_token: synthetic\n",
                      b"    users:\n        fixture:\n", b"    http_unix_socket: /fixture\n"):
            with self.assertRaises(SyncError):
                Metadata.parse(b"version: 1\n", b"fixture.invalid:\n" + extra)

    def test_opaque_token_not_in_representations(self):
        data = b"fixture.invalid:\n    users:\n        fixture:\n            oauth_token: private-marker\n    user: fixture\n"
        metadata = Metadata.parse(b"version: 1\n", data)
        self.assertFalse("private-marker" in repr(metadata))
        self.assertFalse("private-marker" in repr(metadata.hosts))

    def test_unselected_inline_token_change_is_not_preserved(self):
        template = ("fixture.invalid:\n    users:\n        fixture:\n        other:\n"
                    "            oauth_token: %s\n    user: fixture\n")
        before = Metadata.parse(b"version: 1\n", (template % "synthetic-one").encode())
        after = Metadata.parse(b"version: 1\n", (template % "synthetic-two").encode())
        self.assertFalse(before.preserved_except(after, REF.host, REF.account))

    def test_initial_default_materialization_cannot_change_protocol(self):
        before = Metadata.parse(b"", b"")
        after = Metadata.parse(b"version: 1\ngit_protocol: ssh\n", b"")
        self.assertFalse(before.preserved_except(after, REF.host, REF.account))


class BackendTest(unittest.TestCase):
    def test_lookup_cancellation_is_not_an_unavailable_credential(self):
        with patch.object(self.backend, "_call", side_effect=Cancelled("cancelled")):
            with self.assertRaises(Cancelled):
                self.backend._lookup(REF, Metadata.parse(b"", b""))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-gh-backend-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "gh"
        self.directory.mkdir(mode=0o700)
        self.binary = self.root / "native-fixture"
        # Only the process boundary is replaced below. This marker file is never executed.
        self.binary.write_bytes(b"\xcf\xfa\xed\xfe")
        self.binary.chmod(0o700)
        self.registered = {REF}
        self.tokens = {REF: "synthetic-before"}
        self.active = {REF.host: REF.account}
        self.protocols = {REF.host: "https"}
        self.events, self.environments = [], []
        self.validation_ok, self.write_failure, self.unreadable_after = True, False, False
        self.write_config()
        self.config = parse_github({"executable": str(self.binary), "config_directory": str(self.directory),
                                   "accounts": [{"host": REF.host, "account": REF.account}]},
                                  repository=self.root / "repo", local=self.root / "secrets").bind()
        self.backend = github.Backend(self.config)
        mocked = patch.object(github, "run", side_effect=self.child)
        self.addCleanup(mocked.stop)
        mocked.start()
        platform = patch.object(github.sys, "platform", "darwin")
        self.addCleanup(platform.stop)
        platform.start()

    def write_config(self):
        (self.directory / "config.yml").write_text("version: 1\nprompt: disabled\n")
        lines = []
        for host in sorted({ref.host for ref in self.registered}):
            lines.append(host + ":")
            if host in self.protocols:
                lines.append("    git_protocol: " + self.protocols[host])
            lines.append("    users:")
            for ref in sorted(self.registered):
                if ref.host == host:
                    lines.append("        " + ref.account + ":")
            lines.append("    user: " + self.active[host])
        (self.directory / "hosts.yml").write_text("\n".join(lines) + ("\n" if lines else ""))
        for name in ("hosts.yml", "config.yml"):
            (self.directory / name).chmod(0o600)

    def child(self, argv, payload, *, environment, cwd, limit):
        self.environments.append(environment)
        if str(argv[-1]).endswith("github_validate.py"):
            request = json.loads(payload)
            self.assertEqual(set(request), {"host", "account", "token", "ca_file"})
            self.events.append(("validate", request["host"], request["account"]))
            self.assertFalse(any(request["token"] in item for item in argv))
            self.assertFalse(any(request["token"] in value for value in environment.values()))
            self.assertEqual(environment["HOME"], str(cwd))
            return Result(0, b"verified\n") if self.validation_ok else Result(1, b"refused\n")
        args = argv[1:]
        if args == ["--version"]:
            self.assertNotEqual(Path(environment["GH_CONFIG_DIR"]), self.directory)
            self.assertTrue(Path(environment["GH_CONFIG_DIR"]).is_relative_to(cwd))
            self.events.append(("version",))
            return Result(0, b"gh version 2.83.2 (fixture)\n")
        if args[:2] == ["auth", "token"]:
            self.assertEqual(args[2::2], ["--hostname", "--user"])
            ref = Ref("github", host=args[3], account=args[5])
            self.events.append(("read", ref))
            token = self.tokens.get(ref)
            return Result(0, token.encode() + b"\n") if token else Result(1, b"")
        if args[:2] == ["auth", "login"]:
            self.assertEqual(args, ["auth", "login", "--hostname", REF.host, "--with-token"])
            self.events.append(("write", REF))
            self.assertFalse(any(payload.rstrip(b"\n").decode() in item for item in argv))
            self.tokens[REF] = payload.removesuffix(b"\n").decode()
            self.registered.add(REF)
            self.active[REF.host] = REF.account
            self.write_config()
            if self.unreadable_after:
                self.tokens.pop(REF)
            return Result(1 if self.write_failure else 0, b"")
        raise AssertionError("unexpected credential child command")

    def test_constructor_does_not_discover(self):
        self.assertEqual(self.events, [])
        self.assertIsNone(self.backend.before)

    def test_qualified_discovery_reads_only_configured_accounts(self):
        self.registered.add(OTHER)
        self.tokens[OTHER] = "synthetic-other"
        self.write_config()
        result = self.backend.discover()
        self.assertTrue(result[REF].token == self.tokens[REF])
        self.assertEqual([event for event in self.events if event[0] == "read"], [("read", REF)])

    def test_inherited_credentials_and_debug_not_passed(self):
        with patch.dict(os.environ, {key: "private-marker" for key in (*TOKEN_OVERRIDES, "SOPS_AGE_KEY", "GH_DEBUG", "HTTPS_PROXY")}):
            self.backend.discover()
        for env in self.environments:
            self.assertFalse(any(value == "private-marker" for value in env.values()))

    def test_registered_unavailable_is_blocked(self):
        self.tokens.clear()
        self.assertEqual(self.backend.discover()[REF].state, "blocked")

    def test_readable_orphan_is_blocked(self):
        self.registered.clear()
        self.write_config()
        self.assertEqual(self.backend.discover()[REF].state, "blocked")

    def test_unregistered_missing_has_orphan_warning(self):
        self.registered.clear()
        self.tokens.clear()
        self.write_config()
        saved = self.backend.discover()[REF]
        self.assertEqual(saved.state, "absent")
        self.assertIn("orphaned", saved.reason)

    def test_timeout_is_not_absence(self):
        self.registered.clear()
        self.tokens.clear()
        self.write_config()
        delegate = self.child
        def timeout(argv, *args, **kwargs):
            if "token" in argv:
                raise SyncError("fixture timeout")
            return delegate(argv, *args, **kwargs)
        with patch.object(github, "run", side_effect=timeout):
            self.assertEqual(self.backend.discover()[REF].state, "blocked")

    def test_migration_and_endpoint_override_refused_before_child(self):
        for content in ("prompt: disabled\n", "version: 1\nhttp_unix_socket: /test.sock\n"):
            (self.directory / "config.yml").write_text(content)
            with self.assertRaises(SyncError):
                github.Backend(self.config).discover()
            self.assertEqual(self.events, [])

    def test_shim_refused_without_executing_it(self):
        self.binary.write_bytes(b"#!/bin/sh\n")
        config = replace(self.config, executable=Executable.resolve(str(self.binary)))
        with self.assertRaises(SyncError):
            github.Backend(config).discover()
        self.assertEqual(self.events, [])

    def test_config_permissions_and_symlinks_refused(self):
        path = self.directory / "hosts.yml"
        path.chmod(0o644)
        with self.assertRaises(SyncError):
            self.backend.discover()
        path.unlink()
        path.symlink_to(self.root / "not-read")
        with self.assertRaises((SyncError, OSError)):
            github.Backend(self.config).discover()
        self.assertEqual(self.events, [])

    def test_unsupported_version_never_reads_selected_credentials(self):
        with patch.object(github, "run", return_value=Result(0, b"gh version 99.0.0 (fixture)\n")):
            with self.assertRaises(SyncError):
                self.backend.discover()
        self.assertEqual(self.backend.saved, {})

    def test_changed_ca_refuses_preflight(self):
        ca = self.root / "public-ca"
        ca.write_text("public-fixture-one")
        ca.chmod(0o600)
        config = replace(self.config, ca_file=ca, ca_signature=Executable._signature(ca.stat()))
        backend = github.Backend(config)
        backend.discover()
        ca.write_text("public-fixture-two")
        with self.assertRaises(SyncError):
            backend.preflight(REF, "synthetic-after", "overwrite")
        self.assertFalse(any(e[0] == "write" for e in self.events))

    def test_account_switch_and_local_delete_refused_before_validation(self):
        self.registered.add(OTHER)
        self.active[REF.host] = OTHER.account
        self.write_config()
        self.backend.discover()
        for action in ("overwrite", "remove"):
            with self.assertRaises(SyncError):
                self.backend.preflight(REF, "synthetic-after", action)
        self.assertFalse(any(e[0] in {"validate", "write"} for e in self.events))

    def test_selected_token_validation_refusal_never_writes(self):
        self.backend.discover()
        self.validation_ok = False
        with self.assertRaises(SyncError):
            self.backend.preflight(REF, "synthetic-after", "overwrite")
        with self.assertRaises(SyncError):
            self.backend.commit(REF, "synthetic-after", "overwrite")
        self.assertFalse(any(e[0] == "write" for e in self.events))

    def test_preflight_required_for_exact_token_and_action(self):
        self.backend.discover()
        with self.assertRaises(SyncError):
            self.backend.commit(REF, "synthetic-after", "overwrite")
        self.backend.preflight(REF, "synthetic-after", "overwrite")
        with self.assertRaises(SyncError):
            self.backend.commit(REF, "different-synthetic", "overwrite")
        self.assertFalse(any(e[0] == "write" for e in self.events))

    def test_active_overwrite_with_stdin_token_preserves_other_account(self):
        self.registered.add(OTHER)
        self.tokens[OTHER] = "synthetic-other"
        self.write_config()
        self.backend.discover()
        self.backend.preflight(REF, "synthetic-after", "overwrite")
        self.backend.commit(REF, "synthetic-after", "overwrite")
        self.assertTrue(self.tokens[REF] == "synthetic-after")
        self.assertTrue(self.tokens[OTHER] == "synthetic-other")
        self.assertEqual(self.active[REF.host], REF.account)
        self.backend.recheck()

    def test_unregistered_add(self):
        self.registered.clear()
        self.tokens.clear()
        self.protocols.clear()
        self.write_config()
        self.backend.discover()
        self.backend.preflight(REF, "synthetic-after", "add")
        self.backend.commit(REF, "synthetic-after", "add")
        self.assertEqual(self.backend.saved[REF].state, "available")

    def test_state_race_aborts_before_write(self):
        self.backend.discover()
        self.backend.preflight(REF, "synthetic-after", "overwrite")
        self.tokens[REF] = "synthetic-concurrent"
        with self.assertRaises(SyncError):
            self.backend.commit(REF, "synthetic-after", "overwrite")
        self.assertFalse(any(e[0] == "write" for e in self.events))

    def test_postlaunch_failure_reports_uncertain(self):
        self.backend.discover()
        self.backend.preflight(REF, "synthetic-after", "overwrite")
        self.write_failure = True
        with self.assertRaises(CommittedError):
            self.backend.commit(REF, "synthetic-after", "overwrite")
        self.assertTrue(self.tokens[REF] == "synthetic-after")

    def test_postwrite_unreadable_reports_uncertain(self):
        self.backend.discover()
        self.backend.preflight(REF, "synthetic-after", "overwrite")
        self.unreadable_after = True
        with self.assertRaises(CommittedError):
            self.backend.commit(REF, "synthetic-after", "overwrite")


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.url = "https://fixture.invalid/api/v3/user"
        self.response = unittest.mock.MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.status = 200
        self.response.geturl.return_value = self.url
        self.response.headers = {"X-OAuth-Scopes": "repo, read:org, gist"}
        self.response.read.return_value = b'{"login":"Fixture"}'
        self.opener = unittest.mock.Mock()
        self.opener.open.return_value = self.response
        mocked = patch.object(github_validate.urllib.request, "build_opener", return_value=self.opener)
        self.addCleanup(mocked.stop)
        self.build = mocked.start()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_patch = patch.object(github_validate.ssl, "create_default_context", return_value=self.context)
        self.addCleanup(ssl_patch.stop)
        ssl_patch.start()

    def validate(self, host="fixture.invalid"):
        github_validate.validate(host, "fixture", "synthetic-validation")

    def test_fixed_enterprise_endpoint_and_required_tls(self):
        self.validate()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.url)
        self.assertTrue(self.context.check_hostname)
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        handlers = self.build.call_args.args
        self.assertTrue(any(isinstance(h, github_validate.NoRedirect) for h in handlers))
        https = next(h for h in handlers if isinstance(h, github_validate.urllib.request.HTTPSHandler))
        self.assertIs(https._context, self.context)

    def test_public_github_endpoint(self):
        self.response.geturl.return_value = "https://api.github.com/user"
        self.validate("github.com")
        self.assertEqual(self.opener.open.call_args.args[0].full_url, "https://api.github.com/user")

    def test_redirect_rejected_before_follow(self):
        with self.assertRaises(SyncError):
            github_validate.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid/")

    def test_mismatching_account_or_missing_scopes_refused(self):
        self.response.read.return_value = b'{"login":"other"}'
        with self.assertRaises(SyncError):
            self.validate()
        self.response.read.return_value = b'{"login":"fixture"}'
        self.response.headers = {"X-OAuth-Scopes": "repo"}
        with self.assertRaises(SyncError):
            self.validate()

    def test_status_endpoint_size_and_duplicate_fields_refused(self):
        cases = [("status", 403), ("geturl", "https://other.invalid/"),
                 ("read", b"x" * (github_validate.MAX_RESPONSE + 1)),
                 ("read", b'{"login":"fixture","login":"fixture"}')]
        for field, value in cases:
            with self.subTest(field=field):
                if field == "status":
                    self.response.status = value
                else:
                    getattr(self.response, field).return_value = value
                with self.assertRaises(SyncError):
                    self.validate()
                self.response.status = 200
                self.response.geturl.return_value = self.url
                self.response.read.return_value = b'{"login":"fixture"}'

    def test_git_identity_profile_does_not_require_gh_cli_scopes(self):
        self.response.headers = {}
        github_validate.validate("fixture.invalid", "fixture", "synthetic-validation", profile="git-https")
        self.response.read.return_value = b'{"login":"other"}'
        with self.assertRaises(SyncError):
            github_validate.validate("fixture.invalid", "fixture", "synthetic-validation", profile="git-https")

    def test_unknown_validation_profile_refuses_before_network(self):
        with self.assertRaises(SyncError):
            github_validate.validate("fixture.invalid", "fixture", "synthetic-validation", profile="unchecked")
        self.opener.open.assert_not_called()


class ProcessTest(unittest.TestCase):
    def test_timeout_stops_owned_descendant_holding_capture_pipes(self):
        with tempfile.TemporaryDirectory(prefix="envy-gh-descendant-") as directory:
            code = ("import fcntl, os, time\n"
                    "if os.fork():\n    os._exit(0)\n"
                    "with open('held', 'w') as handle:\n"
                    "    fcntl.flock(handle, fcntl.LOCK_EX)\n"
                    "    handle.write('ready'); handle.flush()\n"
                    "    time.sleep(5)\n")
            with self.assertRaises(SyncError):
                run([sys.executable, "-I", "-B", "-c", code], b"",
                    environment={"HOME": directory, "PATH": "/usr/bin:/bin"},
                    cwd=directory, timeout=0.5)
            # The descendant actually acquired the lock. Successful reacquisition
            # proves it no longer holds its file; no native credential process runs.
            with (Path(directory) / "held").open() as handle:
                self.assertEqual(handle.read(), "ready")
                deadline = time.monotonic() + 1
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            self.fail("owned descendant retained its lock after group termination")
                        # SIGKILL delivery is asynchronous even after the direct
                        # child is reaped; observe actual descendant termination.
                        time.sleep(0.01)

    def test_real_child_capture_size_timeout_and_status(self):
        with tempfile.TemporaryDirectory(prefix="envy-gh-process-") as directory:
            env = {"HOME": directory, "PATH": "/usr/bin:/bin"}
            def child(code, **kwargs):
                return run([sys.executable, "-I", "-B", "-c", code], b"synthetic-input",
                           environment=env, cwd=directory, **kwargs)
            result = child("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(7)")
            self.assertEqual(result.status, 7)
            self.assertTrue(result.output == b"synthetic-input")
            self.assertFalse("synthetic-input" in repr(result))
            with self.assertRaises(SyncError):
                child("print('x' * 1000)", limit=10)
            with self.assertRaises(SyncError):
                child("import time; time.sleep(5)", timeout=0.05)


if __name__ == "__main__":
    unittest.main()
