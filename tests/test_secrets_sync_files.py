import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secrets_sync.files import SecureFile, locks
from secrets_sync.errors import SyncError, CommittedError


class FilesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / ".secrets"
        self.path.write_bytes(b"synthetic-before")
        self.path.chmod(0o600)
        self.file = SecureFile(self.path, 100)
        self.addCleanup(self.file.close)

    def test_replacement_mode_and_stale_detection(self):
        before = self.file.snapshot()
        self.file.install(b"synthetic-after", before, lambda: self.file.recheck(before))
        self.assertTrue(self.path.read_bytes() == b"synthetic-after")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(SyncError):
            self.file.recheck(before)

    def test_setup_is_no_clobber(self):
        self.path.unlink()
        before = self.file.snapshot()
        self.path.write_bytes(b"external-change")
        with self.assertRaises(SyncError):
            self.file.install(b"candidate", before, lambda: None)
        self.assertTrue(self.path.read_bytes() == b"external-change")

    def test_unsafe_types_never_follow_or_block(self):
        for kind in ("symlink", "fifo", "directory", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as folder:
                target = Path(folder) / "target"
                if kind == "symlink":
                    target.symlink_to(self.path)
                elif kind == "fifo":
                    os.mkfifo(target)
                elif kind == "hardlink":
                    os.link(self.path, target)
                else:
                    target.mkdir()
                opened = SecureFile(target, 100)
                try:
                    with self.assertRaises((SyncError, OSError)):
                        opened.snapshot()
                finally:
                    opened.close()

    def test_failed_write_leaves_no_staging(self):
        before = self.file.snapshot()
        with mock.patch("secrets_sync.files.os.fsync", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.file.install(b"candidate", before, lambda: None)
        self.assertTrue(self.file.snapshot() == before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_post_commit_failure_is_not_reported_as_rollback(self):
        before = self.file.snapshot()
        original = os.fsync
        def fail_directory(fd):
            if fd == self.file.parent:
                raise OSError(5, "synthetic IO error")
            original(fd)
        with mock.patch("secrets_sync.files.os.fsync", side_effect=fail_directory):
            with self.assertRaises(CommittedError):
                self.file.install(b"candidate", before, lambda: None)
        self.assertTrue(self.path.read_bytes() == b"candidate")

    def test_cooperative_lock(self):
        lock_dir = self.root / "locks"
        with locks([self.path], lock_dir):
            with self.assertRaises(SyncError):
                with locks([self.path], lock_dir):
                    pass

    def test_lock_directory_rejects_symlink_before_creating_children(self):
        outside = self.root / "outside"
        outside.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(outside)
        with self.assertRaises(OSError):
            with locks([self.path], alias / "must-not-create"):
                pass
        self.assertFalse((outside / "must-not-create").exists())
