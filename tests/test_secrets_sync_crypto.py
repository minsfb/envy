"""Real crypto with ephemeral synthetic keys; no host secret/config access."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.crypto import Crypto, decode_payload, encode_payload, envelope, run_child, resolve_executable
from secrets_sync.errors import SyncError


class CryptoTest(unittest.TestCase):
    def test_mise_shims_resolve_before_crypto_home_is_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            actual = Path(root) / "sops"
            actual.write_text("#!/bin/sh\nexit 0\n")
            actual.chmod(0o700)
            def which(name):
                return str(Path(root) / "shims/sops") if name == "sops" else "/fixture/mise"
            with mock.patch("secrets_sync.crypto.shutil.which", side_effect=which), \
                    mock.patch("secrets_sync.crypto.run_child", return_value=str(actual).encode()) as run:
                self.assertEqual(resolve_executable("sops"), str(actual.resolve()))
            self.assertEqual(run.call_args.args[0], ["/fixture/mise", "which", "sops"])
            self.assertEqual(run.call_args.kwargs["environment"]["MISE_OFFLINE"], "1")
            self.assertNotIn("SOPS_AGE_KEY", run.call_args.kwargs["environment"])

    @classmethod
    def setUpClass(cls):
        cls.crypto = Crypto()
        raw = cls.crypto.call([cls.crypto.age_keygen], limit=4096)
        cls.key = next(line for line in raw.decode().splitlines() if line.startswith("AGE-"))
        cls.recipient = cls.crypto.recipient(cls.key)

    def test_real_round_trip_isolated_from_ambient_identity(self):
        values = {"EXAMPLE": "synthetic-fixture-value"}
        with mock.patch.dict(os.environ, {"SOPS_AGE_KEY": "invalid", "SOPS_AGE_KEY_CMD": "false",
                                          "SOPS_AGE_KEY_FILE": "/nonexistent"}):
            encrypted = self.crypto.encrypt(values, self.key, self.recipient)
            self.assertTrue(self.crypto.decrypt(encrypted, self.key, self.recipient) == values)
        self.assertFalse(values["EXAMPLE"].encode() in encrypted)
        self.assertFalse(self.key.encode() in encrypted)

    def test_wrong_recipient_and_corrupt_mac_are_rejected(self):
        raw = self.crypto.encrypt({"A": "synthetic"}, self.key, self.recipient)
        with self.assertRaises(SyncError):
            envelope(raw, "wrong")
        document = json.loads(raw)
        document["sops"]["mac"] = document["sops"]["mac"].replace("data:", "data:AAAA", 1)
        with self.assertRaises(SyncError):
            self.crypto.decrypt(json.dumps(document).encode(), self.key, self.recipient)

    def test_extra_recipients_and_providers_rejected(self):
        raw = self.crypto.encrypt({}, self.key, self.recipient)
        for field in ("age", "kms", "key_groups"):
            document = json.loads(raw)
            document["sops"][field] = document["sops"]["age"] * 2
            with self.subTest(field=field), self.assertRaises(SyncError):
                envelope(json.dumps(document).encode())

    def test_payload_validation(self):
        for raw in (b'{"version":1,"version":1,"entries":{}}',
                    b'{"version":true,"entries":{}}', b'{"version":1,"entries":{"A":1}}'):
            with self.assertRaises(SyncError):
                decode_payload(raw)
        for values in ({"A": "line\nbreak"}, {"A": "x" * 65537}, {"bad-name": "x"}):
            with self.assertRaises(SyncError):
                encode_payload(values)

    def test_subprocess_timeout_and_output_limit_are_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            for code, timeout, limit in (("import time;time.sleep(10)", .1, 100),
                                         ("print('x'*200)", 5, 100)):
                with self.assertRaises(SyncError):
                    run_child([sys.executable, "-I", "-B", "-c", code], b"",
                              environment={"HOME": root}, cwd=root, timeout=timeout, limit=limit)


if __name__ == "__main__":
    unittest.main()
