"""SOPS/age boundary: anonymous key pipes, bounded IO, no ambient providers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import subprocess
import tempfile
import time

from .errors import SyncError

MAX_PLAIN = 16 * 1024 * 1024
MAX_CIPHER = 32 * 1024 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
RECIPIENT = re.compile(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}\Z")


def strict_json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SyncError("duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(SyncError("invalid JSON constant")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SyncError("invalid JSON document") from None


def validate_entries(entries):
    if not isinstance(entries, dict) or len(entries) > 4096:
        raise SyncError("invalid entry map")
    for name, value in entries.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise SyncError("invalid entry name")
        if not isinstance(value, str):
            raise SyncError("entry value must be a string")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            raise SyncError("invalid entry encoding") from None
        if size > 65536 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            raise SyncError("unsupported entry value")
    return entries


def encode_payload(entries):
    validate_entries(entries)
    result = json.dumps({"version": 1, "entries": entries}, sort_keys=True,
                        ensure_ascii=True, separators=(",", ":")).encode()
    if len(result) > MAX_PLAIN:
        raise SyncError("payload exceeds size limit")
    return result


def decode_payload(raw):
    if len(raw) > MAX_PLAIN:
        raise SyncError("payload exceeds size limit")
    data = strict_json(raw)
    if (not isinstance(data, dict) or set(data) != {"version", "entries"}
            or type(data["version"]) is not int or data["version"] != 1):
        raise SyncError("unsupported payload schema")
    return validate_entries(data["entries"])


def envelope(raw, recipient=None):
    if len(raw) > MAX_CIPHER:
        raise SyncError("ciphertext exceeds size limit")
    data = strict_json(raw)
    if not isinstance(data, dict) or set(data) != {"data", "sops"}:
        raise SyncError("unsupported ciphertext envelope")
    metadata = data["sops"]
    allowed = {"age", "kms", "gcp_kms", "azure_kv", "hc_vault", "pgp", "lastmodified",
               "mac", "version", "unencrypted_suffix"}
    if not isinstance(metadata, dict) or set(metadata) - allowed:
        raise SyncError("unsupported encryption metadata")
    if any(metadata.get(key) for key in ("kms", "gcp_kms", "azure_kv", "hc_vault", "pgp")):
        raise SyncError("alternate key providers are not permitted")
    if metadata.get("version") != "3.10.2":
        raise SyncError("unsupported SOPS document version (requires 3.10.2)")
    age = metadata.get("age")
    if (not isinstance(age, list) or len(age) != 1 or not isinstance(age[0], dict)
            or set(age[0]) != {"recipient", "enc"}
            or not isinstance(age[0]["recipient"], str)
            or not RECIPIENT.fullmatch(age[0]["recipient"])
            or not isinstance(age[0]["enc"], str)):
        raise SyncError("exactly one native age recipient is required")
    if recipient is not None and age[0]["recipient"] != recipient:
        raise SyncError("entered identity does not match recipient")
    for value in (data["data"], metadata.get("mac")):
        if not isinstance(value, str) or not value.startswith("ENC[AES256_GCM,"):
            raise SyncError("authenticated ciphertext is required")
    return age[0]["recipient"]


def run_child(argv, payload, *, environment, cwd, identity=None,
              timeout=30.0, limit=MAX_CIPHER):
    """Drain both output pipes while feeding stdin; kill/reap on every failure."""
    descriptors = ()
    read_key = None
    process = None
    selector = selectors.DefaultSelector()
    try:
        if identity is not None:
            key = identity.encode("ascii") + b"\n"
            if len(key) > 257:
                raise SyncError("invalid age identity")
            read_key, write_key = os.pipe()
            try:
                os.write(write_key, key)  # bounded below PIPE_BUF
            finally:
                os.close(write_key)
            descriptors = (read_key,)
            environment = {**environment, "SOPS_AGE_KEY_FILE": f"/dev/fd/{read_key}"}
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, cwd=cwd,
                                   pass_fds=descriptors, close_fds=True)
        if read_key is not None:
            os.close(read_key)
            read_key = None
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        view = memoryview(payload)
        output, errors = bytearray(), bytearray()
        if view:
            selector.register(process.stdin, selectors.EVENT_WRITE, "in")
        else:
            process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SyncError("crypto operation timed out")
            for event, _ in selector.select(min(remaining, 0.1)):
                if event.data == "in":
                    try:
                        count = os.write(event.fd, view[:4096])
                        view = view[count:]
                    except BrokenPipeError:
                        view = view[:0]
                    if not view:
                        selector.unregister(event.fileobj)
                        event.fileobj.close()
                else:
                    chunk = os.read(event.fd, 65536)
                    if not chunk:
                        selector.unregister(event.fileobj)
                        event.fileobj.close()
                    else:
                        event.data.extend(chunk)
                        bound = limit if event.data is output else 65536
                        if len(event.data) > bound:
                            raise SyncError("crypto output exceeds size limit")
        try:
            status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SyncError("crypto operation timed out") from None
        if status:
            raise SyncError("crypto operation failed; diagnostic output suppressed")
        return bytes(output)
    except OSError:
        raise SyncError("unable to execute crypto tool") from None
    finally:
        selector.close()
        if read_key is not None:
            os.close(read_key)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()


def resolve_executable(name):
    candidate = shutil.which(name)
    if candidate and "shims" not in Path(candidate).parts:
        return str(Path(candidate).resolve())
    mise = shutil.which("mise")
    if not mise:
        raise SyncError("sops and age-keygen must already be installed")
    # Resolve installed tools before unlocking, without installing or inheriting
    # credential providers. A mise shim cannot run inside crypto's empty HOME.
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "MISE_OFFLINE": "1",
           "MISE_AUTO_INSTALL": "0", "MISE_YES": "0"}
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "MISE_DATA_DIR", "MISE_CONFIG_DIR",
                "MISE_GLOBAL_CONFIG_FILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    raw = run_child([mise, "which", name], b"", environment=env, cwd=Path.home(), limit=4096)
    try:
        path = Path(raw.decode("utf-8").strip())
    except UnicodeError:
        raise SyncError("unable to resolve installed crypto tool") from None
    if (not path.is_absolute() or any(ord(c) < 32 for c in str(path))
            or "shims" in path.parts or not path.is_file() or not os.access(path, os.X_OK)):
        raise SyncError("unable to resolve installed crypto tool")
    return str(path.resolve())


class Crypto:
    def __init__(self, sops=None, age_keygen=None):
        self.sops = sops or resolve_executable("sops")
        self.age_keygen = age_keygen or resolve_executable("age-keygen")
        if not self.sops or not self.age_keygen:
            raise SyncError("sops and age-keygen must already be installed")
        self.sops = str(Path(self.sops).resolve())
        self.age_keygen = str(Path(self.age_keygen).resolve())

    def call(self, argv, payload=b"", identity=None, limit=MAX_CIPHER):
        # This affects only this transaction process and its children.
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
        with tempfile.TemporaryDirectory(prefix="envy-secrets-crypto-") as isolated:
            env = {"HOME": isolated, "XDG_CONFIG_HOME": isolated, "TMPDIR": isolated,
                   "PATH": "/usr/bin:/bin", "LANG": "C", "SOPS_DISABLE_VERSION_CHECK": "1"}
            return run_child(argv, payload, environment=env, cwd=isolated,
                             identity=identity, limit=limit)

    def recipient(self, identity):
        if (not isinstance(identity, str) or len(identity) > 256
                or not re.fullmatch(r"AGE-SECRET-KEY-[A-Z0-9]+", identity)):
            raise SyncError("invalid native age identity")
        raw = self.call([self.age_keygen, "-y"], identity.encode() + b"\n", limit=1024)
        try:
            result = raw.decode("ascii").strip()
        except UnicodeError:
            raise SyncError("invalid age recipient") from None
        if not RECIPIENT.fullmatch(result):
            raise SyncError("invalid age recipient")
        return result

    def decrypt(self, raw, identity, recipient):
        return decode_payload(self.decrypt_bytes(raw, identity, recipient))

    def decrypt_bytes(self, raw, identity, recipient):
        envelope(raw, recipient)
        plaintext = self.call([self.sops, "--decrypt", "--input-type", "json",
                               "--output-type", "binary", "/dev/stdin"],
                              raw, identity=identity, limit=MAX_PLAIN)
        return plaintext

    def decrypt_inventory(self, raw, identity, recipient):
        from .inventory import decode_inventory
        return decode_inventory(self.decrypt_bytes(raw, identity, recipient))

    def encrypt_inventory(self, inventory, identity, recipient):
        from .inventory import encode_inventory
        plain = encode_inventory(inventory)
        raw = self.call([self.sops, "--encrypt", "--age", recipient,
                         "--input-type", "binary", "--output-type", "json", "/dev/stdin"], plain)
        if self.decrypt_inventory(raw, identity, recipient) != inventory:
            raise SyncError("ciphertext round-trip verification failed")
        return raw

    def encrypt(self, entries, identity, recipient):
        plain = encode_payload(entries)
        raw = self.call([self.sops, "--encrypt", "--age", recipient,
                         "--input-type", "binary", "--output-type", "json", "/dev/stdin"], plain)
        if self.decrypt(raw, identity, recipient) != entries:
            raise SyncError("ciphertext round-trip verification failed")
        return raw
