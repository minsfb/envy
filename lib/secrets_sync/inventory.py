"""Strict typed inventory; values never participate in representations or errors.

This module has no credential, filesystem, network, or terminal access. Local
credential discovery is a separate, explicitly authorized provider operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import re
from types import MappingProxyType
from typing import Mapping

from .crypto import MAX_PLAIN, NAME, strict_json, validate_entries
from .errors import SyncError
from .model import classify, validate_action
from .ssh_format import Material

TOKEN_OVERRIDES = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                             "GITHUB_ENTERPRISE_TOKEN"})
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
ACCOUNT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\Z")


def validate_host_account(host, account):
    if (not isinstance(host, str) or len(host) > 253
            or not all(HOST_LABEL.fullmatch(label) for label in host.split("."))):
        raise SyncError("invalid canonical GitHub host")
    if not isinstance(account, str) or not ACCOUNT.fullmatch(account):
        raise SyncError("invalid canonical GitHub account")


@dataclass(frozen=True, order=True)
class Ref:
    kind: str
    name: str = ""
    host: str = ""
    account: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, str):
            raise SyncError("invalid credential type")
        if self.kind == "environment":
            if (not isinstance(self.name, str) or not NAME.fullmatch(self.name)
                    or self.host != "" or self.account != ""):
                raise SyncError("invalid environment identity")
        elif self.kind in {"github", "git"}:
            if self.name != "":
                raise SyncError("invalid GitHub identity")
            validate_host_account(self.host, self.account)
        elif self.kind == "ssh":
            if (self.host != "" or self.account != "" or not isinstance(self.name, str)
                    or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", self.name)):
                raise SyncError("invalid SSH fingerprint identity")
            raw = base64.b64decode(self.name[7:] + "=", validate=True)
            if base64.b64encode(raw).decode().rstrip("=") != self.name[7:]:
                raise SyncError("noncanonical SSH fingerprint identity")
        else:
            raise SyncError("unsupported credential type")

    @property
    def label(self):
        if self.kind == "environment":
            return f"env:{self.name}"
        if self.kind == "ssh":
            return f"ssh:{self.name}"
        return f"{'gh' if self.kind == 'github' else 'git'}:{self.host}/{self.account}"


def validate_value(ref, value):
    if not isinstance(ref, Ref):
        raise SyncError("invalid typed identity")
    if ref.kind == "environment":
        validate_entries({ref.name: value})
    elif ref.kind == "ssh":
        if Material.decode(value).fingerprint != ref.name:
            raise SyncError("SSH record fingerprint does not match key material")
    elif (not isinstance(value, str) or not 1 <= len(value) <= 4096
          or any(not 33 <= ord(char) <= 126 for char in value)):
        raise SyncError("invalid GitHub credential encoding")


@dataclass(frozen=True, repr=False)
class Inventory:
    entries: Mapping[Ref, str]
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version not in (1, 2, 3):
            raise SyncError("unsupported payload schema")
        if not isinstance(self.entries, Mapping) or len(self.entries) > 4096:
            raise SyncError("invalid typed inventory")
        copied = dict(self.entries)
        for ref, value in copied.items():
            validate_value(ref, value)
        if self.version == 1 and any(ref.kind != "environment" for ref in copied):
            raise SyncError("typed credentials require version 2")
        if self.version < 3 and any(ref.kind in {"git", "ssh"} for ref in copied):
            raise SyncError("Git and SSH credentials require version 3")
        object.__setattr__(self, "entries", MappingProxyType(copied))

    @classmethod
    def environment(cls, values):
        validate_entries(values)
        return cls({Ref("environment", name=name): value for name, value in values.items()})

    def blocked(self, accounts=(), names=(), github_enabled=False, *, git_accounts=(), ssh_names=()):
        """Policy only: never infer which saved logins exist from configuration."""
        eligible = set(accounts)
        excluded = set(names) | (TOKEN_OVERRIDES if github_enabled else set())
        return {ref for ref in self.entries
                if (ref.kind == "github" and (ref.host, ref.account) not in eligible)
                or (ref.kind == "git" and (ref.host, ref.account) not in git_accounts)
                or (ref.kind == "ssh" and Material.decode(self.entries[ref]).filename not in ssh_names)
                or (ref.kind == "environment" and ref.name in excluded)}

    def reconcile(self, source, actions, *, blocked=(), identity=None):
        """Validate all actions, then copy destination; absence never deletes.

        The caller supplies blocked state from both discovery and configuration.
        No-op returns this immutable inventory so callers can avoid re-encryption.
        """
        rows = classify(source.entries, self.entries, blocked)
        for ref, action in actions.items():
            if ref not in rows:
                raise SyncError("selected entry is unavailable")
            validate_action(rows[ref], action)
            if action in {"add", "overwrite"} and identity and identity in source.entries[ref]:
                raise SyncError("unlocking identity cannot be synchronized")
        if all(action == "keep" for action in actions.values()):
            return self
        result = dict(self.entries)
        for ref, action in actions.items():
            if action in {"add", "overwrite"}:
                result[ref] = source.entries[ref]
            elif action == "remove":
                del result[ref]
        required = (3 if any(r.kind in {"git", "ssh"} for r in result) else
                    2 if any(r.kind == "github" for r in result) else 1)
        version = max(self.version, required)
        candidate = Inventory(result, version)
        encode_inventory(candidate)  # enforce aggregate serialized bound before committing
        return candidate


def decode_inventory(raw):
    if len(raw) > MAX_PLAIN:
        raise SyncError("payload exceeds size limit")
    data = strict_json(raw)
    if (not isinstance(data, dict) or set(data) != {"version", "entries"}
            or type(data["version"]) is not int or data["version"] not in (1, 2, 3)):
        raise SyncError("unsupported payload schema")
    if data["version"] == 1:
        return Inventory.environment(data["entries"])
    records = data["entries"]
    if not isinstance(records, list) or len(records) > 4096:
        raise SyncError("invalid typed inventory")
    entries = {}
    for record in records:
        if not isinstance(record, dict):
            raise SyncError("invalid credential record")
        kind = record.get("type")
        if kind == "environment" and set(record) == {"type", "name", "value"}:
            ref, value = Ref(kind, name=record["name"]), record["value"]
        elif kind == "github" and set(record) == {"type", "host", "account", "token"}:
            ref, value = Ref(kind, host=record["host"], account=record["account"]), record["token"]
        elif data["version"] == 3 and kind == "git" and set(record) == {"type", "host", "account", "password"}:
            ref, value = Ref(kind, host=record["host"], account=record["account"]), record["password"]
        elif data["version"] == 3 and kind == "ssh" and set(record) == {"type", "fingerprint", "filename", "public", "private"}:
            ref = Ref(kind, name=record["fingerprint"])
            value = Material(record["filename"], record["public"], record["private"]).encode()
        else:
            raise SyncError("unsupported credential record schema")
        if ref in entries:
            raise SyncError("duplicate credential identity")
        validate_value(ref, value)
        entries[ref] = value
    return Inventory(entries, data["version"])


def encode_inventory(inventory):
    # Revalidate inputs even when used without the decoder.
    inventory = Inventory(inventory.entries, inventory.version)
    if inventory.version == 1:
        entries = {ref.name: value for ref, value in inventory.entries.items()}
    else:
        entries = []
        for ref, value in sorted(inventory.entries.items()):
            if ref.kind == "environment":
                record = {"type": ref.kind, "name": ref.name, "value": value}
            elif ref.kind == "ssh":
                material = Material.decode(value)
                record = {"type": ref.kind, "fingerprint": ref.name, "filename": material.filename,
                          "public": material.public, "private": material.private}
            else:
                record = {"type": ref.kind, "host": ref.host, "account": ref.account,
                          "token" if ref.kind == "github" else "password": value}
            entries.append(record)
    raw = json.dumps({"version": inventory.version, "entries": entries},
                     sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    if len(raw) > MAX_PLAIN:
        raise SyncError("payload exceeds size limit")
    return raw
