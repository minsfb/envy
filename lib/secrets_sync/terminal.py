"""Controlling-TTY interaction, without getpass's insecure fallback behavior."""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import signal
import termios

from .errors import Cancelled, SyncError
from .ui import (SelectionError, select_actions, selection_view, help_view, counts,
                 color_enabled, styled, updates_view, pending_view)


class Terminal:
    def __init__(self):
        self.fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            if any(not os.isatty(fd) for fd in (0, 1, 2)):
                raise SyncError("interactive controlling terminal is required; no redirection")
            identity = os.fstat(0).st_rdev
            if any(os.fstat(fd).st_rdev != identity for fd in (1, 2)):
                raise SyncError("input and output must share a terminal")
            # /dev/tty has a different device number; compare foreground groups.
            if os.tcgetpgrp(self.fd) != os.getpgrp() or os.tcgetpgrp(0) != os.getpgrp():
                raise SyncError("foreground controlling terminal is required")
            self.color = color_enabled(os.environ)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def emit(self, value):
        pending = memoryview(value.encode("utf-8"))
        while pending:
            pending = pending[os.write(self.fd, pending):]

    def line(self, prompt, limit=256, flush=False, *, strip=True, encoding="ascii"):
        if flush:
            termios.tcflush(self.fd, termios.TCIFLUSH)
        self.emit(prompt)
        result = bytearray()
        while True:
            chunk = os.read(self.fd, 1)
            if not chunk:
                raise Cancelled("cancelled; no target committed")
            if chunk == b"\n":
                break
            result.extend(chunk)
            if len(result) > limit:
                termios.tcflush(self.fd, termios.TCIFLUSH)
                raise SyncError("terminal input exceeds limit")
        try:
            value = result.decode(encoding)
            return value.strip() if strip else value
        except UnicodeError:
            raise SyncError("terminal input must be ASCII") from None

    def identity(self):
        self.emit("Paste one AGE-SECRET-KEY-... line from your password manager.\n"
                  "Use the private age identity, not a password or public age1... recipient.\n"
                  "Input is hidden; the identity is not saved.\n")
        return self.hidden("Age identity (hidden): ", strip=True)

    def hidden(self, prompt, *, strip=False, limit=1024, encoding="ascii"):
        previous = termios.tcgetattr(self.fd)
        hidden = list(previous)
        hidden[3] &= ~(termios.ECHO | termios.ECHONL)
        try:
            termios.tcsetattr(self.fd, termios.TCSAFLUSH, hidden)
            if termios.tcgetattr(self.fd)[3] & (termios.ECHO | termios.ECHONL):
                raise SyncError("could not disable terminal echo")
            return self.line(prompt, limit, strip=strip, encoding=encoding)
        finally:
            try:
                termios.tcsetattr(self.fd, termios.TCSAFLUSH, previous)
                self.emit("\n")
            except OSError:
                raise SyncError("terminal restoration failed; run stty sane in your terminal") from None

    def selection(self, rows, operation, source, target, reasons=None, actions=None, *, destinations=None, permitted=None, rename=None):
        def show_all():
            try:
                width = os.get_terminal_size(self.fd).columns or 80
            except OSError:
                width = 80
            self.emit(selection_view(rows, actions, operation, source, target, reasons, width, self.color, destinations))
            if rename:
                self.emit("SSH alternate filename: name ID BASENAME (resets that selection).\n")

        if actions is None:
            actions = {}
            show_all()
        while True:
            value = self.line("Select > ")
            if value == "cancel":
                raise Cancelled("cancelled; no target committed")
            if value in {"help", "?"}:
                self.emit(help_view(operation, color=self.color))
                if rename:
                    self.emit("  name ID BASENAME  Choose an SSH target filename, then select add/overwrite.\n\n")
                continue
            if value == "list":
                show_all()
                continue
            if value == "":
                continue
            if value == "apply":
                return actions
            if value == "name" or value.startswith("name "):
                match = re.fullmatch(r"name +([0-9]+) +([A-Za-z0-9][A-Za-z0-9_.-]{0,127})", value)
                try:
                    if not rename or not match or not 1 <= int(match[1]) <= len(rows):
                        raise SyncError("Use name ID BASENAME for an incoming SSH key only")
                    label = list(rows)[int(match[1]) - 1]
                    rename(label, match[2])
                except (OSError, SyncError):
                    self.emit("\nError: SSH filename unavailable or unsafe. Selection unchanged.\n\n")
                    continue
                actions = {key: action for key, action in actions.items() if key != label}
                self.emit(f"\n{label} -> {destinations[label]}\nSelection reset to Keep; choose add or overwrite.\n\n")
                continue
            try:
                candidate = select_actions(value, rows, actions, reasons, permitted)
            except SelectionError as error:
                self.emit("\n" + styled(f"Error: {error}", "error", self.color)
                          + "\nSelection unchanged.\n\n")
                continue
            self.emit(updates_view(rows, actions, candidate, operation, self.color))
            actions = candidate

    def confirm(self, operation, target, recipient, actions, *, destinations=None, warnings=()):
        self.emit(f"\nREVIEW {operation.upper()}\nTarget: {target}\nRecipient: {recipient}\n\n")
        for name, action in sorted(actions.items()):
            self.emit(styled(f"  {action:<9}  {name}", action, self.color) + "\n")
            if destinations is not None:
                self.emit(f"    Destination: {destinations[name]}\n")
        for warning in warnings:
            self.emit(f"Warning: {warning}\n")
        self.emit(f"\nPending: {counts(actions)}\nAll other entries stay unchanged. Nothing has been written.\n"
                  "y/yes commits; n/Enter returns to selection; help/? or cancel.\n")
        while True:
            value = self.line("Proceed? [y/N] > ", flush=True).lower()
            if value in {"help", "?"}:
                self.emit(help_view(operation, confirmation=True, color=self.color))
                continue
            if value in {"y", "yes"}:
                return True
            if value in {"", "n", "no"}:
                termios.tcflush(self.fd, termios.TCIFLUSH)
                self.emit("\nBack to selection; choices preserved.\n" + pending_view(actions) + "\n")
                return False
            if value == "cancel":
                raise Cancelled("cancelled; no target committed")
            self.emit("\n" + styled("Error: Enter y/yes, n/Enter, help/? or cancel.", "error", self.color)
                      + "\nNothing written.\n\n")


@contextmanager
def signals():
    def cancel(signum, frame):
        raise Cancelled("cancelled; no target committed")
    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
