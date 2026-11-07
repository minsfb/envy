"""Bounded, private native credential subprocess boundary."""
from __future__ import annotations

from dataclasses import dataclass
import os
import resource
import selectors
import signal
import subprocess
import time

from .errors import SyncError


@dataclass(frozen=True, repr=False)
class Result:
    status: int
    output: bytes


def run(argv, payload, *, environment, cwd, timeout=30, limit=1024 * 1024):
    process = None
    selector = selectors.DefaultSelector()
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, cwd=cwd,
                                   close_fds=True, start_new_session=True, umask=0o077)
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        pending = memoryview(payload)
        if pending:
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
        else:
            process.stdin.close()
        output, errors = bytearray(), bytearray()
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SyncError("GitHub operation timed out")
            for event, _ in selector.select(min(remaining, 0.1)):
                if event.data == "input":
                    try:
                        count = os.write(event.fd, pending[:4096])
                        pending = pending[count:]
                    except BrokenPipeError:
                        pending = pending[:0]
                    if not pending:
                        selector.unregister(event.fileobj)
                        event.fileobj.close()
                else:
                    chunk = os.read(event.fd, 65536)
                    if not chunk:
                        selector.unregister(event.fileobj)
                        event.fileobj.close()
                    else:
                        event.data.extend(chunk)
                        if len(event.data) > (limit if event.data is output else 65536):
                            raise SyncError("GitHub child output exceeds limit")
        try:
            status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SyncError("GitHub operation timed out") from None
        return Result(status, bytes(output))
    except OSError:
        raise SyncError("unable to execute GitHub operation") from None
    finally:
        selector.close()
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise SyncError("GitHub child termination unverified") from None
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
