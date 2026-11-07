"""Bounded software SSH key encoding and selected native consistency validation.

This is container parsing, not a cryptographic implementation. Native OpenSSH
validates the private key; fingerprints use the standard public blob SHA256.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .crypto import strict_json
from .errors import SyncError
from .github_process import run

LIMIT = 65536
TYPES = {b"ssh-ed25519", b"ssh-rsa", b"ecdsa-sha2-nistp256", b"ecdsa-sha2-nistp384", b"ecdsa-sha2-nistp521"}
PROTECTED_CIPHERS = {b"aes256-ctr", b"aes192-ctr", b"aes128-ctr", b"aes256-cbc"}


def validate_basename(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
            or value in {"known_hosts", "known_hosts2", "authorized_keys", "authorized_keys2", "config", "environment", "rc"}
            or value.endswith((".pub", "-cert", ".pem"))):
        raise SyncError("SSH filename must be a safe nonreserved key basename")
    return value


class Reader:
    def __init__(self, data): self.data, self.offset = data, 0

    def integer(self):
        if len(self.data) - self.offset < 4: raise SyncError("invalid SSH key encoding")
        value = int.from_bytes(self.data[self.offset:self.offset + 4], "big")
        self.offset += 4
        return value

    def string(self):
        count = self.integer()
        if count > LIMIT or self.offset + count > len(self.data):
            raise SyncError("invalid SSH key field")
        value = self.data[self.offset:self.offset + count]
        self.offset += count
        return value

    def end(self):
        if self.offset != len(self.data): raise SyncError("trailing SSH key data")


def public_blob(blob):
    reader = Reader(blob)
    kind = reader.string()
    if kind not in TYPES: raise SyncError("unsupported software SSH key type")
    if kind == b"ssh-ed25519":
        if len(reader.string()) != 32: raise SyncError("invalid Ed25519 public key")
    elif kind == b"ssh-rsa":
        exponent, modulus = reader.string(), reader.string()
        if (not exponent or not modulus or exponent[0] & 128 or modulus[0] & 128
                or int.from_bytes(exponent, "big") <= 1 or len(modulus) < 128):
            raise SyncError("invalid RSA public key")
    else:
        curve, point = reader.string(), reader.string()
        size = {b"nistp256": 65, b"nistp384": 97, b"nistp521": 133}.get(curve)
        if kind != b"ecdsa-sha2-" + curve or size != len(point) or not point.startswith(b"\x04"):
            raise SyncError("invalid ECDSA public key")
    reader.end()
    return kind


def decode_public(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 16384:
        raise SyncError("invalid SSH public key")
    text = value.removesuffix("\n")
    if any(ord(c) < 32 or ord(c) > 126 for c in text):
        raise SyncError("invalid SSH public key text")
    parts = text.split()
    if len(parts) < 2: raise SyncError("invalid SSH public key fields")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        if public_blob(blob) != parts[0].encode("ascii"):
            raise SyncError("SSH public key type mismatch")
    except (ValueError, binascii.Error):
        raise SyncError("invalid SSH public key encoding") from None
    return blob


def private_container(value):
    if not isinstance(value, str) or not 1 <= len(value) <= LIMIT:
        raise SyncError("invalid SSH private key size")
    lines = value.splitlines()
    if (len(lines) < 3 or lines[0] != "-----BEGIN OPENSSH PRIVATE KEY-----"
            or lines[-1] != "-----END OPENSSH PRIVATE KEY-----"
            or "\r" in value or "\x00" in value):
        raise SyncError("only native OpenSSH software private keys are supported")
    try:
        raw = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (ValueError, binascii.Error):
        raise SyncError("invalid SSH private key encoding") from None
    if not raw.startswith(b"openssh-key-v1\0"):
        raise SyncError("invalid OpenSSH private key container")
    reader = Reader(raw[len(b"openssh-key-v1\0"):])
    cipher, kdf, options = reader.string(), reader.string(), reader.string()
    if cipher == b"none":
        if kdf != b"none" or options: raise SyncError("invalid unprotected SSH key container")
    else:
        if cipher not in PROTECTED_CIPHERS or kdf != b"bcrypt":
            raise SyncError("unsupported SSH key protection")
        parameters = Reader(options)
        salt, rounds = parameters.string(), parameters.integer()
        parameters.end()
        if not 1 <= len(salt) <= 1024 or not 1 <= rounds <= 1000000:
            raise SyncError("unsupported SSH key protection parameters")
    if reader.integer() != 1: raise SyncError("SSH key container must contain exactly one key")
    blob = reader.string()
    public_blob(blob)
    body = reader.string()
    if not body: raise SyncError("empty SSH private key body")
    reader.end()
    return blob, cipher != b"none"


@dataclass(frozen=True, repr=False)
class Material:
    filename: str
    public: str
    private: str | None = None

    def __post_init__(self):
        validate_basename(self.filename)
        blob = decode_public(self.public)
        if self.private is not None and private_container(self.private)[0] != blob:
            raise SyncError("SSH private/public key pair does not match")

    @property
    def fingerprint(self):
        return "SHA256:" + base64.b64encode(hashlib.sha256(decode_public(self.public)).digest()).decode().rstrip("=")

    @property
    def protected(self):
        return self.private is not None and private_container(self.private)[1]

    def encode(self):
        return json.dumps({"filename": self.filename, "public": self.public, "private": self.private},
                          sort_keys=True, separators=(",", ":"))

    @classmethod
    def decode(cls, value):
        if not isinstance(value, str) or len(value) > LIMIT * 2:
            raise SyncError("invalid SSH record")
        data = strict_json(value.encode())
        if not isinstance(data, dict) or set(data) != {"filename", "public", "private"}:
            raise SyncError("unsupported SSH record fields")
        return cls(**data)


def validate_native(material, passphrase="", *, staging_parent=None):
    if (not isinstance(passphrase, str) or len(passphrase.encode()) > 1024
            or any(c in passphrase for c in "\r\n\x00")):
        raise SyncError("invalid SSH passphrase input")
    # Selected-key validation only. macOS OpenSSH reloads protected keys, which
    # cannot use a duplicated /dev/fd offset. Use private, disposable staging;
    # never decrypt/re-encode the private bytes or stage the vault age identity.
    with tempfile.TemporaryDirectory(prefix="envy-ssh-check-", dir=staging_parent or "/tmp") as temporary:
        root = Path(temporary).resolve()
        path = root / "key"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write((material.private if material.private is not None else material.public).encode("ascii"))
        env = {"HOME": str(root), "PATH": "/usr/bin:/bin", "LANG": "C"}
        if material.private is None:
            result = run(["/usr/bin/ssh-keygen", "-l", "-E", "sha256", "-f", str(path)],
                         b"", environment=env, cwd=root, limit=16384)
            if result.status or material.fingerprint.encode() not in result.output.split():
                raise SyncError("native SSH public-key validation failed")
            return
        result = run(["/usr/bin/ssh-keygen", "-y", "-f", str(path)],
                     passphrase.encode() + b"\n", environment=env, cwd=root, limit=16384)
        if result.status:
            raise SyncError("SSH private-key validation failed; check its passphrase and pair")
        try:
            verified = decode_public(result.output.decode("ascii"))
        except (UnicodeError, SyncError):
            raise SyncError("native SSH validation returned an unsupported public key") from None
        if verified != decode_public(material.public):
            raise SyncError("native SSH private/public key verification failed")
