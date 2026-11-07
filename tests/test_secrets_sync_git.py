"""Native Git provider with only the credential/API process boundary modeled."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync import git_credentials as git
from secrets_sync.errors import CommittedError, SyncError, UsageError
from secrets_sync.git_config import GitConfig, parse_git
from secrets_sync.github_config import Executable
from secrets_sync.github_process import Result
from secrets_sync.inventory import Ref

FIRST = Ref("git", host="fixture.invalid", account="fixture")
OTHER = Ref("git", host="fixture.invalid", account="other")


class GitBackendTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-git-provider-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        helper = self.root / "git-credential-osxkeychain"
        helper.write_bytes(b"\xcf\xfa\xed\xfe")
        helper.chmod(0o700)
        self.config = GitConfig("osxkeychain", ((FIRST.host, FIRST.account), (OTHER.host, OTHER.account)),
                                Executable.resolve(str(helper)))
        self.tokens = {FIRST: "synthetic-before", OTHER: "synthetic-other"}
        self.events = []
        self.validate_ok, self.write_fail, self.unreadable = True, False, False
        process = patch.object(git, "run", side_effect=self.child)
        process.start()
        self.addCleanup(process.stop)
        platform = patch.object(git.sys, "platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)
        self.backend = git.Backend(self.config)

    def child(self, argv, data, *, environment, cwd, limit):
        self.assertFalse(any("synthetic-" in str(arg) for arg in argv), "credential in argv")
        self.assertFalse(any("synthetic-" in value for value in environment.values()), "credential in environment")
        if str(argv[-1]).endswith("github_validate.py"):
            request = json.loads(data)
            self.assertEqual(request["profile"], "git-https")
            self.events.append("validate")
            return Result(0, b"verified\n") if self.validate_ok else Result(1, b"suppressed")
        if argv[0] == "/usr/bin/security":
            self.assertIn("-p", argv)
            self.assertEqual(argv[argv.index("-p") + 1], "")
            self.assertEqual(argv[argv.index("-P") + 1], "0")
            ref = Ref("git", host=argv[argv.index("-s") + 1], account=argv[argv.index("-a") + 1])
            self.events.append("metadata")
            return Result(0 if ref in self.tokens else 44, b"")
        fields = dict(line.split(b"=", 1) for line in data.splitlines() if b"=" in line)
        self.assertTrue(fields.get(b"protocol") == b"https" and fields.get(b"path") == b"", "unscoped Git helper query")
        self.assertTrue(fields[b"host"].endswith(b":0"), "Git query must not match arbitrary ports")
        ref = Ref("git", host=fields[b"host"].decode().removesuffix(":0"), account=fields[b"username"].decode())
        action = argv[-1]
        self.events.append(action)
        if action == "get":
            value = self.tokens.get(ref)
            return Result(0, b"" if self.unreadable or value is None else b"password=" + value.encode() + b"\n")
        if action == "store":
            self.tokens[ref] = fields[b"password"].decode()
            return Result(1 if self.write_fail else 0, b"")
        raise AssertionError("unexpected native credential command")

    def test_constructor_does_not_discover_and_reads_are_exact(self):
        self.assertEqual(self.events, [])
        saved = self.backend.discover()
        self.assertEqual(set(saved), {FIRST, OTHER})
        self.assertTrue(saved[FIRST].token == self.tokens[FIRST])
        self.assertNotIn("store", self.events)

    def test_password_only_reply_roundtrips_and_preserves_other_account(self):
        self.backend.discover()
        self.backend.preflight(FIRST, "synthetic-after", "overwrite")
        self.backend.commit(FIRST, "synthetic-after", "overwrite")
        self.assertTrue(self.tokens[FIRST] == "synthetic-after")
        self.assertTrue(self.tokens[OTHER] == "synthetic-other")
        self.backend.recheck()

    def test_missing_is_absent_but_existing_unreadable_is_blocked(self):
        self.tokens.pop(FIRST)
        self.unreadable = True
        saved = self.backend.discover()
        self.assertEqual(saved[FIRST].state, "absent")
        self.assertEqual(saved[OTHER].state, "blocked")

    def test_invalid_metadata_or_reply_is_blocked(self):
        for output in (b"username=wrong\npassword=synthetic-before\n", b"password=one\npassword=two\n",
                       b"protocol=http\npassword=synthetic-before\n"):
            def child(argv, *args, **kwargs):
                return Result(0, output) if argv[-1] == "get" else self.child(argv, *args, **kwargs)
            with self.subTest(), patch.object(git, "run", side_effect=child):
                self.assertEqual(git.Backend(self.config).discover()[FIRST].state, "blocked")

    def test_native_state_capability_is_accepted_but_never_echoed_on_store(self):
        delegate = self.child
        def child(argv, *args, **kwargs):
            result = delegate(argv, *args, **kwargs)
            if argv[-1] == "get" and result.output:
                return Result(0, result.output + b"capability[]=state\nstate[]=osxkeychain:seen=1\n")
            if argv[-1] == "store":
                self.assertFalse(b"state[]=" in args[0], "read marker would suppress actual native overwrite")
            return result
        with patch.object(git, "run", side_effect=child):
            self.assertEqual(self.backend.discover()[FIRST].state, "available")
            self.backend.preflight(FIRST, "synthetic-after", "overwrite")
            self.backend.commit(FIRST, "synthetic-after", "overwrite")

    def test_unrecognized_state_and_refresh_credentials_stay_blocked(self):
        for extra in (b"state[]=unknown\n", b"capability[]=unknown\n", b"oauth_refresh_token=synthetic-refresh\n"):
            with self.subTest(), self.assertRaises(SyncError):
                git.decode_reply(b"password=synthetic-before\n" + extra, FIRST)

    def test_preflight_and_fresh_comparison_required(self):
        self.backend.discover()
        with self.assertRaises(SyncError):
            self.backend.commit(FIRST, "synthetic-after", "overwrite")
        self.backend.preflight(FIRST, "synthetic-after", "overwrite")
        self.tokens[OTHER] = "synthetic-race"
        with self.assertRaises(SyncError):
            self.backend.commit(FIRST, "synthetic-after", "overwrite")
        self.assertNotIn("store", self.events)

    def test_local_delete_and_failed_validation_never_store(self):
        self.backend.discover()
        with self.assertRaises(SyncError):
            self.backend.preflight(FIRST, "synthetic-after", "remove")
        self.validate_ok = False
        with self.assertRaises(SyncError):
            self.backend.preflight(FIRST, "synthetic-after", "overwrite")
        self.assertNotIn("store", self.events)

    def test_native_error_after_launch_is_uncertain(self):
        self.backend.discover()
        self.backend.preflight(FIRST, "synthetic-after", "overwrite")
        self.write_fail = True
        with self.assertRaises(CommittedError):
            self.backend.commit(FIRST, "synthetic-after", "overwrite")
        self.assertTrue(self.tokens[FIRST] == "synthetic-after")

    def test_gh_policy_has_no_helper_resolution_or_duplicate_native_store(self):
        with patch.object(Executable, "resolve") as resolve:
            config = parse_git({"backend": "gh", "accounts": [{"host": FIRST.host, "account": FIRST.account}]})
        resolve.assert_not_called()
        self.assertIsNone(config.executable)
        for backend in ("store", "cache", "!custom-command", None, [], {}):
            with self.assertRaises(UsageError):
                parse_git({"backend": backend, "accounts": []})


if __name__ == "__main__":
    unittest.main()
