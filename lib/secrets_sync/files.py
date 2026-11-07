"""Descriptor-anchored snapshots and one-file commit with cooperative locks."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import stat
import uuid

from .errors import CommittedError, SyncError


def safe_directory(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}:
        raise SyncError("unsafe parent directory")
    if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        raise SyncError("parent directory is writable by other users")


@dataclass(repr=False, frozen=True)
class Snapshot:
    signature: tuple | None
    content: bytes


class SecureFile:
    def __init__(self, path, limit):
        self.path = Path(path)
        self.limit = limit
        self.committed = False
        self.parents = []
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise SyncError("target must be an absolute normalized path")
        try:
            descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            self.parents.append((None, "/", descriptor, os.fstat(descriptor)))
            for name in self.path.parent.parts[1:]:
                parent = descriptor
                descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=parent)
                info = os.fstat(descriptor)
                self.parents.append((parent, name, descriptor, info))
                safe_directory(info)
            self.parent = descriptor
            self.name = self.path.name
        except BaseException:
            self.close()
            raise

    def close(self):
        for _, _, descriptor, _ in reversed(self.parents):
            os.close(descriptor)
        self.parents = []

    def check_parents(self):
        for parent, name, descriptor, expected in self.parents:
            actual = os.stat(name, dir_fd=parent, follow_symlinks=False)
            current = os.fstat(descriptor)
            if ((actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
                    or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino)):
                raise SyncError("parent directory changed during sync")
            safe_directory(current)

    def snapshot(self):
        self.check_parents()
        try:
            fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.parent)
        except FileNotFoundError:
            return Snapshot(None, b"")
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or before.st_size > self.limit):
                raise SyncError("unsafe target type, ownership, link count, or size")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(65536, self.limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > self.limit:
                    raise SyncError("input exceeds size limit")
            after = os.fstat(fd)
            attrs = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            signature = tuple(getattr(before, key) for key in attrs)
            if signature != tuple(getattr(after, key) for key in attrs):
                raise SyncError("input changed while reading")
            raw = b"".join(chunks)
            return Snapshot(signature + (hashlib.sha256(raw).digest(),), raw)
        finally:
            os.close(fd)

    def recheck(self, expected):
        if self.snapshot() != expected:
            raise SyncError("input changed during sync; restart selection")

    def install(self, content, expected, recheck):
        if len(content) > self.limit:
            raise SyncError("output exceeds size limit")
        temporary = f".{self.name.lstrip('.')}.{uuid.uuid4().hex}"
        created = committed = False
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.parent)
            created = True
            try:
                os.fchmod(fd, 0o600)
                pending = memoryview(content)
                while pending:
                    written = os.write(fd, pending[:65536])
                    if written <= 0:
                        raise SyncError("unable to write target staging")
                    pending = pending[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
            previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
            try:
                recheck()
                self.recheck(expected)
                if expected.signature is None:
                    os.link(temporary, self.name, src_dir_fd=self.parent,
                            dst_dir_fd=self.parent, follow_symlinks=False)
                    committed = True
                    self.committed = True
                    os.unlink(temporary, dir_fd=self.parent)
                else:
                    os.replace(temporary, self.name, src_dir_fd=self.parent, dst_dir_fd=self.parent)
                    committed = True
                    self.committed = True
                created = False
                try:
                    os.fsync(self.parent)
                except OSError as error:
                    if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
                        raise
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        except BaseException:
            if committed:
                raise CommittedError("target committed; completion/durability uncertain; do not blindly retry") from None
            raise
        finally:
            if created:
                os.unlink(temporary, dir_fd=self.parent)


@contextmanager
def locks(paths, directory):
    directory = Path(directory)
    # Validate before creating each component; mkdir(parents=True) would follow
    # an unsafe/symlink ancestor before SecureFile could reject it.
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in directory.parts[1:]:
            safe_directory(os.fstat(parent))
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
    finally:
        os.close(parent)
    anchored = SecureFile(directory / "anchor", 1)
    descriptors = []
    try:
        info = os.fstat(anchored.parent)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SyncError("sync lock directory must be private")
        for path in sorted({str(path) for path in paths}):
            name = hashlib.sha256(path.encode()).hexdigest() + ".lock"
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=anchored.parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                raise SyncError("unsafe sync lock file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError("another secrets sync is running") from None
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
        anchored.close()
