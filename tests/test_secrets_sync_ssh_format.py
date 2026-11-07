"""SSH format/native validation with only freshly generated disposable keys."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.errors import SyncError
from secrets_sync.ssh_format import Material, decode_public, validate_native, validate_basename


class SSHFormatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="envy-ssh-format-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.env = {"HOME": str(cls.root), "PATH": "/usr/bin:/bin", "LANG": "C"}
        cls.keys = []
        for name, password in (("first", b""), ("second", b""), ("protected", b"synthetic-passphrase")):
            path = cls.root / name
            args = ["/usr/bin/ssh-keygen", "-t", "ed25519", "-f", str(path), "-C", "fixture"]
            result = subprocess.run(args, input=password + b"\n" + password + b"\n", env=cls.env,
                                    capture_output=True, start_new_session=True, timeout=15)
            if result.returncode:
                raise RuntimeError("owned native SSH fixture generation failed")
            cls.keys.append((path.read_text(), Path(str(path) + ".pub").read_text()))

    def test_native_fingerprint_and_public_only_roundtrip(self):
        private, public = self.keys[0]
        value = Material("id_ed25519", public, None)
        blob = base64.b64decode(public.split()[1])
        expected = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        self.assertEqual(value.fingerprint, expected)
        self.assertTrue(Material.decode(value.encode()) == value, "SSH public roundtrip mismatch")
        validate_native(value)

    def test_native_public_validation_rejects_invalid_curve_point(self):
        def field(value):
            return len(value).to_bytes(4, "big") + value
        blob = field(b"ecdsa-sha2-nistp256") + field(b"nistp256") + field(b"\x04" + b"\0" * 64)
        public = "ecdsa-sha2-nistp256 " + base64.b64encode(blob).decode()
        with self.assertRaises(SyncError):
            validate_native(Material("key", public, None))

    def test_private_pair_and_protection_preserved(self):
        for i, (private, public) in enumerate(self.keys):
            value = Material("id_ed25519", public, private)
            self.assertEqual(value.protected, i == 2)
            decoded = Material.decode(value.encode())
            self.assertTrue(decoded.private == private, "private bytes changed")
            self.assertTrue(decoded.public == public, "public bytes changed")

    def test_native_rsa_and_nist_ecdsa_matrix(self):
        for kind, bits in (("rsa", "2048"), ("ecdsa", "256"), ("ecdsa", "384"), ("ecdsa", "521")):
            for protected in (False, True):
                with self.subTest(kind=kind, bits=bits, protected=protected):
                    path = self.root / (kind + bits + ("-protected" if protected else "-clear"))
                    password = "synthetic-passphrase" if protected else ""
                    result = subprocess.run(
                        ["/usr/bin/ssh-keygen", "-t", kind, "-b", bits, "-f", str(path), "-C", "fixture"],
                        input=(password + "\n" + password + "\n").encode(), env=self.env,
                        capture_output=True, start_new_session=True, timeout=15)
                    self.assertEqual(result.returncode, 0, "owned native SSH fixture generation failed")
                    public_path = Path(str(path) + ".pub")
                    material = Material(path.name, public_path.read_text(), path.read_text())
                    self.assertEqual(material.protected, protected)
                    self.assertTrue(Material.decode(material.encode()) == material, "SSH bytes changed")
                    validate_native(material, password)
                    native = subprocess.run(
                        ["/usr/bin/ssh-keygen", "-l", "-E", "sha256", "-f", str(public_path)],
                        env=self.env, capture_output=True, timeout=15)
                    self.assertEqual(native.returncode, 0, "native public fingerprint check failed")
                    self.assertTrue(material.fingerprint.encode() in native.stdout.split(),
                                    "native public fingerprint mismatch")

    def test_pair_mismatch_refuses(self):
        with self.assertRaises(SyncError):
            Material("id_ed25519", self.keys[1][1], self.keys[0][0])

    def test_native_checks_clear_and_protected_private_consistency(self):
        for index, password in ((0, ""), (2, "synthetic-passphrase")):
            private, public = self.keys[index]
            validate_native(Material("id_ed25519", public, private), password)
        with self.assertRaises(SyncError):
            validate_native(Material("id_ed25519", self.keys[2][1], self.keys[2][0]), "incorrect")

    def test_unsafe_names_and_unsupported_key_types_refuse(self):
        for name in ("../id_ed25519", "/tmp/key", "known_hosts", "authorized_keys", "config", "id_rsa.pub", ".hidden", "bad\nname"):
            with self.assertRaises(SyncError): validate_basename(name)
        for public in ("sk-ssh-ed25519@openssh.com AAAA", "ssh-ed25519-cert-v01@openssh.com AAAA", "ssh-dss AAAA", "ssh-ed25519 !invalid!"):
            with self.assertRaises(SyncError): decode_public(public)
        with self.assertRaises(SyncError): Material("key", self.keys[0][1], "-----BEGIN RSA PRIVATE KEY-----\ninvalid\n")

    def test_native_validation_leaves_no_key_staging_or_secret_argv(self):
        from unittest.mock import patch
        from secrets_sync import ssh_format
        calls = []
        native = ssh_format.run
        def observed(argv, payload, **kwargs):
            calls.append(argv)
            self.assertNotIn("synthetic-passphrase", str(argv))
            self.assertNotIn("synthetic-passphrase", str(kwargs["environment"]))
            stage = Path(argv[-1])
            self.assertEqual(stage.stat().st_mode & 0o777, 0o600)
            self.assertEqual(stage.parent.stat().st_mode & 0o777, 0o700)
            return native(argv, payload, **kwargs)
        with patch.object(ssh_format, "run", side_effect=observed):
            validate_native(Material("key", self.keys[2][1], self.keys[2][0]), "synthetic-passphrase")
        self.assertEqual(len(calls), 1)
        self.assertFalse(Path(calls[0][-1]).exists())


if __name__ == "__main__": unittest.main()
