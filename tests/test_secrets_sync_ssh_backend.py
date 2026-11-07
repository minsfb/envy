"""Production SSH file provider against owned, disposable native key fixtures."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.errors import CommittedError, SyncError
from secrets_sync.inventory import Ref
from secrets_sync.ssh_config import SSHConfig
from secrets_sync.ssh_format import Material
from secrets_sync.ssh import Backend


class SSHBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys_dir = tempfile.TemporaryDirectory(prefix="envy-ssh-provider-keys-")
        cls.addClassCleanup(cls.keys_dir.cleanup)
        root = Path(cls.keys_dir.name).resolve()
        cls.materials = []
        for name, password in (("one", ""), ("two", ""), ("protected", "synthetic-passphrase")):
            path = root / name
            result = subprocess.run(["/usr/bin/ssh-keygen", "-t", "ed25519", "-f", str(path), "-C", "fixture"],
                                    input=(password + "\n" + password + "\n").encode(), capture_output=True,
                                    env={"HOME": str(root), "PATH": "/usr/bin:/bin"}, start_new_session=True, timeout=15)
            if result.returncode:
                raise RuntimeError("owned native key generation failed")
            cls.materials.append(Material("id_ed25519", Path(str(path) + ".pub").read_text(), path.read_text()))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="envy-ssh-provider-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "ssh"
        self.directory.mkdir(mode=0o700)
        self.config = SSHConfig(self.directory, ("id_ed25519",))
        self.backend = Backend(self.config, self.root)

    def put(self, material):
        for suffix, value in (("", material.private), (".pub", material.public)):
            if value is not None:
                path = self.directory / (material.filename + suffix)
                path.write_text(value)
                path.chmod(0o600)

    def ref(self, material):
        return Ref("ssh", name=material.fingerprint)

    def test_constructor_does_not_read_keys_and_discovery_is_bounded(self):
        self.put(self.materials[0])
        self.put(replace(self.materials[1], filename="not-configured"))
        saved = self.backend.discover()
        self.assertEqual(set(saved), {self.ref(self.materials[0])})
        self.assertTrue(Material.decode(next(iter(saved.values())).token) == self.materials[0], "key discovery mismatch")

    def test_missing_directory_discovery_and_cancel_leave_it_absent(self):
        self.directory.rmdir()
        self.assertEqual(self.backend.discover(), {})
        self.assertFalse(self.directory.exists())
        ref = self.ref(self.materials[0])
        self.assertEqual(self.backend.compare(ref, self.materials[0].encode()), "source-only")
        self.assertFalse(self.directory.exists())

    def test_shared_read_directory_preserved_through_discovery_and_import(self):
        self.put(self.materials[0])
        self.directory.chmod(0o755)
        before = self.directory.stat()
        self.assertEqual(set(self.backend.discover()), {self.ref(self.materials[0])})
        self.assertEqual(self.directory.stat().st_ctime_ns, before.st_ctime_ns)
        incoming = self.materials[2]
        ref = self.ref(incoming)
        self.assertEqual(self.backend.compare(ref, incoming.encode()), "differing")
        self.backend.preflight(ref, incoming.encode(), "overwrite", passphrase="synthetic-passphrase")
        self.assertEqual(self.directory.stat().st_ctime_ns, before.st_ctime_ns)
        with patch("secrets_sync.ssh.os.chmod", side_effect=AssertionError("unexpected chmod")):
            self.backend.commit(ref, incoming.encode(), "overwrite")
            self.backend.recheck()
        self.assertEqual(self.directory.stat().st_mode, before.st_mode)
        self.assertTrue((self.directory / incoming.filename).read_text() == incoming.private,
                        "imported key mismatch")
        for suffix in ("", ".pub"):
            self.assertEqual((self.directory / (incoming.filename + suffix)).stat().st_mode & 0o777, 0o600)

    def test_missing_directory_is_created_private_only_for_selected_import(self):
        self.directory.rmdir()
        self.backend.discover()
        incoming = self.materials[0]
        self.backend.preflight(self.ref(incoming), incoming.encode(), "add")
        self.assertFalse(self.directory.exists())
        self.backend.commit(self.ref(incoming), incoming.encode(), "add")
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_directory_mode_change_after_preflight_refuses_without_repair(self):
        for mode in (0o755, 0o775):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(0o700)
                backend = Backend(self.config, self.root)
                backend.discover()
                incoming = self.materials[0]
                backend.preflight(self.ref(incoming), incoming.encode(), "add")
                self.directory.chmod(mode)
                with self.assertRaises(SyncError):
                    backend.commit(self.ref(incoming), incoming.encode(), "add")
                self.assertFalse(backend.mutation_started)
                self.assertFalse((self.directory / incoming.filename).exists())
                self.assertEqual(self.directory.stat().st_mode & 0o777, mode)

    def test_same_filename_different_key_requires_overwrite_or_rename(self):
        self.put(self.materials[0])
        self.backend.discover()
        incoming = self.materials[1]
        ref = self.ref(incoming)
        self.assertEqual(self.backend.compare(ref, incoming.encode()), "differing")
        with self.assertRaises(SyncError):
            self.backend.preflight(ref, incoming.encode(), "add")
        warnings = self.backend.warnings(ref, incoming.encode(), "overwrite")
        self.assertIn(self.materials[0].fingerprint, " ".join(warnings))
        self.assertIn(incoming.fingerprint, " ".join(warnings))
        self.backend.rename(ref, incoming.encode(), "imported-key")
        self.assertEqual(self.backend.compare(ref, incoming.encode()), "source-only")
        self.backend.preflight(ref, incoming.encode(), "add")
        self.backend.commit(ref, incoming.encode(), "add")
        self.assertTrue((self.directory / "id_ed25519").read_text() == self.materials[0].private,
                        "original private key changed")
        self.assertTrue((self.directory / "imported-key").read_text() == incoming.private,
                        "renamed private key mismatch")

    def test_public_only_cannot_replace_unrelated_private_key(self):
        self.put(self.materials[0])
        self.backend.discover()
        incoming = replace(self.materials[1], private=None)
        self.assertEqual(self.backend.compare(self.ref(incoming), incoming.encode()), "blocked")
        with self.assertRaises(SyncError):
            self.backend.preflight(self.ref(incoming), incoming.encode(), "overwrite")

    def test_selected_protected_key_preserves_bytes_and_private_modes(self):
        self.backend.discover()
        material = self.materials[2]
        ref = self.ref(material)
        with self.assertRaises(SyncError):
            self.backend.preflight(ref, material.encode(), "add", passphrase="wrong")
        self.assertFalse((self.directory / material.filename).exists())
        self.backend.preflight(ref, material.encode(), "add", passphrase="synthetic-passphrase")
        self.backend.commit(ref, material.encode(), "add")
        self.assertTrue((self.directory / material.filename).read_text() == material.private, "protected bytes changed")
        for path in self.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.backend.recheck()

    def test_change_after_selection_or_preflight_refuses_without_write(self):
        self.put(self.materials[0])
        self.backend.discover()
        ref, value = self.ref(self.materials[1]), self.materials[1].encode()
        self.backend.preflight(ref, value, "overwrite")
        (self.directory / "id_ed25519.pub").write_text(self.materials[1].public)
        with self.assertRaises(SyncError):
            self.backend.commit(ref, value, "overwrite")
        self.assertFalse(self.backend.mutation_started)

    def test_local_deletion_and_unselected_target_refused(self):
        self.backend.discover()
        value = self.materials[0]
        with self.assertRaises(SyncError):
            self.backend.preflight(self.ref(value), value.encode(), "remove")
        with self.assertRaises(SyncError):
            self.backend.commit(self.ref(value), value.encode(), "add")

    def test_partial_pair_write_is_uncertain_not_rolled_back(self):
        self.backend.discover()
        material = self.materials[0]
        ref = self.ref(material)
        self.backend.preflight(ref, material.encode(), "add")
        from secrets_sync import ssh
        original = ssh.SecureFile.install
        def install(target, *args, **kwargs):
            if target.path.suffix == ".pub":
                raise OSError("synthetic second-file failure")
            return original(target, *args, **kwargs)
        with patch.object(ssh.SecureFile, "install", new=install), self.assertRaises(CommittedError):
            self.backend.commit(ref, material.encode(), "add")
        self.assertTrue((self.directory / material.filename).exists())
        self.assertFalse((self.directory / (material.filename + ".pub")).exists())

    def test_symlink_and_unsafe_private_modes_are_blocked(self):
        self.put(self.materials[0])
        private = self.directory / "id_ed25519"
        private.chmod(0o644)
        self.assertEqual(self.backend.discover(), {})
        self.assertIn("id_ed25519", self.backend.issues)
        ref, value = self.ref(self.materials[1]), self.materials[1].encode()
        self.assertEqual(self.backend.compare(ref, value), "blocked")
        with self.assertRaises(SyncError):
            self.backend.preflight(ref, value, "overwrite")

    def test_duplicate_selected_target_names_refuse_whole_preflight(self):
        self.backend.discover()
        first, second = self.materials[:2]
        self.backend.preflight(self.ref(first), first.encode(), "add")
        with self.assertRaises(SyncError):
            self.backend.preflight(self.ref(second), second.encode(), "add")
        self.assertFalse(self.backend.mutation_started)

    def test_certificate_companion_blocks_software_key_portability(self):
        self.put(self.materials[0])
        (self.directory / "id_ed25519-cert.pub").write_text("unread managed certificate sentinel")
        self.assertEqual(self.backend.discover(), {})
        self.assertIn("id_ed25519", self.backend.issues)

    def test_symlink_private_key_does_not_read_or_replace_referent(self):
        target = self.root / "untouched"
        target.write_text("synthetic sentinel")
        (self.directory / "id_ed25519").symlink_to(target)
        self.assertEqual(self.backend.discover(), {})
        self.assertIn("id_ed25519", self.backend.issues)
        self.assertTrue(target.read_text() == "synthetic sentinel")


if __name__ == "__main__":
    unittest.main()
