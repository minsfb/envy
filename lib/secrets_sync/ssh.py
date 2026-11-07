"""Explicit file-key sync; no SSH configuration, agent or remote registration."""
from __future__ import annotations

import base64
from dataclasses import replace
import os
import stat

from .errors import CommittedError, SyncError
from .files import SecureFile, Snapshot
from .github import Saved
from .inventory import Ref
from .ssh_config import check_ssh_directory
from .ssh_format import LIMIT, Material, private_container, public_blob, validate_basename, validate_native


class Backend:
    def __init__(self, config, staging_parent):
        self.config, self.staging_parent = config, staging_parent
        self.before, self.materials, self.saved, self.issues = {}, {}, {}, {}
        self.names, self.prepared = {}, {}
        self.initialized = False
        self.directory_signature = None
        self.mutation_started, self.mutation_ref = False, None

    def _directory(self):
        check_ssh_directory(self.config.directory)
        try:
            info = self.config.directory.lstat()
            return info.st_dev, info.st_ino, info.st_mode, info.st_uid
        except FileNotFoundError:
            # Creating an arbitrary hierarchy is not an SSH key merge. The
            # standard ~/.ssh case has an existing account directory.
            if not self.config.directory.parent.is_dir():
                raise SyncError("SSH directory parent must already exist") from None
            return None

    def _snapshot(self, name):
        if self._directory() != self.directory_signature:
            raise SyncError("SSH directory changed during sync")
        if self.directory_signature is None:
            return Snapshot(None, b"")
        target = SecureFile(self.config.directory / name, LIMIT)
        try:
            basename = name.removesuffix(".pub")
            try:
                os.stat(basename + "-cert.pub", dir_fd=target.parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SyncError("certificate-associated SSH keys are not portable through this provider")
            value = target.snapshot()
        finally:
            target.close()
        if value.signature is not None:
            modes = {0o600, 0o644} if name.endswith(".pub") else {0o600}
            if stat.S_IMODE(value.signature[2]) not in modes:
                raise SyncError("SSH key permissions are unsafe")
        return value

    def _inspect(self, name):
        validate_basename(name)
        snapshots = tuple(self._snapshot(name + suffix) for suffix in ("", ".pub"))
        private, public = snapshots
        value = None
        try:
            if private.signature is not None:
                secret = private.content.decode("ascii")
                if public.signature is not None:
                    visible = public.content.decode("ascii")
                else:
                    blob, _ = private_container(secret)
                    visible = public_blob(blob).decode() + " " + base64.b64encode(blob).decode() + "\n"
                value = Material(name, visible, secret)
            elif public.signature is not None:
                value = Material(name, public.content.decode("ascii"))
        except (ValueError, UnicodeError):
            raise SyncError("unsupported SSH file encoding") from None
        self.before[name], self.materials[name] = snapshots, value
        return value

    def discover(self):
        if self.initialized:
            raise SyncError("SSH discovery already initialized")
        self.directory_signature = self._directory()
        self.initialized = True
        for name in self.config.filenames:
            try:
                value = self._inspect(name)
            except (OSError, SyncError):
                self.issues[name] = "unsafe, unsupported or unreadable SSH key files"
                continue
            if value is None:
                continue
            ref = Ref("ssh", name=value.fingerprint)
            if ref in self.saved:
                self.saved[ref] = Saved("blocked", reason="same fingerprint has multiple configured filenames")
            else:
                self.saved[ref] = Saved("available", value.encode())
        return dict(self.saved)

    def recheck(self):
        if not self.initialized or self._directory() != self.directory_signature:
            raise SyncError("SSH directory changed or discovery missing")
        for name, expected in self.before.items():
            actual = tuple(self._snapshot(name + suffix) for suffix in ("", ".pub"))
            if actual != expected:
                raise SyncError("SSH key files changed during sync; restart selection")

    def incoming(self, ref, value):
        material = Material.decode(value)
        if ref.kind != "ssh" or ref.name != material.fingerprint:
            raise SyncError("SSH identity mismatch")
        return replace(material, filename=self.names.get(ref, material.filename))

    def _target(self, ref, value):
        material = self.incoming(ref, value)
        name = material.filename
        if name not in self.config.filenames and self.names.get(ref) != name:
            raise SyncError("SSH filename not configured; choose an alternate name explicitly")
        if name in self.issues:
            raise SyncError("SSH target files are unsafe or unsupported")
        if name not in self.before:
            self._inspect(name)
        return material, self.materials[name]

    def compare(self, ref, value):
        try:
            material, current = self._target(ref, value)
        except (OSError, SyncError):
            return "blocked"
        if current is None:
            return "source-only"
        if current.private is not None and material.private is None and current.fingerprint != material.fingerprint:
            return "blocked"  # Never leave a different private/public pair.
        if material == current or (material.private is None and material.public == current.public):
            return "identical"
        return "differing"

    def destination(self, ref, value):
        material = self.incoming(ref, value)
        path = self.config.directory / material.filename
        return f"{path} + {path}.pub" if material.private is not None else str(path) + ".pub"

    def rename(self, ref, value, name):
        validate_basename(name)
        self.recheck()
        if name in self.issues:
            raise SyncError("alternate SSH target is unsafe")
        if name not in self.before:
            self._inspect(name)
        self.names[ref] = name
        self.prepared.clear()

    def warnings(self, ref, value, action):
        material, current = self._target(ref, value)
        warnings = [f"SSH {material.fingerprint}: {self.destination(ref, value)}"]
        if material.private is not None:
            warnings.append("Includes PRIVATE key; protection: " + ("passphrase retained" if material.protected else "UNPROTECTED"))
        if current is not None and current.fingerprint != material.fingerprint:
            warnings.append(f"REPLACES A DIFFERENT KEY: {current.fingerprint} -> {material.fingerprint}. "
                            "Keep it or use name ID BASENAME to avoid replacing this key.")
        return warnings

    def _eligible(self, ref, value, action):
        state = self.compare(ref, value)
        if (action, state) not in {("add", "source-only"), ("overwrite", "differing")}:
            raise SyncError("SSH action does not match destination; local deletion is unsupported")
        return self._target(ref, value)[0]

    def preflight(self, ref, value, action, *, passphrase=""):
        self.recheck()
        material = self._eligible(ref, value, action)
        if any(other != ref and prepared[0].filename == material.filename for other, prepared in self.prepared.items()):
            raise SyncError("selected SSH keys target the same filenames")
        validate_native(material, passphrase, staging_parent=self.staging_parent)
        self.recheck()
        self.prepared[ref] = material, value, action

    def commit(self, ref, value, action):
        self.recheck()
        material = self._eligible(ref, value, action)
        if self.prepared.get(ref) != (material, value, action):
            raise SyncError("SSH write requires selected-key preflight")
        self.mutation_ref, self.mutation_started = ref, True
        try:
            if self.directory_signature is None:
                # Anchor creation at the existing safe parent, not a symlink.
                anchor = SecureFile(self.config.directory, 1)
                try:
                    anchor.check_parents()
                    os.mkdir(anchor.name, 0o700, dir_fd=anchor.parent)
                finally:
                    anchor.close()
                self.directory_signature = self._directory()
            for index, (suffix, content) in enumerate((("", material.private), (".pub", material.public))):
                if content is None:
                    continue
                target = SecureFile(self.config.directory / (material.filename + suffix), LIMIT)
                try:
                    target.install(content.encode("ascii"), self.before[material.filename][index], self.recheck)
                    updated = list(self.before[material.filename])
                    observed = target.snapshot()
                    if observed.content != content.encode("ascii"):
                        raise SyncError("SSH write verification failed")
                    updated[index] = observed
                    self.before[material.filename] = tuple(updated)
                finally:
                    target.close()
            previous = self.materials[material.filename]
            self.materials[material.filename] = (replace(material, private=previous.private)
                                                 if material.private is None and previous else material)
            del self.prepared[ref]
            self.recheck()
        except BaseException:
            raise CommittedError("SSH pair write may be partial; inspect both paths before retrying") from None
