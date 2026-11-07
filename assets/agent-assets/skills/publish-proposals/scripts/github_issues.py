"""Fixed GitHub Issues protocol with a narrow source-qualified Linux profile.

The legacy gh-api profile remains unavailable. HTTPS admission is a source
decision, never a plan field or enable switch. See https-transport-candidate.md
for the supported runtime, credential client, configuration, proxy and trust.
"""

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import socket
import stat
import subprocess
import ssl
import sys
import time
from urllib.parse import urlsplit


BODY_BYTES = 60 * 1024
STDOUT_BYTES = 8 * 1024 * 1024
STDERR_BYTES = 64 * 1024
CALL_SECONDS = 30
TERM_SECONDS = 5
REAP_SECONDS = 5
INVOCATION_SECONDS = 900
MAX_CALLS = 2048
TOTAL_BYTES = 256 * 1024 * 1024
DISCOVERY_BYTES = 32 * 1024 * 1024
MAX_PAGES = 10
PAGE_NODES = 100
CREDENTIAL_BYTES = 4096
WORKER_INPUT_BYTES = 1024 * 1024 + 8192
CONFIG_BYTES = 64 * 1024
GH_VERSION = "gh 2.86.0"
WRAPPED_GH_VERSION = "gh 2.100.0"
HTTPS_PROFILE = "fixed-https-graphql-v1"
HTTPS_POLICY = {"request": "fixed-https-post-v1", "redirects": "refused", "retries": "none",
                "credentials": "fixed-gh-auth-token-v2", "credential_bytes": CREDENTIAL_BYTES,
                "worker_input_bytes": WORKER_INPUT_BYTES, "proxy": "https-env-subset-v1",
                "tls": "linux-runtime-default-verified", "worker": "isolated-no-site-v1",
                "config": "gh-v1-no-unix-socket"}
QUALIFIED_SOURCES = {
    "http_client": "c2c0694155837d72fc710a4138e5d1e53ae1a652b117a2e23a5b1b0db527a0f9",
    "ssl": "039a0e3f1782e14824b1624c7ffefc2de31714b4a9994424be45a4271966097c",
    "socket": "7d4d4c66e6f4bcc961ab462c4f08002ca97def8713a4be1c7373bdbd970a5274",
}

REPOSITORY = "id databaseId name owner { login } visibility isArchived hasIssuesEnabled viewerPermission"
ISSUE = "__typename id databaseId number url title body repository { id databaseId name owner { login } }"
OPERATIONS = {
    "named": "query($owner:String!,$name:String!){viewer{login databaseId} repository(owner:$owner,name:$name){" + REPOSITORY + "}}",
    "context": "query($id:ID!){viewer{login databaseId} node(id:$id){__typename ... on Repository{" + REPOSITORY + "}}}",
    "page": "query($id:ID!,$after:String){node(id:$id){__typename ... on Repository{id issues(first:100,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){nodes{" + ISSUE + "} pageInfo{hasNextPage endCursor}}}}}",
    "read": "query($id:ID!,$number:Int!){node(id:$id){__typename ... on Repository{id issue(number:$number){" + ISSUE + "}}}}",
    "create": "mutation($repositoryId:ID!,$title:String!,$body:String!){createIssue(input:{repositoryId:$repositoryId,title:$title,body:$body}){issue{" + ISSUE + "}}}",
}


class Error(Exception):
    """Fixed codes only; never raw responses, stderr, input or exception text."""


def need(condition, code="invalid_github_response"):
    if not condition:
        raise Error(code)


def fields(value, names):
    need(type(value) is dict and set(value) == set(names.split()))


def string(value, limit=4096, empty=False):
    need(type(value) is str and (bool(value) or empty)
         and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))
    need(len(value.encode("utf-8")) <= limit)
    return value


def token(value, pattern=r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"):
    need(type(value) is str and re.fullmatch(pattern, value) is not None)
    return value


def node_id(value):
    return token(value, r"[A-Za-z0-9_+=/-]{1,256}")


def integer(value):
    need(type(value) is int and 0 < value < 2 ** 53)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "invalid_github_json")
        result[key] = value
    return result


def _bad_number(_):
    raise Error("invalid_github_json")


def _json_integer(value):
    need(len(value) <= 17, "invalid_github_json")
    result = int(value)
    need(-(2 ** 53) < result < 2 ** 53, "invalid_github_json")
    return result


def parse(raw):
    need(type(raw) is bytes and len(raw) <= STDOUT_BYTES, "client_output_limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_int=_json_integer, parse_float=_bad_number, parse_constant=_bad_number)
        fields(value, "data")  # errors, partial data and extensions are not accepted.
        need(type(value["data"]) is dict)
        return value["data"]
    except (ValueError, UnicodeError, RecursionError):
        raise Error("invalid_github_json") from None


def policy():
    # Closed protocol constants; publication.py independently checks this binding.
    return {"discovery": "best-effort-single-attempt", "consistency": "not-guaranteed",
            "coordination": "one-canonical-store-and-coordinated-context",
            "reconciliation": "required-artifact-not-authorship-or-global-uniqueness",
            "permission": "observed-role-not-a-create-permission-guarantee",
            "body_bytes": 61440, "page_nodes": 100, "pages": 10,
            "discovery_bytes": 33554432, "call_seconds": 30, "term_seconds": 5,
            "reap_seconds": 5, "invocation_seconds": 900, "stdout_bytes": 8388608,
            "stderr_bytes": 65536, "calls": 2048, "output_bytes": 268435456}


def _resolve_client():
    # Not a registry, environment switch, executable argument or plan field.
    # Enabling a real implementation requires a separately reviewed source change.
    raise Error("github_compatibility_unavailable")


def _fingerprint(selected, version, *, executable=True):
    path = Path(selected)
    need(path.is_absolute() and ".." not in path.parts, "invalid_client_identity")
    target = path.resolve(strict=True)
    before = target.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.getuid()}
         and not before.st_mode & 0o022 and (not executable or before.st_mode & 0o111)
         and before.st_nlink == 1 and before.st_size <= 128 * 1024 * 1024, "invalid_client_identity")
    raw = target.read_bytes()
    after = target.stat()
    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
              "st_size", "st_mtime_ns", "st_ctime_ns")
    need(all(getattr(before, name) == getattr(after, name) for name in stable), "client_changed")
    string(version, 256)
    return {"executable": str(path), "resolved": str(target),
            "sha256": hashlib.sha256(raw).hexdigest(), "version": version}


def operation(name, variables):
    need(name in OPERATIONS, "unsupported_operation")
    if name == "named":
        fields(variables, "owner name")
        token(variables["owner"]); token(variables["name"])
    else:
        fields(variables, {"context": "id", "page": "id after", "read": "id number",
                           "create": "repositoryId title body"}[name])
        node_id(variables["repositoryId" if name == "create" else "id"])
        if name == "page" and variables["after"] is not None:
            string(variables["after"], 4096)
        if name == "read":
            integer(variables["number"])
        if name == "create":
            string(variables["title"], 256); string(variables["body"], BODY_BYTES)
    payload = canonical({"query": OPERATIONS[name], "variables": variables})
    need(len(payload) <= 1024 * 1024, "request_limit")
    return payload


class _Transport:
    """One bounded owned process per call; no retry or descendant-closure claim."""

    def __init__(self):
        selected, version = _resolve_client()
        self.identity = _fingerprint(selected, version)
        self.started = time.monotonic()
        self.calls = self.output_bytes = 0
        self.last = None

    def call(self, host, name, variables):
        token(host, r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}")
        payload = operation(name, variables)
        need(self.calls < MAX_CALLS and self.output_bytes < TOTAL_BYTES
             and time.monotonic() < self.started + INVOCATION_SECONDS, "invocation_limit")
        need(_fingerprint(self.identity["executable"], self.identity["version"]) == self.identity, "client_changed")
        argv = [self.identity["resolved"], "api", "--hostname", host,
                "--method", "POST", "graphql", "--input", "-"]
        self.calls += 1
        deadline = min(time.monotonic() + CALL_SECONDS, self.started + INVOCATION_SECONDS)
        return parse(self._run(argv, payload, STDOUT_BYTES, deadline))

    def _run(self, argv, payload, output_limit, deadline, *, telemetry_disabled=False):
        """Owned pipes only; callers supply fixed argv, never a token argument."""
        need(time.monotonic() < deadline, "client_timeout")
        # The only child-environment addition is this source-qualified startup
        # control. No caller-supplied environment map, token or routing override.
        child_options = {}
        if telemetry_disabled:
            child_options["env"] = dict(os.environ, GH_TELEMETRY="disabled")
        child = None
        selector = selectors.DefaultSelector()
        streams = []
        output, error = bytearray(), bytearray()
        self.last = {"reaped": False, "term": False, "kill": False, "returncode": None}
        try:
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True, **child_options)
            self.last["pid"] = child.pid
            streams = [child.stdin, child.stdout, child.stderr]
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if stream is child.stdin else selectors.EVENT_READ)
            position = 0
            while selector.get_map():
                need(time.monotonic() < deadline, "client_timeout")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    stream = key.fileobj
                    if stream is child.stdin:
                        try:
                            if position < len(payload):
                                position += os.write(stream.fileno(), payload[position:position + 65536])
                        except BrokenPipeError:
                            raise Error("client_failed") from None
                        if position == len(payload):
                            selector.unregister(stream); stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            selector.unregister(stream); stream.close()
                            continue
                        self.output_bytes += len(chunk)
                        buffer = output if stream is child.stdout else error
                        buffer.extend(chunk)
                        need(len(output) <= output_limit and len(error) <= STDERR_BYTES
                             and self.output_bytes <= TOTAL_BYTES, "client_output_limit")
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise Error("client_timeout") from None
            self.last.update(reaped=True, returncode=child.returncode)
            need(child.returncode == 0, "client_failed")
            return bytes(output)
        except OSError:
            raise Error("client_failed") from None
        finally:
            selector.close()
            for stream in streams:
                stream.close()
            if child is not None:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                        self.last["term"] = True
                    except ProcessLookupError:
                        pass
                    try:
                        child.wait(timeout=TERM_SECONDS)
                    except subprocess.TimeoutExpired:
                        if child.poll() is None:
                            try:
                                os.killpg(child.pid, signal.SIGKILL)
                                self.last["kill"] = True
                            except ProcessLookupError:
                                pass
                        try:
                            child.wait(timeout=REAP_SECONDS)
                        except subprocess.TimeoutExpired:
                            raise Error("client_not_reaped") from None
                self.last.update(reaped=child.returncode is not None, returncode=child.returncode)


def _qualify_https_runtime():
    # Only static implementation metadata is inspected here. In particular no
    # gh --version command (which can migrate config), credential or CA read.
    need(sys.platform == "linux" and sys.implementation.name == "cpython"
         and sys.version_info[:3] == (3, 12, 14), "github_compatibility_unavailable")
    for name, module in {"http_client": http.client, "ssl": ssl, "socket": socket}.items():
        need(hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == QUALIFIED_SOURCES[name],
             "github_compatibility_unavailable")
    need(ssl.OPENSSL_VERSION_INFO[:3] == (1, 1, 1), "github_compatibility_unavailable")


def _resolve_https_client():
    _qualify_https_runtime()
    selected = shutil.which("gh")
    need(selected is not None and Path(selected).is_absolute(), "github_compatibility_unavailable")
    _credential_selection(selected, GH_VERSION)
    return selected, GH_VERSION


def _credential_selection(selected, version):
    need(version in {GH_VERSION, WRAPPED_GH_VERSION}, "github_compatibility_unavailable")
    identity = _fingerprint(selected, version)
    raw = Path(identity["resolved"]).read_bytes()
    need(hashlib.sha256(raw).hexdigest() == identity["sha256"], "client_changed")
    _gh_build(raw, version)
    return identity


def _gh_build(raw, version=GH_VERSION):
    """Read only Go's inline build metadata; never execute a version probe.

    Supports the tagged 64-bit Linux ELF build, not launchers or pointer-based
    old Go metadata. This is installed-package metadata, not a signed attestation.
    """
    code = "github_compatibility_unavailable"
    need(version in {GH_VERSION, WRAPPED_GH_VERSION}, code)
    go, go_gh, keyring, release = ((b"go1.25.5", "v2.13.0", "v0.2.6", b"2\\.86\\.0")
                                 if version == GH_VERSION else
                                 (b"go1.26.8", "v2.16.0", "v0.2.8", b"2\\.100\\.0"))
    need(raw.startswith(b"\x7fELF\x02\x01"), code)
    matches = [m.start() for m in re.finditer(b"\xff Go buildinf:", raw) if m.start() % 16 == 0]
    need(len(matches) == 1, code)
    offset = matches[0]
    need(raw[offset + 14:offset + 16] == b"\x08\x02", code)
    offset += 32

    def item():
        nonlocal offset
        length = 0
        for shift in range(0, 21, 7):
            need(offset < len(raw), code)
            byte = raw[offset]
            offset += 1
            length |= (byte & 127) << shift
            if byte < 128:
                need(length <= 65536 and offset + length <= len(raw), code)
                result = raw[offset:offset + length]
                offset += length
                return result
        raise Error(code)

    need(item() == go, code)
    module = item()
    need(len(module) >= 33 and module[-17:-16] == b"\n", code)
    lines = module[16:-16].splitlines()
    need(b"path\tgithub.com/cli/cli/v2/cmd/gh" in lines
         and b"build\tGOOS=linux" in lines
         and not any(line.startswith(b"=>") for line in lines), code)
    flags = [line for line in lines if line.startswith(b"build\t-ldflags=")]
    need(len(flags) == 1 and re.search(rb"(?: |\")-X github\.com/cli/cli/v2/internal/build\.Version="
                                      + release + rb"(?: |\")", flags[0]), code)
    for package, version in (("cli/go-gh/v2", go_gh), ("cli/safeexec", "v1.0.1"),
                             ("zalando/go-keyring", keyring), ("spf13/cobra", "v1.10.2")):
        prefix = ("dep\tgithub.com/" + package + "\t").encode("ascii")
        deps = [line for line in lines if line.startswith(prefix)]
        need(len(deps) == 1 and deps[0].split(b"\t")[2] == version.encode("ascii"), code)


def _config_scalar(value):
    # Deliberately not a general YAML parser. Only one-line scalar/map syntax
    # whose interpretation agrees with go-gh's yaml.Node.Value is admitted.
    if value.startswith("#"):
        return ""
    match = re.fullmatch(r'''("(?:[^"\\]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*"|'(?:[^']|'')*'|[^'"\[\]{},&*!|>%@`#][^\r\n]*|)(?: +\#.*)?''', value)
    need(match is not None, "unsupported_gh_config")
    scalar = match[1].rstrip()
    if scalar.startswith('"'):
        return json.loads(scalar)
    if scalar.startswith("'"):
        return scalar[1:-1].replace("''", "'")
    scalar = re.split(r" +#", scalar, maxsplit=1)[0].rstrip()
    need(": " not in scalar and not scalar.endswith(":"), "unsupported_gh_config")
    return scalar


def _config_settings(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise Error("unsupported_gh_config") from None
    need(not any(ord(c) < 32 and c != "\n" for c in text), "unsupported_gh_config")
    settings, aliases, alias_indent = {}, set(), None
    parent = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"( *)([a-zA-Z0-9_-]+):(?: +(.*)|)", line)
        need(match is not None, "unsupported_gh_config")
        indent, key, value = match.groups()
        value = _config_scalar(value or "")
        if indent:
            need(parent == "aliases" and settings[parent] == "" and key not in aliases,
                 "unsupported_gh_config")
            if alias_indent is None:
                alias_indent = len(indent)
            need(len(indent) == alias_indent, "unsupported_gh_config")
            aliases.add(key)
        else:
            need(key not in settings and key != "hosts", "unsupported_gh_config")
            settings[key] = value
            parent = key
    return settings


def _credential_context():
    """No repair: establish non-migrating config before launching gh.

    Inherits the exact POSIX go-gh config precedence. General config is read
    privately and bounded; hosts.yml is only stat'ed here, never parsed/logged.
    Coordinated, stable caller-owned config is required, not a hostile-writer
    guarantee across the subsequent gh process opening its configuration.
    """
    base = os.environ.get("GH_CONFIG_DIR")
    if not base:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        if config_home:
            base = str(Path(config_home) / "gh")
        else:
            user_home = os.environ.get("HOME")
            need(user_home, "unsupported_gh_config")
            base = str(Path(user_home) / ".config" / "gh")
    need(Path(base).is_absolute() and ".." not in Path(base).parts, "unsupported_gh_config")
    settings, host_size = {}, 0
    try:
        for name, limit in (("config.yml", CONFIG_BYTES), ("hosts.yml", 1024 * 1024)):
            path = Path(base) / name
            try:
                before = path.stat()
            except FileNotFoundError:
                continue
            need(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.getuid()}
                 and not before.st_mode & 0o022 and before.st_size <= limit, "unsupported_gh_config")
            if name == "hosts.yml":
                host_size = before.st_size
                continue
            descriptor = os.open(path.resolve(strict=True), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                need(stat.S_ISREG(opened.st_mode) and opened.st_ino == before.st_ino
                     and opened.st_dev == before.st_dev, "unsupported_gh_config")
                raw = stream.read(CONFIG_BYTES + 1)
                after = os.fstat(stream.fileno())
            stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
            need(len(raw) <= CONFIG_BYTES and all(getattr(opened, k) == getattr(after, k) for k in stable),
                 "unsupported_gh_config")
            settings = _config_settings(raw)
    except OSError:
        raise Error("unsupported_gh_config") from None
    # Empty general + empty/missing hosts uses gh's schema-1 fallback. A
    # hosts-only old configuration must not trigger gh's startup migration.
    need((not settings and host_size == 0) or settings.get("version") == "1", "unsupported_gh_config")
    need(not settings.get("http_unix_socket", ""), "unsupported_security_context")


def _endpoint(host):
    token(host, r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}")
    need(len(host) <= 253 and all(0 < len(part) <= 63 and not part.startswith("-")
                                 and not part.endswith("-") for part in host.split(".")), "invalid_endpoint")
    need(not host.endswith((".github.com", ".ghe.com", ".github.localhost"))
         and host not in {"ghe.com", "github.localhost"}, "invalid_endpoint")
    return ("api.github.com", "/graphql") if host == "github.com" else (host, "/api/graphql")


def _proxy_input(name):
    upper, lower = os.environ.get(name.upper()), os.environ.get(name.lower())
    need(upper is None or lower is None or upper == lower, "unsupported_security_context")
    return upper if upper is not None else lower


def _proxy(api_host):
    """Conservative common Go/stdlib env subset; never native proxy fallback.

    Unsupported/ambiguous inputs stop instead of choosing another route. In
    particular HTTPS_PROXY is for HTTPS destinations; HTTP_PROXY is not its
    fallback. No system configuration, gh config, or credential file is parsed.
    """
    need(not _proxy_input("ALL_PROXY"), "unsupported_security_context")
    value, bypass = _proxy_input("HTTPS_PROXY"), _proxy_input("NO_PROXY")
    use_proxy = bool(value)
    if bypass:
        for entry in bypass.split(","):
            entry = entry.strip().lower()
            if not entry:
                continue
            if entry == "*":
                use_proxy = False
                continue
            # Go tests literal IP/CIDR exclusions against literal destination
            # IPs only, never DNS answers. Our fixed endpoints are DNS names.
            try:
                ipaddress.ip_network(entry, strict=False)
                continue
            except ValueError:
                pass
            match_ip = re.fullmatch(r"(?:\[([^\]]+)\]|([0-9.]+)):([0-9]{1,5})", entry)
            if match_ip:
                try:
                    ipaddress.ip_address(match_ip[1] or match_ip[2])
                    need(0 < int(match_ip[3]) < 65536, "unsupported_security_context")
                    continue
                except ValueError:
                    raise Error("unsupported_security_context") from None
            match = re.fullmatch(r"((?:\*\.|\.)?[a-z0-9]+(?:[.-][a-z0-9]+)*)(?::([0-9]{1,5}))?", entry)
            need(match is not None, "unsupported_security_context")
            name, port = match.groups()
            name = name.removeprefix("*")
            need(port is None or 0 < int(port) < 65536, "unsupported_security_context")
            if port is not None and port != "443":
                continue
            if api_host.endswith("." + name.lstrip(".")) or (not name.startswith(".") and api_host == name):
                use_proxy = False
    if not value:
        return None
    need(len(value) <= 4096 and not any(ord(c) <= 32 or ord(c) >= 127 for c in value), "unsupported_security_context")
    try:
        parsed = urlsplit(value)
        need(parsed.scheme == "http" and parsed.hostname is not None and parsed.username is None
             and parsed.password is None and parsed.path in {"", "/"} and not parsed.query
             and not parsed.fragment, "unsupported_security_context")
        need(re.fullmatch(r"[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*", parsed.hostname) is not None,
             "unsupported_security_context")
        port = 80 if parsed.port is None else parsed.port
        need(0 < port < 65536 and not parsed.netloc.endswith(":"), "unsupported_security_context")
    except ValueError:
        raise Error("unsupported_security_context") from None
    return (parsed.hostname, port) if use_proxy else None


def _credential(raw):
    need(type(raw) is bytes and 0 < len(raw) <= CREDENTIAL_BYTES, "invalid_credential_output")
    value = raw[:-1] if raw.endswith(b"\n") else raw
    need(re.fullmatch(rb"[A-Za-z0-9._~+/-]+=*", value) is not None, "invalid_credential_output")
    return value.decode("ascii")


def _security_context(api_host):
    need(not os.environ.get("SSLKEYLOGFILE") and not os.environ.get("GH_DEBUG")
         and os.environ.get("PYTHONHTTPSVERIFY") != "0", "unsupported_security_context")
    _trust_paths()
    return _proxy(api_host)


def _trust_paths():
    # Keep all inherited trust inputs. Fail on unsupported explicit paths
    # instead of silently ignoring a missing file or selecting another bundle.
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name not in os.environ:
            continue
        value = os.environ[name]
        paths = value.split(":") if name == "SSL_CERT_DIR" else [value]
        need(all(p and Path(p).is_absolute() for p in paths), "unsupported_security_context")
        need(all(Path(p).is_dir() if name == "SSL_CERT_DIR" else Path(p).is_file() for p in paths),
             "unsupported_security_context")


def _tls_context():
    _trust_paths()
    context = ssl.create_default_context()
    need(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname,
         "unsupported_security_context")
    context.set_alpn_protocols(["http/1.1"])
    return context


def _https_exchange(host, name, variables, credential):
    """Exactly one HTTP/1.1 submission. No redirect, refresh, or retry engine.

    Runs in an owned child: its parent's deadline includes DNS, CONNECT, TLS,
    request transmission and response reads, not merely socket idle timeouts.
    """
    api_host, path = _endpoint(host)
    payload = operation(name, variables)
    credential = _credential(credential)
    proxy = _security_context(api_host)
    connection = response = None
    try:
        context = _tls_context()
        address, port = proxy if proxy else (api_host, 443)
        connection = http.client.HTTPSConnection(address, port, timeout=CALL_SECONDS, context=context)
        if proxy:
            # Authorization is deliberately NOT a CONNECT header. HTTPSConnection
            # validates and sends SNI for the tunnel host, not the proxy host.
            connection.set_tunnel(api_host, 443)
        connection.request("POST", path, body=payload, headers={
            "Authorization": "Bearer " + credential,
            "Accept": "application/vnd.github+json", "Content-Type": "application/json",
            "User-Agent": "envy-proposal-publisher", "Connection": "close",
            "Accept-Encoding": "identity", "Content-Length": str(len(payload)),
        })
        response = connection.getresponse()
        need(not 300 <= response.status < 400, "github_redirect_refused")
        need(response.status == 200, "github_http_failed")
        need(response.getheader("Content-Encoding", "identity").lower() == "identity", "invalid_github_response")
        lengths = [value for key, value in response.getheaders() if key.lower() == "content-length"]
        transfers = [value for key, value in response.getheaders() if key.lower() == "transfer-encoding"]
        need(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers)
             and (not transfers or transfers[0].lower() == "chunked"), "invalid_github_response")
        expected = None
        if lengths:
            need(re.fullmatch(r"[0-9]{1,10}", lengths[0]) is not None, "invalid_github_response")
            expected = int(lengths[0])
            need(expected <= STDOUT_BYTES, "client_output_limit")
        raw = response.read(STDOUT_BYTES + 1)
        need(len(raw) <= STDOUT_BYTES, "client_output_limit")
        # HTTPResponse.read(amt) tolerates an EOF before Content-Length, unlike
        # its unbounded read. A valid JSON prefix is not a complete response.
        need(expected is None or len(raw) == expected, "invalid_github_response")
        return raw
    except (OSError, ValueError, http.client.HTTPException):
        raise Error("github_https_failed") from None
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def _https_worker():
    # A private framed pipe protocol, not an arbitrary URL/query client. Only
    # fixed error codes reach the parent; exception text/HTTP bodies never do.
    try:
        raw = sys.stdin.buffer.read(WORKER_INPUT_BYTES + 1)
        need(len(raw) <= WORKER_INPUT_BYTES, "request_limit")
        request = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                             parse_int=_json_integer, parse_float=_bad_number, parse_constant=_bad_number)
        fields(request, "host operation variables credential")
        need(type(request["credential"]) is str, "invalid_credential_output")
        result = _https_exchange(request["host"], request["operation"], request["variables"],
                                 request["credential"].encode("ascii"))
        sys.stdout.buffer.write(result)
        return 0
    except BaseException:
        # Includes interruptions. The parent knows only that this one possible
        # submission failed; it must not infer that no mutation was delivered.
        return 1


class _CredentialCommand:
    """Trusted-code metadata for the one fixed auth-token operation, not argv.

    A wrapper must be admitted read-only by its static selector, including its
    entire effective environment/setup/sandbox and final native gh selection.
    The supervisor executes the launcher, never substitutes the native payload.
    Artifacts are bounded, nonsecret source/configuration or executable inputs.
    """

    def __init__(self, native, version, artifacts=(), *, launcher=None):
        self.native, self.version, self.artifacts = native, version, artifacts
        self.launcher = launcher


class _HttpsTransport(_Transport):
    """Fixed HTTPS transport, admitted only by the source-qualified Linux gate."""

    def __init__(self):
        self.identity, self.selection = self._selected_identity()
        self.runtime = _fingerprint(sys.executable, sys.implementation.name + " " + ".".join(map(str, sys.version_info[:3])))
        self.https = self._https_identity()
        self.source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.started = time.monotonic()
        self.calls = self.output_bytes = 0
        self.last = None

    def _select_credential_client(self):
        """Trusted source hook: _CredentialCommand or native/version/artifacts.

        A statically authored private subclass may resolve its package here.
        No CLI, environment selector, plan field or module discovery calls it.
        It must be bounded, read-only and preserve inherited security inputs.
        The core still checks the native binary and records this method's source.
        """
        selected, version = _resolve_https_client()
        return selected, version, ()

    def _selected_identity(self):
        _qualify_https_runtime()
        method = type(self)._select_credential_client
        source = Path(method.__code__.co_filename).resolve(strict=True)
        before = source.stat()
        need(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.getuid()}
             and not before.st_mode & 0o022 and before.st_size <= 1024 * 1024,
             "invalid_client_identity")
        raw = source.read_bytes()
        after = source.stat()
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
        need(all(getattr(before, name) == getattr(after, name) for name in stable), "client_changed")
        command = self._select_credential_client()
        if type(command) in {tuple, list} and len(command) == 3:
            command = _CredentialCommand(*command)
        need(type(command) is _CredentialCommand, "invalid_client_identity")
        selected, version, artifacts = command.native, command.version, command.artifacts
        # The newer startup path needs the compiled telemetry control and a
        # separately source-qualified wrapper context. The public PATH resolver
        # still selects only its existing direct-client profile.
        need(version != WRAPPED_GH_VERSION or command.launcher is not None,
             "github_compatibility_unavailable")
        need(type(artifacts) in {tuple, list} and len(artifacts) <= 8, "invalid_client_identity")
        identity = _credential_selection(selected, version)
        launcher = (_fingerprint(command.launcher, "gh-auth-token-launcher-v1")
                    if command.launcher is not None else dict(identity))
        selection = {"kind": "fixed-code-v2", "entry": string(method.__qualname__, 256),
                     "source": {"path": str(source), "sha256": hashlib.sha256(raw).hexdigest()},
                     "artifacts": [_fingerprint(path, "selection-artifact-v2", executable=False) for path in artifacts],
                     "launcher": launcher, "cwd": str(Path.cwd()),
                     "control": "inherit" if command.launcher is None else "telemetry-disabled"}
        return identity, selection

    def _https_identity(self):
        modules = {"http_client": http.client, "ssl": ssl, "socket": socket, "ssl_extension": ssl._ssl}
        sources = {}
        for name, module in modules.items():
            path = Path(module.__file__).resolve(strict=True)
            need(path.stat().st_size <= 128 * 1024 * 1024, "invalid_client_identity")
            sources[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return {"interpreter": dict(self.runtime), "sources": sources, "tls_version": ssl.OPENSSL_VERSION,
                "selection": json.loads(canonical(self.selection)),
                "policy": dict(HTTPS_POLICY)}

    def _worker_argv(self):
        return [self.runtime["resolved"], "-I", "-S", "-B", str(Path(__file__).resolve()), "--https-worker"]

    def call(self, host, name, variables):
        api_host, _ = _endpoint(host)
        operation(name, variables)
        _security_context(api_host)
        need(self.calls < MAX_CALLS and self.output_bytes < TOTAL_BYTES
             and time.monotonic() < self.started + INVOCATION_SECONDS, "invocation_limit")
        need(self._selected_identity() == (self.identity, self.selection)
             and _fingerprint(self.runtime["executable"], self.runtime["version"]) == self.runtime
             and self._https_identity() == self.https
             and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == self.source, "client_changed")
        deadline = min(time.monotonic() + CALL_SECONDS, self.started + INVOCATION_SECONDS)
        self.calls += 1
        _credential_context()
        # Fixed suffix, no shell, no token in argv/environment/receipts/disk.
        # A statically admitted wrapper is invoked at its selected path. Never
        # execute its final native identity as a shortcut around wrapper policy.
        raw = self._run([self.selection["launcher"]["executable"], "auth", "token", "--hostname", host],
                        b"", CREDENTIAL_BYTES, deadline,
                        telemetry_disabled=self.selection["control"] == "telemetry-disabled")
        credential = _credential(raw)
        payload = canonical({"host": host, "operation": name, "variables": variables, "credential": credential})
        need(len(payload) <= WORKER_INPUT_BYTES, "request_limit")
        return parse(self._run(self._worker_argv(), payload, STDOUT_BYTES, deadline))


def destination(value):
    fields(value, "host account account_id owner repository repository_id audience")
    token(value["host"], r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}")
    for key in ("account", "owner", "repository"):
        token(value[key])
        need(value[key] not in {".", ".."})
    for key in ("account_id", "repository_id"):
        token(value[key], r"[1-9][0-9]{0,19}")
    fields(value["audience"], "kind realm id")
    need(value["audience"] in ({"kind": "internet", "realm": "internet", "id": "public"},
                              {"kind": "repository", "realm": value["host"], "id": value["repository_id"]}), "unsupported_audience")


def context(data, target, pinned=None):
    fields(data, "viewer " + ("repository" if pinned is None else "node"))
    viewer = data["viewer"]
    fields(viewer, "login databaseId")
    need(viewer["login"] == target["account"] and str(integer(viewer["databaseId"])) == target["account_id"], "account_changed")
    repo = data["repository" if pinned is None else "node"]
    fields(repo, "id databaseId name owner visibility isArchived hasIssuesEnabled viewerPermission" + (" __typename" if pinned is not None else ""))
    if pinned is not None:
        need(repo["__typename"] == "Repository" and repo["id"] == pinned, "repository_changed")
    node_id(repo["id"]); fields(repo["owner"], "login")
    need(str(integer(repo["databaseId"])) == target["repository_id"] and repo["name"] == target["repository"]
         and repo["owner"]["login"] == target["owner"], "repository_changed")
    need(repo["visibility"] in {"PRIVATE", "PUBLIC"} and repo["isArchived"] is False
         and repo["hasIssuesEnabled"] is True and repo["viewerPermission"] in {"ADMIN", "MAINTAIN", "WRITE"}, "unsupported_repository")
    if repo["visibility"] == "PUBLIC":
        need(target["audience"]["kind"] == "internet", "audience_mismatch")
    return {"destination": json.loads(canonical(target)), "repository_node_id": repo["id"],
            "visibility": repo["visibility"], "issues_enabled": repo["hasIssuesEnabled"],
            "archived": repo["isArchived"], "permission": repo["viewerPermission"]}


def issue(value, target, repository_id, marker):
    fields(value, "__typename id databaseId number url title body repository")
    need(value["__typename"] == "Issue")
    node_id(value["id"])
    ident, number = str(integer(value["databaseId"])), str(integer(value["number"]))
    repo = value["repository"]
    fields(repo, "id databaseId name owner"); fields(repo["owner"], "login")
    need(repo["id"] == repository_id and str(integer(repo["databaseId"])) == target["repository_id"]
         and repo["name"] == target["repository"] and repo["owner"]["login"] == target["owner"], "artifact_repository_changed")
    need(value["url"] == "https://" + target["host"] + "/" + target["owner"] + "/" + target["repository"] + "/issues/" + number, "artifact_url_mismatch")
    string(value["title"], 256); string(value["body"], 128 * 1024, empty=True)
    return {"id": ident, "number": number, "url": value["url"], "title": value["title"],
            "body": value["body"], "marker": marker, "destination": json.loads(canonical(target))}


class GitHubIssues:
    """Trusted-fixture adapter; normal construction refuses before any child."""

    def __init__(self, target):
        destination(target)
        self.target = json.loads(canonical(target))
        self.transport = _Transport()
        observed = context(self.transport.call(target["host"], "named", {"owner": target["owner"], "name": target["repository"]}), self.target)
        self.repository_id = observed["repository_node_id"]
        self.identity = {"name": "github-issues", "version": 1, "profile": "unavailable-graphql-v1",
                         "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                         "client": dict(self.transport.identity), "context": observed, "policy": policy()}

    def read_context(self):
        return context(self.transport.call(self.target["host"], "context", {"id": self.repository_id}), self.target, self.repository_id)

    def _request(self, request):
        fields(request, "kind destination marker title body")
        need(request["kind"] == "issue.create" and request["destination"] == self.target, "destination_changed")
        token(request["marker"], r"proposal-v1:[0-9a-f]{32}:[0-9a-f]{32}")
        string(request["title"], 256); string(request["body"], BODY_BYTES)

    def _node(self, data, member):
        fields(data, "node"); value = data["node"]
        fields(value, "__typename id " + member)
        need(value["__typename"] == "Repository" and value["id"] == self.repository_id, "repository_changed")
        return value[member]

    def find(self, request):
        self._request(request)
        after, cursors, seen, node_ids, matches, used = None, set(), set(), set(), [], 0
        numbers = set()
        for _ in range(MAX_PAGES):
            data = self.transport.call(self.target["host"], "page", {"id": self.repository_id, "after": after})
            used += len(canonical(data)); need(used <= DISCOVERY_BYTES, "discovery_limit")
            page = self._node(data, "issues"); fields(page, "nodes pageInfo")
            need(type(page["nodes"]) is list and len(page["nodes"]) <= PAGE_NODES)
            for raw in page["nodes"]:
                item = issue(raw, self.target, self.repository_id, request["marker"])
                need(item["id"] not in seen and raw["id"] not in node_ids, "duplicate_issue_id")
                need(item["number"] not in numbers, "duplicate_issue_id")
                seen.add(item["id"]); node_ids.add(raw["id"])
                numbers.add(item["number"])
                if "<!-- " + request["marker"] + " -->" in item["body"]:
                    matches.append(item)
                    need(len(matches) <= 2, "duplicate_matches")
            info = page["pageInfo"]; fields(info, "hasNextPage endCursor")
            need(type(info["hasNextPage"]) is bool)
            after = info["endCursor"]
            if after is not None:
                string(after, 4096); need(after not in cursors, "pagination_cycle"); cursors.add(after)
            if not info["hasNextPage"]:
                return {"traversal_complete": True, "consistency": "not-guaranteed", "matches": matches}
            need(after is not None and bool(page["nodes"]), "pagination_incomplete")
        raise Error("discovery_limit")

    def create(self, request):
        self._request(request)
        data = self.transport.call(self.target["host"], "create", {"repositoryId": self.repository_id, "title": request["title"], "body": request["body"]})
        fields(data, "createIssue"); fields(data["createIssue"], "issue")
        return issue(data["createIssue"]["issue"], self.target, self.repository_id, request["marker"])

    def read(self, artifact):
        fields(artifact, "id number url marker title body destination")
        need(artifact["destination"] == self.target, "destination_changed")
        token(artifact["number"], r"[1-9][0-9]{0,19}")
        number = integer(int(artifact["number"]))
        raw = self._node(self.transport.call(self.target["host"], "read", {"id": self.repository_id, "number": number}), "issue")
        return issue(raw, self.target, self.repository_id, artifact["marker"])


class HttpsGitHubIssues(GitHubIssues):
    """Versioned fixed HTTPS adapter; construction is still source-gated.

    The caller must finish local authority/store checks before constructing it.
    Stored executable paths are comparison data only, never code selection.
    """

    def _transport(self):
        # Private static subclasses may supply a qualified transport subclass.
        # This is trusted code composition, never a runtime provider selector.
        return _HttpsTransport()

    def __init__(self, target, expected=None):
        destination(target)
        self.target = json.loads(canonical(target))
        self.transport = self._transport()
        identity = {"name": "github-issues", "version": 2, "profile": HTTPS_PROFILE,
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "client": dict(self.transport.identity), "https": self.transport.https, "policy": policy()}
        if expected is not None:
            # Crucially, compare runtime/client/source/policy BEFORE the first
            # call can obtain a credential or submit the named-repository read.
            need(type(expected) is dict and "context" in expected, "provider_changed")
            need(canonical(identity) == canonical({key: value for key, value in expected.items() if key != "context"}),
                 "provider_changed")
            need(expected["context"].get("destination") == self.target, "destination_changed")
        observed = context(self.transport.call(target["host"], "named", {"owner": target["owner"], "name": target["repository"]}), self.target)
        if expected is not None:
            need(canonical(observed) == canonical(expected["context"]), "destination_changed")
        self.repository_id = observed["repository_node_id"]
        self.identity = dict(identity, context=observed)


def _worker_main(argv):
    # Credentials already arrived over the private pipe; this fixed HTTP worker
    # neither selects nor executes a credential client or packaging adapter.
    try:
        need(argv == ["--https-worker"], "unsupported_operation")
        _qualify_https_runtime()
    except Error:
        return 1
    return _https_worker()


if __name__ == "__main__":
    sys.exit(_worker_main(sys.argv[1:]))
