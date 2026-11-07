"""Read the bounded gh block-mapping subset; never use token scalars as sources.

Opaque token scalars are retained only for unselected-state comparisons. This is
not a YAML editor. Unsupported YAML is refused before any native gh invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .errors import SyncError
from .inventory import validate_host_account

MAX_CONFIG = 2 * 1024 * 1024


@dataclass(frozen=True, repr=False)
class Scalar:
    raw: str

    def public(self):
        value = self.raw
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                raise SyncError("unsupported gh quoted scalar") from None
        elif value.startswith("'"):
            if not re.fullmatch(r"'(?:[^']|'')*'", value):
                raise SyncError("unsupported gh quoted scalar")
            value = value[1:-1].replace("''", "'")
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) >= 127 for c in value):
            raise SyncError("unsupported gh scalar encoding")
        return value


def block_mapping(raw):
    if len(raw) > MAX_CONFIG:
        raise SyncError("gh configuration exceeds size limit")
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        raise SyncError("unsupported gh configuration encoding") from None
    if any(ord(c) < 32 and c != "\n" or ord(c) == 127 for c in text):
        raise SyncError("unsupported gh configuration controls")
    root = {}
    stack = [(-1, root)]
    previous_indent, previous_value = -1, root
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        # Bound nesting independently of file length; no recursive YAML parser.
        if indent > 32 or indent % 2:
            raise SyncError("unsupported gh mapping indentation")
        match = re.fullmatch(r'''((?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[A-Za-z0-9_.-]+)):(?: +(.*))?''', line[indent:])
        if not match:
            raise SyncError("unsupported gh mapping syntax")
        if indent > previous_indent:
            if not isinstance(previous_value, dict):
                raise SyncError("gh scalar cannot contain a mapping")
            stack.append((indent, previous_value))
        else:
            while stack[-1][0] > indent:
                stack.pop()
            if stack[-1][0] != indent:
                raise SyncError("inconsistent gh mapping indentation")
        if len(stack) > 7 or (len(stack) == 2 and indent != 0):
            raise SyncError("unsupported gh mapping depth")
        key = Scalar(match[1]).public()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            raise SyncError("unsupported gh mapping key")
        mapping = stack[-1][1]
        if key in mapping:
            raise SyncError("duplicate gh configuration key")
        value = (match[2] or "").strip()
        if value in ("", "{}"):
            parsed = {}
        else:
            if value[0] not in "\"'" and (value[0] in "&*!|>{[" or ": " in value or " #" in value):
                raise SyncError("unsupported gh scalar syntax")
            parsed = Scalar(value)
            parsed.public()  # validate syntax only; never a credential source
        mapping[key] = parsed
        previous_indent, previous_value = indent, parsed
    return root


def public(mapping, key, default=""):
    value = mapping.get(key, {})
    if value == {}:
        return default
    if not isinstance(value, Scalar):
        raise SyncError("unexpected gh mapping where scalar required")
    return value.public()


GENERAL_KEYS = frozenset({"version", "git_protocol", "editor", "prompt", "prefer_editor_prompt",
                          "pager", "aliases", "http_unix_socket", "browser", "color_labels",
                          "accessible_colors", "accessible_prompter", "spinner"})
HOST_KEYS = frozenset({"users", "user", "oauth_token", "git_protocol"})
ACCOUNT_KEYS = frozenset({"oauth_token", "git_protocol"})
GENERAL_DEFAULTS = {"version": "1", "git_protocol": "https", "editor": "", "prompt": "enabled",
                    "prefer_editor_prompt": "disabled", "pager": "", "http_unix_socket": "",
                    "browser": "", "color_labels": "disabled", "accessible_colors": "disabled",
                    "accessible_prompter": "disabled", "spinner": "enabled"}


@dataclass(frozen=True, repr=False)
class Metadata:
    general: dict
    hosts: dict

    @classmethod
    def parse(cls, general_raw, hosts_raw):
        general, hosts = block_mapping(general_raw), block_mapping(hosts_raw)
        # Empty config AND empty hosts use gh's schema-1 built-in fallback.
        if (general or hosts) and public(general, "version") != "1":
            raise SyncError("gh configuration requires migration; initialize gh separately and retry")
        if set(general) - GENERAL_KEYS:
            raise SyncError("unsupported gh general configuration")
        if public(general, "http_unix_socket"):
            raise SyncError("custom gh API transport is unsupported")
        for key, value in general.items():
            if key == "aliases":
                if not isinstance(value, dict) or any(not isinstance(v, Scalar) for v in value.values()):
                    raise SyncError("unsupported gh aliases")
                if set(value) & {"auth", "version"}:
                    raise SyncError("authentication command aliases are unsupported")
            else:
                public(general, key)
        for host, entry in hosts.items():
            validate_host_account(host, "fixture")
            if not isinstance(entry, dict) or set(entry) - HOST_KEYS:
                raise SyncError("unsupported gh host configuration")
            users = entry.get("users", {})
            if not isinstance(users, dict):
                raise SyncError("unsupported gh account mapping")
            for user, details in users.items():
                validate_host_account(host, user)
                if not isinstance(details, dict) or set(details) - ACCOUNT_KEYS:
                    raise SyncError("unsupported gh account configuration")
                if "oauth_token" in details and not isinstance(details["oauth_token"], Scalar):
                    raise SyncError("ambiguous gh token metadata")
                if "git_protocol" in details and (
                        not isinstance(details["git_protocol"], Scalar)
                        or public(details, "git_protocol") not in {"https", "ssh"}):
                    raise SyncError("unsupported gh account Git protocol")
            active = public(entry, "user")
            if (users and active not in users) or (active and active not in users):
                raise SyncError("inconsistent gh active-account metadata")
            if "oauth_token" in entry and (not active or not isinstance(entry["oauth_token"], Scalar)):
                raise SyncError("ambiguous gh active-token metadata")
            if public(entry, "git_protocol", "https") not in {"https", "ssh"}:
                raise SyncError("unsupported gh Git protocol")
        return cls(general, hosts)

    def users(self, host):
        return frozenset(self.hosts.get(host, {}).get("users", {}))

    def active(self, host):
        return public(self.hosts.get(host, {}), "user")

    def preserved_except(self, other, host, account):
        """Ignore only selected auth fields; never decode unselected token values."""
        if self.general != other.general:
            # gh may materialize built-in defaults on the first selected login.
            if self.general or any(public(other.general, k, v) != v for k, v in GENERAL_DEFAULTS.items()):
                return False
            if other.general.get("aliases", {}) != {"co": Scalar("pr checkout")}:
                return False
        if {k: v for k, v in self.hosts.items() if k != host} != {
                k: v for k, v in other.hosts.items() if k != host}:
            return False
        before, after = self.hosts.get(host, {}), other.hosts.get(host, {})
        # A selected token may change, but its other account metadata may not.
        # In particular, account-level protocol settings must not be discarded
        # merely because this is the account receiving a credential update.
        before_account = before.get("users", {}).get(account, {})
        after_account = after.get("users", {}).get(account, {})
        if {k: v for k, v in before_account.items() if k != "oauth_token"} != {
                k: v for k, v in after_account.items() if k != "oauth_token"}:
            return False
        if {k: v for k, v in before.items() if k not in {"user", "users", "oauth_token"}} != {
                k: v for k, v in after.items() if k not in {"user", "users", "oauth_token"}}:
            return False
        return ({k: v for k, v in before.get("users", {}).items() if k != account}
                == {k: v for k, v in after.get("users", {}).items() if k != account})
