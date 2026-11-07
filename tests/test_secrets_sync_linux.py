"""Linux native compatibility boundaries; no real bus, gh or credentials."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync import github
from secrets_sync.errors import SyncError
from secrets_sync.github_process import Result
from secrets_sync.github_config import Executable


class LinuxCompatibilityTest(unittest.TestCase):
    def test_native_environment_preserves_only_session_transport(self):
        with patch.object(github.sys, "platform", "linux"), patch.dict(os.environ, {
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/fixture/bus", "XDG_RUNTIME_DIR": "/fixture/runtime",
            "GH_TOKEN": "synthetic-hidden", "DBUS_STARTER_ADDRESS": "ignored", "GH_DEBUG": "api",
        }, clear=True):
            native = github.environment(Path("/fixture/tmp"), Path("/fixture/gh"))
            isolated = github.environment(Path("/fixture/tmp"), Path("/fixture/gh"), native=False)
        self.assertEqual(native["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/fixture/bus")
        self.assertEqual(native["XDG_RUNTIME_DIR"], "/fixture/runtime")
        for key in ("GH_TOKEN", "DBUS_STARTER_ADDRESS", "GH_DEBUG"):
            self.assertNotIn(key, native)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", isolated)
        self.assertNotIn("XDG_RUNTIME_DIR", isolated)

    def check(self, replies):
        calls = []
        def run(argv, data, **kwargs):
            calls.append(argv)
            return Result(0, json.dumps(replies.pop(0)).encode())
        with patch.object(github, "run", side_effect=run), patch.object(Executable, "resolve") as executable:
            executable.return_value.path = Path("/usr/bin/busctl")
            github.linux_storage_gate({}, Path("/fixture"))
        return calls

    def test_existing_bus_with_no_login_collection_uses_native_fallback(self):
        calls = self.check([{"type": "ao", "data": ["/org/freedesktop/secrets/collection/session"]},
                            {"type": "o", "data": ["/"]}])
        self.assertEqual(len(calls), 2)
        self.assertFalse(any("SearchItems" in call for call in calls))

    def test_login_or_default_keyring_requires_separate_native_proof(self):
        for replies in ([{"type": "ao", "data": ["/org/freedesktop/secrets/collection/login"]}],
                        [{"type": "ao", "data": []}, {"type": "o", "data": ["/collection/custom"]}]):
            with self.subTest(), self.assertRaisesRegex(SyncError, "keyring"):
                self.check(replies)

    def test_unavailable_service_is_not_proof_of_empty_storage(self):
        with patch.object(github, "run", return_value=Result(1, b"raw synthetic error")), \
                patch.object(Executable, "resolve"), self.assertRaises(SyncError):
            github.linux_storage_gate({}, Path("/fixture"))


if __name__ == "__main__":
    unittest.main()
