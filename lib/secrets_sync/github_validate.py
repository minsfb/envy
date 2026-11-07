"""Isolated HTTPS identity/capability worker. Token input is stdin only.

No redirects, credential discovery, API override, response logging or retries.
The parent enforces a total deadline and captures this worker's static output.
"""
from __future__ import annotations

from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request

# A -I script invocation ignores ambient PYTHONPATH and imports only this trusted
# checkout plus the interpreter's standard library for this worker.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secrets_sync.crypto import strict_json
from secrets_sync.errors import SyncError
from secrets_sync.inventory import Ref, validate_value

MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError("GitHub validation redirect refused")


def validate(host, account, token, ca_file=None, *, profile="github"):
    if profile not in {"github", "git-https"}:
        raise SyncError("unsupported credential validation profile")
    ref = Ref("github", host=host, account=account)
    validate_value(ref, token)
    api_host, path = ("api.github.com", "/user") if host == "github.com" else (host, "/api/v3/user")
    url = "https://" + api_host + path
    context = ssl.create_default_context(cafile=ca_file)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, headers={"Authorization": "token " + token,
                                     "Accept": "application/vnd.github+json",
                                     "User-Agent": "envy-secrets-sync"})
    with opener.open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != url:
            raise SyncError("GitHub identity validation refused")
        scopes = {part.strip() for part in response.headers.get("X-OAuth-Scopes", "").split(",")}
        if profile == "github" and not {"repo", "read:org", "gist"} <= scopes:
            raise SyncError("GitHub token capabilities could not be verified")
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise SyncError("GitHub validation response exceeds limit")
        document = strict_json(raw)
        login = document.get("login") if isinstance(document, dict) else None
        if not isinstance(login, str) or login.lower() != account:
            raise SyncError("GitHub token account does not match")


def main():
    try:
        if len(sys.argv) != 1:
            raise SyncError("unsupported validation arguments")
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise SyncError("validation input exceeds limit")
        request = strict_json(raw)
        if (not isinstance(request, dict) or set(request) not in (
                {"host", "account", "token", "ca_file"}, {"host", "account", "token", "ca_file", "profile"})):
            raise SyncError("invalid validation request")
        ca_file = request["ca_file"]
        if ca_file is not None and (not isinstance(ca_file, str) or not Path(ca_file).is_absolute()):
            raise SyncError("invalid validation CA configuration")
        validate(request["host"], request["account"], request["token"], ca_file, profile=request.get("profile", "github"))
        print("verified")
        return 0
    except BaseException:
        # No exception text, HTTP body, URL, headers or token crosses the worker boundary.
        print("GitHub identity/capability validation unavailable or refused")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
