#!/usr/bin/env python3
"""Owned loopback archive for cooperative, fixed-corpus agent protocol trials.

The operator supplies corpus/state paths; workers receive only the loopback URL.
This separates normal provider responses from the oracle, but does not sandbox a
worker with independent filesystem access. Audit actual tool access separately.
"""

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unicodedata


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ArchiveError(Exception):
    def __init__(self, code, retryable=False, status=400):
        self.code, self.retryable, self.status = code, retryable, status


class Archive:
    def __init__(self, corpus, state_path, mode, trial_id):
        self.corpus, self.state_path = Path(corpus).resolve(), Path(state_path)
        self.index = json.loads((self.corpus / "index.json").read_text())
        self.mode, self.trial_id = mode, trial_id
        if mode not in self.index["modes"]:
            raise ArchiveError("unknown_mode")
        self.artifacts = {a["artifact_id"]: a for a in self.index["artifacts"]}
        self.excluded = set(self.index["modes"][mode]["excluded_artifact_ids"])
        self.lock = threading.Lock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "trial_id": trial_id, "corpus_id": self.index["corpus_id"], "mode": mode,
            "attempts": {}, "events": [],
        }
        if any(self.state[k] != v for k, v in (("trial_id", trial_id), ("mode", mode),
                                              ("corpus_id", self.index["corpus_id"]))):
            raise ArchiveError("state_identity_mismatch")

    def _save(self):
        descriptor, temporary = tempfile.mkstemp(prefix=".archive-", dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded(self.state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _body(self, artifact):
        path = (self.corpus / artifact["path"]).resolve()
        if not path.is_relative_to(self.corpus):
            raise ArchiveError("invalid_fixture_path", status=500)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise ArchiveError("changed_fixture", status=500)
        return content

    def _metadata(self, artifact):
        text = artifact.get("search_snippet_override")
        if text is None:
            text = self._body(artifact).decode("utf-8").split("\n\n", 1)[-1][:160]
        return {"source_id": artifact["artifact_id"], "namespace": "fictional-archive",
                "external_id": artifact["artifact_id"], "title": artifact["title"],
                "type": artifact["document_kind"], "url": "https://example.invalid/archive/" + artifact["artifact_id"],
                "version": {"revision": artifact["revision"], "modified_at": artifact["published_at"]},
                "snippet": text, "published_at": artifact["published_at"]}

    def capabilities(self):
        return {"corpus_id": self.index["corpus_id"], "routes": self.index["routes"],
                "search_behavior": "Exact fixture cases, otherwise every normalized query token must match. No stemming or synonyms.",
                "formats": ["text"], "maximum_page_size": 5,
                "transport": {"method": "POST", "request_content_type": "application/json",
                              "capabilities": {"path": "/capabilities", "request": {}},
                              "seeds": {"path": "/seeds", "request": {}}},
                "search_request": {"path": "/search", "required": ["route", "query"],
                                   "optional": {"page_size": "integer 1–5, default 5", "cursor": "null or exact returned next_cursor"},
                                   "example": {"route": "archive.title", "query": "Tern Queue", "page_size": 5}},
                "read_request": {"path": "/read", "required": ["artifact_id"],
                                 "optional": {"revision": "string, default 1"},
                                 "artifact_id_basis": "Use source_id or external_id from a returned seed/search row"},
                "response_behavior": "Search returns JSON sources/source_ids, pagination, next_cursor and total_matches. Read failures return JSON status and retryable; they contain no source body.",
                "read_behavior": "POST /read returns exact plain text on success; failures return only JSON status.",
                "reference_syntax": "[[artifact_id]] names an archive artifact, not an endorsement or inspection."}

    def seeds(self):
        ids = self.index["modes"][self.mode]["seed_artifact_ids"]
        return {"sources": [self._metadata(self.artifacts[key]) for key in ids if key not in self.excluded]}

    def search(self, request):
        if set(request) - {"route", "query", "page_size", "cursor"}:
            raise ArchiveError("unknown_search_field")
        route, query = request.get("route"), request.get("query")
        if route not in {"archive.title", "archive.body"} or not isinstance(query, str) or not 0 < len(query) <= 256:
            raise ArchiveError("invalid_search")
        query = normalized(query)
        size = request.get("page_size", 5)
        if not query or type(size) is not int or not 1 <= size <= 5:
            raise ArchiveError("invalid_search")
        identity = {"corpus_id": self.index["corpus_id"], "route": route, "query": query,
                    "mode": self.mode, "trial_id": self.trial_id}
        offset, cursor = 0, request.get("cursor")
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if {k: v for k, v in decoded.items() if k != "offset"} != identity:
                    raise ValueError()
                offset = decoded["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ArchiveError("invalid_cursor") from None
        case = next((c for c in self.index["search_cases"] if c["route"] == route and normalized(c["query"]) == query), None)
        if case:
            ids = [key for key in case["result_ids"] if key not in self.excluded]
        else:
            matches = []
            for key, artifact in self.artifacts.items():
                if key in self.excluded:
                    continue
                haystack = artifact["title"]
                if route == "archive.body":
                    haystack += " " + (artifact["search_body_override"] if "search_body_override" in artifact
                                       else self._body(artifact).decode("utf-8"))
                if all(token in normalized(haystack) for token in query.split()):
                    matches.append(artifact)
            matches.sort(key=lambda a: a["artifact_id"])
            matches.sort(key=lambda a: a["published_at"], reverse=True)
            ids = [a["artifact_id"] for a in matches]
        page = ids[offset:offset + size]
        more = offset + size < len(ids)
        next_cursor = base64.urlsafe_b64encode(encoded({**identity, "offset": offset + size})).decode().rstrip("=") if more else None
        result = {"source_ids": page, "sources": [self._metadata(self.artifacts[key]) for key in page],
                  "pagination": "more" if more else "complete", "next_cursor": next_cursor,
                  "total_matches": len(ids), "has_more": more,
                  "note": "Exact archive metadata results; body snippets do not establish inspection."}
        with self.lock:
            self.state["events"].append({"kind": "search", "route": route, "query": query, "offset": offset,
                                         "returned": page, "pagination": result["pagination"]})
            self._save()
        return result

    def read(self, request):
        if set(request) - {"artifact_id", "revision"}:
            raise ArchiveError("unknown_read_field")
        key, revision = request.get("artifact_id"), request.get("revision", "1")
        if not isinstance(key, str) or not isinstance(revision, str):
            raise ArchiveError("invalid_read")
        with self.lock:
            attempt_key = key + ":" + revision
            attempt = self.state["attempts"].get(attempt_key, 0) + 1
            self.state["attempts"][attempt_key] = attempt
            artifact = self.artifacts.get(key)
            if key in self.excluded:
                outcome = {"status": "out_of_scope", "retryable": False}
            elif artifact is None:
                outcome = {"status": "not_found", "retryable": False}
            elif revision != artifact["revision"]:
                outcome = {"status": "revision_unavailable", "retryable": False}
            else:
                choices = self.index["read_contract"]["overrides"].get(key, self.index["read_contract"]["default_outcomes"])
                outcome = choices[min(attempt - 1, len(choices) - 1)]
            result = {"artifact_id": key, "revision": revision, "attempt": attempt,
                      "status": outcome["status"], "retryable": outcome["retryable"]}
            if "code" in outcome:
                result["code"] = outcome["code"]
            content = None
            if result["status"] == "pass":
                try:
                    content = self._body(artifact)
                except (ArchiveError, OSError):
                    result.update(status="fixture_corrupt", code="fixture_corrupt", retryable=False)
            self.state["events"].append({"kind": "read", **result})
            self._save()
            if result["status"] != "pass":
                return 503 if result["retryable"] else 403, "application/json", encoded(result)
            return 200, "text/plain; charset=utf-8", content


def serve(archive, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ArchiveError("invalid_request_size")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ArchiveError("invalid_request")
                if self.path == "/search":
                    status, media, content = 200, "application/json", encoded(archive.search(request))
                elif self.path == "/read":
                    status, media, content = archive.read(request)
                elif self.path == "/seeds" and not request:
                    status, media, content = 200, "application/json", encoded(archive.seeds())
                elif self.path == "/capabilities" and not request:
                    status, media, content = 200, "application/json", encoded(archive.capabilities())
                else:
                    raise ArchiveError("unknown_route", status=404)
            except (ValueError, UnicodeError):
                status, media, content = 400, "application/json", encoded({"code": "invalid_json", "retryable": False})
            except ArchiveError as error:
                status, media, content = error.status, "application/json", encoded({"code": error.code, "retryable": error.retryable})
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--mode", choices=("fresh", "reuse"), required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = serve(Archive(args.corpus, args.state, args.mode, args.trial_id), args.port)
    print(json.dumps({"url": "http://127.0.0.1:" + str(server.server_port)}), flush=True)
    server.serve_forever()
