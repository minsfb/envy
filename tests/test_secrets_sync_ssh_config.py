"""Public SSH policy validation, without reading any key file."""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.errors import SyncError
from secrets_sync.ssh_config import parse_ssh


class SSHConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="envy-ssh-policy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "ssh"
        self.repository = self.root / "repo"
        self.local = self.root / "local.env"
        self.raw = {"directory": str(self.directory), "filenames": ["id_ed25519", "id_rsa"]}

    def parse(self, raw=None, protected=()):
        return parse_ssh(self.raw if raw is None else raw, repository=self.repository,
                         local=self.local, protected=protected)

    def test_missing_directory_stays_absent_and_names_are_sorted(self):
        value = self.parse()
        self.assertEqual(value.directory, self.directory)
        self.assertEqual(value.filenames, ("id_ed25519", "id_rsa"))
        self.assertFalse(self.directory.exists())

    def test_empty_names_permits_no_implicit_discovery(self):
        self.assertEqual(self.parse({**self.raw, "filenames": []}).filenames, ())

    def test_bad_names_duplicates_and_unknown_options_refuse(self):
        for raw in ({**self.raw, "filenames": ["../key"]},
                    {**self.raw, "filenames": ["key", "key"]},
                    {**self.raw, "filenames": "id_rsa"},
                    {**self.raw, "filenames": ["config"]},
                    {**self.raw, "filenames": ["key" + str(i) for i in range(33)]},
                    {**self.raw, "executable": "arbitrary"}):
            with self.subTest(), self.assertRaises(SyncError):
                self.parse(raw)

    def test_safe_existing_permissions_are_preserved(self):
        self.directory.mkdir()
        for mode in (0o700, 0o710, 0o750, 0o755):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(mode)
                before = self.directory.stat()
                self.parse()
                after = self.directory.stat()
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_unsafe_write_permissions_refuse_without_repair(self):
        self.directory.mkdir()
        for mode in (0o702, 0o720, 0o770, 0o775, 0o777):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(mode)
                before = self.directory.stat()
                with self.assertRaises(SyncError):
                    self.parse()
                self.assertEqual(self.directory.stat().st_mode, before.st_mode)
                self.assertEqual(self.directory.stat().st_ctime_ns, before.st_ctime_ns)

    def test_final_directory_must_be_owned_by_current_user(self):
        self.directory.mkdir(mode=0o755)
        original = Path.lstat
        def metadata(path):
            info = original(path)
            if path == self.directory:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0 if os.getuid() else 1)
            return info
        with patch.object(Path, "lstat", metadata), self.assertRaises(SyncError):
            self.parse()

    def test_symlink_directory_refuses_without_changing_referent(self):
        self.directory.mkdir(mode=0o755)
        before = self.directory.stat()
        linked = self.root / "linked"
        linked.symlink_to(self.directory)
        with self.assertRaises(SyncError):
            self.parse({**self.raw, "directory": str(linked)})
        self.assertEqual(self.directory.stat().st_mode, before.st_mode)
        self.assertEqual(self.directory.stat().st_ctime_ns, before.st_ctime_ns)

    def test_overlapping_and_protected_destinations_refuse(self):
        for path in (self.repository, self.local, self.root):
            with self.subTest(), self.assertRaises(SyncError):
                self.parse({**self.raw, "directory": str(path)})
        with self.assertRaises(SyncError):
            self.parse(protected=(self.directory / "recovery.key",))


if __name__ == "__main__":
    unittest.main()
